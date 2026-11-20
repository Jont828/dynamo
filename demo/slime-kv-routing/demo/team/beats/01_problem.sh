# beats/01_problem.sh: beat 01, the problem. What GSM8K is and what an answer (sample) is; why RL on a
# model that's already right about 60% of the time; two real answers from the start of the run (step
# 0's two logged samples: Darla's bill, wrong, reward 0; John's zoo, right, reward 1); the reward (Slime's
# built-in math grader); the RL loop; what runs where on the node; and what 10 times around the loop
# will and won't show (beat 08 shows the rewards per step).
# Data: build/01_problem.data.sh, from fill/01_problem.py. Every line of model text and every number
# comes from there. The model's text is verbatim; the right answer is cut to fit, with … at each cut.
#
# Three screens, each cleared: 1. two cards (GSM8K; why RL); 2. the two answers (28 rows); 3. the loop
# and the node (21 rows). Screen 2's blocks hold for a skim, not their full reading time: the model's
# text is there to look at, and the narration carries the point.

PROBLEM_TITLE="The Problem: Grade-School Math"

# problem_row LABEL TEXT [NOTE]: one transcript row. LABEL sits in a gutter of PROBLEM_GUTTER
# columns; NOTE, if given, starts at column PROBLEM_NOTE_COL in the dim color.
problem_row() {
    local label="$1" text="$2" note="${3:-}" left
    padr left "$PROBLEM_GUTTER" "  ${label}${NC}"
    if [[ -n "$note" ]]; then
        padr text $(( PROBLEM_NOTE_COL - PROBLEM_GUTTER )) "$text"
        text+="${DIM}◀ ${note}"
    fi
    printf '%s%s%s\n' "$left" "$text" "$NC"
}

# problem_rows LABEL STYLE LINE...: transcript rows, LABEL on the first only, each LINE in STYLE.
problem_rows() {
    local label="$1" style="$2" line first=1
    shift 2
    for line in "$@"; do
        if (( first )); then problem_row "$label" "${style}${line}"; first=0; else problem_row "" "${style}${line}"; fi
    done
}

# problem_title TEXT [NOTE]: a one-row title for screens 2 and 3, NOTE on a dim row under it, then a
# blank row.
problem_title() {
    printf '  %s%s%s\n' "$BOLD" "$1" "$NC"
    if [[ -n "${2:-}" ]]; then printf '     %s%s%s\n' "$DIM" "$2" "$NC"; fi
    printf '\n'
}

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

# problem_gpus N → _REP: N GPU marks, "▣ ▣ ".
problem_gpus() { _rep '▣ ' "$1"; }

problem_screen_why() {
    beat_header "🧮" "$PROBLEM_TITLE"
    card "GSM8K: grade-school math word problems" \
        "GSM8K is OpenAI's set of grade-school math word problems: ${PROBLEM_N_TRAIN} for training, each labeled with its final number. The model's response to a problem is an answer (Slime calls it a sample): one request to Dynamo."
    card "Why train a model that's already right about 60% of the time?" \
        "Qwen3-0.6B got ${PROBLEM_N_RIGHT} of this run's ${PROBLEM_N_ANSWERS} answers right. That suits RL, which learns by comparing right and wrong answers to the same problem, so it needs some of each."
    printf '\n'
    narrate "Two real answers from the start of this run, one wrong and one right:"
}

problem_screen_answers() {
    local k label
    clear_screen
    problem_title "🧮 Two answers from the start of this run, as Slime logged them" \
        "driver.log lines ${PROBLEM_LOG_LINES} · chat-template tokens and blank lines left out · … marks a cut"

    # The wrong one, whole.
    problem_row "${NAR}system" "${NAR}${PROBLEM_SYSTEM}  ${DIM}◀ from the dataset, on every problem"
    problem_rows "${BOLD}problem" "$BOLD" "${PROBLEM_WRONG_Q[@]}"
    nap 1.5
    for (( k=0; k<${#PROBLEM_WRONG_A[@]}; k++ )); do
        label=""; if (( k == 0 )); then label="${BOLD}answer"; fi
        if (( k == PROBLEM_WRONG_BOXED_ROW )); then
            problem_row "$label" "${PROBLEM_WRONG_A[k]}" 'the grader reads the last \boxed{}'
        else
            problem_row "$label" "${PROBLEM_WRONG_A[k]}"
        fi
    done
    nap 2
    problem_row "${BOLD}reward" "${NUM}${BOLD}${PROBLEM_WRONG_REWARD}${NC}   ${PROBLEM_WRONG_PRED} isn't the label, ${PROBLEM_WRONG_LABEL}, which is ${PROBLEM_WRONG_FIX}"
    nap 2

    # The right one, cut to fit.
    printf '\n'
    problem_rows "${BOLD}problem" "$BOLD" "${PROBLEM_RIGHT_Q[@]}"
    nap 1.5
    problem_rows "${BOLD}answer" "" "${PROBLEM_RIGHT_A[@]}"
    nap 2.5
    problem_row "${BOLD}reward" "${OK}${BOLD}${PROBLEM_RIGHT_REWARD}${NC}   ${PROBLEM_RIGHT_PRED} matches the label, ${PROBLEM_RIGHT_LABEL}"
    nap 1

    printf '\n'
    narrate "The reward is Slime's built-in math grader (--rm-type math): 1 if the last \\boxed{} matches the label, else 0."
}

problem_screen_loop() {
    local bw=46 mg=16 i ind="  " g1 g2 g3 g4 a b l eng trn
    problem_gpus "$PROBLEM_ENGINES"; eng=$_REP
    problem_gpus "$PROBLEM_TRAIN_GPUS"; trn=$_REP
    local -a dyn=("frontend + router" "${eng} ${PROBLEM_ENGINES} SGLang engines, 1 GPU each")
    local -a sli=("sends problems, grades answers (CPU)" "${trn} Megatron-LM trainer, ${PROBLEM_TRAIN_GPUS} GPU")

    clear_screen
    problem_title "🧮 The RL loop, and what runs where"

    printf '  %sEach time around (%s times in this run):%s\n' "$BOLD" "$PROBLEM_STEPS" "$NC"
    printf '   %s①%s rollout      Dynamo serves answers to a batch of problems, and Slime grades each answer as it arrives\n' "$NUM" "$NC"
    printf '   %s②%s train        the trainer nudges the weights toward the answers that scored 1, away from those that scored 0\n' "$NUM" "$NC"
    printf '   %s③%s weight sync  the trainer writes the new weights to Lustre, and each engine loads them for the next rollout\n' "$NUM" "$NC"
    nap 5

    # The node: two boxes side by side, the rollout between them, Lustre below.
    printf '\n%s%sOne AKS node · %s · %s of its %s × %s GPUs%s\n' "$ind" "$BOLD" "$PROBLEM_VM" \
        $(( PROBLEM_ENGINES + PROBLEM_TRAIN_GPUS )) "$PROBLEM_NODE_GPUS" "$PROBLEM_GPU_NAME" "$NC"
    padr g1 "$mg" "   ${NUM}①${NC} rollout"
    g2=" ◀─ problems ── "
    g3=" ── answers ──▶ "
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
    l=" Azure Managed Lustre: /data, with the model, the dataset, and each new version of the weights "
    dwidth "$l"
    _rep '═' $(( (2 * bw + mg - _W) / 2 )); a=$_REP
    _rep '═' $(( 2 * bw + mg - _W - (2 * bw + mg - _W) / 2 )); b=$_REP
    printf '%s%s%s%s%s%s%s%s\n' "$ind" "$DIM" "$a" "$NC$BOLD" "$l" "$NC$DIM" "$b" "$NC"
    nap 4

    printf '\n'
    narrate "${PROBLEM_STEPS} times around the loop is enough to see every part work, but too few to see learning."
}

beat_01() {
    problem_screen_why
    problem_screen_answers
    problem_screen_loop
}
