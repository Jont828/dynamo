# Qwen3-235B Recipe Deployment: Lessons Learned for DeepSeek R1

**Date**: 2026-04-15
**Context**: Deploying Qwen/Qwen3-235B-A22B-FP8 (MoE, 235B params, 22B active) with
disaggregated TRT-LLM on AKS 4x ND96isr H100 nodes. Applying these lessons to
deploying DeepSeek-R1 (MoE, 671B params, ~37B active) with disaggregated SGLang.

---

## Issues Encountered Deploying Qwen3-235B Recipe

These are the issues we hit deploying the Qwen 235B recipe (from
`recipes/qwen3-235b-a22b-fp8/trtllm/disagg/deploy.yaml`) to our AKS cluster,
**not** via the DGDR automated flow but via direct DGD apply. Each issue is something
to watch for when deploying DeepSeek R1.

### 1. CUDA OOM at Insufficient TP

**What happened**: The recipe specified TP=2 for prefill workers (2 GPUs per worker).
Qwen3-235B-FP8 requires ~78GB per GPU at TP=2, leaving only 266MB free on H100 80GB
for KV cache and engine buffers. Workers crashed immediately with
`torch.OutOfMemoryError`.

**Fix**: Changed to TP=4 for all workers.

**DeepSeek R1 implication**: DeepSeek R1 is 671B params (vs Qwen's 235B). Even with
MoE sparsity (~37B active), the full weight set must be loaded. The recipe specifies
TP=8 with EP=8 (WideEP) which uses all 8 GPUs per node — this is the minimum viable
config for H100 80GB. **No room to reduce TP below 8** for this model on H100.
With H200 141GB, the recipe's TP=8 has more headroom. On our H100 80GB nodes, we
need to verify that TP=8/EP=8 fits, and if not, may need to reduce
`--mem-fraction-static` below 0.75.

### 2. Shared Memory Too Small (8Gi Default)

**What happened**: Workers crashed immediately with `Cannot allocate memory` UCX/MPI
errors. Kubernetes defaults `emptyDir.medium=Memory` to 8Gi for `/dev/shm`. TRT-LLM's
MPI workers for multi-GPU communication need much more.

**Fix**: Set `sharedMemory.size: 256Gi`.

**DeepSeek R1 implication**: The DeepSeek SGLang recipe already sets
`sharedMemory.size: 80Gi`. SGLang uses NCCL (not MPI) for multi-GPU communication,
which typically needs less shared memory than TRT-LLM's MPI. 80Gi should be
sufficient, but if we see memory allocation errors, bump to 256Gi. **The recipe
already handles this — no change expected.**

### 3. NIXL/UCX Transport Failure Without InfiniBand

**What happened**: Workers loaded the model successfully (~17 min) then crashed when
creating the NIXL cache transceiver: `Failed to create NIXL backend: UCX` with
`Failed to create UCX worker: Input/output error`. Root cause: NIXL defaults to
InfiniBand verbs, which requires `ulimit -l unlimited` (RLIMIT_MEMLOCK). On AKS,
the default is 64KB, and even with `IPC_LOCK` capability, the kernel limit stays at
64KB.

**Fix**: Set `UCX_TLS=tcp,cuda_copy,cuda_ipc` and `UCX_NET_DEVICES=all` env vars on
all workers, plus `IPC_LOCK` capability.

**DeepSeek R1 implication**: **This is the biggest risk.** The SGLang recipe does NOT
set any UCX environment variables. SGLang's disaggregated mode also uses NIXL for
KV cache transfer between prefill and decode workers. On AKS without InfiniBand
exposed to pods, we will hit the exact same NIXL/UCX failure.

**Must add to DeepSeek recipe:**
```yaml
env:
  - name: UCX_TLS
    value: tcp,cuda_copy,cuda_ipc
  - name: UCX_NET_DEVICES
    value: all
securityContext:
  capabilities:
    add:
      - IPC_LOCK
```

Additionally, SGLang's `--disaggregation-bootstrap-port 30001` is for the bootstrap
channel, but the actual KV cache transfer goes through NIXL/UCX. The UCX transport
config is still critical.

### 4. DeepGEMM MoE Backend Assertion Failure

**What happened**: Engine warmup crashed with `RuntimeError: Assertion error
(deepgemm-src/csrc/.../layout.hpp:49): sfa_dtype == torch::kFloat and sfb_dtype ==
torch::kFloat`. The DeepGEMM MoE kernel expects float32 scaling factors, but the
FP8 quantized model has different dtypes.

**Fix**: Removed `backend: DEEPGEMM` from `moe_config` in engine configs.

**DeepSeek R1 implication**: The SGLang recipe doesn't use explicit MoE backend
selection — SGLang handles MoE routing internally. The recipe references a
`deepep.json` for DeepEP tuning parameters (SM counts, NVLink/RDMA chunked token
limits), but this is SGLang-native, not a TRT-LLM engine config. **No DeepGEMM
risk.** However, the DeepEP config in `deepep.json` is tuned for H200 NVLink
bandwidth — on H100 it may need adjustment (H100 has 900GB/s NVLink vs H200's
900GB/s, similar but different memory subsystem).

### 5. Frontend Missing Model-Path and PVC Mount

**What happened**: Frontend discovered all workers (via Kubernetes endpoint slices)
and returned healthy, but `/v1/models` was empty and `/v1/chat/completions` returned
404. The frontend couldn't load the tokenizer config because it didn't have access to
the model files.

**Fix**: Added `--model-name`, `--model-path`, and PVC volumeMount to the frontend.

**DeepSeek R1 implication**: The SGLang recipe's frontend section does NOT specify
`--model-name` or `--model-path` args — it relies on the Dynamo frontend auto-discovering
these from worker registration. The SGLang workers pass `--served-model-name
deepseek-ai/DeepSeek-R1` which gets registered with the frontend. **However**, the
frontend still needs the tokenizer. If the model isn't downloadable from HuggingFace
at runtime (e.g., gated model, no HF_TOKEN on frontend), we need to mount the PVC
and add `--model-path`.

**Recommendation**: Proactively add PVC mount and `--model-path` to the frontend
spec, or ensure the frontend pod has `HF_TOKEN` to download the tokenizer at runtime.

### 6. Multi-Node Misconfiguration

**What happened**: The DGDR-generated DGD set `multinode.nodeCount: 8` on the decode
worker, confusing GPU count with node count. This requires Grove (PodCliqueSets) to
be installed.

**Fix**: Removed multinode entirely and used TP=4 single-node pods.

**DeepSeek R1 implication**: The disagg-8gpu recipe uses TP=8 (all 8 GPUs on one
node), so `multinode` is NOT set. This is correct — each worker fits on a single
node. **No risk here with disagg-8gpu.** The disagg-16gpu recipe DOES set
`multinode.nodeCount: 2`, which requires Grove. **Avoid disagg-16gpu unless Grove
is installed on the cluster.**

### 7. CUDA Graph Warmup Takes Very Long

**What happened**: After workers loaded and registered, the first few inference
requests took ~66 seconds each (vs expected ~168ms). This is CUDA graph compilation
happening on each worker's first request for each unique batch size/sequence length.

**Not a recipe issue**, but important operationally: the first inference request to
each worker triggers CUDA graph compilation. With 4 prefill + 4 decode workers, it
takes several requests (routed to different workers) before all are warm.

**DeepSeek R1 implication**: Same behavior expected with SGLang. SGLang also uses
CUDA graphs for decode. The warmup period may be even longer given the model size
(671B vs 235B). **Plan for warmup requests after deployment** — use aiperf with
`--warmup-request-count` before measuring latency.

### 8. Model Loading Time

**What happened**: Each worker takes ~17 minutes to load Qwen3-235B-FP8 from Lustre
PVC. This is dominated by reading ~235GB of weights from storage and initializing GPU
tensors.

**DeepSeek R1 implication**: DeepSeek R1 is ~1.3TB of weights (671B params at FP8/16
mixed precision). Loading will take significantly longer — likely **30-60 minutes per
worker** depending on storage throughput. With Lustre managed storage (high IOPS,
high bandwidth), this should be on the lower end. **Set
`--watchdog-timeout 3600`** (already in the recipe) to prevent health check timeouts
during loading.

---

## DeepSeek R1 SGLang Disagg: Known Adaptations Needed

Based on the Qwen experience and analysis of the recipe at
`recipes/deepseek-r1/sglang/disagg-8gpu/deploy.yaml`, here's what we need to adapt
for our AKS cluster:

### Must-Have Changes

| # | Change | Why |
|---|--------|-----|
| 1 | Add `UCX_TLS=tcp,cuda_copy,cuda_ipc` and `UCX_NET_DEVICES=all` env vars | AKS nodes don't expose IB to pods |
| 2 | Add `IPC_LOCK` capability to workers | Required for UCX memory registration |
| 3 | Change PVC from `model-cache` to `pvc-lustre` | Our cluster uses Azure Lustre |
| 4 | Update mount path if model is cached at different path | Our Lustre mounts at `/model-store` |
| 5 | Verify HF_TOKEN secret exists or add `envFromSecret` | Needed for gated model download |
| 6 | Add `--model-path` and PVC mount to frontend | In case tokenizer can't be downloaded |

### Likely Needed Changes

| # | Change | Why |
|---|--------|-----|
| 7 | Reduce `--mem-fraction-static` from 0.75 to ~0.60 | H100 80GB vs recipe's H200 141GB |
| 8 | Possibly increase `sharedMemory.size` from 80Gi to 256Gi | If NCCL needs more on H100 |
| 9 | Adjust DeepEP config (`deepep.json`) for H100 | Tuned for H200, may need SM count changes |

### May Need Changes

| # | Change | Why |
|---|--------|-----|
| 10 | Verify TP=8/EP=8 fits on H100 80GB | 671B MoE at FP8+FP16 mixed, 80GB per GPU |
| 11 | Image version: recipe uses `sglang-runtime:1.0.0` | Verify compatibility with H100 |

### Not Needed

| Setting | Why it's fine |
|---------|---------------|
| `sharedMemory: 80Gi` | SGLang NCCL uses less than TRT-LLM MPI |
| `multinode` | disagg-8gpu fits on single node (TP=8) |
| DeepGEMM removal | Not applicable to SGLang |
| ConfigMap engine config | SGLang uses CLI args, not config files |

---

## GPU Budget: Will DeepSeek R1 Fit?

**Qwen3-235B**: 235B params, 22B active → needs TP=4 on H100 80GB → 4 GPU per worker
→ we deployed 4 prefill + 4 decode = 32 GPUs total.

**DeepSeek R1**: 671B params, ~37B active → needs TP=8 on H100 80GB → 8 GPU per worker.

With disagg-8gpu recipe:
- 1 prefill worker × 8 GPUs = 8 GPUs
- 1 decode worker × 8 GPUs = 8 GPUs
- Total: **16 GPUs** (2 nodes)

With our 32-GPU cluster (4 nodes), we have room for:
- 2 prefill × 8 GPUs + 2 decode × 8 GPUs = **32 GPUs** (all 4 nodes)

**Recommendation**: Start with 1+1 (16 GPUs) to validate, then scale to 2+2 (32 GPUs)
for production throughput.

---

## Pre-Deployment Checklist for DeepSeek R1

- [ ] Verify model is cached on Lustre PVC (1.3TB, check path)
- [ ] Create adapted deploy.yaml with UCX/IPC_LOCK/PVC changes
- [ ] Tear down Qwen deployment to free GPUs
- [ ] Apply DeepSeek DGD
- [ ] Wait for model loading (~30-60 min)
- [ ] Send warmup requests (aiperf with `--warmup-request-count`)
- [ ] Verify disaggregated flow (check `prefill_worker_id` vs `decode_worker_id`)
- [ ] Run benchmark with aiperf
