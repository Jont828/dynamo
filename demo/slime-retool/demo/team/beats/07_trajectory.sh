# Beat 07: a closer look inside a rollout: one trajectory, turn by turn (results agent).
# Data: build/07_trajectory.data.sh (fill/07_trajectory.py): tools/trajectory.py's real output for sample 958,
# and one note per turn whose quotes are checked against the model's own text in runs/run1/turns.joined.jsonl.

TRAJECTORY_TITLE="Inside a Rollout: One Trajectory, Turn by Turn"

# trajectory_note LINE...: a note under one turn of the tool's output, indented, paced like narration.
trajectory_note() {
    local line
    for line in "$@"; do
        nap "$NAR_LEAD"
        printf '%s\n' "${NAR}   # ${line}${NC}"
        read_hold "$NAR_FLOOR" "$line"
    done
}

beat_07() {
    local k i start end n=${#TRAJECTORY_TURN_START[@]}
    beat_header "🔍" "$TRAJECTORY_TITLE" \
        "From step ${TRAJECTORY_STEP}'s rollout (the ${TRAJECTORY_STEP_ORD} of ${TRAJECTORY_NSTEPS}), served on ${TRAJECTORY_VERSION}: the starting weights after ${TRAJECTORY_STEP} training updates"
    narrate "While the job runs, zoom into one rollout: one trajectory (one attempt) of its ${TRAJECTORY_STEP_TRAJ}." \
            "--sample ${TRAJECTORY_SAMPLE} is Slime's index for it: the ${TRAJECTORY_PROBLEM_NTH} of step ${TRAJECTORY_STEP}'s $(( TRAJECTORY_STEP_TRAJ / TRAJECTORY_GROUP_SIZE )) problems, attempt ${TRAJECTORY_ATTEMPT} of ${TRAJECTORY_GROUP_SIZE}." \
            "\"${TRAJECTORY_NCODE} ${TRAJECTORY_NOUT}\": show up to ${TRAJECTORY_NCODE} code lines and ${TRAJECTORY_NOUT} output line per turn."
    printf '\n'
    show_command "$TRAJECTORY_CMD"
    nap "$PAUSE_SHORT"
    printf '%s\n' "$TRAJECTORY_HEAD" "${TRAJECTORY_Q[@]}"
    nap "$PAUSE_LONG"
    for (( k=0; k<n; k++ )); do
        start=${TRAJECTORY_TURN_START[$k]}
        if (( k + 1 < n )); then end=${TRAJECTORY_TURN_START[$((k + 1))]}; else end=${#TRAJECTORY_LINES[@]}; fi
        for (( i=start; i<end; i++ )); do
            printf '%s\n' "${TRAJECTORY_LINES[$i]}"
        done
        nap 0.6
        trajectory_note "${TRAJECTORY_NOTES[$k]}"
    done
    printf '\n'
    narrate_wrap "The model never planned ${TRAJECTORY_NTURNS} turns or saw a limit: it writes until it calls the tool or answers." \
                 "The rollout code stops it at an answer, ${TRAJECTORY_MAX_REQUESTS} requests, or ${TRAJECTORY_TOKEN_CAP} tokens." \
                 "Turns 2–${TRAJECTORY_NTURNS} resent the whole history, and ${TRAJECTORY_ENGINE} still held ${TRAJECTORY_HIT_LO}–${TRAJECTORY_HIT_HI}% of each prompt in its cache." \
                 "All ${TRAJECTORY_GROUP_RIGHT} attempts at this problem were right, so none beat the group's average: GRPO learns nothing from this group."
    nap "$PAUSE_MED"
}
