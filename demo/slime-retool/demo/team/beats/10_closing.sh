# Beat 10: closing card (results agent). Data: build/10_closing.data.sh (fill/10_closing.py): the run's node
# and GPUs, its totals (summary.json, compare-kv-rr.json), and the weight syncs' write and load times.

CLOSING_TITLE="Done: Slime Trained Qwen3-4B for 10 Steps, and Dynamo Served Every Turn"
CLOSING_HOLD="${CLOSING_HOLD:-12}"   # seconds on the final card

closing_row() {  # label text
    local c
    padr c 13 "$1"
    printf '%s\n' "   ${BOLD}${c}${NC}${2}"
}

beat_10() {
    beat_header "🎉" "$CLOSING_TITLE"   # the last frame of the recording: the card alone
    closing_row "Hardware" "One ${CLOSING_INSTANCE} node on AKS: ${CLOSING_ENGINE_GPUS} GPUs for Dynamo's SGLang engines, ${CLOSING_TRAINER_GPUS} for Slime's trainer"
    closing_row "Storage" "Azure Managed Lustre: the model, the dataset, and ${CLOSING_SYNCS} weight syncs"
    closing_row "Work" "${CLOSING_TRAJ} trajectories, ${CLOSING_REQ} requests (${CLOSING_RPT} per trajectory), ${CLOSING_OUT_TOKENS} tokens generated"
    closing_row "Time" "RayJob ${CLOSING_RAYJOB}: rollouts ${CLOSING_ROLLOUT_S} s, training ${CLOSING_TRAIN_S} s, weight syncs ${CLOSING_SYNC_S} s (write ${CLOSING_WRITE_S} s + load ${CLOSING_LOAD_S} s)"
    closing_row "Answers" "${CLOSING_RIGHT} of ${CLOSING_TRAJ} correct (${CLOSING_RIGHT_PCT}%). Ten steps are too few to show learning."
    closing_row "KV routing" "Prefill work nearly halved (${CLOSING_PREFILL_KV} vs ${CLOSING_PREFILL_RR} tokens), with no measurable speedup here."
    printf '\n'
    printf '%s\n' "   ${PROMPT}${BOLD}Slime trains. Dynamo serves every turn. New weights every step. 🔁${NC}"
    printf '\n'
    hold "$CLOSING_HOLD"
}
