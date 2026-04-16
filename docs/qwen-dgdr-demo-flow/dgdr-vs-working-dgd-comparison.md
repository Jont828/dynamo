# DGDR-Generated DGD vs Working DGD: Comparison

**Date**: 2026-04-15
**DGDR**: `qwen-235b-fp8-dgdr-iterating.yaml` (rapid search, autoApply: false)
**Working DGD**: `qwen235-recipe-lustre.yaml`

## What the Overrides Fixed (confirmed working)

These overrides from our DGDR were correctly merged into the generated DGD:

| Override | Applied? | Verified |
|----------|:--------:|:--------:|
| `sharedMemory.size: 256Gi` on both workers | ✅ | Lines 76, 126 |
| `UCX_TLS=tcp,cuda_copy,cuda_ipc` env var | ✅ | Lines 59-60, 109-110 |
| `UCX_NET_DEVICES=all` env var | ✅ | Lines 61-62, 111-112 |
| `IPC_LOCK` capability | ✅ | Lines 67-69, 117-119 |
| No planner service | ✅ | Planner absent from generated DGD |
| PVC name `pvc-lustre` | ✅ | Lines 7-9, volume mounts |
| Image `tensorrtllm-runtime:1.0.0` | ✅ | Lines 24, 63, 113 |
| Frontend `--model-name` and `--model-path` | ✅ | Lines 16-19 |

## Remaining Differences (would-be blockers)

### 1. Model Path Doubled: `/model-store/model-store/...` (BUG)

**Generated**: `--model-path /model-store/model-store/hub/models--Qwen--...`
**Working**:   `--model-path /model-store/hub/models--Qwen--...`

The profiler prepends the PVC mount path (`/model-store`) to the `pvcModelPath`
which already includes `/model-store`. This results in a doubled path that won't
resolve. This affects Frontend, PrefillWorker, and DecodeWorker.

**Root cause**: `pvcModelPath` in the DGDR is set to the full absolute path
(`/model-store/hub/...`), but the generator treats it as relative to `pvcMountPath`.

**Fix options**:
- Change DGDR `pvcModelPath` to `hub/models--Qwen--Qwen3-235B-A22B-FP8/...` (relative)
- Or fix the generator to not prepend mount path when pvcModelPath is absolute

### 2. Decode Worker: TP=8 (8 GPU) vs Working TP=4 (4 GPU)

**Generated**: `--tensor-parallel-size 8`, `gpu: "8"`, 2 replicas = 16 GPUs
**Working**:   TP=4, `gpu: "4"`, 4 replicas = 16 GPUs

Both use 16 decode GPUs total. The profiler's choice of TP=8 is *valid* for H100
80GB — the model fits at TP=8. But it uses 8 GPUs per pod (one full node) vs
our working config which uses 4 GPUs per pod (half node, more flexible scheduling).

**Will it work?** Probably yes — TP=8 fits on a single node. The previous DGDR run
set `multinode.nodeCount: 8` here (bug), but this time there's **no multinode** set!
The profiler bug appears to be fixed.

### 3. Prefill Batch Size: max-batch-size 1 vs Working 2

**Generated**: `--max-batch-size 1 --max-num-tokens 4512`
**Working**:   `--max-batch-size 2 --max-num-tokens 8192`

The profiler chose a smaller batch. This is a performance tuning difference, not a
correctness issue. The deployment will work with batch=1, just lower throughput.

### 4. Decode Batch Size: max-num-tokens 512 vs Working 1024

**Generated**: `--max-num-tokens 512`
**Working**:   `--max-num-tokens 1024`

Again a performance tuning difference. Decode will work but may have lower throughput.

### 5. No `--disaggregation-mode` flag

**Generated**: Args don't include `--disaggregation-mode prefill/decode`
**Working**:   Explicitly sets `--disaggregation-mode prefill` and `decode`

The generated DGD relies on `subComponentType: prefill/decode` which the Dynamo
operator maps to `--disaggregation-mode` at pod creation time. This should work
correctly — the operator injects the flag based on `subComponentType`.

### 6. Engine Config: Inline JSON vs ConfigMap

**Generated**: `--override-engine-args '{"cache_transceiver_config": ...}'`
**Working**:   `--extra-engine-args /engine_configs/prefill.yaml` (ConfigMap)

The generated DGD uses inline engine args. The working recipe uses ConfigMap-based
YAML configs with more settings (CUDA graphs, free_gpu_memory_fraction, MoE config).
The generated config is minimal — no CUDA graph config, no `free_gpu_memory_fraction`,
no `moe_tensor_parallel_size`, no `moe_expert_parallel_size`.

Key missing engine settings in generated DGD:
- `free_gpu_memory_fraction` (0.7 for prefill, 0.95 for decode)
- `cuda_graph_config.enable_padding: true`
- `moe_tensor_parallel_size` / `moe_expert_parallel_size`
- `enable_chunked_prefill: false`
- `print_iter_log: false`

These are performance/stability settings, not correctness blockers. The deployment
will likely work but with suboptimal performance.

### 7. No `--max-seq-len` flag

**Generated**: No max-seq-len specified
**Working**:   `--max-seq-len 8192`

Without this, TRT-LLM will use the model's full context length (40960 for Qwen3-235B).
This uses much more GPU memory for KV cache, potentially causing OOM at runtime.

### 8. Missing `HF_HOME` env var

**Generated**: Not set (but `envFromSecret: hf-token-secret` IS set on workers)
**Working**:   `HF_HOME=/model-store`

Without `HF_HOME`, HuggingFace will use the default cache dir (`~/.cache/huggingface`),
not the Lustre PVC. Workers may try to download model files instead of using cached ones.

### 9. No Node Affinity

**Generated**: No affinity rules
**Working**:   `nodeAffinity` requires `nvidia.com/gpu.present: "true"`

Without affinity, worker pods could be scheduled on non-GPU nodes and fail. The
operator may handle this implicitly via GPU resource requests.

## Summary Verdict

| Category | Count | Impact |
|----------|:-----:|--------|
| Fixed by overrides | 8 | All critical fixes applied correctly |
| Bugs to fix | 1 | Doubled model path (#1) — **deployment will fail** |
| Performance gaps | 5 | #3, #4, #6, #7, #8 — deployment works but suboptimal |
| Layout differences | 1 | #2 (TP=8 vs TP=4 decode) — valid, just different |
| Likely handled by operator | 2 | #5, #9 — operator injects disagg mode and GPU scheduling |

**Bottom line**: The DGDR-generated DGD is **very close** to working. The one
deployment blocker is the doubled model path (`/model-store/model-store/...`). If we
fix `pvcModelPath` in the DGDR to be relative (remove the leading `/model-store/`),
the generated DGD should deploy successfully, albeit with different performance
characteristics than our hand-tuned recipe.

## Recommended DGDR Change

```yaml
modelCache:
  pvcName: pvc-lustre
  pvcMountPath: /model-store
  # Use relative path (generator prepends pvcMountPath)
  pvcModelPath: hub/models--Qwen--Qwen3-235B-A22B-FP8/snapshots/39eb2b067ea6b8e3e1dd97d3cd0c7ffeaf3e1a35
```
