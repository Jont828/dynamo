# beats/05_slime.sh: beat 05, Step 4: set up the training side with Slime.
# What Slime is, and the topic sentence for Steps 4 and 5 (one ray job submit, declared as a RayJob).
# Then the slime-functions ConfigMap typed in vim: its header fast, dynamo_generate.py's function at
# normal speed, with callouts on what generate does, who writes it, and why the weight files need the
# permissions hook; no reward code (--rm-type math). Then the apply. The kit applied this ConfigMap once
# at setup, before its runs, and kept no output, so a dim note (lib.sh unsaved_note) says so and the beat
# shows the ConfigMap as the cluster has it today (a read-only snapshot), as beat 04 does for its Services.
# Data: build/05_slime.data.sh, from fill/05_slime.py. Plan: plans/C_slime_rayjob.md.

# slime_vim: "write" slime-functions.yaml. The generate function itself is typed at normal speed.
slime_vim() {
    narrate "Write them into a ConfigMap:"
    vim_open "$SLIME_FILE"
    vim_type fast "${SLIME_Y_HEAD[@]}"
    vim_type normal "${SLIME_Y_GENKEY[@]}"
    vim_type fast "${SLIME_Y_IMPORTS[@]}"
    vim_type normal "${SLIME_Y_FUNC[@]}"
    vim_type fast "${SLIME_Y_ABORT[@]}"
    vim_callout "$SLIME_C1_FROM" "$SLIME_C1_TO" \
        "Slime calls ${BOLD}generate${NC} once per answer. This one runs Slime's built-in generate_streaming, aimed at Dynamo's frontend (DYNAMO_ROLLOUT_URL, from Step 5) instead of Slime's own router."
    vim_callout "$SLIME_C2_FROM" "$SLIME_C2_TO" \
        "Who writes it? The tutorial ships it, ready for single-turn tasks like GSM8K. Write your own generate only for custom rollout logic, such as ReTool's multi-turn tool use, and send its requests to Dynamo too."
    vim_type fast "${SLIME_Y_PERM[@]}"
    vim_callout "$SLIME_C3_FROM" "$SLIME_C3_TO" \
        "Slime runs this hook after each weight write, before the engines load it. The trainer runs as root and writes files only root can read; the engines run as user 1000."
    vim_close
}

# slime_apply: no reward code; create the ConfigMap. The kit applied it at setup, before this run, and
# kept no output: unsaved_note says so, then the ConfigMap's real state today.
slime_apply() {
    printf '\n'
    narrate "No reward code: Step 5's --rm-type math is built in." \
            "Create the ConfigMap; Step 5 mounts it on the trainer's PYTHONPATH:"
    show_command "kubectl apply -n \$NAMESPACE -f ${SLIME_FILE}"
    unsaved_note "$SLIME_SETUP_AT"
    show_command "$SLIME_GET_CMD" "  # on ${SLIME_SNAP_DATE}: still there, with both files"
    printf '%s\n' "${SLIME_GET_OUT[@]}"
    nap 1.5
}

beat_05() {
    beat_header "🧩" "Step 4: Set Up the Training Side with Slime" \
        "Slime (THUDM's RL training framework) drives the loop: answers from Dynamo, grading, training, weight syncs." \
        "Steps 4 and 5 build one ray job submit (python3 train.py <flags>) as a KubeRay RayJob. First, two files it loads."
    slime_vim
    slime_apply
}
