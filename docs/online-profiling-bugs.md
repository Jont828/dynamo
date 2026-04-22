# Online Profiling Bugs

## Bug 1: Decode benchmarking produces no results (empty num_request sweep)

### Symptom

During thorough (online) profiling, the decode benchmarking phase produces no results. The profiler logs show:

```
Log file not found: /data/decode_1gpus_tp1/agg-817a/vllmdecodeworker/0.log
Sweeping num_request: []
ERROR: No decode results produced in THOROUGH mode.
```

This happens for all decode candidates (tp=1 and tp=2).

### Root Cause

**Label selector mismatch between the profiler's log fetcher and the DGD controller's pod labels.**

The profiler calls `DynamoDeploymentClient.get_deployment_logs()` to fetch worker pod logs after a deployment is ready. This method uses the label selector:

```python
# deploy/utils/dynamo_deployment.py, line 460
label_selector = f"nvidia.com/selector={self.deployment_name}-{component.lower()}"
# Example: nvidia.com/selector=agg-817a-vllmdecodeworker
```

However, the DGD controller (non-Grove / DCD pathway) creates DynamoComponentDeployment (DCD) resources with a **worker hash suffix** appended to the name:

```go
// deploy/operator/internal/controller/dynamographdeployment_controller.go, line 1085
dcdName := dynamo.GetDCDResourceName(dgd, serviceName, computedHash)
// Example: agg-817a-vllmdecodeworker-8bccede7
```

The `nvidia.com/selector` label on pods is set to the full DCD name including the hash:

```go
// deploy/operator/internal/controller/dynamocomponentdeployment_controller.go, line 1062
podLabels[commonconsts.KubeLabelDynamoSelector] = kubeName
// kubeName = opt.dynamoComponentDeployment.Name = "agg-817a-vllmdecodeworker-8bccede7"
```

So the profiler queries for `nvidia.com/selector=agg-817a-vllmdecodeworker` but pods have `nvidia.com/selector=agg-817a-vllmdecodeworker-8bccede7`. The selector finds **zero pods**, no logs are written, and the subsequent `get_kv_cache_size_from_dynamo_log()` hits `FileNotFoundError` → returns `max_kv_tokens=0` → `max_concurrency=0` → `sweep_num_request=[]` → no decode results.

**Note**: The Grove pathway (line 1410 in `graph.go`) uses `GetDCDResourceName(dgd, componentName, "")` (empty suffix) which *would* match, but Grove is not installed in this cluster. This bug only affects non-Grove (DCD-based) deployments.

**This bug exists on main** — it is not specific to any branch or environment. It affects any thorough profiling run on a cluster without Grove.

### Fix

**Applied fix**: Changed `get_deployment_logs()` in `deploy/utils/dynamo_deployment.py` to use `nvidia.com/dynamo-graph-deployment-name` + `nvidia.com/dynamo-component` labels instead of `nvidia.com/selector`.

An operator-side fix (changing `nvidia.com/selector` to exclude the hash) was not feasible because:
- The Deployment's `spec.selector.matchLabels` uses `nvidia.com/selector=kubeName` (with hash) at line 972
- Kubernetes Deployment selectors are **immutable after creation** — changing the label value would break existing deployments
- The Deployment selector and pod template labels must match

The pods already have the correct labels for a two-label query:
- `nvidia.com/dynamo-graph-deployment-name` = DGD name (set at `graph.go` line 330, propagated via DCD `Spec.Labels`)
- `nvidia.com/dynamo-component` = original-case service name (set at `graph.go` line 328)

These labels are consistent across both Grove and DCD pathways, making this the most robust fix.

The change:
1. Store original-case service names alongside lowercased (for directory paths): `self._original_components`
2. Use two-label selector: `nvidia.com/dynamo-graph-deployment-name={dgd_name},nvidia.com/dynamo-component={ServiceName}`

**Upstream issue**: https://github.com/ai-dynamo/dynamo/issues/6962

### Files

- `deploy/utils/dynamo_deployment.py` — **fixed**: `get_deployment_logs()` label selector, `create_deployment()` component name storage
- `deploy/operator/internal/controller/dynamocomponentdeployment_controller.go` — pod label assignment (line 1062), DCD name with hash (line 1085)
- `deploy/operator/internal/controller/dynamographdeployment_controller.go` — `computedHash` passed to `GetDCDResourceName` (line 1085)
- `deploy/operator/internal/dynamo/graph.go` — `GetDCDResourceName()` (line 496), Grove label without hash (line 1410)
- `components/src/dynamo/profiler/thorough.py` — `_benchmark_decode_candidates()` (line 148-256), log path construction (line 208-209)
- `components/src/dynamo/profiler/utils/config_modifiers/vllm.py` — `get_kv_cache_size_from_dynamo_log()` (line 332-363)

---

## Bug 2: Sidecar error on profiler failure causes infinite retry loop

### Symptom

When the profiler completes without producing a DGD config (due to Bug 1), the DGDR controller retries the profiling Job indefinitely, creating an infinite loop:

```
Initializing → SweepingPrefill → SweepingDecode → GeneratingDGD → (retry) → Initializing → ...
```

The watcher output shows the DGDR cycling through phases repeatedly:

```
test-profiling-phases-online   Profiling   Initializing         3s
test-profiling-phases-online   Profiling   SweepingPrefill      22s
test-profiling-phases-online   Profiling   SweepingDecode       7m47s
test-profiling-phases-online   Profiling   GeneratingDGD        12m
test-profiling-phases-online   Profiling   SweepingPrefill      12m    ← retry starts
```

### Root Cause

**This bug exists on main** — it is not specific to any branch. The sidecar script and the profiler failure handling are both unchanged from main.

The profiler pod has two containers:
- **profiler**: Runs the actual profiling. When profiling fails to produce a DGD config (e.g., due to Bug 1), `_write_final_output()` in `profile_sla.py` writes `status: failed` to `profiler_status.yaml` and the profiler exits with code 0.
- **output-copier (sidecar)**: Reads `profiler_status.yaml` after the profiler terminates. The sidecar's shell script has a `case "$STATUS"` block that calls `exit 1` on `status: failed`:

```bash
# deploy/operator/internal/controller/dynamographdeploymentrequest_controller.go
# sidecarScriptTemplate (on main):
case "$STATUS" in
  success)
    ...
    ;;
  failed)
    ERROR=$(grep "^error:" "$STATUS_FILE" ...)
    MESSAGE=$(grep "^message:" "$STATUS_FILE" ...)
    echo "ERROR: Profiler failed: ${ERROR:-$MESSAGE}"
    exit 1      # <--- causes Job failure + controller retry
    ;;
  ...
esac
```

The sidecar's `exit 1` causes the **Job** to be marked as failed. The DGDR controller sees the failed Job and creates a new profiler pod, starting the whole profiling process over — deploying DGDs, benchmarking prefill, benchmarking decode, hitting the same error, and retrying indefinitely.

Each retry wastes ~12 minutes of GPU time deploying and benchmarking 4 configurations that will produce the same result.

### Verification

Confirmed this is on main by checking the git history:

- `deploy/utils/dynamo_deployment.py` — not changed on branch (Bug 1 root cause)
- `deploy/operator/internal/controller/dynamocomponentdeployment_controller.go` — not changed on branch
- `deploy/operator/internal/controller/dynamographdeploymentrequest_controller.go` — changed on branch, but the `case "$STATUS"` block with `exit 1` on `failed` is **unchanged from main**. Branch changes only added the `relay_phase` function for continuous phase polling and phase/message preservation in the final ConfigMap.
- `components/src/dynamo/profiler/profile_sla.py` — changed on branch, but the `_write_final_output()` function that writes `status: failed` when no DGD config is produced was **already on main**. Branch changes only added `phase=ProfilingPhase.GeneratingDGD` to the existing `write_profiler_status()` call.

### Expected Behavior

When the profiler completes but doesn't produce a DGD config, the system should transition to a terminal `Failed` state instead of retrying. Two possible approaches:

1. **Sidecar writes failure info to ConfigMap and exits with code 0**. The sidecar should still relay the `status: failed` information to the ConfigMap (phase, message, error), but exit cleanly so the Job succeeds. The DGDR controller then reads the failed phase from the ConfigMap and transitions to a terminal `Failed` DGDR state.

2. **DGDR controller detects repeated Job failures** and stops retrying after N attempts, transitioning to `Failed`.

### Files

- `deploy/operator/internal/controller/dynamographdeploymentrequest_controller.go` — `sidecarScriptTemplate` (the `case "$STATUS"` block with `exit 1` on `failed`)
- `deploy/operator/internal/controller/` — DGDR controller Job management and retry logic
- `components/src/dynamo/profiler/profile_sla.py` — `_write_final_output()` (line 260-278) writes `status: failed`
- `components/src/dynamo/profiler/utils/profiler_status.py` — `ProfilerStatus.FAILED` enum value
