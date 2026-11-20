# NeMo RL + Dynamo: GRPO training on AKS with KubeRay

A Kubernetes-native run of NeMo RL's Dynamo generation path (`policy.generation.dynamo: true`, from
[NVIDIA-NeMo/RL#4905](https://github.com/NVIDIA-NeMo/RL/pull/4905)): GRPO fine-tuning of Qwen2.5-1.5B on
OpenMathInstruct-2, with every rollout served by Dynamo's frontend and KV-aware router in front of two vLLM
engines, submitted as one KubeRay **RayJob**. Every step is `kubectl apply`, `get`, `logs`, `wait` or `delete`;
there is no `kubectl exec`. There is no Dynamo operator and no DynamoGraphDeployment either: NeMo RL's driver
starts etcd, NATS and `dynamo.frontend` itself, and its own vLLM engines register with that frontend.

- **Run it:** `./demo.sh` (narrated, about 20 minutes; assumes the one-time setup below is done).
- **Recording of this flow (9 min 17 s, narrated, 9 steps):**
  [asciinema.org/a/GHfS1N9o4d9goBqj](https://asciinema.org/a/GHfS1N9o4d9goBqj) (unlisted), or
  `asciinema play recordings/nemo-rl-dynamo-rayjob.public.cast`, the same recording. `tools/redact_cast.py` masked
  the pod IPs, the frontend's URL and the AKS node-pool ID. The recording is paced: waits are capped at 3 s, and the
  live view's `[+mm:ss]` stamps keep the real elapsed time. To record it again, see
  [Re-record the demo](#re-record-the-demo).

## Quick start

```bash
# Prerequisites (one-time): NVIDIA GPU operator or device plugin, KubeRay operator 1.7+, optional HF token
#   helm install kuberay-operator kuberay/kuberay-operator -n kuberay --create-namespace --version 1.7.1
#   kubectl -n nemo-rl-demo create secret generic hf-token-secret --from-literal=HF_TOKEN=hf_...
# The manifests pin one node (kubernetes.io/hostname); pick a free one with
#   python3 tools/gpu_free.py --need 3 --pick   and edit k8s/*.yaml to match.

kubectl apply -k .                                   # namespace, scripts ConfigMap, cache PVC (idempotent)
kubectl apply -f k8s/setup-job.yaml                  # one-time, ~23 min: image pull, models, dataset
kubectl -n nemo-rl-demo wait --for=condition=complete job/nemo-rl-dynamo-setup --timeout=90m

kubectl apply -f k8s/rayjob.yaml                     # each run
kubectl -n nemo-rl-demo wait --for=jsonpath='{.status.jobDeploymentStatus}'=Running rayjob/nemo-rl-grpo-demo --timeout=15m
kubectl -n nemo-rl-demo logs -f job/nemo-rl-grpo-demo --pod-running-timeout=10m
kubectl -n nemo-rl-demo get rayjob nemo-rl-grpo-demo # JOB STATUS: SUCCEEDED
kubectl apply -f k8s/evidence-job.yaml               # the frontend's own routing lines, from the run's log
kubectl -n nemo-rl-demo logs -f job/nemo-rl-dynamo-evidence
kubectl -n nemo-rl-demo delete rayjob nemo-rl-grpo-demo && kubectl -n nemo-rl-demo delete job nemo-rl-dynamo-evidence

# NeMo RL CI's own Dynamo test, unchanged (Qwen3-0.6B, 2 steps, 2 GPUs):
#   kubectl apply -f k8s/rayjob-smoke.yaml && kubectl -n nemo-rl-demo logs -f job/nemo-rl-grpo-smoke --pod-running-timeout=15m
# When done with the demo entirely (deletes the cache disk too):
#   kubectl delete -k .
```

To change the run, edit `entrypoint` in `k8s/rayjob.yaml`: `STEPS`, `RUN_NAME`, and any NeMo RL override after
the script name (for example `grpo.num_prompts_per_step=16`). `ENGINES=N` also changes the split, but then set
the head's `nvidia.com/gpu` to N + 1 to match.

## How many GPUs?

**Three, on one node:** one for the Megatron trainer (policy and reference model) and one for each of two vLLM
engines, so the KV router has a choice. NeMo RL's split is `cluster.gpus_per_node=3` with
`policy.generation.colocated.resources.gpus_per_node=2`; the remaining GPU trains. Generation can't share the
trainer's GPUs here: NeMo RL rejects colocated generation with `dynamo: true`, because a sleeping engine would
stay registered with the frontend and keep receiving requests. The smoke test, NeMo RL CI's own layout, uses two
GPUs (one trainer, one engine). Multi-node would add a worker group and needs a ReadWriteMany cache; it is out of
scope here.

## Results

Recorded RayJob run `grpo-20261113-001238` (2026-11-13 UTC, node `vmss000000`, 3 GPUs), from the report the job
prints at the end and the run's full log (not in this repo; `scripts/fetch-run.sh` copies a run's log off the cache
volume):

- **Training reward** (mean over 32 prompts × 16 sampled answers, on training prompts; this config runs no
  validation): 0.072 at step 1 → 0.305 at step 25. The first 3 steps average 0.080 and the last 3 0.278; the
  highest is 0.343, at step 19. Answers cut off at the 512-token limit (`truncation_rate`) went from 60–68% in
  steps 1–8 to 44–54% in steps 23–25.
- **Every rollout went through Dynamo's KV router:** 13,312 requests, 6,763 to one engine and 6,549 to the other.
  That is 26 batches of 512: generation runs one step ahead, so the batch for a 26th step was done when training
  stopped at 25. 98% of requests went to an engine that already held part of their prompt in its KV cache
  (26,300 cached blocks in total). The router applied 270,223 KV-cache events from the two engines, with 0 errors.
- **The engines serve the trainer's policy:** `gen_kl_error` was 0.0006–0.0014 on 24 of the 25 steps. On step 11 it
  was 0.00209, just over the 0.002 that NeMo RL's CI requires of its 2-step test; the report flags that with `!`
  and doesn't fail on it. `token_mult_prob_error` was 1.012–1.026. The Megatron trainer pushed its weights into
  both engines over NCCL 25 times, 0.11 s each on average.
- **Time:** 16 min 34 s from `kubectl apply` to `SUCCEEDED`. The first 54 s were the head pod, `ray start` and the
  submitter Job (including a 27 s pull of the submitter's Ray image). The training process ran 15 min 31 s:
  - 2 min 16 s of startup: etcd, NATS and the frontend started at 0:52–0:53, both engines registered at 1:32,
    and the trainer was ready at 1:46;
  - a 46 s first step;
  - then 30.1 s per step (13.7 s of training). The trainer never waited for rollouts.
- **Verdict:** PASS. Every check NeMo RL's CI runs on its Dynamo overlay passed: 2 engines registered, requests
  routed, both engines publishing KV events, events applied, and no Dynamo process left after shutdown. All 25
  steps ran, and the process exited with 0.
- **Also run:** NeMo RL CI's own Dynamo test (`k8s/rayjob-smoke.yaml`, 2 GPUs) passed unchanged in 3 min 38 s. A
  5-step two-engine validation run took 6 min 33 s, including the one-time conversion of the checkpoint for
  Megatron. The setup Job took 23 min, 12 of them pulling the image. Details are in `REPORT.md`.

## What runs

| | |
|---|---|
| Image | `docker.io/jont828/nemo-rl:main-d51b2842-dynamo` (`sha256:f1008ba7…`, 17.3 GB compressed), built from NeMo RL `d51b2842` with CI's own command (`image/build-image.sh`) |
| NeMo RL | `d51b2842` on main, 2 commits after #4905; its CI run passed `L1_Functional_Tests_Dynamo` |
| Dynamo | `ai-dynamo` and `ai-dynamo-runtime` 1.5.1 in the vLLM workers' venv (NeMo RL's `vllm` extra); etcd v3.5.21 and nats-server v2.11.6 in `/opt/dynamo/bin` (`BUILD_DYNAMO=1`) |
| Engines | NeMo RL's own `VllmAsyncGenerationWorker` actors, vLLM 0.29.0, 2 × TP 1, each registered with the frontend through `dynamo.common.backend` |
| Router | `dynamo.frontend --router-mode kv`, started by the driver's `ManagedDynamoRuntime` on the head pod |
| Trainer | Megatron: megatron-core 0.20.0, Megatron-Bridge 0.7.0, TransformerEngine 2.18.0, 1 GPU; torch 2.13.0+cu130, NCCL 2.30.7 on both sides |
| Ray / KubeRay | Ray 2.58.0 (NeMo RL's lock); KubeRay operator 1.7.1; RayJob in `K8sJobMode`; submitter `rayproject/ray:2.58.0-py313` |
| Config | `examples/configs/grpo_math_1B_dynamo_single_controller.yaml` (SingleController GRPO): Qwen2.5-1.5B, 32 prompts × 16 samples per step, max 512 tokens, async with one version of look-ahead, no validation (`val_period: 0`) |
| Cache | `nemo-rl-dynamo-cache`: 512 GiB Premium SSD Azure Disk (`managed-csi-premium`): models, OpenMathInstruct-2 (all splits, 25 GB), Megatron checkpoints, vLLM compile cache, run logs |

How the Dynamo side compares with the other RL demos on this cluster:

| | verl (verl-recipe) | NeMo RL (`dynamo: true`) | Slime |
|---|---|---|---|
| Who starts Dynamo | the recipe's `DynamoHttpServer` Ray actor | NeMo RL's driver (`ManagedDynamoRuntime`) | the Dynamo operator, from a DGD |
| Engines | `dynamo.vllm` processes | NeMo RL's own vLLM Ray actors, registered via `dynamo.common.backend` | SGLang engines with Dynamo sidecars |
| Placement | colocated (engines sleep while training) | non-colocated (required) | separate pods |
| Weight update | CUDA IPC | NeMo RL's native NCCL refit | written to disk, then reloaded |
| Kubernetes objects per run | 1 RayJob | 1 RayJob | DGD + RayJob |

## Known rough edges

- **No public image to pull.** NeMo RL publishes release images only (v0.8.0 still has the removed
  `backend: dynamo` path), and CI's nightly images go to registries outsiders can't read. This kit's image was
  built in the cluster from NeMo RL main with CI's own command: 1 h 38 min cold, because CI restores its
  source-built CUDA wheels (DeepEP, flash-attn, FlashMLA, mamba-ssm, …) from a cache image.
- **`tests/functional/` isn't in the image.** NeMo RL's `.dockerignore` lists `tests/functional/*/`, which Docker
  cleans to `tests/functional/*`; CI runs its tests from a mounted checkout. The smoke RayJob ships CI's two
  scripts in the scripts ConfigMap (byte-identical, `scripts/ci/`) and puts them back before running them.
- **No uv cache in the image** (`STRIP_UV_CACHE=1` keeps it at 17 GB): run `/opt/nemo_rl_venv/bin/python`, or
  set `UV_NO_SYNC=1` before anything calls `uv run`, as `smoke-entrypoint.sh` does.
- **The first OpenMathInstruct-2 load is heavy.** `load_dataset(…, split="train_1M")` (datasets 5.0.1) downloads
  and prepares all four splits: 25 GB and about 10 minutes, done once by the setup Job.
- **The reward starts low with this config.** It caps prompt plus answer at 512 tokens, and Qwen2.5-1.5B (a base
  model) hits that limit in 60–68% of its answers in the first steps. The reward starts near 0.07, and about half
  the prompt groups score the same on all 16 answers, so they give the update no signal. Over the recorded 25 steps
  the reward rose to about 0.3 as fewer answers were cut off. A larger `policy.max_total_sequence_length` cuts off
  fewer answers; that wasn't tried here. The runs don't save the answers (`log_full_train_data: false`), so how
  many cut-off answers already held a `\boxed{}` answer isn't known.
- **Dynamo 1.5.1's router log has no `effective_cached_blocks`.** `Selected worker` names the engine; the cached
  prefix is `overlap_blocks` on each `route request completed` line, which is what the report and the evidence
  Job count.
- **The frontend's lines live in the run log, not in a pod log.** It runs inside the head pod as a child of the
  driver, and the head pod is deleted 30 s after the job ends; `k8s/evidence-job.yaml` reads them back from the
  cache volume.
- **Shared cluster.** Other teams' jobs took whole nodes or tainted them during this work, so every manifest
  pins one node by hostname. Check `tools/gpu_free.py` and edit the pin before a run.

## Re-record the demo

`demo.sh` runs the RayJob live, so recording it takes a run on the cluster (about 20 minutes, 3 GPUs) after the
one-time setup in [Quick start](#quick-start). It reads no saved logs. You need
[asciinema](https://docs.asciinema.org/getting-started/) 3.x and `python3`.

```bash
asciinema rec --headless --window-size 120x42 -c ./demo.sh recordings/nemo-rl-dynamo-rayjob.raw.cast
python3 tools/pace_cast.py recordings/nemo-rl-dynamo-rayjob.raw.cast recordings/nemo-rl-dynamo-rayjob.cast
python3 tools/redact_cast.py recordings/nemo-rl-dynamo-rayjob.cast recordings/nemo-rl-dynamo-rayjob.public.cast
```

`pace_cast.py` caps the waits for viewing. `redact_cast.py` masks pod IPs and the node-pool ID, then scans for
anything else to review, such as tokens, GUIDs, emails or home directories. Commit only the `.public.cast`.

## Files

| Path | Role |
|---|---|
| `demo.sh` | Narrated RayJob demo; drives `kubectl` from your machine |
| `kustomization.yaml` | Namespace, scripts ConfigMap, cache PVC |
| `k8s/namespace.yaml`, `k8s/cache-pvc.yaml` | The namespace and the cache volume |
| `k8s/setup-job.yaml` | One-time Job that pulls the image onto the node and fills the cache (`scripts/setup-cache.sh`) |
| `k8s/rayjob.yaml` | The training RayJob (3 GPUs, two engines) |
| `k8s/rayjob-smoke.yaml` | NeMo RL CI's Dynamo overlay test as a RayJob (2 GPUs) |
| `k8s/evidence-job.yaml` | Reads the frontend's routing lines from the newest run's log (`scripts/evidence.sh`) |
| `scripts/rayjob-entrypoint.sh` | Job driver: `run_grpo_single_controller.py` with the Dynamo config, the live view, then the report |
| `scripts/watch_run.py`, `scripts/report_run.py` | Live view and results, from the run's `run.log` |
| `scripts/smoke-entrypoint.sh`, `scripts/ci/` | The smoke test's driver, and CI's two test scripts it restores |
| `scripts/env.sh` | Shared paths and the config name |
| `tools/gpu_free.py` | Free GPUs per node |
| `tools/pace_cast.py`, `tools/redact_cast.py` | Re-time a recording for viewing; mask pod IPs and the node-pool ID for sharing |
| `image/build-image.sh` | The image build (in-cluster BuildKit); it writes `build.log` and `metadata.json` next to itself. `image/NOTES.md` is the build and validation log, and `image/metadata.json` the build's metadata |
| `scripts/follow-rayjob.sh`, `scripts/fetch-run.sh` | Validation helpers: save a RayJob's log; copy a run's `run.log` off the cache volume |
| `recordings/nemo-rl-dynamo-rayjob.public.cast` | The demo recording, paced and redacted |
| `REPORT.md` | What was run, versions, timeline, results and problems. The job and run logs it cites aren't in this repo |
