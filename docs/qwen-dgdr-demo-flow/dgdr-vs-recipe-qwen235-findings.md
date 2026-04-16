# DGDR vs Recipe: Qwen3-235B-A22B-FP8 Disaggregated TRT-LLM Deployment Findings

**Date**: 2026-04-15
**Cluster**: AKS with 4x ND H100 nodes (8 GPU each, 32 GPUs total)
**Model**: Qwen/Qwen3-235B-A22B-FP8 (MoE, 235B params, 22B active)
**Backend**: TensorRT-LLM (PyTorch backend)
**Mode**: Disaggregated prefill/decode

## Summary

The DGDR profiler successfully profiled the model and generated a DGD spec, but the
generated spec could not be deployed due to several missing runtime requirements that
the profiler does not currently account for. A manually adapted recipe (based on
`recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`) was used to work through
these issues iteratively.

## Side-by-Side Comparison

| Setting | DGDR-Generated DGD (`trtllm-disagg`) | Working Recipe (`qwen3-235b-disagg`) |
|---------|--------------------------------------|--------------------------------------|
| **Image** | `jont828/tensorrtllm-runtime:demo-v5` | `nvcr.io/nvidia/ai-dynamo/tensorrtllm-runtime:1.0.0` |
| **Shared Memory** | Not set (defaults to **8Gi**) | **256Gi** |
| **IPC_LOCK capability** | Not set | `capabilities.add: [IPC_LOCK]` |
| **UCX transport** | Not set (defaults to IB verbs) | `UCX_TLS=tcp`, `UCX_NET_DEVICES=all` |
| **Prefill TP** | TP=4, EP=4 | TP=4, MoE-TP=4, EP=1 |
| **Prefill GPUs** | 4 GPU x 2 replicas = 8 | 4 GPU x 4 replicas = 16 |
| **Prefill batch** | `max-batch-size 1`, `max-num-tokens 3520` | `max-batch-size 2`, `max-num-tokens 8192` |
| **Decode TP** | **TP=8**, EP=4, `--enable-attention-dp` | **TP=4**, EP=4 |
| **Decode GPUs** | **8 GPU x 2 replicas = 16** | **4 GPU x 4 replicas = 16** |
| **Decode multinode** | `multinode.nodeCount: 8` (requires Grove) | None (single-node pods) |
| **Decode batch** | `max-batch-size 24`, `max-num-tokens 32` | `max-batch-size 512`, `max-num-tokens 1024` |
| **Engine config** | Inline `--override-engine-args` JSON | ConfigMap YAML with DeepGEMM, CUDA graphs |
| **Planner** | Included (crashes: missing `pmdarima`) | Not included |
| **Frontend** | `python3 -m dynamo.frontend` | `python3 -m dynamo.frontend --router-mode kv` |
| **Total GPUs** | 24 | 32 |

## Failure Modes Encountered

### 1. Shared Memory Too Small (8Gi default)

**Symptom**: Workers crash immediately with `Cannot allocate memory` UCX errors and
`RuntimeError: Executor worker returned error`.

**Root cause**: TRT-LLM uses MPI for multi-GPU communication within a pod. MPI's
shared memory transport (`/dev/shm`) defaults to 8Gi via Kubernetes `emptyDir.medium=Memory`.
TRT-LLM's MPI workers need much more for IPC with large models.

**Fix**: Set `sharedMemory.size: 256Gi` on all worker services in the DGD spec.

**DGDR action item**: The profiler/operator should **always** set `sharedMemory.size: 256Gi`
(or a configurable large value) for TRT-LLM workers that use tensor parallelism.

### 2. CUDA OOM at TP=2 (Recipe's Original Config)

**Symptom**: Prefill workers crash with `torch.OutOfMemoryError: CUDA out of memory.
GPU 0 has a total capacity of 79.18 GiB of which 266.56 MiB is free`.

**Root cause**: The original recipe specifies TP=2 for prefill (2 GPUs per worker).
Qwen3-235B-A22B-FP8 requires ~78GB per GPU at TP=2, leaving only 266MB for KV cache
and engine buffers. This is insufficient.

**Fix**: Use TP=4 minimum for both prefill and decode on H100 80GB.

**Recipe action item**: The recipe at `recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`
should be updated to use TP=4 for prefill, not TP=2.

### 3. NIXL UCX Backend Fails Due to Memlock Limits (Despite InfiniBand Being Present)

> **UPDATE 2026-04-16**: This issue has been **resolved** by deploying two DaemonSets
> on the AKS cluster that fix `RLIMIT_MEMLOCK` and expose RDMA devices to pods. See
> `aks-infiniband-rdma-setup.md` for full details. The `UCX_TLS=tcp,cuda_copy,cuda_ipc`
> workaround is no longer needed — UCX can now auto-detect and use native InfiniBand
> verbs (`rc_verbs`/`rc_mlx5`) for NIXL KV cache transfers over the 400 Gb/s RDMA fabric.
> The DGDR overrides should now use `IPC_LOCK` capability + `rdma/hca_shared_devices_a`
> resource request instead of forcing TCP transport.

**Symptom**: Workers load model successfully (~17 min), then crash at executor creation:
`RuntimeError: Failed to create NIXL backend: UCX` with underlying error
`Failed to create UCX worker: Input/output error`.

**Root cause**: The ND H100 v5 nodes **do** have InfiniBand hardware — each GPU has a
dedicated 400 Gb/s NVIDIA Quantum-2 CX7 InfiniBand NIC, and the IB devices are exposed
to pods (`/dev/infiniband/uverbs0-8`, 9 mlx5 devices, `PORT_ACTIVE` with InfiniBand
link layer). The problem was **not** missing InfiniBand, but three cluster-level issues:

1. **`ib_umad` kernel module not loaded** — the RDMA shared device plugin needs it
2. **`RLIMIT_MEMLOCK` = 64KB** — inherited from kubelet's systemd unit, blocking `mlock()`
3. **`/dev/infiniband/*` not exposed to pods** — needs a Kubernetes RDMA device plugin

These were resolved by deploying:
- An `ib-node-config` DaemonSet that loads `ib_umad`, sets `LimitMEMLOCK=infinity`
  on both containerd and kubelet via systemd drop-ins, and restarts both services
- The Mellanox `k8s-rdma-shared-dev-plugin` DaemonSet that exposes RDMA device files
  to pods requesting `rdma/hca_shared_devices_a`

**Previous workaround** (no longer needed): Set `UCX_TLS=tcp,cuda_copy,cuda_ipc` and
`UCX_NET_DEVICES=all` to force TCP transport instead of IB verbs.

**Current fix**: With the cluster-level fixes in place, worker pods only need:
```yaml
resources:
  limits:
    rdma/hca_shared_devices_a: 1
```
`IPC_LOCK` capability is **not needed** — `RLIMIT_MEMLOCK=unlimited` is set at
the kubelet level (via the `ib-node-config` DaemonSet), so `mlock()` works
without the capability, even as non-root (`uid=1000`) with zero effective
capabilities. `IPC_LOCK` only bypasses the rlimit check; with unlimited rlimit,
the check passes on its own.

**Remaining gap**: GPU Direct RDMA (`nvidia_peermem` kernel module) is not available
on the standard AKS Ubuntu image. NIXL RDMA transfers go through host memory
(GPU → host → RDMA → host → GPU) rather than zero-copy (GPU → RDMA → GPU). This is
still much faster than TCP but not optimal. See `aks-infiniband-rdma-setup.md` for details.

**DGDR action item**: The profiler should detect whether RDMA devices and sufficient
memlock limits are available. If so, request `rdma/hca_shared_devices_a` on workers
and skip the TCP fallback. If not, fall back to `UCX_TLS=tcp,cuda_copy,cuda_ipc`.

### 4. Decode Multinode Misconfiguration (Grove Required)

**Symptom**: The DGDR-generated DGD sets `multinode.nodeCount: 8` on the decode worker.
This requires Grove (PodCliqueSets) to be installed, but the Helm chart has
`global.grove.install: false`.

**Root cause**: The profiler chose TP=8 for decode, which fits on a single 8-GPU node.
However, the generated DGD incorrectly sets `multinode.nodeCount: 8`, which would
request 8 *nodes* per decode replica — likely confusing GPU count with node count.

**Fix**: Either remove `multinode` entirely (TP=8 fits on one node) or install Grove.
The working recipe uses TP=4 single-node pods to avoid the dependency.

**DGDR action item**: Fix the `nodeCount` calculation. When TP size <= GPUs per node,
`multinode` should not be set. `nodeCount` should only be set when TP > GPUs per node
(true multi-node tensor parallelism).

### 5. Planner Image Missing `pmdarima`

**Symptom**: Planner pod crashes in `CrashLoopBackOff` with
`ModuleNotFoundError: No module named 'pmdarima'`.

**Root cause**: The demo planner image (`jont828/dynamo-planner:demo-v5`) does not have
the `pmdarima` package installed. Note that this is a **DGDR-specific issue** — the
planner is only included when the DGDR generates a DGD and the `features.planner`
field is set. The hand-crafted recipes (e.g., `recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`)
do **not** include a planner service at all.

**Fix**: Either fix the planner image or remove the Planner service from the DGD.
Static deployments don't need the planner.

**Can we just remove the planner from the DGDR and expect it to work?** Yes — the
planner is an optional component for dynamic GPU scaling (auto-scaling worker replicas
based on load/SLA). Removing it means the deployment uses a fixed number of replicas
as specified in the DGD. The core inference flow (frontend → prefill → decode) works
independently of the planner. To remove it from the DGDR, simply omit the
`features.planner` field from the DGDR spec. The remaining issues (#1–4, #6–8) still
need to be fixed independently — the planner crash is orthogonal to those.

## What the DGDR/Operator Should Generate for TRT-LLM Disagg

Based on these findings, a correct DGDR-generated DGD for TRT-LLM disaggregated
deployment should include:

```yaml
# On all TRT-LLM worker services:
sharedMemory:
  size: 256Gi
extraPodSpec:
  mainContainer:
    securityContext:
      capabilities:
        add:
          - IPC_LOCK
# If RDMA/IB is available on the cluster (memlock fixed, RDMA device plugin deployed):
resources:
  limits:
    rdma/hca_shared_devices_a: 1
# If RDMA/IB is NOT available (no device plugin, memlock still limited):
#   env:
#     - name: UCX_TLS
#       value: tcp,cuda_copy,cuda_ipc
#     - name: UCX_NET_DEVICES
#       value: all
```

## DGDR Overrides: What's Available and What's Needed

The DGDR CRD supports an `overrides` field with two sub-fields:

### Currently Available Overrides

#### `overrides.profilingJob` (batchv1.JobSpec)
Allows overriding the profiling Job specification. Used today for things like injecting
HF_TOKEN secrets into the profiler container:

```yaml
spec:
  overrides:
    profilingJob:
      template:
        spec:
          containers:
            - name: profiler
              env:
                - name: HF_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: hf-token-secret
                      key: HF_TOKEN
```

#### `overrides.dgd` (raw DynamoGraphDeployment)
Allows providing a full or partial DGD to use as the base for the generated deployment.
**Current limitation**: The controller currently only reads `metadata.name` from the
DGD override — the rest of the DGD spec from the override is **not merged** into the
profiler output. The override doc says "Fields from profiling results are merged on
top" but the code at `computeDGDName()` only extracts the name.

```yaml
spec:
  overrides:
    dgd:
      apiVersion: nvidia.com/v1alpha1
      kind: DynamoGraphDeployment
      metadata:
        name: my-custom-dgd-name  # Only this is actually used today
```

### Overrides Needed to Fix Deployment Issues

To produce a correct DGD for TRT-LLM disaggregated deployment without InfiniBand,
the DGDR would need to support overriding the following on generated worker services.
These could be implemented via the `overrides.dgd` merge (once it fully merges service
specs) or via new dedicated fields:

#### 1. Shared Memory (critical)
```yaml
spec:
  overrides:
    dgd:
      apiVersion: nvidia.com/v1alpha1
      kind: DynamoGraphDeployment
      spec:
        services:
          TRTLLMDecodeWorker:
            sharedMemory:
              size: 256Gi
          TRTLLMPrefillWorker:
            sharedMemory:
              size: 256Gi
```

#### 2. UCX Transport and Security Context (critical for non-IB clusters; optional when IB fixed)

> **UPDATE 2026-04-16**: With the `ib-node-config` and `rdma-shared-dp-ds` DaemonSets
> deployed, the `UCX_TLS` override is no longer needed. Remove it and instead add
> `rdma/hca_shared_devices_a: 1` to worker resource limits. `IPC_LOCK` is still needed.

```yaml
# When RDMA is available (preferred):
spec:
  overrides:
    dgd:
      apiVersion: nvidia.com/v1alpha1
      kind: DynamoGraphDeployment
      spec:
        services:
          TRTLLMDecodeWorker:
            extraPodSpec:
              mainContainer:
                securityContext:
                  capabilities:
                    add:
                      - IPC_LOCK
          TRTLLMPrefillWorker:
            extraPodSpec:
              mainContainer:
                securityContext:
                  capabilities:
                    add:
                      - IPC_LOCK

# When RDMA is NOT available (fallback):
# Add UCX_TLS=tcp,cuda_copy,cuda_ipc and UCX_NET_DEVICES=all env vars on workers
```

#### 3. Engine Config as ConfigMap (nice to have)
The profiler currently inlines engine args via `--override-engine-args` JSON. For
production, ConfigMap-based YAML configs with DeepGEMM MoE backend, CUDA graphs,
and tuned `free_gpu_memory_fraction` are preferred.

### Recommended New DGDR Fields

Rather than requiring users to craft raw DGD overrides, consider adding first-class
fields to the DGDR spec:

```yaml
spec:
  # Existing fields...
  
  # New: UCX transport configuration for disaggregated serving
  nixlTransport: tcp  # auto | tcp | rdma (auto = detect IB, fallback to tcp)
  
  # New: shared memory size for workers (default 256Gi for trtllm)
  sharedMemorySize: 256Gi
  
  # New: disable planner for static deployments
  features:
    planner:
      enabled: false
```

## Recommendations

### For the Profiler/DGDR

1. **Always set shared memory** to 256Gi for TRT-LLM workers with TP > 1
2. **Detect InfiniBand/RDMA availability** and request `rdma/hca_shared_devices_a` when
   available; fall back to `UCX_TLS=tcp,cuda_copy,cuda_ipc` when IB is unavailable
3. **Fix multinode.nodeCount** calculation: only set when TP > GPUs per node
4. **Validate minimum TP** against GPU memory: Qwen3-235B-FP8 needs TP >= 4 on H100 80GB
5. **Make planner optional**: don't include if image doesn't have required dependencies
6. **IPC_LOCK capability is NOT needed** when `RLIMIT_MEMLOCK=unlimited` is set at
   the node level (via `ib-node-config` DaemonSet). Only request
   `rdma/hca_shared_devices_a` on workers to get IB devices exposed.

### For the Recipe

1. **Update `recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`**: change prefill
   from TP=2 to TP=4 (TP=2 OOMs on H100 80GB)
2. **Add `rdma/hca_shared_devices_a: 1`** resource limit to workers for clusters with
   InfiniBand. For non-IB clusters, add `UCX_TLS=tcp,cuda_copy,cuda_ipc`.
   `IPC_LOCK` capability is not needed when memlock is unlimited at the node level.
3. **Add `sharedMemory.size: 256Gi`** (already present in recipe but critical)
4. **Document hardware requirements**: note that TP=4 minimum is required for H100 80GB

### For the Operator

1. **Default shared memory** for TRT-LLM backend should be 256Gi, not 8Gi
2. **Request `rdma/hca_shared_devices_a`** when the cluster has the RDMA device
   plugin deployed; fall back to `UCX_TLS=tcp,cuda_copy,cuda_ipc` otherwise.
   `IPC_LOCK` capability injection is unnecessary when node-level memlock is unlimited.

### 6. DeepGEMM Assertion Failure with FP8 Model

**Symptom**: Workers load model and create NIXL backend successfully, then crash at
executor warmup: `RuntimeError: Assertion error (deepgemm-src/csrc/.../layout.hpp:49):
sfa_dtype == torch::kFloat and sfb_dtype == torch::kFloat`.

**Root cause**: DeepGEMM MoE backend expects float32 scaling factors, but the Qwen3
FP8 model has different scaling factor dtypes. This is a compatibility issue with
DeepGEMM in TRT-LLM 1.3.0rc5.post1 and this specific FP8 model.

**Fix**: Remove `backend: DEEPGEMM` from `moe_config` in both prefill and decode
engine configs. Use the default MoE backend instead.

**Recipe action item**: The recipe at `recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`
specifies `backend: DEEPGEMM` which crashes with this TRT-LLM version. Either remove
it or gate it behind a version check.

### 7. Frontend Missing Model-Path and PVC Mount

**Symptom**: Frontend returns 200 on `/health` with all 8 workers discovered, but
`/v1/models` returns empty list and `/v1/chat/completions` returns 404.

**Root cause**: The frontend discovers workers via Kubernetes endpoint slices but needs
to load the model's tokenizer config to register the model route. Without `--model-path`
and the PVC mounted, it tries to download config from HuggingFace using the local
path as a HF ID, which fails with 404.

**Fix**: Add `--model-path` to frontend args and mount the PVC:
```yaml
args:
  - python3 -m dynamo.frontend --router-mode kv --http-port 8000
    --model-name "Qwen/Qwen3-235B-A22B-FP8"
    --model-path "/model-store/hub/models--Qwen--Qwen3-235B-A22B-FP8/snapshots/..."
volumeMounts:
  - name: pvc-lustre
    mountPath: /model-store
```

**Recipe action item**: The original recipe doesn't include `--model-name` or
`--model-path` on the frontend. The DGDR-generated DGD correctly includes these.

## Final Working Configuration

The following configuration successfully deploys Qwen3-235B-A22B-FP8 with disaggregated
prefill/decode on 4x ND96isr H100 nodes (32 GPUs total):

- **TP=4** for both prefill and decode (TP=2 OOMs on H100 80GB)
- **4x prefill workers** (4 GPU each) + **4x decode workers** (4 GPU each) = 32 GPUs
- **Shared memory**: 256Gi
- **UCX transport**: InfiniBand RDMA via `rc_verbs`/`rc_mlx5` (400 Gb/s, after cluster-level
  IB fix — see `aks-infiniband-rdma-setup.md`). Previous TCP workaround no longer needed.
- **IPC_LOCK** capability on all workers
- **No DeepGEMM** MoE backend (assertion failure with FP8 scaling factors)
- **Frontend** with `--model-name`, `--model-path`, and PVC mount
- **MoE config**: `moe_tensor_parallel_size: 4`, `moe_expert_parallel_size: 1`
- **Image**: `nvcr.io/nvidia/ai-dynamo/tensorrtllm-runtime:1.0.0`

### Verified Performance

```
Model: Qwen/Qwen3-235B-A22B-FP8
TTFT: 168ms (prefill → decode handoff via NIXL/TCP)
Total: 331ms for simple query
Prefill/Decode disaggregation: Working (separate worker pools)
KV cache transfer: NIXL over UCX/TCP with cuda_copy/cuda_ipc
```

### Issues Found and Fixed (Summary)

| # | Issue | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | Shared memory too small | 8Gi default, TRT-LLM MPI needs more | `sharedMemory.size: 256Gi` |
| 2 | CUDA OOM at TP=2 | 235B FP8 needs ~78GB/GPU, only 80GB available | TP=4 minimum |
| 3 | NIXL UCX backend fails | `ulimit -l` 64KB blocks IB verbs | **RESOLVED**: cluster-level IB fix (see `aks-infiniband-rdma-setup.md`) |
| 4 | UCX TCP lacks GPU support | `UCX_TLS=tcp` can't register GPU memory | No longer relevant — using native IB verbs now |
| 5 | DeepGEMM assertion | FP8 scaling factor dtype mismatch | Remove `backend: DEEPGEMM` |
| 6 | Frontend 404 on chat | Missing model-path and PVC mount | Add `--model-path` + PVC to frontend |
| 7 | Planner crash | Missing `pmdarima` in image | Remove planner service |
| 8 | Decode multinode | `nodeCount: 8` requires Grove | Remove multinode (TP=4 fits one node) |

## How to Fix a Broken DGDR-Generated DGD

When the DGDR profiler produces a DGD that doesn't deploy correctly, you can extract it,
patch it, and apply it manually. This section walks through the process step by step.

### Step 1: Extract the Generated DGD

The profiler stores its output in a ConfigMap annotation. Extract it:

```bash
# Get the DGDR name
DGDR_NAME=qwen-235-fp8-dgdr

# Extract the generated DGD spec from the annotation
kubectl get dgdr $DGDR_NAME -o jsonpath='{.metadata.annotations.nvidia\.com/dgd-spec}' | python3 -m json.tool > generated-dgd.json

# Or if autoApply was true and a DGD was already created, get it directly:
kubectl get dgd -o yaml > generated-dgd.yaml
```

### Step 2: Convert to a Deployable YAML

Take the extracted JSON and convert it into a full DGD YAML manifest. The profiler
output is the `spec` portion — you need to wrap it:

```yaml
apiVersion: nvidia.com/v1alpha1
kind: DynamoGraphDeployment
metadata:
  name: qwen3-235b-disagg    # rename from the auto-generated name
spec:
  # ... paste profiler output spec here ...
```

### Step 3: Apply Critical Patches

Apply these patches in order. Each addresses a deployment-breaking issue.

#### Patch 1: Shared Memory (all workers crash without this)

On every worker service (`TRTLLMPrefillWorker`, `TRTLLMDecodeWorker`), add:

```yaml
sharedMemory:
  size: 256Gi
```

The DGDR does not set this. The Kubernetes default is 8Gi, which causes immediate
`Cannot allocate memory` crashes when TRT-LLM MPI workers try to communicate.

#### Patch 2: UCX Transport (required on non-InfiniBand clusters)

On every worker service, add these environment variables:

```yaml
extraPodSpec:
  mainContainer:
    env:
      - name: UCX_TLS
        value: tcp,cuda_copy,cuda_ipc
      - name: UCX_NET_DEVICES
        value: all
```

Without this, NIXL tries InfiniBand verbs which fail on AKS/GKE/EKS where IB is not
exposed to pods. Using `tcp` alone also fails because it can't register GPU memory —
you need `cuda_copy` and `cuda_ipc` for GPU-to-GPU KV cache transfer.

#### Patch 3: IPC_LOCK Capability (required for UCX)

On every worker service, add:

```yaml
extraPodSpec:
  mainContainer:
    securityContext:
      capabilities:
        add:
          - IPC_LOCK
```

Also add `ulimit -l unlimited 2>/dev/null || true` before the main process in the
worker command.

#### Patch 4: Fix Decode TP and Remove Multinode (if TP <= GPUs per node)

The profiler may set `multinode.nodeCount` even when TP fits on a single node. If
`tensor_parallel_size <= 8` (GPUs per node), remove `multinode` entirely:

```yaml
# REMOVE this block from decode worker:
# multinode:
#   nodeCount: 8
```

Also consider reducing decode TP from 8 to 4 if you want single-node pods (simpler
scheduling, no Grove dependency):

```yaml
resources:
  limits:
    gpu: "4"    # was "8"
```

And update the engine args to match the new TP size.

#### Patch 5: Remove DeepGEMM MoE Backend (if using FP8 model)

If the engine config specifies `backend: DEEPGEMM` in `moe_config`, remove it. The
DeepGEMM kernel has an assertion failure with FP8 scaling factors in TRT-LLM 1.3.0:

```yaml
# In engine config, change:
moe_config:
  backend: DEEPGEMM    # REMOVE this line
  max_num_tokens: 8192
```

If the profiler uses `--override-engine-args` JSON inline, convert it to a ConfigMap
for easier editing (see the working recipe for ConfigMap format).

#### Patch 6: Fix Frontend (add model-path and PVC mount)

The DGDR-generated frontend usually has `--model-name` and `--model-path` correct.
If not, add:

```yaml
args:
  - python3 -m dynamo.frontend --router-mode kv --http-port 8000
    --model-name "Qwen/Qwen3-235B-A22B-FP8"
    --model-path "/model-store/hub/models--Qwen--Qwen3-235B-A22B-FP8/snapshots/<hash>"
volumeMounts:
  - name: pvc-lustre
    mountPath: /model-store
```

#### Patch 7: Remove Planner (if image is missing dependencies)

If the planner crashes with `ModuleNotFoundError: No module named 'pmdarima'`, remove
the entire Planner service from the DGD spec:

```yaml
# DELETE this entire service block:
# Planner:
#   componentType: planner
#   ...
```

#### Patch 8: Use Correct Image

Replace the profiler's demo image with the official NVCR image:

```yaml
image: nvcr.io/nvidia/ai-dynamo/tensorrtllm-runtime:1.0.0
# NOT: jont828/tensorrtllm-runtime:demo-v5
```

### Step 4: Create ConfigMaps (if converting from inline engine args)

If the profiler used `--override-engine-args` with inline JSON, create ConfigMaps
for cleaner management:

```bash
# Create prefill config
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: prefill-config
data:
  prefill.yaml: |
    backend: pytorch
    trust_remote_code: true
    tensor_parallel_size: 4
    moe_tensor_parallel_size: 4
    moe_expert_parallel_size: 1
    enable_attention_dp: false
    enable_chunked_prefill: false
    kv_cache_config:
      enable_block_reuse: true
      free_gpu_memory_fraction: 0.7
      dtype: fp8
    cache_transceiver_config:
      backend: DEFAULT
    cuda_graph_config:
      enable_padding: true
      max_batch_size: 2
    disable_overlap_scheduler: true
    moe_config:
      max_num_tokens: 8192
EOF

# Create decode config (similar, with decode-appropriate settings)
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: decode-config
data:
  decode.yaml: |
    backend: pytorch
    trust_remote_code: true
    tensor_parallel_size: 4
    moe_tensor_parallel_size: 4
    moe_expert_parallel_size: 1
    enable_attention_dp: false
    enable_chunked_prefill: false
    kv_cache_config:
      enable_block_reuse: false
      free_gpu_memory_fraction: 0.95
      dtype: fp8
    cache_transceiver_config:
      backend: DEFAULT
    cuda_graph_config:
      enable_padding: true
      max_batch_size: 512
    disable_overlap_scheduler: false
    moe_config:
      max_num_tokens: 8192
EOF
```

Then update worker args to reference the ConfigMap:
```yaml
args:
  - |
    ulimit -l unlimited 2>/dev/null || true
    python3 -m dynamo.trtllm \
      --model-path "${MODEL_PATH}" \
      --served-model-name "Qwen/Qwen3-235B-A22B-FP8" \
      --max-batch-size 2 \
      --max-num-tokens 8192 \
      --max-seq-len 8192 \
      --extra-engine-args "${ENGINE_ARGS}" \
      --disaggregation-mode prefill
volumeMounts:
  - name: prefill-config
    mountPath: /engine_configs
volumes:
  - name: prefill-config
    configMap:
      name: prefill-config
env:
  - name: ENGINE_ARGS
    value: /engine_configs/prefill.yaml
```

### Step 5: Apply and Verify

```bash
# Apply ConfigMaps first
kubectl apply -f configmaps.yaml

# Apply the patched DGD
kubectl apply -f patched-dgd.yaml

# Watch pods come up
kubectl get pods -w

# Wait for workers to load (~17 min for Qwen3-235B on Lustre)
kubectl logs -f <prefill-worker-pod> -c worker

# Once workers show "NIXL backend created", test:
kubectl port-forward svc/qwen3-235b-disagg-frontend 8000:8000

curl http://localhost:8000/v1/models
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen/Qwen3-235B-A22B-FP8", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Quick Reference: Minimum Viable Patch

If you just want the shortest path from broken DGDR output to a working deployment,
these are the **must-have** patches (in a single kubectl patch or YAML edit):

1. `sharedMemory.size: 256Gi` on all workers
2. `rdma/hca_shared_devices_a: 1` resource limit on all workers (if RDMA device
   plugin is deployed and node-level memlock is unlimited; otherwise use
   `UCX_TLS=tcp,cuda_copy,cuda_ipc`). `IPC_LOCK` capability is not needed when
   `RLIMIT_MEMLOCK=unlimited` is set at the node level.
3. Remove `multinode` if TP <= GPUs per node
4. Remove Planner service if image lacks `pmdarima`

Everything else (DeepGEMM removal, ConfigMap conversion, frontend model-path) depends
on whether those issues affect your specific model and image version.
