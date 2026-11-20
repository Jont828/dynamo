# beats/02_cluster.sh: beat 02, Step 1: prepare the AKS cluster.
# First the install commands a plain AKS cluster needs, emulated: this cluster already runs them all,
# the narration says so, and no output is shown for them. Then the cluster's real state, from
# read-only snapshots taken on 2026-11-19.
# Data: build/02_cluster.data.sh, from fill/02_cluster.py (snapshots and the kit's manifests). Every
# version and flag value in the commands comes from there.

CLUSTER_TITLE="Step 1: Prepare the AKS Cluster"

# cluster_type LINE...: show_command_lines for an emulated command: typed 4 characters per tick
# (4× normal speed), with shorter pauses because it doesn't run, and no output.
cluster_type() {
    local line chunk=4 prompt_pause=0.2 cmd_pause=0.3
    cursor_show
    printf '%s ' "${PROMPT}❯${NC}"
    nap "$prompt_pause"
    hl_bash_reset
    for line in "$@"; do
        seg_reset; hl_bash "$line"
        type_segments "$chunk"
        printf '\n'
    done
    cursor_hide
    nap "$cmd_pause"
}

cluster_install() {
    beat_header "🧰" "$CLUSTER_TITLE"
    narrate "What a plain AKS cluster needs first, at this cluster's versions (all installed already, so nothing runs here):"
    narrate "GPU nodes: ${CLUSTER_POOL_COUNT} × ${CLUSTER_VM_SHORT}, ${CLUSTER_GPUS_PER_NODE} × ${CLUSTER_GPU_NAME} each, without AKS's GPU driver:"
    cluster_type "${CLUSTER_CMD_NODEPOOL[@]}"
    narrate "Node Feature Discovery labels hardware; the GPU Operator installs the driver (${CLUSTER_DRIVER}) and device plugin:"
    cluster_type "${CLUSTER_CMD_NFD[@]}"
    cluster_type "${CLUSTER_CMD_NVIDIA_REPO[@]}"
    cluster_type "${CLUSTER_CMD_GPU_OP[@]}"
    narrate "KubeRay, for Ray jobs like Slime's trainer:"
    cluster_type "${CLUSTER_CMD_KUBERAY_REPO[@]}"
    cluster_type "${CLUSTER_CMD_KUBERAY[@]}"
    narrate "Dynamo's platform, plus Grove and the KAI scheduler, which place a Dynamo deployment's Pods as one group:"
    cluster_type "${CLUSTER_CMD_KAI[@]}"
    cluster_type "${CLUSTER_CMD_GROVE[@]}"
    cluster_type "${CLUSTER_CMD_DYNAMO[@]}"
    narrate_wrap "Azure Managed Lustre, the shared file system for the run: ${CLUSTER_AMLFS_TIB} TiB, ${CLUSTER_AMLFS_SKU_SHORT}, ${CLUSTER_AMLFS_MBPS} MB/s, in its own ${CLUSTER_AMLFS_SUBNET_PREFIX} subnet. Then its CSI driver:"
    cluster_type "${CLUSTER_CMD_AMLFS[@]}"
    cluster_type "${CLUSTER_CMD_CSI[@]}"
    nap "$PAUSE_SHORT"
}

cluster_state() {
    clear_screen
    step_header_bars "🧰" "$CLUSTER_TITLE"
    narrate "The cluster's state on ${CLUSTER_SNAPSHOT_DATE}, from read-only queries:"
    show_command "${CLUSTER_CMD_SHOW_HELM[0]}"
    printf '%s\n' "${CLUSTER_OUT_HELM[@]}"
    nap "$PAUSE_LONG"
    show_command "${CLUSTER_CMD_SHOW_NODES[0]}"
    printf '%s\n' "${CLUSTER_OUT_NODES[@]}"
    nap "$PAUSE_MED"
    show_command_lines "${CLUSTER_CMD_SHOW_AMLFS[@]}"
    printf '%s\n' "${CLUSTER_OUT_AMLFS[@]}"
    nap "$PAUSE_MED"
    printf '\n'
    narrate "This run uses one of these GPU nodes: ${CLUSTER_ENGINE_GPUS} GPUs for Dynamo's engines, ${CLUSTER_TRAIN_GPUS} for training."
}

beat_02() {
    cluster_install
    cluster_state
    nap "$PAUSE_MED"
}
