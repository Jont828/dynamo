# Beat 08: Step 6: run the loop 10 times (results owner D). Ported from the ReTool team demo's beat 08.
# Data: build/08_loop.data.sh (fill/08_loop.py): the RayJob's clock (rayjob.yaml); Slime's write and train timers and
# its rollout times (driver.log); each engine's pause and resume around every weight load (the Dynamo sidecar's
# is_pause lines in worker-0.log and worker-1.log); requests per engine per step (frontend.log); the reward and the
# trainer-vs-engine logprob gap per step (driver.log, as summarized in summary.json).

LOOP_TITLE="Step 6: Run the Loop 10 Times"
LOOP_PRE_TICK="${LOOP_PRE_TICK:-0.04}"      # per frame (2 run-seconds) before the initial sync is done
LOOP_EVENT_TICK="${LOOP_EVENT_TICK:-0.6}"   # after each change before then, so the milestones read in order
LOOP_STEP0_TICK="${LOOP_STEP0_TICK:-0.3}"   # per frame (1 run-second) through step 0, the row the eye follows first
LOOP_TICK="${LOOP_TICK:-0.17}"              # per frame (1 run-second) for steps 1-9
LOOP_POST_TICK="${LOOP_POST_TICK:-0.04}"    # after the last weight sync, until the job ends
LOOP_HEADER=""
LOOP_ROWS=()   # the frame without its status line; rebuilt only when an event passes

loop_milestones() {  # t
    local t="$1"
    if (( t >= LOOP_ENGINES_T )); then
        fl "  ${OK}✓${NC} Slime attached to the ${LOOP_N_ENGINES} Dynamo engines (${LOOP_ENGINE_SVCS})"
    else
        fl "  ${DIM}▸ KubeRay starts the Ray head Pod, then submits train.py...${NC}"
    fi
    if (( t >= LOOP_V1_LOAD_T )); then
        fl "  ${OK}✓${NC} Initial weight sync, v1: written in ${LOOP_V1_WRITE_S} s, loaded by both engines in ${LOOP_V1_LOAD_S} s"
    elif (( t >= LOOP_V1_WRITE_T )); then
        fl "  ${DIM}▸ Both engines pause and load v1 from Lustre...${NC}"
    elif (( t >= LOOP_V1_WRITE_FROM_T )); then
        fl "  ${DIM}▸ The trainer writes its starting weights, v1, to Lustre...${NC}"
    elif (( t >= LOOP_ENGINES_T )); then
        fl "  ${DIM}▸ The trainer starts on its GPU and loads Qwen3-0.6B_torch_dist...${NC}"
    else
        fl ""
    fi
}

loop_row() {  # step t
    local k="$1" t="$2" c0 c1 c2 c3 v="v$(( $1 + 1 ))" next="v$(( $1 + 2 ))"
    local r_txt r_col="$NC" w_txt="" t_txt="" t_col="$NC" s_txt="" s_col="$NC"
    if (( t < ${LOOP_START_T[$k]} )); then
        fl ""
        return 0
    fi
    if (( t >= ${LOOP_ROLLOUT_T[$k]} )); then
        r_txt="${v} · ${LOOP_ROLL_S[$k]} s · ${LOOP_SPLIT[$k]} requests"
        w_txt="${LOOP_REWARD[$k]} (${LOOP_RIGHT[$k]}/${LOOP_N_ANSWERS})"
    else
        r_txt="${v} · generating and grading..."; r_col="$DIM"
    fi
    if (( t >= ${LOOP_TRAIN_T[$k]} )); then
        t_txt="${LOOP_TRAIN_S[$k]} s"
    elif (( t >= ${LOOP_ROLLOUT_T[$k]} )); then
        t_txt="training..."; t_col="$DIM"
    fi
    if (( t >= ${LOOP_LOAD_T[$k]} )); then
        s_txt="${next} · write ${LOOP_WRITE_S[$k]} s + load ${LOOP_LOAD_S[$k]} s"; s_col="$OK"
    elif (( t >= ${LOOP_WRITE_T[$k]} )); then
        s_txt="both engines loading ${next}..."; s_col="$DIM"
    elif (( t >= ${LOOP_TRAIN_T[$k]} )); then
        s_txt="writing ${next} to Lustre..."; s_col="$DIM"
    fi
    padr c0 8 "  $k"
    padr c1 34 "$r_txt"
    padr c2 16 "$w_txt"
    padr c3 9 "$t_txt"
    fl "${c0}${r_col}${c1}${NC}${c2}${t_col}${c3}${NC}${s_col}${s_txt}${NC}"
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

# Play the run on its own clock: 2 s frames until the initial sync is done (pausing on each milestone), 1 s frames
# through the 10 steps (slower through step 0), then on to the job's end.
loop_play() {
    local t=0 ev=0 last=-1 n=${#LOOP_EVENTS[@]} dt tick changed
    LOOP_HEADER="  STEP  ROLLOUT                           REWARD          TRAIN    WEIGHT SYNC"
    frame_reset
    cursor_hide
    while :; do
        while (( ev < n )) && (( ${LOOP_EVENTS[$ev]} <= t )); do ev=$((ev + 1)); done
        changed=0
        if (( ev != last )); then changed=1; fi
        loop_frame "$t" "$changed"
        frame_draw
        if (( t >= LOOP_DONE_T )); then break; fi
        if (( t < LOOP_V1_LOAD_T )); then
            dt=2; tick=$LOOP_PRE_TICK
            if (( changed && last >= 0 )); then tick=$LOOP_EVENT_TICK; fi
        elif (( t < ${LOOP_LOAD_T[0]} )); then
            dt=1; tick=$LOOP_STEP0_TICK
        elif (( t < ${LOOP_LOAD_T[9]} )); then
            dt=1; tick=$LOOP_TICK
        else
            dt=1; tick=$LOOP_POST_TICK
        fi
        last=$ev
        nap "$tick"
        t=$(( t + dt ))
        # Land exactly on the initial sync's end, so the coarse frames don't skip a milestone.
        if (( t > LOOP_V1_LOAD_T && t - dt < LOOP_V1_LOAD_T )); then t=$LOOP_V1_LOAD_T; fi
        if (( t > LOOP_DONE_T )); then t=$LOOP_DONE_T; fi
    done
    frame_reset
    cursor_show
}

beat_08() {
    local cmd='kubectl logs -n $NAMESPACE -f job/slime-grpo' note="   # condensed to one row per step"
    beat_header "🔁" "$LOOP_TITLE"
    narrate "Now all 10 steps, in the run's order: the initial weight sync, then rollout → train → weight sync, 10 times." \
            "Follow the job's log, condensed here from ${LOOP_LOG_LINES} lines to one row per step:"
    printf '\n'
    show_command "$cmd" "$note"

    clear_screen
    step_header_bars "🔁" "$LOOP_TITLE"
    print_command "$cmd" "$note"
    printf '\n'
    local -a legend=(
        "Dynamo serves the step's ${LOOP_N_ANSWERS} requests on weights vN, round-robin. Slime grades each answer."
        "The share of the ${LOOP_N_ANSWERS} answers that were right: each answer's reward is 1 if right, else 0."
        "The trainer turns the ${LOOP_N_ANSWERS} graded answers into one GRPO update, on its one GPU."
        "The trainer writes vN+1 to Lustre (write); both engines pause, load it, and resume (load)."
    )
    printf '%s\n' "  ${BOLD}ROLLOUT${NC}      ${NAR}${legend[0]}${NC}" \
                  "  ${BOLD}REWARD${NC}       ${NAR}${legend[1]}${NC}" \
                  "  ${BOLD}TRAIN${NC}        ${NAR}${legend[2]}${NC}" \
                  "  ${BOLD}WEIGHT SYNC${NC}  ${NAR}${legend[3]}${NC}"
    printf '\n'
    # The legend stays up while the table plays: hold for about half its reading time first.
    read_hold_at $(( NAR_WPM * 2 )) "$PAUSE_LONG" "${legend[@]}"
    loop_play
    cursor_hide   # loop_play shows it again; the screen below the table is narration only
    nap "$PAUSE_MED"
    printf '\n'
    narrate "Reward ${LOOP_REWARD_MIN}–${LOOP_REWARD_MAX}, with no trend: it starts and ends at ${LOOP_REWARD_MAX}. Each step draws ${LOOP_N_PROMPTS} new problems." \
            "Ten steps × ${LOOP_N_ANSWERS} answers test the plumbing, not learning: Slime's own example runs take ${LOOP_REAL_LO} to ${LOOP_REAL_HI} steps." \
            "Logprob gap, engines vs trainer: ${LOOP_GAP_MIN}–${LOOP_GAP_MAX} every step. A sanity check that no engine was left on older weights."
    nap "$PAUSE_MED"
}
