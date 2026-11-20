# Beat 07: Inside one step: generate, grade, train, sync (results owner D).
# Data: build/07_step.data.sh (fill/07_step.py): the real output of three log pipelines, which the fill script runs on
# runs/rr/run1's saved logs; the engines' pause and resume times (the Dynamo sidecar's is_pause lines); step 0's
# routing (frontend.log); and every number the narration quotes, each asserted against the logs.
#
# The weight sync is shown from both sides: Slime's "Timer update_weights" (3.2 s) ends when the trainer has written
# v1 to Lustre; only then do the engines pause, flush, load, and resume (1.5 s more). Real total: 4.7 s.

STEP_TITLE="Inside One Step: Generate, Grade, Train, Sync"

# step_out LINE...: real output lines, then a short glance. The narration below them keeps them on screen.
step_out() {
    printf '%s\n' "$@"
    nap "$PAUSE_MED"
}

beat_07() {
    beat_header "🔍" "$STEP_TITLE"
    narrate "First, a weight sync gives both engines the trainer's weights as v1 (version 1). Slime's timer covers only the write:"
    show_command "$STEP_DRV_CMD"
    step_out "${STEP_DRV_OUT[@]}"
    printf '\n'
    narrate "Then Slime calls each engine through its Service: pause, flush the cache, load v1 from Lustre, resume:"
    show_command_lines "${STEP_W0_CMD[@]}"
    step_out "${STEP_W0_OUT[@]}"
    printf '\n'
    narrate "The engines' sidecars timed the pause: ${STEP_V1_PAUSE} to ${STEP_V1_RESUME}, ${STEP_V1_LOAD} s. So the sync took ${STEP_V1_TOTAL} s, not ${STEP_V1_WRITE} s." \
            "No restart: same Pods, same processes. Dynamo's frontend isn't involved, and no requests are in flight."
    nap "$PAUSE_SHORT"

    clear_screen
    step_header_bars "🔍" "$STEP_TITLE"
    narrate "Step 0's rollout: ${STEP_N_REQ} requests (${STEP_N_PROMPTS} problems × ${STEP_N_SAMPLES} answers), ${STEP_PER_ENGINE} per engine. Slime grades each answer as it finishes:"
    show_command_lines "${STEP_LOG_CMD[@]}"
    step_out "${STEP_LOG_OUT[@]}"
    printf '\n'
    narrate "Those two are Darla's (wrong) and John's (right) answers, shown at the start. In all, ${STEP_RIGHT} of ${STEP_N_REQ} were right: ${STEP_REWARD}."
    card "GRPO scores each answer against its siblings" \
         "For example, with ${STEP_EX_K} of ${STEP_N_SAMPLES} right, each right answer scores ${NUM}${STEP_EX_RIGHT}${NC} and each wrong one ${NUM}${STEP_EX_WRONG}${NC}." \
         "zero_std/count_1 is ${STEP_ALL_RIGHT}: in ${STEP_ALL_RIGHT} problems all ${STEP_N_SAMPLES} answers were right, so those ${STEP_ALL_RIGHT_N} score 0 and teach nothing."
    narrate "Training took ${STEP_TRAIN} s. Steps 1–9 took ${STEP_TRAIN_LO}–${STEP_TRAIN_HI} s: the first training pass pays a one-time cost." \
            "Then the next sync, v2: ${STEP_V2_WRITE} s writing + ${STEP_V2_LOAD} s loading. Step 1's rollout starts as the engines resume."
    nap "$PAUSE_MED"
}
