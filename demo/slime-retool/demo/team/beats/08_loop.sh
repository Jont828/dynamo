# Beat 08: Step 6: run the loop 10 times (results agent).
# Data: build/08_loop.data.sh (fill/08_loop.py): the RayJob's clock (rayjob.yaml), Slime's per-step times and
# correct answers (summary.json, from the driver log), each weight load from the SGLang sidecar's is_pause lines,
# and real `kubectl logs | grep` output for the first weight sync (runs/run1/worker-0.final.log).

LOOP_TITLE="Step 6: Run the Loop 10 Times"
LOOP_SLOW_TICK="${LOOP_SLOW_TICK:-0.08}"    # per frame (4 run-seconds) until step 0's weight sync is done
LOOP_EVENT_TICK="${LOOP_EVENT_TICK:-0.5}"   # after each change before then, so the order of events reads
LOOP_FAST_TICK="${LOOP_FAST_TICK:-0.06}"    # per frame (12 run-seconds) for steps 1-9
LOOP_SLOW_DT=4
LOOP_FAST_DT=12   # few frames: each nap forks sleep, which a loaded machine slows down
LOOP_HEADER=""
LOOP_ROWS=()   # the frame without its status line; rebuilt only when an event passes

loop_milestones() {  # t
    local t="$1"
    if (( t >= LOOP_ENGINES_T )); then
        fl "  ${OK}✓${NC} Slime attached to the 4 Dynamo engines (${LOOP_ENGINE_SVCS})"
    else
        fl "  ${DIM}▸ KubeRay starts the Ray head Pod and submits train.py...${NC}"
    fi
    if (( t >= LOOP_V1_LOAD_T )); then
        fl "  ${OK}✓${NC} Initial weight sync v1: written to Lustre in ${LOOP_V1_WRITE_S} s, loaded by the 4 engines in ${LOOP_V1_LOAD_S} s"
    elif (( t >= LOOP_V1_WRITE_T )); then
        fl "  ${DIM}▸ The 4 engines load v1 from Lustre...${NC}"
    elif (( t >= LOOP_V1_WRITE_FROM_T )); then
        fl "  ${DIM}▸ The trainer writes its starting weights, v1, to Lustre...${NC}"
    elif (( t >= LOOP_ENGINES_T )); then
        fl "  ${DIM}▸ The trainer starts on its 4 GPUs and loads the torch_dist checkpoint...${NC}"
    else
        fl ""
    fi
}

loop_row() {  # step t
    local k="$1" t="$2" c0 c1 c2 c3 v="v$(( $1 + 1 ))" next="v$(( $1 + 2 ))"
    local r_txt r_col="$NC" t_txt="" t_col="$NC" s_txt="" s_col="$NC" a_txt=""
    if (( t < ${LOOP_START_T[$k]} )); then
        fl ""
        return 0
    fi
    if (( t >= ${LOOP_ROLLOUT_T[$k]} )); then
        r_txt="${v}: ${LOOP_ROLL_S[$k]} s, ${LOOP_RPT[$k]} requests per trajectory"
        a_txt="${LOOP_CORRECT[$k]}%"
    else
        r_txt="${v}: rollout running..."; r_col="$DIM"
    fi
    if (( t >= ${LOOP_TRAIN_T[$k]} )); then
        t_txt="${LOOP_TRAIN_S[$k]} s"
    elif (( t >= ${LOOP_ROLLOUT_T[$k]} )); then
        t_txt="training..."; t_col="$DIM"
    fi
    if (( t >= ${LOOP_LOAD_T[$k]} )); then
        s_txt="${next}: write ${LOOP_WRITE_S[$k]} s, load ${LOOP_LOAD_S[$k]} s"; s_col="$OK"
    elif (( t >= ${LOOP_WRITE_T[$k]} )); then
        s_txt="4 engines loading ${next}..."; s_col="$DIM"
    elif (( t >= ${LOOP_TRAIN_T[$k]} )); then
        s_txt="writing ${next} to Lustre..."; s_col="$DIM"
    fi
    padr c0 8 "  $k"
    padr c1 45 "$r_txt"
    padr c2 10 "$t_txt"
    padr c3 35 "$s_txt"
    fl "${c0}${r_col}${c1}${NC}${t_col}${c2}${NC}${s_col}${c3}${NC}${a_txt}"
}

loop_status() {  # t
    local t="$1"
    if (( t >= LOOP_DONE_T )); then
        fmt_age "$LOOP_DONE_T"
        fl "  ${OK}✅ Job '${LOOP_JOB_ID}' succeeded · ${AGE_STR} after the RayJob was created${NC}"
    else
        ff_elapsed "$t"
    fi
}

# loop_frame t changed: the table at run time t. Its rows are rebuilt only when an event has passed.
loop_frame() {
    local t="$1" k
    if (( $2 )); then
        FRAME=()
        loop_milestones "$t"
        fl ""
        fl "${BOLD}${LOOP_HEADER}${NC}"
        for (( k=0; k<10; k++ )); do
            loop_row "$k" "$t"
        done
        fl ""
        LOOP_ROWS=("${FRAME[@]}")
    else
        FRAME=("${LOOP_ROWS[@]}")
    fi
    loop_status "$t"
}

# Play the run on its own clock: 4 s frames with a pause on each change until step 0's sync is done, then 12 s frames.
loop_play() {
    local t=0 ev=0 last=-1 n=${#LOOP_EVENTS[@]} dt tick changed
    printf -v LOOP_HEADER '  %-6s%-45s%-10s%-35s%s' "STEP" "ROLLOUT" "TRAIN" "WEIGHT SYNC" "CORRECT"
    frame_reset
    cursor_hide
    while :; do
        while (( ev < n )) && (( ${LOOP_EVENTS[$ev]} <= t )); do ev=$((ev + 1)); done
        changed=0
        if (( ev != last )); then changed=1; fi
        loop_frame "$t" "$changed"
        frame_draw
        if (( t >= LOOP_DONE_T )); then break; fi
        if (( t < ${LOOP_LOAD_T[0]} )); then
            dt=$LOOP_SLOW_DT; tick=$LOOP_SLOW_TICK
            if (( changed && last >= 0 )); then tick=$LOOP_EVENT_TICK; fi
        else
            dt=$LOOP_FAST_DT; tick=$LOOP_FAST_TICK
        fi
        last=$ev
        nap "$tick"
        t=$(( t + dt ))
        if (( t > LOOP_DONE_T )); then t=$LOOP_DONE_T; fi
    done
    frame_reset
    cursor_show
}

beat_08() {
    local cmd='kubectl logs -n $NAMESPACE -f job/slime-retool' note="   # condensed to one row per step"
    beat_header "🔁" "$LOOP_TITLE"
    narrate "Back to the start: the RayJob runs one weight sync, then 10 steps of rollout → train → weight sync." \
            "The first sync loads the trainer's starting weights into the engines as v1 (weight version 1). In worker-0's log:"
    printf '\n'
    show_command_lines "${LOOP_LOG_CMD[@]}"
    printf '%s\n' "${LOOP_LOG_OUT[@]}"
    nap "$PAUSE_LONG"
    printf '\n'
    narrate "Slime decides when, not Dynamo. It tells each engine to pause, flush its KV cache, load the weights, and resume." \
            "All 4 engines do it together, in place, between rollouts (no requests in flight): no restart, no rolling update."
    narrate "Now all 10 steps, one row each:"
    printf '\n'
    show_command "$cmd" "$note"

    clear_screen
    step_header_bars "🔁" "$LOOP_TITLE"
    print_command "$cmd" "$note"
    printf '\n'
    local -a legend=(
        "Dynamo serves every turn of the step's 256 trajectories on vN, and reward_func scores each one."
        "No model loading here. It ends with the slowest trajectory. Requests per trajectory: turns, averaged."
        "The job from Steps 4–5 at work: Megatron-LM turns the 256 scored trajectories into one GRPO update."
        "The trainer writes vN+1 to Lustre (write); the 4 engines pause, load it, and resume (load)."
    )
    printf '%s\n' "  ${BOLD}ROLLOUT${NC}      ${NAR}${legend[0]}${NC}" \
                  "               ${NAR}${legend[1]}${NC}" \
                  "  ${BOLD}TRAIN${NC}        ${NAR}${legend[2]}${NC}" \
                  "  ${BOLD}WEIGHT SYNC${NC}  ${NAR}${legend[3]}${NC}"
    printf '\n'
    # The legend stays up while the table plays: hold for half its reading time first.
    read_hold_at $(( NAR_WPM * 2 )) "$PAUSE_LONG" "${legend[@]}"
    loop_play
    printf '\n'
    narrate "CORRECT ran ${LOOP_CORRECT_MIN}–${LOOP_CORRECT_MAX}% with no trend: each step drew new problems, and 10 steps are too few (upstream runs ${LOOP_UPSTREAM_STEPS})." \
            "What it does confirm: the engines served the trainer's weights (their token log-probabilities matched to about ${LOOP_LOGPROB_GAP})."
    nap "$PAUSE_MED"
}
