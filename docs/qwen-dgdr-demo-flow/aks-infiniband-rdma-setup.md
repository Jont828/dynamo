# AKS InfiniBand + RDMA Setup for ND H100 v5 Nodes

**Date**: 2026-04-16
**Cluster**: AKS `nd-h100` with 4x `Standard_ND96isr_H100_v5` nodes (32 GPUs)
**Region**: South Africa North
**Context**: Fixing NIXL UCX memlock failure (Failure #3 in `dgdr-vs-recipe-qwen235-findings.md`)

## Problem Summary

Disaggregated prefill/decode deployments on AKS use NIXL to transfer KV cache
between prefill and decode workers. NIXL uses UCX, which defaults to InfiniBand
verbs (`rc_verbs`). IB verbs require `mlock()` to pin memory for completion
queues and DMA regions. On the standard AKS Ubuntu image, `mlock()` fails even
as root, forcing a fallback to `UCX_TLS=tcp,cuda_copy,cuda_ipc` which leaves
the 400 Gb/s RDMA fabric unused.

## Root Cause Analysis

Three separate issues blocked InfiniBand RDMA from working in pods:

### Issue 1: `ib_umad` Kernel Module Not Loaded

The standard AKS Ubuntu image (`AKS-Ubuntu/2204gen2containerd`) ships with inbox
IB kernel drivers (`mlx5_core`, `mlx5_ib`, `ib_uverbs`, `ib_core`) loaded, but
**not** `ib_umad`. Without `ib_umad`, the `/sys/class/infiniband_mad/` sysfs
entries don't exist, which the Mellanox RDMA shared device plugin requires to
register RDMA resources with kubelet.

The `ib_umad` module is available in the kernel but simply not loaded by default.

### Issue 2: `mlock()` Fails Due to `RLIMIT_MEMLOCK` = 64KB

The real `mlock()` blocker was the memlock rlimit inheritance chain:

```
systemd (PID 1)
  -> kubelet (LimitMEMLOCK=64KB)    <-- the problem
    -> containerd-shim               <-- inherits from kubelet
      -> container process            <-- inherits from shim
```

Key findings:
- `ulimit -l` inside the container showed `64` (64KB)
- Adding `capabilities.add: [IPC_LOCK]` to the pod spec puts `IPC_LOCK` into
  `CapBnd` and `CapEff` (when running as root), but this **does not override
  the rlimit** -- `IPC_LOCK` only bypasses the rlimit check, it doesn't raise it
- On kernel 5.15 with cgroups v2, `mlock()` returns `ENOMEM` even with
  `IPC_LOCK` when the rlimit is exhausted at 64KB

The fix required setting `LimitMEMLOCK=infinity` on **both** containerd AND
kubelet via systemd drop-in overrides. Setting it on containerd alone was
insufficient because the containerd-shim process is spawned by kubelet (via the
CRI), not by containerd directly, and inherits kubelet's rlimits.

### Issue 3: `/dev/infiniband/*` Not Exposed to Pods

The host has `/dev/infiniband/{rdma_cm, uverbs0-8}` (and `umad0-7` after
loading `ib_umad`), but these device files are not mounted into pod containers
by default. A Kubernetes RDMA device plugin is required to expose them.

## What Didn't Work

### InfiniBandDriverLinux VMSS Extension

```
az vmss extension set \
  --resource-group MC_nd-h100_nd-h100_southafricanorth \
  --vmss-name aks-ndh100pool-15137603-vmss \
  --name InfiniBandDriverLinux \
  --publisher Microsoft.HpcCompute \
  --version 1.2
```

**Result**: The extension ran but exited with:
```
VM size standard_nd96isr_h100_v5 not supported yet
```

The InfiniBandDriverLinux extension does not support the ND96isr_H100_v5 VM
size as of April 2026 (version 1.4.0.0). It checks the VM size against a
hardcoded support list fetched from
`https://go.microsoft.com/fwlink/?linkid=2183116` and bails out.

### Ubuntu-HPC VM Image

The `microsoft-dsvm:ubuntu-hpc:2204` marketplace image has everything
pre-configured (OFED, `nvidia_peermem`, memlock=unlimited, IPoIB), but AKS does
not offer `UbuntuHPC` as a native `--os-sku` option. The supported values are
`Ubuntu`, `Ubuntu2204`, `Ubuntu2404`, `AzureLinux`, `AzureLinux3`.

Using it would require a custom VMSS image or shared image gallery reference,
which means replacing the node pool entirely.

### Setting Only containerd `LimitMEMLOCK`

Initially we only set `LimitMEMLOCK=infinity` on containerd via a systemd
drop-in. This made containerd's own process limits unlimited, but containers
still had 64KB because containerd-shim inherits from **kubelet**, not containerd.

Verified via:
```
containerd (PID xxx): Max locked memory = unlimited    <-- fixed
kubelet    (PID yyy): Max locked memory = 65536        <-- still broken
shim       (PID zzz): Max locked memory = 65536        <-- inherits kubelet
```

## What Worked: Two DaemonSets

### DaemonSet 1: `ib-node-config`

A privileged init container that runs on each H100 node and:

1. Loads `ib_umad` kernel module (`nsenter -t 1 -- modprobe ib_umad`)
2. Persists it across reboots (`echo ib_umad > /etc/modules-load.d/ib-umad.conf`)
3. Sets `LimitMEMLOCK=infinity` on containerd AND kubelet via systemd drop-ins:
   - `/etc/systemd/system/containerd.service.d/memlock.conf`
   - `/etc/systemd/system/kubelet.service.d/memlock.conf`
4. Restarts both services (`systemctl daemon-reload && systemctl restart containerd kubelet`)

### DaemonSet 2: `rdma-shared-dp-ds` (Mellanox RDMA Shared Device Plugin)

The [k8s-rdma-shared-dev-plugin](https://github.com/Mellanox/k8s-rdma-shared-dev-plugin)
exposes RDMA device files (`/dev/infiniband/*`) into pods that request the
`rdma/hca_shared_devices_a` resource.

ConfigMap configuration:
```json
{
    "periodicUpdateInterval": 300,
    "configList": [{
         "resourceName": "hca_shared_devices_a",
         "rdmaHcaMax": 1000,
         "selectors": {
           "vendors": ["15b3"],
           "drivers": ["mlx5_core"]
         }
       }
    ]
}
```

**Important**: The selector must use `vendors` + `drivers`, **not** `linkTypes`
or `ifNames`. The IB HCAs (mlx5_0-7) have no network interface names (no IPoIB
configured) and the `linkTypes` selector matches on the net device link type,
not the IB port link layer.

## Verification Results

### Before (standard AKS Ubuntu image)

| Test | Result |
|------|--------|
| `ulimit -l` | `64` (64KB) |
| `mlock(4KB)` | FAIL (errno=12 ENOMEM) |
| `/dev/infiniband/` in pod | NOT FOUND |
| `ibv_devinfo` | "No IB devices found" |
| UCX transports | tcp, sysv, posix, cma only |
| `ibv_rc_pingpong` | Failed |

### After (with both DaemonSets deployed)

| Test | Result |
|------|--------|
| `ulimit -l` | `unlimited` |
| `mlock(4KB)` | OK |
| `mlock(1MB)` | OK |
| `mlock(128MB)` | OK |
| `/dev/infiniband/` in pod | 26 device files (uverbs0-8, umad0-7, issm0-7, rdma_cm) |
| `ibv_devinfo` | 8x mlx5 HCAs, all PORT_ACTIVE, 400 Gb/s NDR |
| UCX transports | rc_verbs, rc_mlx5, dc_mlx5, ud_verbs, ud_mlx5, cuda_copy, cuda_ipc, tcp, ... |
| `ibv_rc_pingpong` loopback | 14.2 Gbit/s, 4.6 us latency |
| RDMA resources on nodes | 1000 per node (all 4 nodes) |

## Other Things That Were Already Correct

- **`singlePlacementGroup: true`** on the VMSS -- required for IB communication
  within the scale set. Already set correctly by AKS.
- **IB hardware and kernel drivers** -- all 8 IB ports are ACTIVE at 400 Gb/s
  NDR with mlx5_core/mlx5_ib/ib_uverbs loaded. No driver installation needed.
- **No `az` CLI configuration** required for IB itself -- the ND H100 v5 series
  auto-configures IB connections between VMs in the same VMSS.

## Impact on NIXL / Disaggregated Serving

With these fixes, the `UCX_TLS=tcp,cuda_copy,cuda_ipc` workaround from
`dgdr-vs-recipe-qwen235-findings.md` (Failure #3) should no longer be needed.
UCX can now use native IB verbs (`rc_verbs`/`rc_mlx5`) for NIXL KV cache
transfers between prefill and decode workers over the 400 Gb/s RDMA fabric.

Worker pods need these additions in their pod spec:
```yaml
resources:
  limits:
    rdma/hca_shared_devices_a: 1
    nvidia.com/gpu: "4"  # (or however many GPUs per worker)
```

`IPC_LOCK` capability is **not needed** when `RLIMIT_MEMLOCK=unlimited` is set
at the node level (which the `ib-node-config` DaemonSet configures). `IPC_LOCK`
only bypasses the rlimit check; with unlimited rlimit, the check passes on its
own. Verified working as `uid=1000` (non-root) with zero effective capabilities.

The `UCX_TLS` / `UCX_NET_DEVICES` env vars can be removed to let UCX
auto-detect IB.

## Remaining Gap: GPU Direct RDMA

The `nvidia_peermem` kernel module (required for zero-copy RDMA directly from
GPU VRAM) is **not** available on the standard AKS Ubuntu image. It is not
loaded and not present as an installable module:

```
modinfo nvidia_peermem -> ERROR: Module nvidia_peermem not found.
```

This means NIXL's RDMA transfers go through host memory (GPU -> host -> RDMA ->
host -> GPU) rather than GPU Direct RDMA (GPU -> RDMA -> GPU). This is still
faster than TCP but not as fast as possible. The Ubuntu-HPC image includes
`nvidia_peermem` pre-installed. Addressing this is a future optimization.

## Files Changed

- `h100-ndsh-cluster.sh` -- Added `az aks get-credentials`, the `ib-node-config`
  DaemonSet, and the `rdma-shared-dp-ds` DaemonSet + ConfigMap as post-cluster-creation
  setup steps.
