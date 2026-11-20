# beats/01_problem.sh: beat 01, the problem. What ReTool is and why Qwen3-4B as it ships needs SFT and
# RL for it; one real trajectory turn by turn (sample 1481: right, +1) and a wrong attempt at the same
# problem (sample 1485, −1.05); ReTool's code; the RL loop; and what runs where on the node.
# Data: build/01_problem.data.sh, from fill/01_problem.py. Every line of model text, code, sandbox
# output, and every number comes from there.
#
# Three screens, each cleared: 1. two cards and the definitions of trajectory and turn;
# 2. the transcript; 3. the code, the loop, and the node. Screens 2 and 3 fit in 32 rows.
# The transcript blocks hold for a skim (PROBLEM_SKIM_*), not their full reading time: the model's
# text is there to look at, and the narration carries the point.

# problem_hl LANG TEXT → _HL: TEXT highlighted as LANG, as one string with escapes.
problem_hl() {
    local k
    hl_line "$1" "$2"
    _HL=""
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do _HL+="${SEG_COL[k]}${SEG_TXT[k]}"; done
    _HL+="$NC"
}

# problem_row LABEL TEXT [NOTE]: one transcript row. LABEL sits in a gutter of PROBLEM_GUTTER
# columns; NOTE, if given, starts at column 56 in the dim color.
problem_row() {
    local label="$1" text="$2" note="${3:-}" left note_col=56
    padr left "$PROBLEM_GUTTER" "  ${label}${NC}"
    if [[ -n "$note" ]]; then
        padr text $(( note_col - PROBLEM_GUTTER )) "$text"
        text+="${DIM}◀ ${note}"
    fi
    printf '%s%s%s\n' "$left" "$text" "$NC"
}

# problem_box TITLE LINE...: a box like card's, CARD_W wide, but each LINE is printed as is
# (no wrapping, so columns inside it stay aligned). LINEs may hold escapes.
problem_box() {
    local title="$1" line pad ind iw=$(( CARD_W - 4 ))
    shift
    printf -v ind '%*s' "$CARD_INDENT" ''
    dwidth "$title"
    _rep '─' $(( CARD_W - 5 - _W ))
    printf '%s%s╭─ %s%s%s%s %s╮%s\n' "$ind" "$CARDB" "$NC" "$BOLD" "$title" "$CARDB" "$_REP" "$NC"
    for line in "$@"; do
        padr pad "$iw" "$line"
        printf '%s%s│%s %s%s %s│%s\n' "$ind" "$CARDB" "$NC" "$pad" "$NC" "$CARDB" "$NC"
    done
    _rep '─' $(( CARD_W - 2 ))
    printf '%s%s╰%s╯%s\n' "$ind" "$CARDB" "$_REP" "$NC"
}

# problem_title TEXT: a one-row title for screens 2 and 3, then a blank row.
problem_title() { printf '  %s%s%s\n\n' "$BOLD" "$1" "$NC"; }

# Box pieces for the node diagram: VAR WIDTH [TEXT]. The borders are dim.
problem_box_top() {
    dwidth "$3"
    _rep '─' $(( $2 - 5 - _W ))
    printf -v "$1" '%s┌─ %s%s%s %s┐%s' "$DIM" "$NC$BOLD" "$3" "$NC$DIM" "$_REP" "$NC"
}
problem_box_mid() {
    local t
    padr t $(( $2 - 4 )) "$3"
    printf -v "$1" '%s│%s %s%s %s│%s' "$DIM" "$NC" "$t" "$NC" "$DIM" "$NC"
}
problem_box_bottom() {
    _rep '─' $(( $2 - 2 ))
    printf -v "$1" '%s└%s┘%s' "$DIM" "$_REP" "$NC"
}

problem_screen_why() {
    beat_header "🧮" "$PROBLEM_TITLE"
    card "ReTool: math with a Python tool" \
        "ReTool, from a 2025 ByteDance Seed paper on RL for strategic tool use, ships as a Slime example. The model solves competition math and can stop partway to run Python, then carry on with the output in view."
    card "Why not use Qwen3-4B as it ships?" \
        "Off the shelf, ${PROBLEM_SFT_BASE} reasons in text and does the arithmetic in its head. Fine-tuning on ReTool-SFT traces (SFT) teaches it to run code mid-solution and carry on from the output: this run starts from that checkpoint, ${PROBLEM_CKPT}. RL then teaches when and how to use the tool, rewarding only the final answer."
    printf '\n'
    narrate "Next, one trajectory: one attempt at one problem from DAPO-Math-17k (${PROBLEM_N_PROBLEMS} problems, each labeled with its answer)." \
            "A turn is one model call, ending at a tool call or an answer: one request to Dynamo."
}

problem_screen_trajectory() {
    local k label text last
    clear_screen
    problem_title "🧮 One trajectory (Slime calls it a sample): sample ${PROBLEM_EX_SAMPLE}, ${PROBLEM_EX_TURNS} turns"

    for (( k=0; k<${#PROBLEM_PROMPT[@]}; k++ )); do
        label=""; text="${PROBLEM_PROMPT[k]}"
        if (( k == 0 )); then label="${BOLD}prompt"; fi
        if (( k == PROBLEM_PROBLEM_ROW )); then text="${BOLD}${text}"; fi
        problem_row "$label" "$text"
    done
    nap 2

    for (( k=0; k<${#PROBLEM_T1[@]}; k++ )); do
        label=""; if (( k == 0 )); then label="${BOLD}turn 1"; fi
        problem_row "$label" "${PROBLEM_T1[k]}"
    done
    problem_row "" "${CMD}${PROBLEM_CALL[0]}"
    problem_row "" "${DIM}${PROBLEM_CALL[1]}"
    problem_row "" "${CMD}${PROBLEM_CALL[2]}" "generation stops at the tool call"
    nap 3.5

    hl_reset python
    for (( k=0; k<${#PROBLEM_CODE[@]}; k++ )); do
        problem_hl python "${PROBLEM_CODE[k]}"
        if (( k == 0 )); then
            problem_row "${OK}sandbox" "$_HL" "ReTool runs the code in a Python subprocess"
        else
            problem_row "" "$_HL"
        fi
    done
    last=$(( ${#PROBLEM_OBS[@]} - 1 ))
    problem_row "" "${CMD}${PROBLEM_OBS[0]}" "the output is appended to the transcript,"
    for (( k=1; k<last; k++ )); do problem_row "" "${STR}${PROBLEM_OBS[k]}"; done
    problem_row "" "${CMD}${PROBLEM_OBS[last]}" "and all of it goes back to the model: turn 2"
    nap 2.5

    last=$(( ${#PROBLEM_T2[@]} - 1 ))
    for (( k=0; k<=last; k++ )); do
        label=""; if (( k == 0 )); then label="${BOLD}turn 2"; fi
        if (( k == last )); then
            problem_row "$label" "${BOLD}${PROBLEM_T2[k]}" "an answer ends the trajectory"
        else
            problem_row "$label" "${PROBLEM_T2[k]}"
        fi
    done
    nap 2
    problem_row "${BOLD}reward" "${OK}${BOLD}${PROBLEM_REWARD}${NC}  reward_func: the answer, ${PROBLEM_PRED}, matches the label, ${PROBLEM_LABEL}, which the model never saw"
    nap 2

    printf '\n'
    for (( k=0; k<${#PROBLEM_ALT[@]}; k++ )); do
        label=""; if (( k == 0 )); then label="${NUM}wrong"; fi
        problem_row "$label" "${PROBLEM_ALT[k]}"
    done
    nap 4.5
    narrate "Training makes what the ${PROBLEM_REWARD} trajectory wrote more likely, and what the ${PROBLEM_ALT_REWARD} one wrote less likely."
}

problem_screen_loop() {
    local bw=46 mg=16 i ind="  " g1 g2 g3 g4 a b l
    local -a dyn=("frontend + router" "▣ ▣ ▣ ▣  ${PROBLEM_ENGINES} SGLang engines, 1 GPU each")
    local -a sli=("generate(), reward_func(), sandbox (CPU)" "▣ ▣ ▣ ▣  Megatron-LM trainer, ${PROBLEM_TRAIN_GPUS} GPUs")
    local sig0 sig1 sig2

    clear_screen
    problem_title "🧮 ReTool's code, the RL loop, and what runs where"

    hl_reset python
    problem_hl python "${PROBLEM_SIGS[0]}"; padr sig0 70 "$_HL"
    problem_hl python "${PROBLEM_SIGS[1]}"; padr sig1 70 "$_HL"
    problem_hl python "${PROBLEM_SIGS[2]}"; padr sig2 70 "$_HL"
    problem_box "ReTool's code: generate_with_retool.py and tool_sandbox.py (Step 4 opens it)" \
        "${sig0}${NAR}# one trajectory, turn by turn" \
        "${sig1}${NAR}# +1 if right, about −1 if wrong" \
        "${sig2}${NAR}# runs the code: ${PROBLEM_SANDBOX_MEM}, ${PROBLEM_SANDBOX_SECS} s at most"
    nap 3

    printf '\n  %sThe RL loop, repeated (%s times in this run):%s\n' "$BOLD" "$PROBLEM_STEPS" "$NC"
    printf '   %s①%s rollout      Dynamo serves every turn while %sgenerate()%s makes trajectories and %sreward_func()%s scores them\n' \
        "$NUM" "$NC" "$FUNC" "$NC" "$FUNC" "$NC"
    printf '   %s②%s train        the trainer updates the weights toward the trajectories that scored higher\n' "$NUM" "$NC"
    printf '   %s③%s weight sync  the trainer writes the new weights to Lustre; each engine loads them for the next rollout\n' "$NUM" "$NC"
    nap 7

    # The node: two boxes side by side, the rollout between them, Lustre below.
    printf '\n%s%sOne AKS node · %s · %s × %s%s\n' "$ind" "$BOLD" "$PROBLEM_VM" "$PROBLEM_NODE_GPUS" "$PROBLEM_GPU_NAME" "$NC"
    padr g1 "$mg" "   ${NUM}①${NC} rollout"
    g2=" ◀─── turns ─── "
    g3=" ── replies ──▶ "
    padr g4 "$mg" ""
    problem_box_top a "$bw" "Dynamo serves the model"
    problem_box_top b "$bw" "Slime trains it, on KubeRay"
    printf '%s%s%s%s\n' "$ind" "$a" "$g1" "$b"
    for i in 0 1; do
        problem_box_mid a "$bw" "${dyn[i]}"
        problem_box_mid b "$bw" "${sli[i]}"
        if (( i == 0 )); then l=$g2; else l=$g3; fi
        printf '%s%s%s%s\n' "$ind" "$a" "$l" "$b"
    done
    problem_box_bottom a "$bw"
    printf '%s%s%s%s\n' "$ind" "$a" "$g4" "$a"
    padr a $(( bw + mg )) "      ▲ ${NUM}③${NC} each engine loads the new weights"
    printf '%s%s      │ %s③%s the trainer writes them\n' "$ind" "$a" "$NUM" "$NC"
    padr a $(( bw + mg )) "      │"
    printf '%s%s      ▼\n' "$ind" "$a"
    l=" Azure Managed Lustre: /data, with the checkpoint, the dataset, and each new version of the weights "
    dwidth "$l"
    _rep '═' $(( (2 * bw + mg - _W) / 2 )); a=$_REP
    _rep '═' $(( 2 * bw + mg - _W - (2 * bw + mg - _W) / 2 )); b=$_REP
    printf '%s%s%s%s%s%s%s%s\n' "$ind" "$DIM" "$a" "$NC$BOLD" "$l" "$NC$DIM" "$b" "$NC"
    nap 7
}

PROBLEM_TITLE="The Problem: Math with a Python Tool"

beat_01() {
    problem_screen_why
    problem_screen_trajectory
    problem_screen_loop
}
