# TRT-LLM MoE Profiler Gap

## Summary

The DGDR profiler cannot generate candidate configs for MoE models on the TRT-LLM backend. `TrtllmConfigModifier.convert_config()` raises `NotImplementedError` for any MoE model, causing all enumerated candidates to fail and the profiling job to produce no results. However, TRT-LLM itself fully supports MoE models — hand-tuned DGDs work fine (see `recipes/deepseek-v32-fp4/trtllm/disagg-kv-router/deploy.yaml`).

## Root Cause

`components/src/dynamo/profiler/utils/config_modifiers/trtllm.py`, lines 85-88:

```python
def convert_config(cls, config, target, is_moe_model=False):
    if is_moe_model:
        raise NotImplementedError(
            "MoE model support is not implemented for TrtLLM backend"
        )
```

The config modifier doesn't know how to generate TRT-LLM-specific MoE settings (`moe_config.backend`, `moe_expert_parallel_size`, `enable_attention_dp`, WIDEEP vs TRTLLM backend selection, etc.), so it blanket-rejects all MoE models.

## Reproduce

Apply this DGDR:

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  namespace: default
  name: deepseek-v32-fp4-trtllm
spec:
  model: nvidia/DeepSeek-V3.2-NVFP4
  backend: trtllm
  searchStrategy: thorough
  autoApply: false
  hardware:
    numGpusPerNode: 8
    totalGpus: 32
    gpuSku: h100_sxm
    vramMb: 81559
  modelCache:
    pvcName: model-cache
    pvcMountPath: /opt/model-cache
    pvcModelPath: hub/models--nvidia--DeepSeek-V3.2-NVFP4/snapshots/7c0f62c6da1da0c81c6e097010cc55854d206812
  workload:
    isl: 4000
    osl: 1000
  sla:
    ttft: 2000.0
    itl: 50.0
```

The profiler enumerates 3 prefill + 4 decode candidates (all using `moe_ep`), then every single one fails with:

```
NotImplementedError: MoE model support is not implemented for TrtLLM backend
```

Result: `Enumeration complete: 0 prefill DGDs, 0 decode DGDs` → profiling job errors out.

## Evidence That TRT-LLM Supports MoE

The hand-tuned DGD at `recipes/deepseek-v32-fp4/trtllm/disagg-kv-router/deploy.yaml` runs DeepSeek-V3.2 on TRT-LLM with:

- TP=8, MoE EP=8, multinode (2 nodes × 4 GPUs per worker)
- Prefill: `moe_config.backend: TRTLLM`
- Decode: `moe_config.backend: WIDEEP` with `use_low_precision_moe_combine: true`
- `enable_attention_dp: true` on both prefill and decode

## Impact

Any DGDR targeting a MoE model with `backend: trtllm` will fail at profiling. Users must manually craft a DGD instead of using the automated profiling pipeline.

## Workaround

Skip the DGDR and apply a hand-tuned DGD directly (e.g. the recipe above).
