#!/usr/bin/env bash
# Read-only snapshot of the h100 cluster for beat 02 ("Step 1: Prepare the AKS cluster").
#
# Every query here is a get/list/show: no apply, delete, exec, install, or scale. Each output goes to
# its own file with a header: the command as it appears on screen ("# screen:"), the command that was
# actually run ("# ran:", with --kube-context and the subscription), the UTC time, and "# ---", then
# the raw output. fill/02_cluster.py reads only the part after "# ---".
#
# The subscription ID is read from a node label at run time and never written to a file: the "# ran:"
# lines say $SUBSCRIPTION. Outputs that would carry IDs (resource IDs, subnets) are filtered with
# --query to the fields the beat uses.
#
# Copied from the ReTool team demo, which ran it on 2026-11-19 02:45Z; the snapshots here are that run's.
# Integration pass (2026-11-19): trimmed to the files this demo's fill scripts read (00, 01, 02, 09). The
# StorageClass, CSIDriver, scheduler-evidence (the ReTool run's Pods), and nfd/kai/grove/network-operator
# values snapshots were deleted, and their queries removed.
#
# Usage: bash snapshots/capture.sh   (from demo/team; needs kubectl, helm, az logged in)
set -euo pipefail
cd "$(dirname "$0")"

CTX=h100
RG=h100                 # $RESOURCE_GROUP on screen
CLUSTER=h100            # $CLUSTER on screen
POOL=ndh100pool
AMLFS=h100-lustre       # $AMLFS_NAME on screen
NODE=aks-ndh100pool-82423760-vmss000003
SUB=$(kubectl --context "$CTX" get node "$NODE" \
  -o jsonpath='{.metadata.labels.kubernetes\.azure\.com/network-subscription}')
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# snap FILE SCREEN_CMD RAN_CMD: run RAN_CMD with bash, write the header and its output to FILE.
snap() {
  local file="$1" screen="$2" ran="$3" out
  out=$(SUB="$SUB" bash -c "$ran" 2>&1)
  {
    printf '# screen: %s\n' "$screen"
    printf '# ran:    %s\n' "${ran//$SUB/\$SUBSCRIPTION}"
    printf '# date:   %s (UTC), read-only\n' "$NOW"
    printf '# ---\n'
    printf '%s\n' "$out"
  } > "$file"
  echo "wrote $file ($(printf '%s\n' "$out" | wc -l | tr -d ' ') lines)"
}

# --- shown on screen (the output is printed as is, tabs expanded) -------------------------------
snap helm-list-filtered.txt \
  "helm list -A --filter 'gpu-operator|nfd|kuberay|kai|grove|dynamo' | cut -f1,2,5,6" \
  "helm --kube-context $CTX list -A --filter 'gpu-operator|nfd|kuberay|kai|grove|dynamo' | cut -f1,2,5,6"

snap nodes-gpu.txt \
  "kubectl get nodes -l nvidia.com/gpu.present=true -L node.kubernetes.io/instance-type,nvidia.com/gpu.count" \
  "kubectl --context $CTX get nodes -l nvidia.com/gpu.present=true -L node.kubernetes.io/instance-type,nvidia.com/gpu.count"

snap amlfs-table.txt \
  "az resource show -g \$RESOURCE_GROUP -n \$AMLFS_NAME --resource-type Microsoft.StorageCache/amlFilesystems --query \"{SKU:sku.name, TiB:properties.storageCapacityTiB, MBps:properties.throughputProvisionedMBps}\" -o table" \
  "az resource show --subscription \"\$SUB\" -g $RG -n $AMLFS --resource-type Microsoft.StorageCache/amlFilesystems --query \"{SKU:sku.name, TiB:properties.storageCapacityTiB, MBps:properties.throughputProvisionedMBps}\" -o table"

# --- context only (not shown): every node, both pools -----------------------------------------------
snap nodes.txt \
  "kubectl get nodes -L node.kubernetes.io/instance-type,nvidia.com/gpu.count" \
  "kubectl --context $CTX get nodes -L node.kubernetes.io/instance-type,nvidia.com/gpu.count"

snap lustre-csi-rollout.txt \
  "kubectl rollout status -n kube-system daemonset/csi-azurelustre-node-noble" \
  "kubectl --context $CTX rollout status -n kube-system daemonset/csi-azurelustre-node-noble --timeout=10s"

# --- evidence for the install commands (not printed on screen) -----------------------------------
snap helm-list-all.txt "helm list -A" "helm --kube-context $CTX list -A"

for r in "gpu-operator gpu-operator" "kuberay-operator kuberay" "dynamo-platform dynamo-system"; do
  set -- $r
  snap "helm-values-$1.txt" "helm get values $1 -n $2" "helm --kube-context $CTX get values $1 -n $2"
done

snap aks-cluster.json "az aks show (filtered)" \
  "az aks show --subscription \"\$SUB\" -g $RG -n $CLUSTER --query \"{kubernetesVersion:currentKubernetesVersion, location:location, networkPlugin:networkProfile.networkPlugin, networkPluginMode:networkProfile.networkPluginMode, networkDataplane:networkProfile.networkDataplane, nodeResourceGroup:nodeResourceGroup}\" -o json"

snap aks-nodepools.json "az aks nodepool show (filtered), both pools" \
  "for p in $POOL nodepool1; do az aks nodepool show --subscription \"\$SUB\" -g $RG --cluster-name $CLUSTER -n \$p --query \"{name:name, mode:mode, vmSize:vmSize, count:count, osSku:osSku, gpuProfile:gpuProfile, enableAutoScaling:enableAutoScaling, availabilityZones:availabilityZones, nodeImageVersion:nodeImageVersion}\" -o json; done"

snap amlfs.json "az resource show (Microsoft.StorageCache/amlFilesystems, filtered)" \
  "az resource show --subscription \"\$SUB\" -g $RG -n $AMLFS --resource-type Microsoft.StorageCache/amlFilesystems --query \"{name:name, location:location, sku:sku.name, zones:zones, storageCapacityTiB:properties.storageCapacityTiB, throughputProvisionedMBps:properties.throughputProvisionedMBps, lustreVersion:properties.clientInfo.lustreVersion, health:properties.health.state, maintenanceWindow:properties.maintenanceWindow, filesystemSubnetName:properties.filesystemSubnet}\" -o json | sed -E 's#\"/subscriptions/.*/virtualNetworks/([^/]+)/subnets/([^\"]+)\"#\"vnet \\1, subnet \\2\"#'"

snap amlfs-subnet.json "az network vnet subnet show (the AMLFS subnet, filtered)" \
  "az network vnet subnet show --subscription \"\$SUB\" -g MC_${RG}_${CLUSTER}_southafricanorth --vnet-name \$(az network vnet list --subscription \"\$SUB\" -g MC_${RG}_${CLUSTER}_southafricanorth --query '[0].name' -o tsv) -n amlfs-subnet --query \"{name:name, addressPrefix:addressPrefix}\" -o json"

snap lustre-csi-install.txt "how the Lustre CSI driver was installed (images, kubectl apply annotation)" \
  "for o in daemonset/csi-azurelustre-node-noble daemonset/csi-azurelustre-node-jammy deployment/csi-azurelustre-controller; do echo \"== \$o\"; kubectl --context $CTX -n kube-system get \$o -o jsonpath='created={.metadata.creationTimestamp} helm-managed-by={.metadata.labels.app\\.kubernetes\\.io/managed-by} last-applied={.metadata.annotations.kubectl\\.kubernetes\\.io/last-applied-configuration}' | sed -E 's/last-applied=\\{.*/last-applied=(present: kubectl apply)/'; echo; kubectl --context $CTX -n kube-system get \$o -o jsonpath='{range .spec.template.spec.containers[*]}  {.name} {.image}{\"\\n\"}{end}'; kubectl --context $CTX -n kube-system get \$o --no-headers | awk '{print \"  ready: \" \$2 \" \" \$3 \" \" \$4}'; done"

snap node-gpu-labels.txt "GPU Operator labels on the run's node (filtered)" \
  "kubectl --context $CTX get node $NODE -o go-template='{{range \$k, \$v := .metadata.labels}}{{\$k}}={{\$v}}{{\"\\n\"}}{{end}}' | grep -E '^(nvidia\\.com/(gpu\\.(product|count|memory|family|present)|cuda\\.(driver-version\\.full|runtime-version\\.full)|mig\\.config)|kubernetes\\.azure\\.com/(accelerator|os-sku-effective|node-image-version)|node\\.kubernetes\\.io/instance-type)='"
