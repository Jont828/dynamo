# E-Commerce Demo: Planner Metrics Fix & Stress Test Results

## Problem

When running `demo-ecommerce.sh --load-test-only --stress-test`, the planner logged
`Metrics contain None or NaN values (no active requests), skipping adjustment` every
tick and never scaled replicas.

## Root Cause

Three issues prevented the planner from seeing frontend metrics in Prometheus.

### 1. Missing `prometheusEndpoint` in Helm values

The `dynamo-platform` chart was installed with all defaults. The
`dynamo-operator.dynamo.metrics.prometheusEndpoint` value was empty, so:

- The operator never injected `PROMETHEUS_ENDPOINT` into any pod.
- No PodMonitors were created by the operator for scraping Dynamo components.

The planner's hardcoded fallback URL happened to match the Prometheus service, so it
could reach Prometheus -- but no metrics were being scraped correctly.

### 2. `dynamo_namespace` label mismatch (primary)

A manually-created `ServiceMonitor` was scraping the frontend but **hardcoded** the
`dynamo_namespace` label to `"dynamo"`:

```yaml
metricRelabelings:
- action: replace
  replacement: dynamo        # <-- hardcoded
  targetLabel: dynamo_namespace
```

The planner filters metrics by its `DYN_NAMESPACE` env var, which the operator sets to
`dynamo-system-vllm-disagg` (the pattern `{k8s_namespace}-{dgd_name}`). Since
`"dynamo" != "dynamo-system-vllm-disagg"`, the planner's Python-side filter matched
zero time series, returning NaN for all histogram averages (TTFT, ITL, ISL, OSL,
request duration).

### 3. Planner ConfigMap namespace override

The planner's ConfigMap (`planner-config-7369`) contained `"namespace": "dynamo"` from
the DGDR profiler output. Since Pydantic's `model_validate` uses the JSON value over
the `default_factory`, this overrode the `DYN_NAMESPACE` env var. Even after fixing the
PodMonitor labels, the planner was still querying for `dynamo_namespace == "dynamo"`.

## Fix Applied

### Step 1: Delete the manual ServiceMonitor

```bash
kubectl delete servicemonitor dynamo-frontend -n dynamo-system
```

### Step 2: Helm upgrade with Prometheus endpoint

```bash
helm upgrade dynamo-platform nvidia-dynamo/dynamo-platform \
  --namespace dynamo-system \
  --reuse-values \
  --set dynamo-operator.dynamo.metrics.prometheusEndpoint=http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090
```

This creates operator-managed PodMonitors with correct `relabelings` that read the
`nvidia.com/dynamo-namespace` pod label (set to `dynamo-system-vllm-disagg`), and
injects `PROMETHEUS_ENDPOINT` into all managed pods.

### Step 3: Patch planner ConfigMap

```bash
# Update namespace and reduce adjustment interval for faster demo
kubectl get configmap planner-config-7369 -n dynamo-system \
  -o jsonpath='{.data.planner_config\.json}' | \
  python3 -c "import json,sys; c=json.load(sys.stdin); \
    c['namespace']='dynamo-system-vllm-disagg'; \
    c['throughput_adjustment_interval']=60; \
    print(json.dumps(c))" | \
  xargs -0 -I{} kubectl create configmap planner-config-7369 \
    -n dynamo-system --from-literal='planner_config.json={}' \
    --dry-run=client -o yaml | kubectl apply -f -
```

### Step 4: Restart planner

```bash
kubectl delete pod -n dynamo-system -l nvidia.com/dynamo-component=Planner
```

## Verification

After the fix, Prometheus metrics show the correct label:

```
dynamo_namespace=dynamo-system-vllm-disagg  model=qwen/qwen3-32b
```

And the planner logs real observations:

```
Observed num_req: 186.55 isl: 4008.00 osl: 500.00
Observed ttft: 9120.68ms itl: 35.47ms
Prefill calculation: 12461.24(p_thpt) / 4698.12(p_engine_cap) = 3(num_p)
Decode calculation: 1554.55(d_thpt) / 345.78(d_engine_cap) = 5(num_d)
Updating decode component VllmDecodeWorker to desired replica count 3
```

## Stress Test Results

### Configuration

- Model: Qwen/Qwen3-32B (TP=2, bfloat16)
- Cluster: 4x ND H100 nodes, 32 H100-80GB GPUs total
- SLA: TTFT <= 500ms, ITL <= 30ms
- aiperf: concurrency 200, ISL 4000, OSL 500, `ignore_eos:true`, 300s duration
- Planner: `max_gpu_budget=8`, `throughput_adjustment_interval=60s`, ARIMA predictor

### Run 1: Before scaling (1 prefill / 1 decode = 4 GPUs)

| Metric          |       avg |      p50 |       p90 |        p99 |
|-----------------|-----------|----------|-----------|------------|
| TTFT (ms)       | 30,938.14 | 3,642.39 | 110,381.56 | 297,016.49 |
| ITL (ms)        |     28.58 |    28.07 |      44.58 |      46.53 |
| Request Latency |  45,200   | 23,318   |  117,512   |  304,139   |
| Throughput      |  0.87 req/s |        |           |            |
| Goodput         |  0.17 req/s |        |           |            |
| Completed       |  283 / 478 sent |   |           |            |

### Planner scaling decision

The planner detected TTFT 18x above SLA and scaled decode workers 1 -> 3:

```
Total GPUs required (16) exceeds max GPU budget (8),
scaling down to 1 prefill and 3 decode replicas
```

### Run 2: After scaling (1 prefill / 3 decode -> scaled back to 1 during test = mixed)

| Metric          |       avg |      p50 |       p90 |        p99 |
|-----------------|-----------|----------|-----------|------------|
| TTFT (ms)       | 15,076.29 |   666.65 |  14,075.65 | 275,070.21 |
| ITL (ms)        |     25.19 |    25.67 |      33.36 |      35.95 |
| Request Latency |  27,644   | 13,848   |   29,090   |  282,193   |
| Throughput      |  1.74 req/s |        |           |            |
| Goodput         |  0.52 req/s |        |           |            |
| Completed       |  563 / 770 sent |   |           |            |
| Errors          |  12 (from scale-down) |  |        |            |

### Improvement (Run 2 vs Run 1)

| Metric         | Improvement |
|----------------|-------------|
| Avg TTFT       | 2x lower    |
| P50 TTFT       | 5.5x lower  |
| P90 TTFT       | 7.8x lower  |
| Throughput     | 2x higher   |
| Goodput        | 3x higher   |
| Requests done  | 2x more     |

Note: Run 2 performance was mixed because the planner scaled decode back down from 3
to 1 mid-test (ARIMA predictor smoothing). The P50 TTFT of 666ms (near-SLA) reflects
the period when all 3 decode workers were active. The 12 SSE errors came from the
known scale-down limitation where in-flight requests on terminated workers fail.

## Scaling Envelope

| Dimension                           | Current | Available |
|--------------------------------------|---------|-----------|
| Cluster GPU total                    | 32 H100 | 32 H100   |
| Planner `max_gpu_budget`             | 8       | up to 32  |
| GPUs per worker (TP=2)               | 2       | 2         |
| Max workers at `max_gpu_budget=8`    | 4       | --        |
| Max workers at `max_gpu_budget=32`   | --      | 16        |
| DGDR profiler recommended           | --      | 8P + 8D = 32 GPUs |
| Current live replicas                | 1P + 1D | --        |

The DGDR profiler recommended 8 prefill + 8 decode replicas (32 GPUs) as the selected
configuration. The planner's `max_gpu_budget=8` caps scaling to 4 workers total.
Increasing `max_gpu_budget` to 32 would allow the planner to use the full cluster
capacity and match the profiler's recommendation.

## Next Steps: Full-Scale Stress Test

To push the system to its limits with the full 32-GPU envelope:

```bash
# 1. Update planner config: max_gpu_budget=32
kubectl get configmap planner-config-7369 -n dynamo-system \
  -o jsonpath='{.data.planner_config\.json}' | \
  python3 -c "import json,sys; c=json.load(sys.stdin); \
    c['max_gpu_budget']=32; print(json.dumps(c))" | \
  xargs -0 -I{} kubectl create configmap planner-config-7369 \
    -n dynamo-system --from-literal='planner_config.json={}' \
    --dry-run=client -o yaml | kubectl apply -f -

# 2. Restart planner
kubectl delete pod -n dynamo-system -l nvidia.com/dynamo-component=Planner

# 3. Run stress test
source ~/go/src/aiperf/venv/bin/activate
aiperf profile \
  --model "Qwen/Qwen3-32B" \
  --tokenizer "Qwen/Qwen3-32B" \
  --endpoint-type chat \
  --url "localhost:8000" \
  --streaming \
  --synthetic-input-tokens-mean 4000 \
  --output-tokens-mean 500 \
  --extra-inputs ignore_eos:true \
  --concurrency 200 \
  --benchmark-duration 600 \
  --goodput "time_to_first_token:500 inter_token_latency:30" \
  --artifact-dir /tmp/ecommerce_stress_test_full_scale \
  --ui-type simple
```

## Notes on Load-Based Scaling

Load-based scaling (`--enable-loadbased-scaling`) uses ForwardPassMetrics (FPM) from
the Dynamo event plane and the KV Router's `/metrics` endpoint for worker discovery.

**Requirements:**
- KV Router must be deployed as a separate component (not just the frontend's embedded
  router)
- Workers must publish FPM events (automatic with vLLM's `InstrumentedScheduler`)
- `DYN_FORWARDPASS_METRIC_PORT` defaults to 20380

**Current limitation in this deployment:** The DGD does not deploy a separate KV Router
component. The frontend embeds the router internally, but the load-based scaler cannot
discover worker counts from the embedded router's metrics. As a result, load-based
scaling reports `"Worker count mismatch: DGD reports P=1, D=1; router metrics reports
P=0, D=0"` and skips all adjustments.

**Workaround:** Use throughput-based scaling only with `--load-predictor constant` to
avoid ARIMA predictor smoothing delays. The constant predictor uses observed traffic
directly without smoothing, providing faster reaction to load changes.

**To enable load-based scaling:** Deploy with a KV Router component (e.g.,
`disagg_planner.yaml` from `examples/backends/vllm/deploy/`) which includes a
separate router service that exposes worker count metrics.

## Full-Scale Test Results (max_gpu_budget=32)

### Scale-up to 3P + 5D (16 GPUs)

With `max_gpu_budget=32`, the planner scaled aggressively on the first tick that saw
burst traffic:

```
Observed num_req: 163.64 isl: 4008.00 osl: 500.00
Observed ttft: 9438.41ms itl: 35.55ms
Prefill calculation: 10930.91 / 4698.12 = 3 prefill replicas
Decode calculation:  1363.64 / 340.84  = 5 decode replicas
Updating prefill component VllmPrefillWorker to desired replica count 3
Updating decode component VllmDecodeWorker to desired replica count 5
```

No GPU budget warning -- 16 GPUs is within the 32 budget. Both prefill and decode
scaled simultaneously.

### Performance at 3P + 5D (16 GPUs active)

When all workers were online and serving traffic, the planner observed:

```
Observed num_req: 240.00 isl: 4008.00 osl: 500.00
Observed ttft: 2130.97ms itl: 22.00ms
```

| Metric    | 1P+1D (4 GPUs) | 3P+5D (16 GPUs) | Improvement |
|-----------|----------------|------------------|-------------|
| TTFT      | 9,438ms        | 2,130ms          | 4.4x lower  |
| ITL       | 35.55ms        | 22.00ms          | Below SLA   |
| num_req   | 163            | 240              | 1.5x more   |

### Oscillation issue

The ARIMA predictor caused oscillation: after scaling to 3P+5D, the improved metrics
made it look like 1P+1D was sufficient, triggering an immediate scale-down. The system
then became overloaded again.

**Root cause:** Throughput-based scaling uses *completed request counts* from
Prometheus histograms. A saturated system completes fewer requests per interval,
which paradoxically makes demand *look lower* to the planner. When the system is
deeply overloaded (TTFT > 60s), only ~15-17 requests complete per 60s interval,
far below the actual 200 concurrent demand. The planner sees `p_thpt=1020 /
p_engine_cap=4698 = 0.22` and computes 1 replica, even though the queue has
hundreds of pending requests.

The correction factor (TTFT_observed / TTFT_SLA) partially compensates by adjusting
engine capacity, but the throughput calculation (`completed_requests * ISL / interval`)
remains the primary driver and underestimates demand when the system is saturated.

**Mitigations:**
1. **`--load-predictor constant`** -- avoids ARIMA smoothing lag (applied, but
   doesn't fix the saturation underestimation)
2. **`--min-endpoint N`** -- sets a floor to prevent scaling below N replicas
3. **Load-based scaling** -- uses per-engine FPM metrics (queued tokens, wall time)
   which directly measure GPU saturation rather than completed requests. Requires
   KV Router (see notes above)
4. **Higher `--loadbased-scaling-down-sensitivity` value** -- slows scale-down
5. **`--no-operation` mode** -- observe-only, useful for tuning before enabling
   actual scaling

### Recommended configuration for the demo

For the e-commerce demo with reliable scale-up behavior:

```json
{
  "max_gpu_budget": 32,
  "min_endpoint": 1,
  "throughput_adjustment_interval": 60,
  "load_predictor": "constant",
  "enable_throughput_scaling": true,
  "enable_load_scaling": false,
  "no_correction": false
}
```

For production deployments, deploy with a KV Router and enable both scaling modes:

```json
{
  "enable_throughput_scaling": true,
  "enable_load_scaling": true,
  "throughput_adjustment_interval": 180,
  "load_adjustment_interval": 5,
  "min_endpoint": 2
}
```
