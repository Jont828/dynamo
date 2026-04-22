# DeepSeek-R1 SGLang Disaggregated Serving: nvidia_peermem Required for KV Transfer

## Summary

When deploying DeepSeek-R1 with SGLang in disaggregated prefill/decode mode on Azure ND H100 v5 nodes,
KV cache transfer between prefill and decode workers fails because the `nvidia_peermem` kernel module
is not installed. NIXL (the GPU-to-GPU RDMA transfer layer used by SGLang) requires this module to
register GPU memory regions for RDMA transfer via InfiniBand.

## Symptoms

- Requests return `completion_tokens: 0` with `reasoning_content: null`
- Requests take exactly 300 seconds (the default `SGLANG_DISAGGREGATION_WAITING_TIMEOUT`) then return empty
- Decode leader logs show:
  ```
  RdmaTransport: Failed to register memory: addr 0x7f... length 156176640
  ```
  ```
  Decode transfer failed for request ... timed out after 300.0s in KVPoll.WaitingForInput
  ```
- Prefill leader logs show:
  ```
  Session 10.244.x.x:15292 failed.
  Prefill transfer failed ... Failed to send kv chunk of ... to 10.244.x.x:37233
  ```

## Environment

- **Cluster**: Azure AKS with 4x ND96isr_H100_v5 nodes (8x H100 80GB per node)
- **Kernel**: 5.15.0-1102-azure
- **NVIDIA Driver**: Installed (nvidia, nvidia_uvm, nvidia_modeset loaded)
- **InfiniBand**: 8x mlx5 400Gb/s NDR ports per node, all ACTIVE
- **RDMA Device Plugin**: `rdma-shared-dp-ds` DaemonSet installed, `rdma/hca_shared_devices_a` available
- **Dynamo Platform**: v1.0.1 with Grove + KAI-Scheduler
- **Image**: `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.0.1`
- **Recipe**: Adapted from `recipes/deepseek-r1/sglang/disagg-16gpu/deploy.yaml`

## Root Cause

The `nvidia_peermem` (or `nv_peer_mem`) kernel module is not present on the Azure ND H100 v5 nodes.
This module is required for GPU Direct RDMA (GDR) — it enables InfiniBand adapters to directly
read/write GPU memory, which NIXL depends on for KV cache transfer between disaggregated prefill
and decode workers.

Verified missing:
```
# On GPU node:
modinfo nvidia_peermem
# modinfo: ERROR: Module nvidia_peermem not found.

find /lib/modules/$(uname -r) -name "*peer*"
# (no results)
```

InfiniBand itself works fine — all ports are ACTIVE at 400Gb/s NDR. The RDMA shared device plugin
provides `/dev/infiniband/uverbs*` to pods. IPC_LOCK is set (memlock unlimited). The only missing
piece is the `nvidia_peermem` module for GPU memory registration.

## Why Qwen 235B TRT-LLM Disaggregated Works

The Qwen 235B deployment uses TRT-LLM with `cache_transceiver_config: {"backend": "DEFAULT"}` for
disaggregated KV transfer. TRT-LLM's cache transceiver uses UCX which can fall back to TCP or
cuda_copy transport when GPU Direct RDMA is not available. SGLang's NIXL does not have this fallback —
it requires GPU Direct RDMA via `nvidia_peermem`.

## How to Reproduce

1. Deploy Dynamo platform v1.0.1 on Azure AKS with ND H100 v5 GPU nodes:
   ```bash
   helm install dynamo-platform nvidia-dynamo/dynamo-platform \
     --version 1.0.1 -n default \
     --set global.grove.install=true \
     --set global.kai-scheduler.install=true
   ```

2. Apply the DeepSeek-R1 SGLang disaggregated recipe (see `deepseek-r1-sglang-disagg-lustre.yaml`
   in this directory).

3. Wait for all 5 pods to reach 1/1 Running (DGD state: successful).

4. Send a request:
   ```bash
   curl http://<frontend>:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model":"deepseek-ai/DeepSeek-R1","messages":[{"role":"user","content":"Hello"}],"max_tokens":50}'
   ```

5. Request will hang for 300s and return `completion_tokens: 0`.

6. Check decode leader logs for `RdmaTransport: Failed to register memory` errors.

## Potential Fixes

1. **Install NVIDIA GPU Operator with RDMA support**: Deploy the GPU Operator with
   `driver.rdma.enabled=true` and `driver.rdma.useHostMofed=true`, which builds and loads
   `nvidia_peermem` automatically.

2. **Build nvidia_peermem via DKMS**: On each GPU node, build the module from the
   [nvidia-peermem source](https://github.com/Mellanox/nv_peer_memory) against the installed
   NVIDIA driver.

3. **Azure support**: Request that `nvidia_peermem` be pre-installed on ND H100 v5 node images,
   or use a VM image that includes it.

4. **Workaround — use TRT-LLM backend**: TRT-LLM's cache transceiver can fall back to non-GDR
   transport for KV transfer. See the working Qwen 235B recipe as reference.

## Other Issues Encountered During Deployment

These were resolved but worth noting:

- **CUDA graph OOM on decode**: H100 80GB has only ~6GB free after loading DeepSeek-R1 weights
  (53GB/GPU). CUDA graph capture OOMs. Fixed with `--disable-cuda-graph` on decode workers.
- **mem-fraction-static tuning**: Had to increase from 0.65 to 0.80 — SGLang needs a minimum
  KV cache allocation and was erroring at lower values.
- **Missing MoE kernel configs**: SGLang warns about missing Triton MoE configs for H100 FP8.
  Performance impact only, not a correctness issue.
