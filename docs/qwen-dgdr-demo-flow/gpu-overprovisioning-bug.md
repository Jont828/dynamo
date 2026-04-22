# GPU Overprovisioning Bug Investigation — TRT-LLM DGDR

**Date:** 2026-04-21
**DGDR:** `ecommerce-shopping-assistant` (Qwen3-235B-A22B-FP8, trtllm, rapid)
**Cluster:** 4× ND96isr H100 nodes (8 GPU each, 32 GPUs total)

## Observed Problem

The profiler generates a DGD config requiring far more GPUs than the 32 available:

| Service | Replicas | nodeCount | TP | EP | GPUs/pod | Total GPUs |
|---------|----------|-----------|----|----|----------|------------|
| decode  | 2        | 8         | 8  | 4  | 8        | 2×8×8=128  |
| prefill | 2        | —         | 4  | 4  | 4        | 2×4=8      |
| **Total** | | | | | | **136** (budget: 32) |

The decode leader pod crashes (CrashLoopBackOff) because MPI can't reach workers
that are stuck in `Pending` — not enough GPUs to schedule them.

## Generated Config (from DGD)

```
decode:
  replicas: 2
  multinode:
    nodeCount: 8
  args:
    --tensor-parallel-size 8
    --pipeline-parallel-size 1
    --expert-parallel-size 4
  gpu request: 8

prefill:
  replicas: 2
  args:
    --tensor-parallel-size 4
    --pipeline-parallel-size 1
    --expert-parallel-size 4
  gpu request: 4
```

## Root Cause Analysis

### Where `nodeCount` is set

`set_multinode_config()` in `components/src/dynamo/profiler/utils/config.py` (line 221):
```python
node_count = math.ceil(gpu_count / num_gpus_per_node)
worker_service.multinode = MultinodeConfig(nodeCount=node_count)
```

This is called from `setup_worker_service_resources()` with the `gpu_count` parameter.
The formula is correct — the bug is in the **input** `gpu_count`.

### Where `decode_gpus=64` originates

The RAPID path generates the DGD through `_generate_dgd_from_pick()` in `rapid.py`,
which calls AIC's `task_config_to_generator_config` + `generate_backend_artifacts`.

AIC's generator calls `BaseConfigModifier.build_dgd_config()` (protocol.py, line 450)
with `decode_gpus=64`.

**AIC appears to compute:**
```
decode_gpus = tensor_parallel_size × num_gpus_per_node = 8 × 8 = 64
```

This treats TP parallelism as "8 nodes of 8 GPUs each" instead of "8 GPUs total."

The per-pod GPU value comes out correct by accident (clamped to `min(64, 8) = 8`),
but `nodeCount = ceil(64/8) = 8` is wrong.

### TRT-LLM TP vs EP semantics

For TRT-LLM MoE models, `expert_parallel_size` (EP) is a sub-partitioning **within**
the tensor-parallel group — it does NOT multiply the GPU count:

- EP must divide TP
- With TP=8, EP=4: 8 GPUs total, 4 handle different expert shards
- GPU count per instance = `TP × PP × DP = 8 × 1 × 1 = 8`

`PickedParallelConfig.num_gpus` in `parallelization.py` correctly returns `tp * pp * dp`.
For a pick of `tp=8, pp=1, dp=1, moe_ep=4`, this returns `8` — correct.

### Correct values

| Field | Current (wrong) | Should be |
|-------|-----------------|-----------|
| `decode_gpus` input | 64 | 8 |
| `multinode.nodeCount` | 8 | absent (single-node, ≤8 GPUs) |
| `gpu` per pod | 8 | 8 (correct by accident) |
| Decode GPUs total (2 replicas) | 128 | 16 |
| **Grand total** | 136 | **24** (within 32 budget) |

## Where the Fix Should Go

This is an **AIC bug**, not a Dynamo profiler bug. The AIC SDK's
`task_config_to_generator_config` is computing `decode_gpus` as
`TP × num_gpus_per_node` rather than just `TP`.

Dynamo's `set_multinode_config` then correctly derives
`nodeCount = ceil(64/8) = 8` from the wrong input.

## Confirmed Fixes in This Session

1. **45-char service name fix** ✅ — `TRTLLMDecodeWorker`→`decode`,
   `TRTLLMPrefillWorker`→`prefill`. No webhook rejection.
2. **sharedMemory override** ✅ — `256Gi` applied to both `decode` and `prefill`
   services via `overrides.dgd` with corrected service names.

## Next Steps

- Fix AIC SDK to pass `decode_gpus = TP` (not `TP × num_gpus_per_node`)
- Or add validation in Dynamo profiler to clamp total GPUs to `max_gpu_budget`
- Re-test once fixed — with correct nodeCount, decode should be single-node
  and MPI should work without cross-node routing
