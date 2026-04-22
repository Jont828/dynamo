# Qwen3-235B-A22B-FP8 Disaggregated TRT-LLM Deployment Summary

**Date**: 2026-04-22
**Cluster**: AKS `demo` with 4x `Standard_ND96isr_H100_v5` nodes (32 GPUs)
**Model**: `Qwen/Qwen3-235B-A22B-FP8` (MoE, 128 experts, top-8)
**Backend**: TensorRT-LLM
**Status**: Deployed and healthy

## Working Deployment

The DGDR was successfully deployed using disaggregated prefill/decode with the
following topology (profiler-selected):

| Service  | Replicas | GPUs/replica | TP | PP | EP | Shared Memory | Total GPUs |
|----------|----------|--------------|----|----|----|---------------|------------|
| prefill  | 2        | 4            | 4  | 1  | 4  | 256Gi         | 8          |
| decode   | 2        | 8            | 8  | 1  | 4  | 256Gi         | 16         |
| frontend | 1        | -            | -  | -  | -  | -             | 0          |
| planner  | 1        | -            | -  | -  | -  | -             | 0          |
| **Total**| 6 pods   |              |    |    |    |               | **24**     |

## DGDR Spec

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: qwen3-235b-fp8-disagg
  namespace: default
spec:
  model: Qwen/Qwen3-235B-A22B-FP8
  backend: trtllm
  searchStrategy: rapid
  autoApply: true
  image: jont828/dynamo-planner:trt-fix-v2

  modelCache:
    pvcName: model-cache
    pvcMountPath: /home/dynamo/.cache/huggingface
    pvcModelPath: hub/models--Qwen--Qwen3-235B-A22B-FP8/snapshots/39eb2b067ea6b8e3e1dd97d3cd0c7ffeaf3e1a35

  hardware:
    gpuSku: h100_sxm
    vramMb: 81920
    totalGpus: 32
    # numGpusPerNode omitted — auto-discovered by operator via DCGM

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

  overrides:
    dgd:
      apiVersion: nvidia.com/v1alpha1
      kind: DynamoGraphDeployment
      metadata:
        name: placeholder
      spec:
        services:
          prefill:
            sharedMemory:
              size: 256Gi
          decode:
            sharedMemory:
              size: 256Gi
```

## Required Fixes

Two bugs were discovered and fixed during this deployment:

### 1. TRT-LLM Service Names Exceed Grove 45-char Limit

**Issue**: [#8480](https://github.com/ai-dynamo/dynamo/issues/8480)
**PR**: [#8563](https://github.com/ai-dynamo/dynamo/pull/8563)
**Branch**: `fix/shorten-trtllm-service-names`

The profiler generated service names `TRTLLMPrefillWorker` (18 chars) and
`TRTLLMDecodeWorker` (18 chars) which, combined with the DGD name
`trtllm-disagg` (13 chars), exceeded the Grove webhook's 45-character
`maxCombinedResourceNameLength` validation. The fix shortens the k8s service
names to `prefill` and `decode` in `backend_components.py`.

### 2. Profiler Sets `multinode.nodeCount` Incorrectly for Data-Parallel Workers

**Issue**: [#8567](https://github.com/ai-dynamo/dynamo/issues/8567)
**PR**: [#8564](https://github.com/ai-dynamo/dynamo/pull/8564)
**Branch**: `fix/shorten-trtllm-service-names` (cherry-picked)

The profiler's `set_multinode_config()` used the total `gpu_count` (including
data-parallel shards) to compute `multinode.nodeCount`. For a decode worker
with dp=8, moe_tp=2, moe_ep=4 on 8-GPU nodes, this produced `gpu_count=64`
and `nodeCount=8`, requesting 128 GPUs across 2 replicas on a 32-GPU cluster.

**Root cause**: `set_multinode_config()` in `config.py` did not distinguish
between per-instance GPUs (TP x PP) and total GPUs (including dp).
Data-parallel shards are independent replicas that each fit on a single node,
so only tensor/pipeline parallelism should drive the multinode decision.

**Fix**: Added `_get_per_instance_gpus()` which parses `--tensor-parallel-size`
and `--pipeline-parallel-size` from the worker's CLI args to derive the
per-instance GPU count (TP x PP). `set_multinode_config()` now uses this value
for the multinode decision. For the Qwen3-235B case (`--tensor-parallel-size 8`
on 8-GPU nodes): `per_instance=8 <= num_gpus_per_node=8` -> no multinode.

### Pre-existing: InfiniBand / RDMA Setup

The cluster required InfiniBand configuration for NIXL KV cache transfers
between prefill and decode workers. See
[aks-infiniband-rdma-setup.md](aks-infiniband-rdma-setup.md) for details on
the three issues fixed (ib_umad, memlock rlimit, RDMA device exposure).

## Image Versions

| Image | Tag | Description |
|-------|-----|-------------|
| `jont828/dynamo-planner` | `trt-fix-v2` | Profiler with both fixes |
| `jont828/dynamo-frontend` | `trt-fix-v2` | Frontend with both fixes |
| `jont828/tensorrtllm-runtime` | `trt-fix-v2` | TRT-LLM worker runtime (retagged from trt-fix) |

Images were built as thin layers on top of `trt-fix` base images, applying
only the changed `config.py` for the multinode fix.

## Parent Issue

[#8469 — DGDR fails to profile and deploy the majority of models and backends](https://github.com/ai-dynamo/dynamo/issues/8469)
