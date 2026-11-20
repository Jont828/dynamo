# beats/03_storage.sh: beat 03, Step 2: stage the model and data on Azure Managed Lustre.
# Why the run needs one shared volume and what goes on it; storage.yaml typed in vim (fast),
# pausing at ReadWriteMany and Retain, at the Lustre CSI block, and at the static PV-claim
# binding; then one narrated command at a time: create the storage, create the setup Job, wait
# for it (its 109 s fast-forwarded on the real phase times), and list the volume from its log.
# Data: build/03_storage.data.sh, from fill/03_storage.py. Plan: plans/03-04_storage_serve.md.

# storage_setup_render T: one frame of the setup Job's fast-forward (T: seconds since the Job
# started). Phases from setup.log: downloads done at STORAGE_DL_T, the torch_dist copy saved at
# STORAGE_CONV_T, then the folders and the listing until the Job completes.
storage_setup_render() {
    local t="$1"
    if (( t < STORAGE_DL_T )); then
        fl "   ${STATUS}▸ Downloading ${STORAGE_MODEL} and ${STORAGE_DATASET} from Hugging Face...${NC}"
    else
        fl "   ${OK}✓${NC} Downloaded the checkpoint and the dataset ${DIM}(${STORAGE_DL_SECS} s)${NC}"
        if (( t < STORAGE_CONV_T )); then
            fl "   ${STATUS}▸ Converting the checkpoint to torch_dist on 1 GPU...${NC}"
        else
            fl "   ${OK}✓${NC} Converted the checkpoint to torch_dist ${DIM}(${STORAGE_CONV_SECS} s)${NC}"
            fl "   ${STATUS}▸ Creating /data/weight-updates and /data/turn-logs...${NC}"
        fi
    fi
    ff_elapsed "$t"
}

# storage_vim: "write" storage.yaml, fast, with a callout after each interesting part.
storage_vim() {
    local -a L=("${STORAGE_YAML[@]}")
    local retain=$STORAGE_LN_RETAIN subdir=$STORAGE_LN_SUBDIR volname=$STORAGE_LN_VOLNAME
    narrate "Write the manifests: a PersistentVolume (PV) for the Lustre folder, and the claim (PVC) that Pods mount:"
    vim_open "$STORAGE_FILE"
    vim_type fast "${L[@]:0:retain}"
    vim_callout "$STORAGE_LN_ACCESS" "$retain" \
        "${BOLD}ReadWriteMany${NC}: Pods on any node can mount it read-write at once. ${BOLD}Retain${NC}: deleting the PV keeps the files."
    vim_type fast "${L[@]:retain:subdir-retain}"
    vim_callout "$STORAGE_LN_CSI" "$subdir" \
        "The ${BOLD}Azure Lustre CSI driver${NC} from Step 1 mounts the file system into the run's own folder," \
        "${BOLD}sub-dir${NC} slime-retool/, which it creates on first mount."
    vim_type fast "${L[@]:subdir:volname-subdir}"
    vim_callout "$STORAGE_LN_CLAIMREF" "$volname" \
        "A ${BOLD}static PV${NC}: no storage class here creates Lustre volumes on demand, so the PV and the claim name each other" \
        "(claimRef, volumeName)."
    vim_type fast "${L[@]:volname}"
    vim_close
}

# storage_commands: create the storage and the setup Job, wait for it, and check the volume.
storage_commands() {
    printf '\n'
    narrate "Create the namespace, the PV, and the claim:"
    show_command "kubectl apply -f ${STORAGE_FILE}"
    printf '%s\n' "${STORAGE_APPLY_OUT[@]}"
    nap "$PAUSE_SHORT"
    show_command "export NAMESPACE=${STORAGE_NAMESPACE}"
    printf '\n'
    narrate "A one-time Job on 1 GPU downloads the checkpoint and dataset, then converts it to the trainer's format, torch_dist:"
    show_command 'kubectl apply -n $NAMESPACE -f manifests/setup-job.yaml'
    printf '%s\n' "$STORAGE_JOB_CREATED_OUT"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "Wait for it to finish, fast-forwarding its ${STORAGE_JOB_SECS} s:"
    show_command "kubectl wait -n \$NAMESPACE --for=condition=complete job/${STORAGE_JOB} --timeout=30m"
    ff_run storage_setup_render 0 "$STORAGE_JOB_SECS"
    nap "$PAUSE_SHORT"
    frame_replace "$STORAGE_JOB_MET_OUT"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "Its log ends by listing what's on the volume:"
    show_command "kubectl logs -n \$NAMESPACE job/${STORAGE_JOB} --tail=${STORAGE_TAIL_N}"
    printf '%s\n' "${STORAGE_TAIL_OUT[@]}"
    nap 3
}

beat_03() {
    local title="Step 2: Stage the Model and Data on Azure Managed Lustre"
    beat_header "📦" "$title" \
        "Every Pod in the run shares one folder, /data: the setup Job, the 4 engines, and the trainer." \
        "It holds the checkpoint, a copy in the trainer's format, the ${STORAGE_PROBLEMS} problems, and each step's new weights."
    storage_vim
    storage_commands
}
