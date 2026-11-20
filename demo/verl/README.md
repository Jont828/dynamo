# verl + Dynamo: GRPO training on AKS with KubeRay

A Kubernetes-native run of the [verl-recipe Dynamo backend](https://github.com/verl-project/verl-recipe/tree/main/dynamo):
GRPO fine-tuning of Qwen2.5-7B-Instruct on GSM8K, with every rollout served by a Dynamo frontend (KV-aware router)
in front of four vLLM engines, submitted as a KubeRay **RayJob**. Every step is `kubectl apply`, `get`, `logs`,
`wait` or `delete`; there is no `kubectl exec`.

- **Run it:** `./demo.sh` (narrated, about 20 minutes; assumes the one-time setup below is done).
- **Recording of this flow (7.5 min, narrated, 9 steps):** `asciinema play recordings/verl-dynamo-rayjob.public.cast`.
  Not uploaded. `tools/redact_cast.py` masked the pod IPs and the AKS node-pool ID, and the recording is paced from
  a 17-minute capture. To record it again, see [Re-record the demo](#re-record-the-demo).
- **Earlier exec-based flow:** [asciinema.org/a/eUIQuhyaa0iufk9S](https://asciinema.org/a/eUIQuhyaa0iufk9S)
  (see [Exec-based flow](#exec-based-flow)).

## How verl is launched

- **The entrypoint is a Python module:** `python3 -m verl.trainer.main_ppo <Hydra overrides>`. The recipe adds its
  presets with `--config-path recipe/dynamo/config --config-name dynamo_trainer_v1_colocate` and registers the
  `dynamo` rollout backend through `VERL_USE_EXTERNAL_MODULES=recipe.dynamo.register`.
- **Ray is started or joined by that process.** `main_ppo` calls `ray.init()`:
  - with no `RAY_ADDRESS`, it starts a private Ray cluster inside the process (the exec-based flow);
  - under `ray job submit`, `RAY_ADDRESS` is set, so it joins the existing cluster. verl's multi-node docs use this
    path, and so does the RayJob here.
- **Dynamo runs inside Ray, not as a separate deployment.** The recipe's `DynamoHttpServer` Ray actor spawns etcd,
  NATS, `dynamo.frontend` and one `dynamo.vllm` process per engine as subprocesses on the trainer's node. In the
  colocated preset the engines must share GPUs with the trainer (weights move over CUDA IPC), so the Dynamo
  Kubernetes operator and DynamoGraphDeployments aren't used.

## What KubeRay changes

| | Exec-based flow (`demo-exec.sh`) | RayJob flow (`demo.sh`) |
|---|---|---|
| Start training | `kubectl exec … setsid -f bash run-demo-train.sh` | `kubectl apply -f k8s/rayjob.yaml` |
| Ray cluster | private, started by `main_ppo` inside a bare pod | created by KubeRay; `main_ppo` joins it as a Ray job |
| Watch | `kubectl exec … watch_run.py` | `kubectl logs -f job/verl-grpo-demo` |
| Status | read files in the pod | `kubectl get rayjob verl-grpo-demo` (`SUCCEEDED`/`FAILED`) |
| Time limit | `timeout` inside a script | `activeDeadlineSeconds: 3600` |
| Teardown | delete the pod by hand; kill orphaned vLLM `EngineCore` processes | automatic: KubeRay deletes the cluster 30 s after the job ends |
| One-time setup | `kubectl exec … build-env.sh` | `kubectl apply -f k8s/setup-job.yaml` (a Job) |
| Multi-node | not possible | add a worker group (needs an RWX cache) |
| Ray dashboard | none | `kubectl port-forward svc/<cluster>-head-svc 8265` |

What KubeRay doesn't remove: the Dynamo image ships without Ray and verl, so the head pod installs them from the
cache's wheelhouse (~30 s) before KubeRay's `ray start`. A prebuilt image (Dynamo image + wheelhouse) would remove
that step and the cache's build half; this subscription has no container registry, so the demo keeps the cache.

## Quick start

```bash
# Prerequisites (one-time): NVIDIA GPU operator or device plugin, KubeRay operator 1.7+, optional HF token
#   helm install kuberay-operator kuberay/kuberay-operator -n kuberay --create-namespace --version 1.7.1
#   kubectl create secret generic hf-token-secret --from-literal=HF_TOKEN=hf_...

kubectl apply -k .                                   # scripts ConfigMap + cache PVC (idempotent)
kubectl apply -f k8s/setup-job.yaml                  # one-time, ~15-20 min: verl, flash-attn, model, data
kubectl wait --for=condition=complete job/verl-dynamo-setup --timeout=90m

kubectl apply -f k8s/rayjob.yaml                     # each run
kubectl wait --for=jsonpath='{.status.jobDeploymentStatus}'=Running rayjob/verl-grpo-demo --timeout=15m
kubectl logs -f job/verl-grpo-demo --pod-running-timeout=10m
kubectl get rayjob verl-grpo-demo                    # JOB STATUS: SUCCEEDED
kubectl delete rayjob verl-grpo-demo                 # before the next run

# When done with the demo entirely (deletes the cache disk too):
#   kubectl delete job verl-dynamo-setup && kubectl delete -k .
```

To change the run, edit `entrypoint` in `k8s/rayjob.yaml` (`STEPS`, `TEST_FREQ`, `RUN_NAME`, extra Hydra overrides).

## How many GPUs?

**One 8-GPU node.** FSDP shards the 7B actor and reference policy over all 8 GPUs, and the four vLLM engines
(tensor parallel 2) share those GPUs: they sleep while the trainer updates and wake with the new weights.
The recipe is tested on one node; more nodes only add rollout throughput. A 0.5B model fits on 1 GPU.

## Results

Recorded RayJob run `grpo-20261106-024855` (2026-11-06 UTC; KubeRay placed the head on `vmss000001` and the cache
disk followed it), compared with the exec-based run from 2026-11-04:

| GSM8K test accuracy (1,319 questions, greedy) | Before training | After step 5 | After step 10 | After step 15 |
|---|---:|---:|---:|---:|
| RayJob (`demo.sh`) | 0.784 | 0.893 | 0.904 | **0.911** (+0.127) |
| Exec-based (`demo-exec.sh`) | 0.785 | 0.898 | 0.911 | 0.922 (+0.136) |

- **Training reward** (512 sampled answers per step): 0.814 at step 1 → 0.953 at step 15. Answers the strict grader
  can parse (`#### 71`, not `#### $71`) went from 83% to 100%; 189 test questions went from wrong to right, 21 the
  other way.
- **Dynamo serves the trainer's policy:** engine vs. trainer token-probability correlation 0.991–0.999 on every step;
  8 trainer ranks × 16 weight pushes into the engines, 0 errors, 5.3 s each. KV-aware routing served 52.8% of
  prompt tokens from cache.
- **Time:** 14 min 19 s from `kubectl apply` to `SUCCEEDED`. About 2 min of that is the head pod, disk attach,
  bootstrap and job submission; the training process ran 12 min 9 s, including 5 min of startup, 15 steps at
  19.8 s each, and 4 validations. KubeRay deleted the cluster 30 s after the job ended.
- **Verdict:** PASS. The one `WARN` is the recipe's startup logprob probe reaching the frontend before its model
  route was up; the recipe retried it and the probe passed.
- **Also run:** a 2-step validation RayJob (`validate-rayjob-1`) succeeded in 7 min 10 s (accuracy 0.792 → 0.863).
  The setup Job took 13 min on a node with the image cached (18 min including the 9.6 GB image pull), 9 min of
  it compiling flash-attn.

## What runs

| | |
|---|---|
| Image | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1` (ai-dynamo 1.5.1, vLLM 0.28.0, PyTorch 2.13); no Ray |
| Added at pod start | Ray 2.60.0, verl `6cbca9ce` (the recipe's `REQUIRED_VERL.txt`), TransferQueue 0.1.9, flash-attn 2.8.3.post1 (sm_90), accelerate 1.13.0, transformers 5.10.4 |
| verl-recipe | `242c73fc`; its `dynamo/` directory is identical to current `main` (`0a9360b`) |
| KubeRay | operator 1.7.1; RayJob in `K8sJobMode`; submitter image `rayproject/ray:2.60.0-py312` |
| Trainer | `verl.trainer.main_ppo`, preset `dynamo_trainer_v1_colocate` (V1 trainer, `colocate_async`) |
| Algorithm | GRPO, 64 prompts × 8 samples per step, lr 1e-6, KL loss 0.001, max response 1,024 tokens |
| Rollout | `rollout.name=dynamo`, 4 × `dynamo.vllm` engines at TP=2, `router_mode=kv`, sleep/wake around each update |
| Validation | full GSM8K test split (1,319), greedy, before training and every `TEST_FREQ` steps |
| Cache | `verl-dynamo-cache`: 512 GiB Premium SSD Azure Disk (`managed-csi-premium`) |

Why Dynamo 1.5.1 and not the cluster's Dynamo platform 1.6.1: the recipe pins and tests vLLM 0.28.0, which is what
Dynamo 1.5.x ships; Dynamo 1.6.1 ships vLLM 0.31.0, which the recipe hasn't been tested with.

## Known rough edges

- **The Dynamo vLLM image has no Ray.** KubeRay runs the container's own command before its generated `ray start`
  (`bash pod-bootstrap.sh && ulimit …; ray start --head … --block`); `pod-bootstrap.sh` installs Ray and verl there.
- **Ray version must match:** pip resolved Ray 2.60.0 for verl's unpinned dependency (an October build got 2.59.0).
  The setup Job warns if it differs from the `rayVersion` and submitter image in `k8s/rayjob.yaml`.
- **`rayVersion` matters:** at 2.53 or later KubeRay uses HTTP health probes; older values inject `wget` probes,
  which this image can't run.
- **Storage:** node-local NVMe from Azure Container Storage (`kaito-local-nvme-disk`) is unsafe for the cache.
  Its PVs carry no node affinity, a head pod landed on another node, and the failover reconciler deleted the
  original volume. The cache is now an Azure Disk.
- The image's accelerate 1.12 breaks loading 7B models under transformers 5; the setup Job installs 1.13.
- Dynamo options need a `++` Hydra prefix; the default logger needs W&B, so the run uses `trainer.logger=["console"]`.
- Ray workers block-buffer stdout; the run sets `PYTHONUNBUFFERED=1` so each step shows live.
- About 1 in 50 engine launches never registers with the frontend; the recipe gives up after 600 s. Re-apply the
  RayJob if the log shows `alert … not healthy within`.

## Re-record the demo

`demo.sh` runs the RayJob live, so recording it takes a run on the cluster (about 20 minutes, one 8-GPU node)
after the one-time setup in [Quick start](#quick-start). It reads no saved logs. You need
[asciinema](https://docs.asciinema.org/getting-started/) 3.x and `python3`.

```bash
asciinema rec --headless --window-size 120x42 -c ./demo.sh recordings/verl-dynamo-rayjob.raw.cast
python3 tools/pace_cast.py recordings/verl-dynamo-rayjob.raw.cast recordings/verl-dynamo-rayjob.cast
python3 tools/redact_cast.py recordings/verl-dynamo-rayjob.cast recordings/verl-dynamo-rayjob.public.cast
```

Commit only the `.public.cast`. The exec-based flow records the same way with `-c ./demo-exec.sh` and the
`verl-dynamo-grpo` names.

## Files

| Path | Role |
|---|---|
| `demo.sh` | Narrated RayJob demo; drives `kubectl` from your machine |
| `kustomization.yaml` | Scripts ConfigMap + cache PVC |
| `k8s/cache-pvc.yaml` | The cache volume |
| `k8s/setup-job.yaml` | One-time Job that fills the cache (`scripts/setup-cache.sh`) |
| `k8s/rayjob.yaml` | The training RayJob |
| `scripts/rayjob-entrypoint.sh` | Job driver: runs training, prints the live view, then the report |
| `scripts/run-demo-train.sh` | `python3 -m verl.trainer.main_ppo` with the GRPO + Dynamo settings, validation, metrics sidecar, summary |
| `scripts/watch_run.py`, `scripts/report_run.py` | Live view and results, from the run's `driver.log` |
| `scripts/setup-cache.sh`, `build-env.sh`, `apply-accelerate-fix.sh`, `pod-bootstrap.sh` | Cache build and per-pod install |
| `tools/gpu_free.py` | Free GPUs per node |
| `tools/pace_cast.py` | Re-times a raw recording for viewing (caps waits, holds screens) |
| `tools/redact_cast.py` | Shareable copy of a recording: masks pod IPs and the node-pool ID, then scans for secrets |
| `demo-exec.sh`, `k8s/pod.yaml` | The earlier exec-based flow (needs the Lustre volume) |
| `recordings/verl-dynamo-rayjob.public.cast` | The RayJob demo's recording, paced and redacted |
| `recordings/verl-dynamo-grpo.public.cast` | The exec-based flow's recording, paced and redacted |

## Exec-based flow

The first version ran everything in one bare pod (`k8s/pod.yaml`) driven by `kubectl exec`, with the cache on the
`pvc-lustre-ray` Lustre volume. `demo-exec.sh` is that version. Its recording is
[asciinema.org/a/eUIQuhyaa0iufk9S](https://asciinema.org/a/eUIQuhyaa0iufk9S) (unlisted; uploaded as the redacted
`recordings/verl-dynamo-grpo.public.cast`). That run, on 2026-11-04, reached GSM8K test accuracy
0.785 → 0.922 in 15 steps.

`demo-exec.sh` needs the Lustre volume, which doesn't mount as of 2026-11-05: AKS reimaged every node to kernel
`6.8.0-1073-azure`, and the Lustre CSI driver (v0.4.0, client 2.16.1) has no client package for that kernel yet
(newest is for 1071). Every `csi-azurelustre-node` pod is in CrashLoopBackOff.
