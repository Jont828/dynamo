# GitHub Issue Draft: DGDR Validation Strategy

**Repo:** `ai-dynamo/dynamo`
**Title:** `[BUG]: DGDR fails to profile and deploy the majority of models and backends`

---

## Summary

DGDR (DynamoGraphDeploymentRequest) is the intended first-class entry point for deploying models on Dynamo without manual tuning. The design doc intends for the v1beta1 DGDR to eventually replace the v1alpha1 DGD as the entrypoint for model deployment. The redesigned DGDR should cover two scenarios:

1. Users who don't care for profiling/SLA and just want to deploy a DGD can express it in a DGDR with `searchStrategy= rapid` and `autoApply=true`. This transition should be undisruptive and easy to migrate DGDs to a DGDR and get 2 min profiling for free.
2. Users who want profiling, planner, and more advanced deployment options can use the full capabilities of DGDR to optimize their deployments.

Based on my own testing and feedback from NVIDIA QA, there is a significant gap between the current DGDR implementation and these intended scenarios. The problems lie largely with profiling and deployment. 

Some issues I've found include:
- The current profiling relies on a look up table of models/hardware from the [AIC support matrix](https://ai-dynamo.github.io/aiconfigurator/support-matrix/) to produce candidate configs, but models that are not in the AIC matrix are unable to be deployed by DGDR. When profiling is run with `thorough` search strategy, the profiler is unable to generate valid config candidates and goes to a "naive fallback" mode that just deploys in agg mode which fails for large models that don't fit on a single GPU.
- When planner is enabled, MoE models fail when profiling and planner is enabled, see #8273.
- When the profiler produces canadidates that will hit an OOM in thorough mode, the deployment will be stuck waiting until it times out. For example, when attempting to deploy Deepseek R1 on a 32 x H100 cluster, these were the candidates that were produced. All the TP=1 will fail and we don't have a way to recover.
```
Prefill candidates (4):

TP=1, DP=8, EP=8 (8 GPUs) ← currently stuck here (OOM)
TP=1, DP=16, EP=16 (16 GPUs)
TP=1, DP=32, EP=32 (32 GPUs)
TP=8, DP=1, EP=8 (8 GPUs)
Decode candidates (5):

TP=1, DP=8, EP=8
TP=1, DP=16, EP=16
TP=1, DP=32, EP=32
TP=1, DP=64, EP=64 ← won't work, only 32 GPUs
TP=8, DP=1, EP=8
```
- When attempting to deploy DeepSeek V3.2 NVFP4 using DGDR based on the provided [DGD recipes](https://github.com/ai-dynamo/dynamo/tree/demo/recipes/deepseek-v32-fp4/trtllm) for TRT-LLM, I hit an error [here](https://github.com/ai-dynamo/dynamo/blob/092b8f58f6976630cd2cfc5d3fd6eb992af476ad/components/src/dynamo/profiler/utils/config_modifiers/trtllm.py#L88) saying that MoE models are not supported for TRT-LLM, even though the hand-tuned DGD for Deepseek on TRT-LLM uses MoE EP=8. This is a gap in functionality that blocks deploying certain models on certain backends with DGDR.

Additionally, QA feedback is as follows:

> DGDR is effectively non-functional for most users. The deploy-by-intent path supports only 6 GPU SKUs, all SXM variants plus L40S, with no published list. PCIe variants are excluded, blocking most cloud and colocation deployments. Beyond SKU restrictions, the AIC profiler crashes with a NaN handling bug in pareto_analysis.py, causing DGDR to reach Failed status after 4 retries on both minimal and explicit hardware configs.

> Needs Improvement:

> - DGDR only supports 6 GPU SKUs, all SXM variants plus L40S. PCIe GPUs (H100-PCIe, A100-PCIe, A30, L4) are excluded, blocking DGDR for most cloud and colocation users. The supported list is only discoverable by hitting validation errors. The docs example uses a format (H100-SXM5-80GB) that doesn’t match what the webhook accepts (h100_sxm). Recommended Fix:
>   - Publish the supported GPU SKU list in the DGDR documentation
>   - Add PCIe variants (h100_pcie, a100_pcie, l4, etc.)
>   - Fix the docs example to use a valid value (e.g., h100_sxm not H100-SXM5-80GB)
> - DGDR hands-off deployment: Non-functional. GPU auto-discovery succeeds (H200 SXM correctly detected from node labels), and the profiling job starts running Pareto analysis across backends (vLLM, TRT-LLM) with multiple parallelism configs. However, the AIC profiler crashes with KeyError: "None of [Index([nan, nan, nan, nan]...)]" in pareto_analysis.py:510 — a NaN handling bug in the rapid search ranking. After 4 retries the DGDR reaches Failed status. Tested with both minimal spec and explicit hardware config (gpuSku: h200_sxm).

We would like to get DGDR to parity with DGD to close the gap in implementation, bugs, and model coverage. Concretely, this looks like the following:

1. A DGDR can always produce a DGD of a model and the DGD will run w/o OOM errors, assuming it can fit on the provided hardware. We should not only support a hard coded list of models and SKUs and should not fail due to bugs in profiling.
2. Every DGD can be deployed as a DGDR with some level of overrides, and the DGDR can produce a similar DGD as the hand-tuned one.
3. In `through` profiling mode, the profiler should be able to generate candidates that are valid and won't cause OOMs, and if a candidate does cause an OOM, the profiler should be able to recover and try other candidates rather than getting stuck.
4. If a model cannot fit, it should fail gracefully and signal to the user, i.e. show that no viable profiling candidates were found rather than just getting stuck deploying a naive agg config.
5. If a DGDR produces a DGD, the DGD should deploy successfully w/o OOM error or scheduling failures.

For point #2, we can create a validation strategy based on a support matrix for DGDR. We can start with the existing hand-tuned recipes in `recipes/` such that for each combination of models and backends in the recipes, we can deploy them successfully with DGDR.

### Hand-Tuned Recipes (`recipes/`)

These are the production-tuned DGDs that represent what users should be able to deploy via DGDR:

| Model | Backend | Mode | GPUs | GPU SKU | Recipe Path | In AIC Matrix? |
|---|---|---|---|---|---|---|
| **DeepSeek-R1** | SGLang | Disagg | 16 | H200 | `recipes/deepseek-r1/sglang/disagg-8gpu/` | ❌ |
| DeepSeek-R1 | SGLang | Disagg | 32 | H200 | `recipes/deepseek-r1/sglang/disagg-16gpu/` | ❌ |
| DeepSeek-R1 | TRT-LLM | Disagg wide-EP | 36 | GB200 | `recipes/deepseek-r1/trtllm/disagg/wide_ep/gb200/` | ❌ |
| DeepSeek-R1 | vLLM | Disagg | 32 | H200 | `recipes/deepseek-r1/vllm/disagg/` | ❌ |
| **DeepSeek-V3.2-NVFP4** | TRT-LLM | Agg | 32 | GB200 | `recipes/deepseek-v32-fp4/trtllm/agg-round-robin/` | ❌ |
| DeepSeek-V3.2-NVFP4 | TRT-LLM | Disagg | 32 | GB200 | `recipes/deepseek-v32-fp4/trtllm/disagg-kv-router/` | ❌ |
| **GLM-5-NVFP4** | SGLang | Disagg | 20 | GB200 | `recipes/glm-5-nvfp4/sglang/disagg/` | ❌ |
| **GPT-OSS 120B** | TRT-LLM | Agg | 4 | GB200 | `recipes/gpt-oss-120b/trtllm/agg/` | ✅ |
| GPT-OSS 120B | TRT-LLM | Disagg | 5 | GB200 / B200 | `recipes/gpt-oss-120b/trtllm/disagg/` | ✅ |
| **Kimi-K2.5-NVFP4** | TRT-LLM | Agg | 8 | B200 | `recipes/kimi-k2.5/trtllm/agg/nvidia/` | ❌ |
| Kimi-K2.5-NVFP4 | TRT-LLM | Agg + KVBM | 8 | B200 | `recipes/kimi-k2.5/trtllm/agg/nvidia/deploy-kvbm.yaml` | ❌ |
| Kimi-K2.5-NVFP4 | TRT-LLM | Agg + SpecDec | 32 | B200 | `recipes/kimi-k2.5/trtllm/agg/nvidia/deploy-specdec.yaml` | ❌ |
| **Llama-3.3-70B FP8** | vLLM | Agg | 4 | H100 / H200 | `recipes/llama-3-70b/vllm/agg/` | ❌ |
| Llama-3.3-70B FP8 | vLLM | Disagg single-node | 8 | H100 / H200 | `recipes/llama-3-70b/vllm/disagg-single-node/` | ❌ |
| Llama-3.3-70B FP8 | vLLM | Disagg multi-node | 16 | H100 / H200 | `recipes/llama-3-70b/vllm/disagg-multi-node/` | ❌ |
| Llama-3.3-70B FP8 | vLLM | Agg + GAIE | 4 | H100 / H200 | `recipes/llama-3-70b/vllm/agg/gaie/` | ❌ |
| Llama-3.3-70B FP8 | vLLM | Disagg + GAIE | 8 | H100 / H200 | `recipes/llama-3-70b/vllm/disagg-single-node/gaie/` | ❌ |
| **Nemotron-3-Super 120B FP8** | SGLang | Agg | 4 | H100 / H200 | `recipes/nemotron-3-super-fp8/sglang/agg/` | ❌ |
| Nemotron-3-Super 120B FP8 | SGLang | Disagg | 4 | H100 / H200 | `recipes/nemotron-3-super-fp8/sglang/disagg/` | ❌ |
| Nemotron-3-Super 120B FP8 | TRT-LLM | Disagg | 4 | H100 / H200 | `recipes/nemotron-3-super-fp8/trtllm/disagg/` | ❌ |
| Nemotron-3-Super 120B FP8 | vLLM | Agg | 4 | H100 / H200 | `recipes/nemotron-3-super-fp8/vllm/agg/` | ❌ |
| **Qwen3-235B-A22B FP8** | TRT-LLM | Agg | 16 | H100 / H200 | `recipes/qwen3-235b-a22b-fp8/trtllm/agg/` | ✅ |
| Qwen3-235B-A22B FP8 | TRT-LLM | Disagg | 16 | H100 / H200 | `recipes/qwen3-235b-a22b-fp8/trtllm/disagg/` | ✅ |
| **Qwen3-32B FP8** | TRT-LLM | Agg | 2 | H100 / H200 / A100 | `recipes/qwen3-32b-fp8/trtllm/agg/` | ✅ |
| Qwen3-32B FP8 | TRT-LLM | Disagg | 8 | H100 / H200 / A100 | `recipes/qwen3-32b-fp8/trtllm/disagg/` | ✅ |
| Qwen3-32B FP8 | vLLM | Disagg | 8 | A100 | `recipes/qwen3-32b-fp8/vllm/disagg/` | ❌ |
| **Qwen3-32B** | vLLM | Agg | 16 | H200 | `recipes/qwen3-32b/vllm/agg-round-robin/` | ✅ |
| Qwen3-32B | vLLM | Disagg KV-router | 16 | H200 | `recipes/qwen3-32b/vllm/disagg-kv-router/` | ✅ |
| **Qwen3-VL-30B FP8** | vLLM | Agg (multimodal) | 1 | GB200 | `recipes/qwen3-vl-30b/vllm/agg-embedding-cache/` | ❌ |

### Feature Examples (`examples/backends/`)

In addition to the production recipes, there are feature-level DGD examples in `examples/backends/{vllm,sglang,trtllm}/deploy/` that cover capabilities like KVBM (CPU offload), Planner, KV router, multinode, GAIE/EPP, and OpenTelemetry tracing. These mostly use `Qwen/Qwen3-0.6B` (single GPU) and are useful for validating that DGDR can express these features, not for model-level profiling coverage. Platform-specific examples also exist for [GKE](https://github.com/ai-dynamo/dynamo/tree/main/examples/deployments/GKE) and [EKS](https://github.com/ai-dynamo/dynamo/tree/main/examples/deployments/EKS).

### AIC Coverage Gap

Cross-referencing the recipes above against the [AIC support matrix](https://ai-dynamo.github.io/aiconfigurator/support-matrix/) for each recipe's target GPU SKU:

- **In AIC and PASS:** Qwen3-235B-A22B-FP8 (trtllm, H100/H200), Qwen3-32B-FP8 (trtllm, H100/H200/A100), Qwen3-32B (vllm, H200), GPT-OSS 120B (trtllm, GB200)
- **NOT in AIC at all:** DeepSeek-R1, Llama-3.3-70B FP8, Nemotron-3-Super 120B FP8, Qwen3-VL-30B FP8, Kimi-K2.5-NVFP4
- **In AIC but all FAIL:** DeepSeek-V3.2-NVFP4 (GB200), GLM-5-NVFP4 (GB200)
- **SKU+backend mismatch:** Qwen3-32B-FP8 vLLM on A100 (FP8 not supported on A100 in AIC)
- **SGLang regression:** All Qwen models FAIL on sglang v0.5.9 (PASS on v0.5.8)

Models not in the AIC matrix will fall back to "naive config generation" — a heuristic that estimates model weight size, picks a basic TP/DP that fits in VRAM, always returns aggregated mode (never disaggregated), and reports zero latency data. If the GPU SKU YAML also doesn't exist in AIC, it defaults to H200 specs (141 GiB VRAM). This means DGDR cannot produce a real profiled deployment for DeepSeek-R1, Llama 3.3 70B, Nemotron-3-Super, or multimodal models.

Some of the models not in AIC's current support matrix will need to be run with `thorough` profiling and will require us to close the gap for profiling on generic models.

A longer term vision in a crawl, walk, run approach is as follows:
1. Crawl: We can deploy the model successfully with DGDR, doesn't need to be optimal
2. Walk: We can refactor the hand-tuned DGD into a DGDR with overrides such that the DGDR + overrides will produce a similar/same DGD as the hand-tuned recipe
3. Run: With minimal overrides, the DGDR can produce a similar DGD as the hand-tuned recipes.