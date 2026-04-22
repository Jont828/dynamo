# GitHub Issue Draft: DGDR rapid search strategy fails on single-GPU clusters

**Title:** `DGDR rapid search strategy fails on single-GPU clusters with ValueError in disagg config`

---

**Describe the Bug**

When creating a `DynamoGraphDeploymentRequest` (DGDR) with `searchStrategy: rapid` on a cluster with only 1 GPU, the profiling job crashes with `ValueError: total_gpus must be greater than 2 for disagg, got 1`. The `rapid` strategy unconditionally attempts to build both aggregated and disaggregated `TaskConfig` objects via AIC's `build_default_task_configs()`, but the disaggregated path requires `total_gpus > 2`. There is no guard in the profiler to skip disagg evaluation or fall back to the naive generator when `total_gpus < 2`.

**Steps to Reproduce**

1. Set up a Kubernetes cluster with a single GPU node (any cloud provider or on-prem — 1x H100, L40S, etc.)
2. Install the GPU Operator and Dynamo platform (v1.0.1)
3. Apply the following DGDR:

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: qwen3-0-6b
spec:
  model: "Qwen/Qwen3-0.6B"
  image: "nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.0.1"
  searchStrategy: rapid
  autoApply: true
```

4. Observe the profiling job pod logs:
```bash
kubectl logs -f -l job-name=profile-qwen3-0-6b -c profiler -n dynamo-system
```

**Expected Behavior**

The profiler should gracefully handle single-GPU clusters by:
- Skipping the disaggregated `TaskConfig` when `total_gpus < 2` and only evaluating aggregated configurations, OR
- Falling back to the naive generator path (`_run_naive_fallback`) which does not construct disagg `TaskConfig` objects, OR
- Returning a clear, actionable error message indicating that 1 GPU is insufficient for disagg and suggesting an agg-only workaround

**Actual Behavior**

The profiling job crashes with an unhandled `ValueError`:

```
INFO  profile_sla._extract_profiler_params: Profiler config: model=Qwen/Qwen3-0.6B, backend=auto, system=h100_sxm, total_gpus=1, ...strategy=rapid
ERROR profile_sla.run_profile: Profile job failed with error
Traceback (most recent call last):
  File ".../dynamo/profiler/profile_sla.py", line 321, in run_profile
    ) = await _execute_strategy(
  File ".../dynamo/profiler/profile_sla.py", line 155, in _execute_strategy
    pick_result = run_rapid(...)
  File ".../dynamo/profiler/rapid.py", line 331, in run_rapid
    return _run_default_sim(...)
  File ".../dynamo/profiler/rapid.py", line 224, in _run_default_sim
    task_configs = build_default_task_configs(...)
  File ".../aiconfigurator/cli/main.py", line 487, in build_default_task_configs
    disagg_task = TaskConfig(serving_mode="disagg", **disagg_kwargs)
  File ".../aiconfigurator/sdk/task.py", line 657, in __init__
    self.config, applied_layers = TaskConfigFactory.create(ctx)
  File ".../aiconfigurator/sdk/task.py", line 285, in create
    cls._finalize_disagg(config, ctx)
  File ".../aiconfigurator/sdk/task.py", line 487, in _finalize_disagg
    raise ValueError(f"total_gpus must be greater than 2 for disagg, got {ctx.total_gpus}")
ValueError: total_gpus must be greater than 2 for disagg, got 1
```

The DGDR transitions to `Failed` state. The workaround is to deploy a `DynamoGraphDeployment` (DGD) directly using an aggregated `agg.yaml` pattern.

**Root Cause Analysis**

The call chain is:
1. Operator auto-discovers `totalGpus=1` from node labels and sets it in the DGDR spec
2. `_execute_strategy()` calls `run_rapid()` with `total_gpus=1`
3. Since the model/GPU combo is AIC-supported, `aic_supported=True`, so it enters `_run_default_sim()` (not the naive fallback)
4. `_run_default_sim()` passes `total_gpus=1` directly to `build_default_task_configs()` with no guard
5. AIC unconditionally constructs a disagg `TaskConfig`, which enforces `total_gpus > 2`

No existing validation catches this:
- `dgdr_validate.py` has no minimum `totalGpus` check
- The webhook validation doesn't check GPU count for `rapid` strategy
- The naive fallback (`_run_naive_fallback`) would work fine since it uses `build_naive_generator_params` (not `build_default_task_configs`) and never creates a disagg `TaskConfig`, but is only reached when `aic_supported=False`

Note: `numGpusPerNode` is NOT related to this error. The rapid path only passes `total_gpus` to `build_default_task_configs` — `numGpusPerNode` is never forwarded to AIC in the rapid strategy and is only used in the thorough strategy's candidate generation and DGD config modifier layer.

**Environment**
- **K8s Distribution:** AKS (Azure Kubernetes Service)
- **K8s Version:** v1.34.3
- **Node OS:** Ubuntu 22.04.5 LTS (amd64)
- **GPU:** NVIDIA H100 NVL (1x GPU)
- **Dynamo Platform Version:** 1.0.1 (Helm chart `dynamo-platform-1.0.1`)
- **Profiler Image:** `nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.0.1`
- **GPU Operator:** v26.3.0

Note: This bug is **not** specific to AKS or any particular driver configuration. It will reproduce on any Kubernetes cluster (EKS, GKE, on-prem, kind, etc.) with only 1 GPU available.
