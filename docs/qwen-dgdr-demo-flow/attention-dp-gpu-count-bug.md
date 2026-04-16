# Attention DP GPU Count Bug in Profiler

**Date**: 2026-04-16
**Affected code**: `components/src/dynamo/profiler/utils/config_modifiers/parallelization_mapping.py`
**Versions affected**: v1.0.1 (confirmed), main HEAD (confirmed same code)
**Image tested**: `jont828/dynamo-frontend:moe-fix` (v1.0.1 base + MoE interpolation fixes)

## Summary

When the profiler picks a decode config with attention data parallelism (`dp > 1`),
the generated DGD requests `tp * pp * dp` GPUs per replica instead of `tp * pp`. This
makes the decode pods unschedulable because the GPU request far exceeds what's available.

## Reproduction

Apply a DGDR with planner enabled for a MoE model:

```yaml
spec:
  model: Qwen/Qwen3-235B-A22B-FP8
  backend: trtllm
  searchStrategy: rapid
  autoApply: true
  features:
    planner:
      mode: disagg
      enable_throughput_scaling: true
      enable_load_scaling: true
      max_gpu_budget: 32
```

The profiler/AIC picks a decode config with attention DP:
```
tp=8, pp=1, dp=8, moe_tp=2, moe_ep=4, --enable-attention-dp
```

Generated DGD has `gpu: "64"` on decode workers (2 replicas = 128 GPUs requested).
The cluster only has 32 GPUs total.

## Root Cause

`PickedParallelConfig.num_gpus` at line 122-123 of `parallelization_mapping.py`:

```python
@property
def num_gpus(self) -> int:
    return self.tp * self.pp * self.dp
```

For `tp=8, pp=1, dp=8`: returns `8 * 1 * 8 = 64`.

But attention DP (`--enable-attention-dp`) does not require additional GPUs. It changes
how the existing TP GPUs are used internally:

- **Without attention DP**: all 8 GPUs shard both attention and expert layers
- **With attention DP**: the 8 GPUs still shard expert layers (via moe_tp × moe_ep),
  but each GPU holds a full copy of the attention layers and processes different tokens
  through them independently (data parallelism on attention)

The physical GPU count is always `tp * pp`. The `dp` dimension is an intra-node
optimization, not an additional resource dimension.

## Why This Only Manifests With Planner Enabled

| Setting | Decode Config Picked | `num_gpus` | Correct? |
|---------|---------------------|------------|----------|
| Without planner | tp=8, dp=1, moe_tp=1, moe_ep=8 | 8 × 1 × 1 = 8 | ✅ |
| With planner | tp=8, dp=8, moe_tp=2, moe_ep=4 | 8 × 1 × 8 = 64 | ❌ |

Without planner, the profiler uses a simpler AIC picking mode that doesn't select
attention-DP configs (dp=1 always). With planner enabled, AIC explores more configs
during the interpolation phase and picks attention DP as optimal for decode, exposing
the bug.

Our MoE interpolation fix (`interpolation.py`, `profile_decode.py`) is **not** the
cause — it only allows the interpolation phase to complete without crashing. The GPU
count bug is in `parallelization_mapping.py` which we did not modify.

## Scope

- **v1.0.1**: Confirmed. Our `moe-fix` image is built from the v1.0.1 base with only
  `interpolation.py` and `profile_decode.py` patched. `parallelization_mapping.py` is
  stock v1.0.1.
- **main HEAD**: Same `num_gpus = tp * pp * dp` formula exists on main (verified via
  `git show main:...parallelization_mapping.py`). The bug potentially affects main too,
  though it may not have been triggered if no one has tested MoE + planner + attention DP
  on main.

## Suggested Fix

```python
@property
def num_gpus(self) -> int:
    return self.tp * self.pp
```

Attention DP reuses the same physical GPUs — `dp` should not be multiplied into the
GPU resource request. The relationship `tp = moe_tp * moe_ep * dp` (or similar) is an
internal parallelism decomposition, not an additional hardware dimension.

**Note**: This needs validation — confirm that `tp * pp` is correct for all
parallelization strategies (TP, TEP, DEP) before applying. The `ParallelizationMapping`
class (separate from `PickedParallelConfig`) has its own `get_num_gpus()` method that
only uses `tp` or `tep` or `dep` (line 87-102), which does not include `dp` — consistent
with the fix above.

## Workaround

Use `autoApply: false` and manually patch the generated DGD to set the correct GPU
count before deploying:

```bash
# Extract generated DGD, fix gpu count, apply manually
kubectl get dgdr <name> -n dynamo-system \
  -o jsonpath='{.metadata.annotations.nvidia\.com/generated-dgd-spec}' > dgd.yaml
# Edit: change gpu: "64" to gpu: "8" on TRTLLMDecodeWorker
kubectl apply -f dgd.yaml
```

## Related Bugs

- **MoE interpolation bug** (`qwen-235b-profiling-issue.md`): 3 bugs in
  `interpolation.py` and `profile_decode.py` that crash during the BuildingCurves
  phase for MoE models with planner enabled. Fixed in the `moe-fix` image.
  That fix is a prerequisite for hitting this GPU count bug — without it, the profiler
  crashes before reaching DGD generation.
