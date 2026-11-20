# beats/03_storage.sh: beat 03, Step 2: stage the model and data on Azure Managed Lustre.
# Who mounts the shared volume and what goes on it (subtitle); storage.yaml typed in vim (fast),
# pausing at ReadWriteMany, at the Lustre CSI block and its sub-dir, and at the static PV and its
# claim; then one narrated command at a time: create the storage, create the setup
# Job, wait for it (its 69 s fast-forwarded on the real phase times), and list /data from its log.
# Data: build/03_storage.data.sh, from fill/03_storage.py. Plan: plans/B_storage_serve.md.

# storage_setup_render T: one frame of the setup Job's fast-forward (T: seconds since the apply). Phase
# times: the converter's first log line (STORAGE_CONV_T) and its saved checkpoint (STORAGE_SAVED_T),
# both in setup-job.log. The first frame holds PAUSE_SHORT and each later phase's first frame 0.6 s,
# so every status line can be read.
storage_setup_render() {
    local t="$1"
    if (( t < STORAGE_CONV_T )); then
        fl "   ${STATUS}▸ Starting the Pod, downloading ${STORAGE_MODEL} and ${STORAGE_DATASET}, then starting the converter...${NC}"
    else
        fl "   ${OK}✓${NC} Downloaded the model (${STORAGE_MODEL_DL_SECS} s) and the dataset (${STORAGE_DATA_DL_SECS} s) from Hugging Face"
        if (( t < STORAGE_SAVED_T )); then
            fl "   ${STATUS}▸ Converting the model to torch_dist on 1 GPU...${NC}"
        else
            fl "   ${OK}✓${NC} Converted the model to torch_dist"
            fl "   ${STATUS}▸ Creating /data/weight-updates and listing /data...${NC}"
        fi
    fi
    ff_elapsed "$t"
    if (( t == 0 )); then FF_TICK_NOW=$PAUSE_SHORT; fi
    if (( t == STORAGE_CONV_T || t == STORAGE_SAVED_T )); then FF_TICK_NOW=0.6; fi
    return 0
}

# storage_vim: "write" storage.yaml, fast, with a callout after each part worth a pause.
storage_vim() {
    local -a L=("${STORAGE_YAML[@]}")
    local acc=$STORAGE_LN_ACCESS subdir=$STORAGE_LN_SUBDIR
    narrate "The tutorial asks for one ReadWriteMany claim (PVC). Here a PersistentVolume (PV) on Azure Managed Lustre backs it:"
    vim_open "$STORAGE_FILE"
    vim_type fast "${L[@]:0:acc+1}"
    vim_callout "$acc" $(( acc + 1 )) \
        "${BOLD}ReadWriteMany${NC}: Pods on any node can mount it read-write at once. The trainer writes new weights, and the engines read them."
    vim_type fast "${L[@]:acc+1:subdir-acc-1}"
    vim_callout "$STORAGE_LN_CSI" "$subdir" \
        "The ${BOLD}Azure Lustre CSI driver${NC} from Step 1 mounts the file system, but only this run's folder:" \
        "${BOLD}sub-dir${NC} ${STORAGE_SUBDIR}/, created on first mount."
    vim_type fast "${L[@]:subdir}"
    vim_callout "$STORAGE_LN_CLAIMREF" "$STORAGE_LN_END" \
        "A ${BOLD}static PV${NC}: the file system already exists, and no storage class here provisions volumes on it." \
        "So the PV and its claim, ${BOLD}${STORAGE_PVC}${NC}, name each other (claimRef, volumeName)."
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
    narrate "A one-time Job downloads the model and GSM8K, then converts the model to the trainer's format, torch_dist:"
    show_command "kubectl apply -n \$NAMESPACE -f ${STORAGE_JOB_FILE}"
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
    narrate "Its log ends by listing /data: GSM8K, the model SGLang serves, and the copy the trainer starts from:"
    show_command "kubectl logs -n \$NAMESPACE job/${STORAGE_JOB} --tail=${STORAGE_TAIL_N}"
    printf '%s\n' "${STORAGE_TAIL_OUT[@]}"
    nap "$PAUSE_LONG"
}

beat_03() {
    local title="Step 2: Stage the Model and Data on Azure Managed Lustre"
    beat_header "📦" "$title" \
        "The setup Job, both engines, and the trainer mount one shared folder, /data: the model, GSM8K, and new weights."
    storage_vim
    storage_commands
}
