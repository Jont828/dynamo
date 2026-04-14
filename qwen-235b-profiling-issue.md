## MoE model profiling fails during interpolation (BuildingCurves) when planner is enabled

### Description

Profiling MoE models (e.g. `Qwen/Qwen3-235B-A22B-FP8`) with `searchStrategy: rapid` and
planner features enabled crashes during the **interpolation phase** (BuildingCurves).
Three bugs in how `interpolation.py` passes parallelism config to AIConfigurator cause
assertion failures in `MOEModel.__init__`.

**Key finding**: The interpolation phase only runs when `features.planner` is configured
in the DGDR. Without planner features, profiling skips interpolation entirely and succeeds
(sweep → select config → write DGD → done).

### Steps to Reproduce

Apply a DGDR with a MoE model, rapid search, **and planner features**:

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  namespace: dynamo-system
  name: qwen-235-dgdr
spec:
  model: Qwen/Qwen3-235B-A22B-FP8
  backend: trtllm
  searchStrategy: rapid
  autoApply: false
  workload:
    isl: 3000
    osl: 300
  sla:
    ttft: 500
    itl: 30
  features:
    planner:
      mode: disagg
      enable_throughput_scaling: true
      enable_load_scaling: true
      max_gpu_budget: 32
```

Without `features.planner`, the same DGDR succeeds (profiling skips interpolation).

### Bugs Found (3 issues)

#### Bug 1: Missing `moe_tp_size` / `moe_ep_size` kwargs (original issue)

`interpolation.py` did not pass `moe_tp_size` and `moe_ep_size` to AIConfigurator calls.
This caused `ModelConfig` fields to remain `None`, triggering:

```
TypeError: unsupported operand type(s) for *: 'NoneType' and 'NoneType'
```

at `aiconfigurator/sdk/models.py` line 749.

**Fix**: Pass `moe_tp_size=best_*_config.moe_tp` and `moe_ep_size=best_*_config.moe_ep`
in all three AIC calls in `interpolation.py`. (Already applied in repo.)

#### Bug 2: Wrong `tp_size` value — using derived `.tp_size` instead of raw `.tp`

`interpolation.py` used `best_prefill_config.tp_size` (a derived property) instead of
`best_prefill_config.tp` (the raw parallelism value). The `tp_size` property in
`PickedParallelConfig` is designed for KV-head splitting semantics:

```python
@property
def tp_size(self) -> int:
    """Effective TP for KV-head splitting (TP or TEP; 1 for DEP)."""
    if self.moe_ep > 1:
        return 1          # <-- returns 1 when moe_ep > 1
    ...
```

For a selected prefill config of `tp=4, moe_tp=1, moe_ep=4`:
- `.tp_size` returns `1` (wrong for AIC)
- `.tp` returns `4` (correct for AIC)

AIC's `ModelConfig` requires `tp_size * attention_dp_size == moe_tp_size * moe_ep_size`,
so passing `tp_size=1` with `moe_tp=1, moe_ep=4` fails the assertion.

**Error**:
```
AssertionError: tp_size (1) * attention_dp_size (1) should be equal to moe_tp_size (1) * moe_ep_size (4)
```

**Fix**: Use `.tp` instead of `.tp_size` in all three AIC calls in `interpolation.py`.

#### Bug 3: Missing `attention_dp_size` in decode AIC calls

For decode configs with `dp > 1` (e.g. `tp=1, dp=8, moe_tp=2, moe_ep=4`), AIC needs
`attention_dp_size` to satisfy the constraint `tp_size * attention_dp_size == moe_tp * moe_ep`.

Two issues:
1. `interpolation.py` did not pass `attention_dp_size` in the `get_max_kv_tokens()` kwargs
2. `profile_decode_aiconfigurator()` consumed `attention_dp_size` as a positional arg
   (for structuring the decode sweep) but never forwarded it into the `**model_config_kwargs`
   that flow to `estimate_perf()` → `_get_model()` → `ModelConfig()`

**Error**:
```
AssertionError: tp_size (1) * attention_dp_size (1) should be equal to moe_tp_size (2) * moe_ep_size (4)
```

**Fix**:
- In `interpolation.py`: Add `attention_dp_size=best_decode_config.dp` to `get_max_kv_tokens()` kwargs
- In `profile_decode.py`: Add `model_config_kwargs.setdefault("attention_dp_size", attention_dp_size)`
  to forward the positional arg into the kwargs dict

### Files Changed

| File | Change |
|------|--------|
| `components/src/dynamo/profiler/interpolation.py` | Use `.tp` instead of `.tp_size`; add `attention_dp_size` to decode kwargs |
| `components/src/dynamo/profiler/utils/profile_decode.py` | Forward `attention_dp_size` into `model_config_kwargs` |

### Verification

Tested locally with AIConfigurator CLI against `Qwen/Qwen3-235B-A22B-FP8` on `h100_sxm`:

```
=== Prefill interpolation (tp=4, moe_tp=1, moe_ep=4) ===
  TTFT: 149.76ms
  SUCCESS

=== Decode get_max_kv_tokens (tp=1, attention_dp_size=8, moe_tp=2, moe_ep=4) ===
  SUCCESS

=== Decode interpolation (tp=1, attention_dp_size=8, moe_tp=2, moe_ep=4) ===
  ITL: 22.02ms, tokens/s/gpu: 454.15
  SUCCESS
```

End-to-end verified with DGDR deployment on AKS cluster (32x H100 SXM):
- DGDR with planner features → profiling → interpolation → DGD generated successfully
- Generated DGD: `trtllm-disagg` with 2x prefill (4 GPU) + 2x decode (8 GPU) + planner

### Impact

All MoE models are affected when using `searchStrategy: rapid` **with planner features
enabled**. Without planner features, profiling succeeds because interpolation is skipped.
Dense models are unaffected.
