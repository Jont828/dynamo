#!/usr/bin/env bash
# Read-only snapshot for beats 03 (storage, setup Job) and 04 (serving): facts the kit's run files don't
# record. Nothing here is shown on screen; the beats' fill scripts and plans/B_storage_serve.md cite it.
#
#   03_storage-objects.txt  when the namespace, PV, and PVC were created, and that they're Bound
#                           (the apply in beat 03 created all three: "created", not "unchanged")
#   03_setup-job.txt        the setup Job's and its Pod's timestamps and exit code (runs/setup/start.txt
#                           is the time of the apply, 2 s before the Job existed; end.txt is its completion)
#   03_lustre-class.txt     the cluster's only Lustre StorageClass and the file system's MGS address
#                           (why storage.yaml uses a static PV)
#   04_engine-services.txt  the two engine Services: when they were created, their selector and port
#
# Every query is a get/show: no apply, delete, exec, install, or scale. Same header as capture.sh: the
# command as it would appear on screen ("# screen:"), the command actually run ("# ran:"), the UTC time,
# "# ---", then the raw output. The subscription ID is read from a node label and never written out.
#
# Usage: bash snapshots/03-04_capture.sh   (from demo/team; needs kubectl and az logged in)
set -euo pipefail
cd "$(dirname "$0")"

CTX=h100
RG=h100                 # $RESOURCE_GROUP on screen
AMLFS=h100-lustre       # $AMLFS_NAME on screen
NS=slime-kv
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

snap 03_storage-objects.txt "(not shown) the namespace, PV, and PVC from manifests/storage.yaml" \
  "kubectl --context $CTX get ns $NS -o jsonpath='namespace/{.metadata.name} created={.metadata.creationTimestamp}{\"\\n\"}'; kubectl --context $CTX get pv pv-lustre-$NS -o jsonpath='persistentvolume/{.metadata.name} created={.metadata.creationTimestamp} phase={.status.phase} claim={.spec.claimRef.namespace}/{.spec.claimRef.name} reclaim={.spec.persistentVolumeReclaimPolicy} class={.spec.storageClassName} access={.spec.accessModes} capacity={.spec.capacity.storage}{\"\\n\"}'; kubectl --context $CTX -n $NS get pvc slime-data -o jsonpath='persistentvolumeclaim/{.metadata.name} created={.metadata.creationTimestamp} phase={.status.phase} volume={.spec.volumeName} capacity={.status.capacity.storage}{\"\\n\"}'"

snap 03_setup-job.txt "(not shown) the setup Job and its Pod: timestamps and exit code" \
  "kubectl --context $CTX -n $NS get job slime-setup -o jsonpath='job.batch/{.metadata.name} created={.metadata.creationTimestamp} start={.status.startTime} completion={.status.completionTime} succeeded={.status.succeeded}{\"\\n\"}'; kubectl --context $CTX -n $NS get pods -l job-name=slime-setup -o jsonpath='{range .items[*]}pod/{.metadata.name} created={.metadata.creationTimestamp} node={.spec.nodeName}{\"\\n\"}{range .status.conditions[*]}  {.type}={.lastTransitionTime}{\"\\n\"}{end}{range .status.containerStatuses[*]}  container {.name} image={.image} started={.state.terminated.startedAt} finished={.state.terminated.finishedAt} exit={.state.terminated.exitCode} reason={.state.terminated.reason}{\"\\n\"}{end}{end}'"

snap 03_lustre-class.txt "(not shown) the Lustre StorageClass's parameters and the file system's MGS address" \
  "kubectl --context $CTX get storageclass azurelustre-static -o jsonpath='storageclass/{.metadata.name} created={.metadata.creationTimestamp} provisioner={.provisioner} parameters={.parameters}{\"\\n\"}'; echo \"amlfs/$AMLFS \$(az resource show --subscription \"\$SUB\" -g $RG -n $AMLFS --resource-type Microsoft.StorageCache/amlFilesystems --query '[systemData.createdAt, properties.clientInfo.mgsAddress]' -o tsv | tr '\\t\\n' '  ' | awk '{print \"created=\" \$1 \" mgsAddress=\" \$2}')\"; echo \"amlFilesystems in the resource group: \$(az resource list --subscription \"\$SUB\" -g $RG --resource-type Microsoft.StorageCache/amlFilesystems --query '[].name' -o tsv | tr '\\n' ' ')\""

snap 04_engine-services.txt "(not shown) the engine Services from manifests/tutorial/engine-services.yaml" \
  "kubectl --context $CTX -n $NS get svc slime-sglang-engine-0 slime-sglang-engine-1 -o jsonpath='{range .items[*]}service/{.metadata.name} created={.metadata.creationTimestamp} type={.spec.type} selector={.spec.selector} ports={.spec.ports}{\"\\n\"}{end}'"
