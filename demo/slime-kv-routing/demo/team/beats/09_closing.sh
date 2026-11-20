# Beat 09: closing card (results owner D). Ported from the ReTool team demo's beat 10, without its KV line.
# Data: build/09_closing.data.sh (fill/09_closing.py): the node and GPUs (pods-at-end.json, rayjob.yaml, events.txt,
# snapshots/nodes.txt), the run's totals (summary.json, frontend.log), and the weight syncs' two halves: the trainer's
# write (driver.log timers) and both engines' load (the Dynamo sidecar's pause and resume lines).

CLOSING_TITLE="Done: Slime Trained Qwen3-0.6B for 10 Steps, and Dynamo Served Every Request"
CLOSING_HOLD="${CLOSING_HOLD:-12}"   # seconds on the final card

closing_row() {  # label text
    local c
    padr c 13 "$1"
    printf '%s\n' "   ${BOLD}${c}${NC}${2}"
}

beat_09() {
    beat_header "🎉" "$CLOSING_TITLE"   # the last frame of the recording: the card alone
    closing_row "Hardware" "One ${CLOSING_INSTANCE} node on AKS: ${CLOSING_ENGINE_GPUS} GPUs for Dynamo's SGLang engines, ${CLOSING_TRAINER_GPUS} for Slime's trainer (${CLOSING_USED_GPUS} of ${CLOSING_NODE_GPUS})"
    closing_row "Storage" "Azure Managed Lustre: the model, the dataset, and ${CLOSING_SYNCS} weight versions, each loaded in place"
    closing_row "Work" "${CLOSING_ANSWERS} answers to ${CLOSING_PROBLEMS} GSM8K problems, one request each: ${CLOSING_PER_ENGINE} per engine; ${CLOSING_TOKENS} tokens generated"
    closing_row "Time" "RayJob ${CLOSING_RAYJOB}, of which ${CLOSING_STARTUP_S} s starting Ray and the trainer, before the first weight sync"
    closing_row "Loop" "Rollouts ${CLOSING_ROLLOUT_S} s, training ${CLOSING_TRAIN_S} s, weight syncs ${CLOSING_SYNC_S} s (write ${CLOSING_WRITE_S} s + load ${CLOSING_LOAD_S} s), over ${CLOSING_STEPS} steps"
    closing_row "Answers" "${CLOSING_RIGHT} of ${CLOSING_ANSWERS} right (${CLOSING_RIGHT_PCT}%), with no trend: ${CLOSING_STEPS} steps test the plumbing, not learning"
    printf '\n'
    printf '%s\n' "   ${PROMPT}${BOLD}Slime trains. Dynamo serves. New weights every step. 🔁${NC}"
    printf '\n'
    cursor_hide   # the card alone; the demo's exit trap shows the cursor again
    hold "$CLOSING_HOLD"
}
