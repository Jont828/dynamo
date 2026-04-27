---
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
title: Installation Guide
---

Everything you need to install before deploying models with Dynamo on Kubernetes — from cluster prerequisites to optional components.

## Cluster Prerequisites

### Kubernetes Cluster

You need a Kubernetes cluster (v1.24+) with GPU-capable nodes. If you don't have one yet, see the cloud provider guides for setup instructions:

- [Amazon EKS](cloud-providers/eks/eks.md)
- [Azure AKS](cloud-providers/aks/aks.md)
- [Google GKE](cloud-providers/gke/gke.md)

For local development, see [Minikube Setup](deployment/minikube.md).

### GPU Operator

The [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html) automates the deployment of all NVIDIA software components required to provision GPUs in Kubernetes — drivers, container toolkit, device plugin, and monitoring tools. Install it before proceeding.

<Tabs>
<Tab title="AKS">

When creating your GPU node pool, **skip the GPU driver installation** — the GPU Operator handles this.

Follow the [Installing the NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html) guide. Verify the pods are running:

```bash
kubectl get pods -n gpu-operator
# Expected: gpu-operator, nvidia-driver-daemonset, nvidia-device-plugin-daemonset, etc. all Running
```

</Tab>
<Tab title="EKS">

EKS Auto Mode provisions GPU nodes automatically. Install the GPU Operator following the [official guide](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). Verify:

```bash
kubectl get pods -n gpu-operator
```

</Tab>
<Tab title="GKE">

GKE can install GPU drivers automatically when creating the node pool with `--accelerator gpu-driver-version=latest`. If using this option, configure the GPU Operator to skip driver installation:

```bash
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator --create-namespace \
  --set driver.enabled=false
```

> [!IMPORTANT]
> GKE requires setting `LD_LIBRARY_PATH=/usr/local/nvidia/lib64` and running `/sbin/ldconfig` in worker container init. See the [GKE guide](cloud-providers/gke/gke.md) for details.

</Tab>
</Tabs>

### Required Tools

| Tool | Minimum Version | Installation |
|------|-----------------|--------------|
| **kubectl** | v1.24+ | [Install kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl) |
| **Helm** | v3.0+ | [Install Helm](https://helm.sh/docs/intro/install/) |

Verify your tools:

```bash
kubectl version --client  # Should show v1.24+
helm version              # Should show v3.0+
```

### Pre-Deployment Checks

Run the pre-deployment check script to verify your cluster meets all requirements:

```bash
./deploy/pre-deployment/pre-deployment-check.sh
```

This validates kubectl connectivity, default StorageClass configuration, and GPU node availability. See [Pre-Deployment Checks](https://github.com/ai-dynamo/dynamo/tree/main/deploy/pre-deployment/README.md) for details.

## Install Dynamo Platform

### Set Environment

```bash
export NAMESPACE=dynamo-system
export RELEASE_VERSION=1.x.x  # match a version from https://github.com/ai-dynamo/dynamo/releases
```

### Install via Helm

```bash
helm install dynamo-platform \
  oci://helm.ngc.nvidia.com/nvidia/ai-dynamo/charts/dynamo-platform \
  --version $RELEASE_VERSION \
  --namespace $NAMESPACE \
  --create-namespace
```

All helm install commands can be customized by passing your own values file or setting individual values:

```bash
helm install ... -f your-values.yaml
helm install ... --set "your-key=your-value"
```

> [!TIP]
> **Shared/Multi-Tenant Clusters**: If a cluster-wide Dynamo operator is already running, do **not** install another one. Check with:
> ```bash
> kubectl get clusterrolebinding -o json | \
>   jq -r '.items[] | select(.metadata.name | contains("dynamo-operator-manager")) |
>   "Cluster-wide operator found in namespace: \(.subjects[0].namespace)"'
> ```

> [!WARNING]
> **Namespace-restricted mode** (`namespaceRestriction.enabled=true`) is deprecated and will be removed in a future release. Use the default cluster-wide mode for all new deployments.

### Build from Source (Optional)

If you need to contribute to Dynamo or use the latest unreleased features from the main branch:

```bash
# 1. Set registry environment
export DOCKER_SERVER=nvcr.io/nvidia/ai-dynamo/  # or your registry
export DOCKER_USERNAME='$oauthtoken'
export DOCKER_PASSWORD=<YOUR_NGC_CLI_API_KEY>
export IMAGE_TAG=$RELEASE_VERSION

# 2. Build and push operator image
cd deploy/operator
docker build -t $DOCKER_SERVER/kubernetes-operator:$IMAGE_TAG . && docker push $DOCKER_SERVER/kubernetes-operator:$IMAGE_TAG
cd -

# 3. Create namespace and image pull secret (only if using a private registry)
kubectl create namespace $NAMESPACE
kubectl create secret docker-registry docker-imagepullsecret \
  --docker-server=$DOCKER_SERVER \
  --docker-username=$DOCKER_USERNAME \
  --docker-password=$DOCKER_PASSWORD \
  --namespace=$NAMESPACE

# 4. Install from local chart
cd deploy/helm/charts
helm dep build ./platform/
helm install dynamo-platform ./platform/ \
  --namespace "$NAMESPACE" \
  --set "dynamo-operator.controllerManager.manager.image.repository=$DOCKER_SERVER/kubernetes-operator" \
  --set "dynamo-operator.controllerManager.manager.image.tag=$IMAGE_TAG" \
  --set "dynamo-operator.imagePullSecrets[0].name=docker-imagepullsecret"
```

### Verify Installation

```bash
# Check CRDs
kubectl get crd | grep dynamo
# Expected: dynamographdeployments, dynamocomponentdeployments, dynamographdeploymentrequests, etc.

# Check operator and platform pods
kubectl get pods -n $NAMESPACE
# Expected: dynamo-operator-*, etcd-*, nats-* pods all Running
```

## Optional Components

The sections below describe components that are **not required for a basic deployment** but become important for specific use cases. Each section explains when you need it so you can decide what to install upfront.

### Grove and KAI Scheduler

**When do I need this?** Grove is required for **multinode deployments** and **disaggregated inference** (DGDR with prefill/decode separation across nodes). KAI Scheduler provides GPU-aware scheduling and is required when using Grove.

Grove is a Kubernetes API for orchestrating AI workloads — it handles gang scheduling, coordinated scaling, and topology-aware placement of disaggregated inference components. See [Grove](grove.md) for details.

**For production**, install Grove and KAI Scheduler **separately** from the dynamo-platform chart for independent lifecycle management:

| dynamo-platform | kai-scheduler | Grove |
|-----------------|---------------|-------|
| 1.0.x           | >= v0.13.0    | >= v0.1.0-alpha.6 |

After installing them separately, enable Dynamo integration:

```bash
helm install dynamo-platform ... \
  --set "global.kai-scheduler.enabled=true" \
  --set "global.grove.enabled=true"
```

**For development/testing**, install as bundled subcharts:

```bash
helm install dynamo-platform ... \
  --set "global.grove.install=true" \
  --set "global.kai-scheduler.install=true"
```

> [!NOTE]
> `global.grove.install` / `global.kai-scheduler.install` deploy the bundled subcharts and automatically enable integration. `global.grove.enabled` / `global.kai-scheduler.enabled` are for externally-managed installations.

**Alternative: LeaderWorkerSet (LWS) + Volcano**

If not using Grove, you can use LWS for multinode deployments. Volcano is required for gang scheduling with LWS:
- [LWS Installation](https://github.com/kubernetes-sigs/lws#installation)
- [Volcano Installation](https://volcano.sh/en/docs/installation/)

See the [Multinode Deployment Guide](./deployment/multinode-deployment.md) for details on orchestrator selection.

### RDMA / InfiniBand / Network Operator

**When do I need this?** RDMA is required for **production disaggregated deployments** where prefill and decode workers transfer KV cache between pods. Without RDMA, KV transfer falls back to TCP with **200-500x performance degradation** (~98s TTFT with TCP vs ~200-500ms with RDMA).

RDMA setup is provider-specific:

<Tabs>
<Tab title="AKS (InfiniBand)">

Azure provides InfiniBand on HPC-capable VM sizes (e.g., ND-series). Install the NVIDIA Network Operator to expose RDMA devices to Kubernetes:

```bash
helm install network-operator nvidia/network-operator \
  --namespace network-operator --create-namespace
```

Verify RDMA devices are available:

```bash
kubectl get nodes -o jsonpath='{.items[*].status.allocatable.rdma/ib}'
```

</Tab>
<Tab title="EKS (EFA)">

AWS uses Elastic Fabric Adapter (EFA) for high-performance networking. EFA is available on p5, g6e, and g7e instance families.

- Install the [EFA device plugin](https://github.com/aws/eks-charts/tree/master/stable/aws-efa-k8s-device-plugin)
- Use EFA-enabled container images (e.g., `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.0.1-efa-amd64`)
- Install GDRCopy for GPU Direct RDMA (GPU Operator v26.x includes this)

Verify EFA resources:

```bash
kubectl get nodes -o jsonpath='{.items[*].status.allocatable.vpc\.amazonaws\.com/efa}'
```

See the [Disaggregated Communication Guide](disagg-communication-guide.md#aws-efa-configuration) for complete EFA setup instructions.

</Tab>
<Tab title="GKE">

GKE supports GPUDirect-TCPXO and Multi-NIC networking for high-bandwidth GPU communication. Consult the [GKE GPU networking documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/gpu-bandwidth-gpudirect-tcpx) for setup.

</Tab>
</Tabs>

For a detailed guide on transport options, UCX configuration, and performance expectations, see the [Disaggregated Communication Guide](disagg-communication-guide.md).

### Prometheus / Observability

**When do I need this?** Install Prometheus if you want to monitor inference metrics (TTFT, ITL, throughput, GPU utilization), set up autoscaling based on metrics, or use the Dynamo Planner for SLA-aware scaling.

Install the kube-prometheus-stack and configure Dynamo integration:

```bash
helm install dynamo-platform ... \
  --set "dynamo-operator.dynamo.metrics.prometheusEndpoint=http://prometheus-kube-prometheus-prometheus.monitoring:9090"
```

The operator automatically creates PodMonitors for Dynamo components. See [Metrics](observability/metrics.md) for dashboard setup and available metrics.

For logging, see [Logging](observability/logging.md) (Grafana Loki + Alloy stack).

### Model Caching / Shared Storage

**When do I need this?** Set up shared storage **before deploying large models (>70B parameters) or deployments with many replicas**. Without it:
- Each pod downloads the full model independently from HuggingFace
- Large models take hours to download per pod
- Many replicas will hit HuggingFace rate limits

The recommended approach is a shared `ReadWriteMany` PVC that all pods mount:

<Tabs>
<Tab title="EKS (EFS)">

Amazon EFS provides elastic NFS storage with ReadWriteMany support. See the [EFS setup guide](cloud-providers/eks/efs.md) for step-by-step instructions to create:

- `model-cache` PVC — shared model weights
- `compilation-cache` PVC — vLLM CUDA graph cache
- `perf-cache` PVC — performance profiling cache

</Tab>
<Tab title="AKS (Azure Files / Lustre)">

| Storage Option | Use Case | Performance |
|----------------|----------|-------------|
| Azure Managed Lustre | Model cache (recommended for large models) | High throughput |
| Azure Files | Model cache (simpler setup) | Moderate |
| Local CSI (ephemeral) | Compilation cache, perf cache | Fastest, node-local |

See the [AKS guide](cloud-providers/aks/aks.md) for storage setup details.

</Tab>
<Tab title="GKE (Filestore / GCS)">

GKE supports Cloud Filestore for NFS-based ReadWriteMany storage. Create a Filestore instance and StorageClass, then create PVCs for model and compilation caches.

</Tab>
</Tabs>

Once you have a shared PVC, mount it in your deployment. See [Model Caching](model-caching.md) for the full walkthrough including download Job and DGD mount configuration.

For large clusters with frequent model updates, consider [Model Express](model-caching.md#option-2-model-express-p2p-distribution) for P2P model distribution.

## Next Steps

Your cluster is now ready. Follow the **[Quickstart: Deploy Your First Model](dgdr.md)** to deploy a model using DGDR.

## Troubleshooting

**"VALIDATION ERROR: Cannot install cluster-wide Dynamo operator"**

```
VALIDATION ERROR: Cannot install cluster-wide Dynamo operator.
Found existing namespace-restricted Dynamo operators in namespaces: ...
```

Cause: Attempting cluster-wide install on a shared cluster with existing namespace-restricted operators.

Solution: Migrate the existing namespace-restricted operators to cluster-wide mode. Namespace-restricted mode is deprecated.

**CRDs already exist**

Cause: Installing CRDs on a cluster where they're already present (common on shared clusters).

Solution: CRDs are installed automatically by the Helm chart. If you encounter conflicts, check existing CRDs with `kubectl get crd | grep dynamo`.

**Pods not starting?**
```bash
kubectl describe pod <pod-name> -n $NAMESPACE
kubectl logs <pod-name> -n $NAMESPACE
```

**Bitnami etcd "unrecognized" image?**

```bash
ERROR: Original containers have been substituted for unrecognized ones.
```

Add to the helm install command:
```bash
--set "etcd.image.repository=bitnamilegacy/etcd" --set "etcd.global.security.allowInsecureImages=true"
```

**Clean uninstall?**

```bash
# Uninstall the platform
helm uninstall dynamo-platform --namespace $NAMESPACE

# List Dynamo CRDs
kubectl get crd | grep "dynamo.*nvidia.com"

# Delete each CRD
kubectl delete crd <crd-name>
```

## Advanced Options

- [Helm Chart Configuration](https://github.com/ai-dynamo/dynamo/tree/main/deploy/helm/charts/platform/README.md)
- [Create Custom Deployments](./deployment/create-deployment.md)
- [Dynamo Operator Details](./dynamo-operator.md)
- [Model Express Server](https://github.com/ai-dynamo/modelexpress)
