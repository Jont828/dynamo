# beats/99_frameworktest.sh: the framework's test beat. It exercises every lib.sh
# helper once. Run it with ./run.sh 99; it is never part of the full build.
# Data: build/99_frameworktest.data.sh, from fill/99_frameworktest.py.

frameworktest_setup_render() {  # t: seconds since the setup Job started
    local t="$1" status
    if (( t < FRAMEWORKTEST_DL_T )); then
        status="Downloading the checkpoint and the dataset from Hugging Face..."
    elif (( t < FRAMEWORKTEST_CONVERT_T )); then
        status="Converting the checkpoint to torch_dist on 1 GPU..."
    else
        status="Creating /data/weight-updates and /data/turn-logs..."
    fi
    fl "   ${STATUS}▸ ${status}${NC}"
    ff_elapsed "$t"
}

beat_99() {
    local -a S=("${FRAMEWORKTEST_STORAGE_LINES[@]}") R=("${FRAMEWORKTEST_RAYJOB_LINES[@]}")
    local pv_end=$FRAMEWORKTEST_PV_END pvc=$FRAMEWORKTEST_PVC_START
    local long=$FRAMEWORKTEST_RAYJOB_LONG last=$FRAMEWORKTEST_RAYJOB_LAST_FLAG

    chapter "Framework test: shell helpers"
    clear_screen
    step_header "🧪" "Framework test: every helper once" \
                "A subtitle line in the narration color; the header holds for its reading time."
    narrate "A narrated command, then its real output from runs/setup/storage-apply.txt:"
    show_command 'kubectl apply -f manifests/storage.yaml'
    printf '%s\n' "$FRAMEWORKTEST_STORAGE_OUT"
    nap "$PAUSE_SHORT"
    card "💡 A card: a boxed explainer" \
         "Each paragraph is word-wrapped to the card's width and appears after the previous paragraph's reading time, so a long explanation reads like subtitles instead of a wall of text." \
         "" \
         "Inline colors work, and so do backslashes: DAPO-Math-17k has ${NUM}17,398${NC} problems, and a right answer ends with Answer: \boxed{3162}."
    narrate "A fast-forwarded wait, drawn in place:"
    show_command 'kubectl wait -n $NAMESPACE --for=condition=complete job/slime-retool-setup'
    ff_run frameworktest_setup_render 0 "$FRAMEWORKTEST_SETUP_SECS"
    frame_replace "job.batch/slime-retool-setup condition met"
    show_command 'kubectl logs -n $NAMESPACE job/slime-retool-setup --tail=3'
    printf '%s\n' "$FRAMEWORKTEST_SETUP_TAIL"
    nap "$PAUSE_SHORT"

    chapter "Framework test: vim"
    narrate "Write the storage manifest: the namespace at normal speed, the PV fast, the PVC collapsed."
    vim_open manifests/storage.yaml
    vim_type normal "${S[@]:0:5}"
    vim_type fast "${S[@]:5:pv_end-5}"
    vim_callout "$FRAMEWORKTEST_CSI_FROM" "$FRAMEWORKTEST_CSI_TO" \
        "The csi block points the PV at Azure Managed Lustre: the CSI driver azurelustre.csi.azure.com mounts the file system's sub-directory slime-retool/, and creates it on first mount."
    vim_type fast "${S[@]:pv_end:1}"
    vim_collapse "PersistentVolumeClaim slime-data, bound to the PV above" "${S[@]:pvc-1}"
    vim_callout "$pvc" "$pvc" "A callout on a folded block marks the fold row."
    vim_close
    narrate "Back on the shell screen, with the scrollback as it was."

    narrate "A long line that soft-wraps, a collapse, and a callout that scrolls back up:"
    vim_open manifests/rayjob.yaml
    vim_type normal "${R[@]:0:6}"
    vim_type fast "${R[@]:6:long-6}"
    vim_callout "$long" "$long" \
        "This line is ${NUM}${FRAMEWORKTEST_RAYJOB_LONG_W}${NC} columns, wider than the 114-column text area, so vim soft-wraps it onto a second row; the line number appears only on the first."
    vim_collapse "the other ${FRAMEWORKTEST_RAYJOB_FOLDED} Slime and Megatron-LM flags" "${R[@]:long:last-long}"
    vim_type fast "${R[@]:last}"
    vim_callout "$FRAMEWORKTEST_RAYJOB_KIND" "$FRAMEWORKTEST_RAYJOB_KIND" \
        "kind: RayJob is far above the screen now, so the callout scrolls back to it, then returns to the end."
    vim_close
    narrate "Done."
    nap "$PAUSE_SHORT"
}
