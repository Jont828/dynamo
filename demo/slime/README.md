# slime + NVIDIA Dynamo on AKS: RL rollouts served by Dynamo

This tutorial runs [slime](https://github.com/THUDM/slime) GRPO training on AKS. The rollouts are
served by SGLang engines that a Dynamo `DynamoGraphDeployment` (DGD) manages, and the trainer runs as
a KubeRay `RayJob`. It follows Dynamo's
[Slime integration guide](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/use-cases/reinforcement-learning/slime.md)
and its [`examples/rl/slime`](https://github.com/ai-dynamo/dynamo/tree/main/examples/rl/slime), and
adds what those leave to you: the trainer deployment, model preparation, the slime training
arguments, and the AKS-specific storage setup.

The demo uses Qwen3-0.6B with GRPO on GSM8K: 10 loops of 64-sample rollout, one training step and
a weight sync to both engines. A run takes about 3 minutes once the images are cached.

A narrated recording of a full run is at <https://asciinema.org/a/H9rsnRJDYIqWG3CN> and in
[`recordings/slime-dynamo-grpo.public.cast`](recordings/slime-dynamo-grpo.public.cast)
(`asciinema play recordings/slime-dynamo-grpo.public.cast`; pod IPs and the AKS node-pool ID are masked).
To reproduce it, run [`demo.sh`](demo.sh); to record it again, see [Re-record the demo](#re-record-the-demo).
Results and findings from the runs are in [`REPORT.md`](REPORT.md).

## How it fits together

```
                          namespace: slime
 ┌──────────────── KubeRay RayJob ────────────────┐     ┌──────────── Dynamo DGD (operator) ────────────┐
 │ head pod (1 H100): Ray head + job driver       │     │ frontend  ── Service slime-sglang-rollout:8000 │
 │   train.sh -> Dynamo's launch-slime.sh         │     │   /generate, round-robin router               │
 │   -> python train.py (slime)                   │     │                                                │
 │   Megatron actor (training, 1 GPU)             │     │ worker-0 (1 H100): SGLang + Dynamo sidecar     │
 │   RolloutManager + dynamo_generate.py ─────────┼─────┼─► Service slime-sglang-engine-0:30000          │
 │                                                │     │ worker-1 (1 H100): SGLang + Dynamo sidecar     │
 │   weight sync: write HF checkpoint ──┐         │     │   Service slime-sglang-engine-1:30000          │
 │   then POST /update_weights_from_disk├─────────┼─────┼─► each engine (native SGLang control API)     │
 └──────────────────────────────────────┼─────────┘     └──────────────────────┬─────────────────────────┘
                                        ▼                                      │
                    /data: Azure Managed Lustre sub-dir (model, data, weight-updates)
```

- **Generation:** slime's rollout function calls `dynamo_generate.generate_streaming`, which sends
  each request to the Dynamo frontend's SGLang-compatible `/generate` endpoint. The frontend routes
  it to a worker, and the worker's Dynamo sidecar forwards it to its SGLang engine.
- **Control and weight updates:** slime calls each SGLang engine directly through its fixed Service.
  It uses `/server_info` for discovery, then pause, flush, `update_weights_from_disk` and continue.
- **slime's own router:** slime still launches its SGLang router and registers the engines with it,
  but no rollout traffic goes through it.

## Do you need multi-node or multi-GPU?

| Part | Needed? | Used here |
|---|---|---|
| Multiple SGLang workers | **Yes**: Dynamo's routing only matters with at least two workers. | 2 workers × 1 GPU |
| Multi-GPU engines (TP/EP) | No | 1 GPU per engine |
| Multi-node | No | All three GPUs can share one node. |
| Multi-GPU trainer | No | 1 GPU (Megatron TP=PP=CP=1) |

Three H100s run the whole demo, so it fits on one ND96isr_H100_v5 node.

## What changed relative to the docs, and why

| Change | Why |
|---|---|
| **Weight sync over the shared disk** (`--update-weight-transport disk`) instead of the default NCCL | NCCL updates fail with stock SGLang 0.5.21 (Dynamo 1.6.x) because it requires `begin/end_weight_update` sessions that slime doesn't send ([ai-dynamo/dynamo#16245](https://github.com/ai-dynamo/dynamo/issues/16245), fix PR closed unmerged). NCCL builds also differ: the slime image has NCCL 2.30.7 and the Dynamo 1.6.1 SGLang image has 2.29.7. Disk sync uses only stock SGLang endpoints. It's also what slime's lab picks for external engines. |
| **`/data` mounted into the DGD workers**; engines load `/data/Qwen3-0.6B` | Disk sync requires the trainer and the engines to see the same path. Loading the same files also makes the engines' initial weights identical to the trainer's. |
| **Lustre sub-dir PV** ([`storage.yaml`](storage.yaml)) | AKS-specific; see [step 1](#1-shared-storage-aks-specific). |
| **Post-write hook** [`slime_dynamo_hooks.make_weights_readable`](rayjob/slime_dynamo_hooks.py) | slime writes the safetensors shards as mode `0600`, owned by root. The Dynamo workers run as uid 1000, so without the hook the first reload fails with `FileNotFoundError` and both engines crash. |
| **Trainer as a KubeRay RayJob** ([`rayjob/`](rayjob)) | The Dynamo docs only say "run Slime in the same namespace". |
| **`PYTHONPATH` and `DYNAMO_ROLLOUT_URL` in the RayJob `runtimeEnvYAML`** | `launch-slime.sh` exports them, but Ray workers on a KubeRay cluster don't inherit the driver's shell. They're started by the pod's raylet. |
| **GPU on the Ray head** | The driver imports Transformer Engine when it creates the Megatron actors. TE needs `libcuda`, which only GPU pods get, so a CPU-only head fails at import. |
| **Full slime training arguments** ([`rayjob/train.sh`](rayjob/train.sh)) | The documented `launch-slime.sh` command has no Megatron model, checkpoint, GRPO or optimizer arguments, so it fails argument validation. |
| **No `--use-score-centering`** | slime rejects score centering with streaming rollouts, which the Dynamo generator uses. |

Dynamo's `launch-slime.sh` and `dynamo_generate.py` are used unchanged, mounted from a ConfigMap. The
DGD is the example's `dynamo.yaml` plus the lines marked `slime-dynamo-aks:`.

## Tested versions

| Component | Version |
|---|---|
| AKS | Kubernetes 1.34.8; 4 × `Standard_ND96isr_H100_v5` (8 × H100 80GB each) |
| Dynamo platform (Helm) | `dynamo-platform` 1.6.1, operator `kubernetes-operator:1.6.1`, Grove v0.1.0-alpha.17 |
| Dynamo images | `dynamo-frontend:1.6.1`, `sglang-runtime:1.6.1` (SGLang 0.5.21, torch 2.13.0+cu130, NCCL 2.29.7) |
| Dynamo example | `examples/rl/slime`, unchanged from v1.5.0 through main (2026-11-04) |
| KubeRay (Helm) | `kuberay-operator` 1.7.1 |
| slime image | `slimerl/slime:nightly-dev-20261027a-cu130`: same digest as `latest-cu130` on 2026-11-03 and the tag in `docker/version.txt`. Ray 2.59.0, torch 2.13.0+cu130, SGLang 0.5.21 (slime-patched), NCCL 2.30.7. |
| Storage | Azure Managed Lustre via `azurelustre.csi.azure.com` (static PV) |

Dynamo 1.5.1 images (SGLang 0.5.18) also worked with this setup.

## Prerequisites

You need:
- An AKS cluster with a GPU node pool and the NVIDIA GPU Operator. The manifests select
  `agentpool: ndh100pool`; change that selector for your pool.
- An RWX filesystem both the trainer and the engines can mount. This tutorial uses Azure Managed
  Lustre.
- Two operators, both installed with Helm:

  ```bash
  # Dynamo platform (see install-dynamo.md for Grove/KAI and other options)
  helm fetch https://helm.ngc.nvidia.com/nvidia/ai-dynamo/charts/dynamo-platform-1.6.1.tgz
  helm install dynamo-platform dynamo-platform-1.6.1.tgz -n dynamo-system --create-namespace
  # KubeRay operator
  helm repo add kuberay https://ray-project.github.io/kuberay-helm/
  helm install kuberay-operator kuberay/kuberay-operator --version 1.7.1 -n kuberay --create-namespace
  ```

  The guide for the Dynamo platform is
  [install-dynamo.md](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/kubernetes/installation/install-dynamo.md).

- On your machine: `kubectl`. The example scripts come from this repo's
  [`examples/rl/slime/`](../../examples/rl/slime/).

Check them:

```bash
kubectl get nodes -l agentpool=ndh100pool -L node.kubernetes.io/instance-type,nvidia.com/gpu.count
helm list -A | grep -E 'dynamo-platform|kuberay'
kubectl get crd dynamographdeployments.nvidia.com rayjobs.ray.io
```

All commands below run from this directory (`demo/slime`). Set `DYNAMO` to the repo root once, for the
steps that read `examples/rl/slime/`:

```bash
DYNAMO=../..   # this repo's root; any ai-dynamo/dynamo checkout works
```

### 1. Shared storage (AKS-specific)

```bash
kubectl apply -f storage.yaml      # namespace slime, PV pv-lustre-slime-dynamo (sub-dir slime-dynamo/), PVC slime-dynamo-data
```

Edit `mgs-ip-address` and the filesystem name in `storage.yaml` for your Lustre instance.

**Don't mount the Lustre root into DGD pods.** Two settings combine badly:
- The Dynamo operator runs DGD pods with `securityContext.fsGroup: 1000`.
- The Azure Lustre CSI driver declares `fsGroupPolicy: File`.

Together they make kubelet recursively `chown`/`chmod` the entire volume every time a worker starts.
A `subPath` doesn't help, because the whole volume is processed before the subPath is applied. On a
shared filesystem this can stall pods in `ContainerCreating` for a long time (an earlier attempt on
this cluster processed 197k files) and changes ownership of other users' files.

A PV that points at a **sub-directory** (`volumeAttributes.sub-dir`) limits that to this
tutorial's few hundred files. The CSI driver creates the sub-dir on first mount.

### 2. (Optional) Pre-pull images

The slime image is 18.5 GB and the Dynamo SGLang runtime is about 14 GB.

```bash
kubectl apply -f prepull.yaml
kubectl -n slime rollout status ds/prepull-gpu --timeout=30m && kubectl -n slime rollout status ds/prepull-frontend --timeout=30m
kubectl delete -f prepull.yaml
```

### 3. Prepare the model and data (once)

This downloads Qwen3-0.6B and GSM8K and converts the checkpoint to Megatron `torch_dist`. It takes
about 1.5 minutes on one GPU.

```bash
kubectl apply -f prepare-job.yaml
kubectl -n slime wait --for=condition=complete job/slime-dynamo-prepare --timeout=30m
kubectl -n slime logs job/slime-dynamo-prepare | tail
```

### 4. Deploy the Dynamo worker set

[`dynamo-dgd.yaml`](dynamo-dgd.yaml) is Dynamo's `examples/rl/slime/dynamo.yaml`, rendered with
1.6.1 images, plus the `slime-dynamo-aks:` lines. To see exactly what changed:

```bash
diff <(DYNAMO_FRONTEND_IMAGE=nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.6.1 \
       SGLANG_RUNTIME_IMAGE=nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.1 \
       DYNAMO_RUNTIME_VERSION=1.6.1 MODEL_PATH=Qwen/Qwen3-0.6B \
       envsubst '${DYNAMO_FRONTEND_IMAGE} ${SGLANG_RUNTIME_IMAGE} ${DYNAMO_RUNTIME_VERSION} ${MODEL_PATH}' \
       < $DYNAMO/examples/rl/slime/dynamo.yaml) dynamo-dgd.yaml
```

Deploy it:

```bash
kubectl -n slime apply -f dynamo-dgd.yaml     # the manifest has no namespace; always pass -n
kubectl -n slime wait --for=condition=Ready dgd/slime-sglang --timeout=15m
```

Run the guide's readiness checks from inside the namespace. All three should return 200:

```bash
W0=$(kubectl -n slime get pod -l dynamo.nvidia.com/slime-role=worker-0 -o name)
kubectl -n slime exec $W0 -- sh -c 'for u in slime-sglang-engine-0:30000/health_generate \
  slime-sglang-engine-1:30000/health_generate slime-sglang-rollout:8000/health; do
  echo "$u -> $(curl -s -o /dev/null -w %{http_code} http://$u)"; done'
```

`/health` on the frontend should list exactly two `slime-slime-sglang-*` instances.

The frontend's `/generate` is token-in only. A request without `input_ids` returns HTTP 400, which is
expected; slime always sends token IDs.

### 5. Run the slime trainer as a RayJob

First, put Dynamo's unmodified example scripts in a ConfigMap. The RayJob mounts it at
`/opt/dynamo-example`.

```bash
kubectl -n slime create configmap dynamo-slime-example \
  --from-file=$DYNAMO/examples/rl/slime/launch-slime.sh \
  --from-file=$DYNAMO/examples/rl/slime/dynamo_generate.py
```

Then start the job:

```bash
kubectl apply -k rayjob            # ConfigMap slime-dynamo-scripts (train.sh, hooks) + RayJob slime-dynamo
kubectl -n slime get rayjob slime-dynamo -w
kubectl -n slime logs -f job/slime-dynamo    # submitter pod: `ray job submit` tails the driver log
```

What the RayJob does:
- KubeRay starts the Ray head pod (1 GPU), then a submitter pod that runs `ray job submit`.
- The job entrypoint is [`train.sh`](rayjob/train.sh). It repeats the readiness checks, then sources
  slime's `scripts/models/qwen3-0.6B.sh` and execs Dynamo's `launch-slime.sh` with the training
  arguments.
- `launch-slime.sh` adds the integration flags:
  `--rollout-external-engine-addrs`, `--custom-generate-function-path dynamo_generate.generate_streaming`
  and `--sglang-incremental-streaming-output`.
- Override the loop count with `NUM_ROLLOUT` in the RayJob `runtimeEnvYAML`.

To watch Ray, run `kubectl -n slime port-forward svc/<raycluster>-head-svc 8265` and open
`localhost:8265`. KubeRay deletes the Ray cluster 60 s after the job finishes; the submitter pod and
its logs remain.

### 6. Verify the integration

1. **RayJob succeeded:**
   ```bash
   kubectl -n slime get rayjob slime-dynamo    # JOB STATUS: SUCCEEDED
   ```
2. **Driver log milestones**, all in `kubectl -n slime logs job/slime-dynamo`:
   - `Detected external SGLang engines: [...engine-0..., ...engine-1...]`
   - per step: `Saving model in HuggingFace format to /data/weight-updates/weight_v0000NN`
   - `rollout N: {... 'rollout/raw_reward': ...}`
   - `step N: {... 'train/train_rollout_logprob_abs_diff': ~0.01 ...}`. A small trainer-vs-rollout
     log-prob gap on every step shows the engines are serving the policy that was just synced. A
     stale engine would make it grow.
3. **Dynamo routed the rollouts.** The frontend logs one `Selected worker` line per request. Strip
   the ANSI colours before counting:
   ```bash
   F=$(kubectl -n slime get pod -l dynamo.nvidia.com/slime-role=frontend -o name)
   kubectl -n slime logs $F | perl -pe 's/\e\[[0-9;]*m//g' | grep 'Selected worker' \
     | grep -o -E 'router_mode="[a-z-]+" worker_id=[0-9]+' | sort | uniq -c
   ```
4. **Both engines reloaded every policy version:**
   ```bash
   for r in worker-0 worker-1; do P=$(kubectl -n slime get pod -l dynamo.nvidia.com/slime-role=$r -o name)
     echo "$r: $(kubectl -n slime logs $P | grep -c 'POST /update_weights_from_disk HTTP/1.1" 200') reloads"; done
   ```
   The engines' `POST /generate` requests come from `127.0.0.1`: that's each worker's Dynamo sidecar
   forwarding the frontend's requests, not slime calling the engine directly.

### 7. Clean up

```bash
kubectl -n slime delete rayjob slime-dynamo
kubectl -n slime delete -f dynamo-dgd.yaml
# Keep storage.yaml/PVC to reuse /data. To remove everything:
kubectl -n slime delete job slime-dynamo-prepare && kubectl -n slime delete configmap dynamo-slime-example
kubectl delete -f storage.yaml     # PV reclaim policy is Retain: the files stay on Lustre
```

## Results (recorded run)

10 GRPO steps with Dynamo 1.6.1, 2 engines and a 1-GPU trainer. Per step: 64 samples (8 prompts × 8),
`--rollout-max-response-len 1024`, lr 1e-6.

| Metric | Value |
|---|---|
| DGD apply → Ready (images cached) | 72 s |
| RayJob apply → SUCCEEDED | 2 min 36 s. Ray head ready and job submitted at 19 s; first weight sync done at 84 s (Megatron init and checkpoint load); 10 loops in 61 s; teardown 3 s. |
| Rollout, 64 samples through Dynamo | 0.9 to 2.5 s |
| Weight sync: write 1.2 GB HF checkpoint to Lustre | 1.4 to 1.6 s (2.9 s for the first) |
| Frontend routing | 640 requests, 320 per worker, `router_mode="round-robin"` |
| Engine reloads (`/update_weights_from_disk` 200) | 11 per engine: the startup sync plus one per step |
| \|trainer − rollout logprob\| | 0.0099 to 0.0121 every step |
| GSM8K mean reward | 0.52 to 0.73 per step. That's the noise of 8 prompts per step; lr 1e-6 over 10 steps isn't a learning curve. |

The same setup also passed four earlier runs, including one with Dynamo 1.5.1 images.

## Troubleshooting (all hit while building this)

| Symptom | Cause | Fix |
|---|---|---|
| `500 ... /update_weights_from_disk`; engine log `FileNotFoundError: .../weight_v000001/model-00001-of-00003.safetensors`; both workers restart | Shards written with mode `0600` by root; the engines run as uid 1000 | The `--custom-update-weight-post-write-path` hook in `rayjob/` |
| Worker pods stuck in `ContainerCreating` with `VolumePermissionChangeInProgress` | fsGroup recursive chown of a large shared volume | Lustre sub-dir PV (step 1) |
| `OSError: libcuda.so.1: cannot open shared object file` in the driver | Ray head without a GPU | Give the head a GPU (`nvidia.com/gpu: 1`) |
| `kubectl delete -f dynamo-dgd.yaml` says NotFound | The example manifest has no namespace | Always pass `-n slime` |
| `400 ... missing field input_ids` from `slime-sglang-rollout:8000/generate` | Native `/generate` is token-in only | Expected for text requests; slime sends `input_ids` |
| `update_weights_from_distributed` HTTP 400 (NCCL transport, Dynamo 1.6.x) | Stock SGLang 0.5.21 requires weight-update sessions ([#16245](https://github.com/ai-dynamo/dynamo/issues/16245)) | Use disk transport, as here |
| NCCL `Message truncated` during weight sync | Different NCCL builds in the trainer and engine images | Use disk transport, or match the images' NCCL versions |
| A worker restarts mid-run | The engine crashed | Slime doesn't recover external engines: delete the RayJob, wait for the DGD to be Ready, and resubmit |

## Scaling up from here

- **More engines:** add `worker-N` components, each with one replica, and a
  `slime-sglang-engine-N` Service to the DGD. Then set `DYNAMO_ENGINE_ADDRS` in `train.sh`. Keep
  each Service selecting exactly one worker.
- **Bigger models and multi-GPU engines:** move the SGLang flags (TP/EP/DP, memory fraction) from
  slime's `--sglang-*` arguments into the DGD worker args. slime reads the topology from
  `/server_info`.
- **Multi-node trainer:** add a `workerGroupSpecs` group to the RayJob with the same image, volumes
  and runtime env, then raise `--actor-num-nodes`. The head still needs a GPU.
- **Routing experiments:** with 8 samples per prompt sharing a prefix, compare the frontend's
  `--router-mode kv` against round-robin using slime's `rollout/prefix_cache_hit_rate` (0 to 0.6
  per step with round-robin here). See Dynamo's
  [RL routing guide](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/use-cases/reinforcement-learning/routing.md).
- **NCCL weight sync:** this needs matching NCCL builds and SGLang weight-update session support
  for stock engines.

## Re-record the demo

`demo.sh` runs steps 4–7 live, so recording it takes a run on the cluster (about 5 minutes, 3 GPUs) after
steps 1–3 and the ConfigMap. It reads no saved logs. You need
[asciinema](https://docs.asciinema.org/getting-started/) 3.x and `python3`.

```bash
asciinema rec --idle-time-limit 3 --window-size 160x45 -c ./demo.sh recordings/slime-dynamo-grpo.cast
python3 -I ../slime-retool/tools/redact_cast.py recordings/slime-dynamo-grpo.cast recordings/slime-dynamo-grpo.public.cast
```

The redaction script masks pod IPs and the node-pool ID, then lists anything else to review. Commit only the
`.public.cast`.

## Files

Apply them in this order: `storage.yaml` → `prepare-job.yaml` → `dynamo-dgd.yaml` → `rayjob/`.

| File | What it contains | Where it comes from |
|---|---|---|
| [`storage.yaml`](storage.yaml) | `slime` Namespace. A static Azure Lustre PV pointing at the `slime-dynamo/` sub-directory (`volumeAttributes.sub-dir`), and the RWX PVC `slime-dynamo-data` that every pod mounts at `/data`. | New, AKS-specific. |
| [`prepull.yaml`](prepull.yaml) | Two optional DaemonSets that pull the slime and Dynamo images onto the nodes ahead of time: the slime image and `sglang-runtime:1.6.1` on GPU nodes, `dynamo-frontend:1.6.1` on all nodes. | New, optional. |
| [`prepare-job.yaml`](prepare-job.yaml) | One-GPU Job in the slime image. It downloads `Qwen/Qwen3-0.6B` and `zhuzilin/gsm8k`, converts the checkpoint to Megatron `torch_dist` with `tools/convert_hf_to_torch_dist.py`, and creates `/data/weight-updates`. Re-running skips finished steps. | slime's quick-start `prepare` and `convert` steps. |
| [`dynamo-dgd.yaml`](dynamo-dgd.yaml) | Three Services: `slime-sglang-rollout:8000` (frontend) and `slime-sglang-engine-{0,1}:30000` (native SGLang API). A `DynamoGraphDeployment`: a frontend with `DYN_SGLANG_ENABLE_GENERATE=1`, and `worker-0`/`worker-1`, each 1 GPU running `sglang.launch_server --incremental-streaming-output --sidecar dynamo.sglang.sidecar`. | Dynamo's `examples/rl/slime/dynamo.yaml` rendered with 1.6.1 images. The only edits are the lines marked `slime-dynamo-aks:` (mount `/data`, load the model from `/data/Qwen3-0.6B`). |
| [`rayjob/rayjob.yaml`](rayjob/rayjob.yaml) | KubeRay `RayJob` `slime-dynamo`. A head-only Ray cluster on the slime image with 1 GPU, 16 CPU and 200 Gi memory. It mounts `/data`, `/dev/shm`, Dynamo's scripts at `/opt/dynamo-example` and this folder's scripts at `/opt/slime-dynamo`. `runtimeEnvYAML` sets `PYTHONPATH` and `DYNAMO_ROLLOUT_URL`. Entrypoint `bash /opt/slime-dynamo/train.sh`; 1 h deadline; the cluster is deleted 60 s after the job finishes. | New. |
| [`rayjob/train.sh`](rayjob/train.sh) | Job entrypoint. It curls the three readiness URLs, sources `scripts/models/qwen3-0.6B.sh`, then execs Dynamo's `launch-slime.sh` with the training arguments: checkpoints, GSM8K data, `--rm-type math`, 10 rollouts × 64 samples, a 1-GPU Megatron trainer, GRPO/Adam, and disk weight sync with the post-write hook. | New; the arguments follow slime's `scripts/run-qwen3-4B*.sh`. |
| [`rayjob/slime_dynamo_hooks.py`](rayjob/slime_dynamo_hooks.py) | `make_weights_readable(args, version_dir, rollout_engines)` chmods each synced checkpoint so the engines (uid 1000) can read it. Passed as `--custom-update-weight-post-write-path`. | New. |
| [`rayjob/kustomization.yaml`](rayjob/kustomization.yaml) | Builds ConfigMap `slime-dynamo-scripts` from `train.sh` and the hook, plus the RayJob. | New. |
| ConfigMap `dynamo-slime-example` (created with `kubectl create configmap`, [step 5](#5-run-the-slime-trainer-as-a-rayjob)) | `launch-slime.sh` and `dynamo_generate.py`, unmodified. | Dynamo's `examples/rl/slime/`. |
| [`demo.sh`](demo.sh) | Narrated end-to-end run of steps 4–7. It deploys the DGD, runs the RayJob, condenses the driver log to milestones, counts frontend routing and engine reloads, then cleans up. Assumes steps 1–3 and the ConfigMap are done. | New. |
| [`recordings/slime-dynamo-grpo.public.cast`](recordings/slime-dynamo-grpo.public.cast) | asciinema v3 recording of `demo.sh` (about 5 min), with pod IPs and the node-pool ID masked. Also at <https://asciinema.org/a/H9rsnRJDYIqWG3CN>. | Recorded 2026-11-05 on the cluster above (run 7 in `REPORT.md`). |
| [`REPORT.md`](REPORT.md) | The test report: versions, the seven runs, the guide's checks with evidence, findings. | Written alongside the runs. |

This folder is a copy of `deploy/aks-dynamo/` from the Jont828/slime branch `jont828-slime-dynamo-aks-demo`
(commit `d054e6fd`), plus the report and the recording.
