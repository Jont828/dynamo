# beats/05_slime.sh: Step 4, set up the training side with Slime: the ReTool task code.
#
# Data: build/05_slime.data.sh (fill/05_slime.py): a view of functions/generate_with_retool.py (real
# lines with their real numbers, the skipped parts as closed vim folds), the sandbox limits, the
# reward's numbers, and the ConfigMap's apply output.
#
# The code appears in a read-only vim view of the existing file (slime_view_*), not typed with
# vim_open: ReTool's example ships this file, and the point of this beat is that nobody running the
# example writes it. The view reuses the vim emulator's layout (VIM_ROWS, VIM_STL, VIM_TW, VIM_GUT),
# its colors (LNR, MARK, FOLDC, EOB, STL), its callout box (_vim_box, _wrap), and its key timings, so it
# looks like the other vim scenes. Unlike vim_callout, a callout here marks a set of lines, by their
# real line numbers. The view's state lives in locals of slime_code, which its helpers share.

# slime_at ROW: move to column 1 of screen ROW and clear the row.
slime_at() { local e=$'\x1b'; printf '%s[%d;1H%s[K' "$e" "$1" "$e"; }

# Highlight every shown line once, in file order (hl_python carries brackets across lines).
slime_view_hl() {
    local k j h n=${#SLIME_VV_KIND[@]}
    slime_vh=()
    hl_reset python
    for (( k=0; k<n; k++ )); do
        h=""
        if [[ "${SLIME_VV_KIND[k]}" == L ]]; then
            hl_line python "${SLIME_VV_TXT[k]}"
            for (( j=0; j<${#SEG_TXT[@]}; j++ )); do h+="${SEG_COL[j]}${SEG_TXT[j]}"; done
        fi
        slime_vh[k]="$h"
    done
    return 0
}

# slime_view_row ROW K: draw view row K on screen ROW: sign and number columns, then the code or the
# closed fold (vim's foldtext, then '-' to the edge). Past the last row: vim's ~.
slime_view_row() {
    local row=$1 k=$2 num g txt
    slime_at "$row"
    if (( k >= ${#SLIME_VV_KIND[@]} )); then
        printf '%s~%s' "$EOB" "$NC"
        return 0
    fi
    num=${SLIME_VV_NUM[k]}
    printf -v g '%3d ' "$num"
    if [[ "${SLIME_VV_KIND[k]}" == L && "$slime_vmark" == *" ${num} "* ]]; then
        g="${MARK}▶ ${g}${NC}"
    else
        g="  ${LNR}${g}${NC}"
    fi
    if [[ "${SLIME_VV_KIND[k]}" == F ]]; then
        txt="${SLIME_VV_TXT[k]} "
        if (( ${#txt} > VIM_TW )); then txt="${txt:0:VIM_TW}"; fi
        _rep '-' $(( VIM_TW - ${#txt} ))
        printf '%s%s%s%s%s' "$g" "$FOLDC" "$txt" "$_REP" "$NC"
    else
        printf '%s%s%s' "$g" "${slime_vh[k]}" "$NC"
    fi
    return 0
}

# The status line: the file name, then vim's ruler (the cursor's line,col and the view's position).
slime_view_status() {
    local e=$'\x1b' n=${#SLIME_VV_KIND[@]} bot above below rp lc fill left w=$(( COLS - 18 ))
    bot=$(( slime_vtop + VIM_ROWS - 1 ))
    if (( bot >= n )); then bot=$(( n - 1 )); fi
    above=$(( ${SLIME_VV_NUM[slime_vtop]} - 1 ))
    below=$(( SLIME_GEN_NLINES - ${SLIME_VV_LAST[bot]} ))
    if (( below <= 0 )); then
        rp=Bot
    elif (( above <= 0 )); then
        rp=Top
    else
        printf -v rp '%2d%%' $(( above * 100 / (above + below) ))
    fi
    if (( slime_vcol == 0 )); then lc="${slime_vcur},0-1"; else lc="${slime_vcur},${slime_vcol}"; fi
    fill=$(( 18 - ${#lc} - ${#rp} ))
    if (( fill < 1 )); then fill=1; fi
    printf -v left "%-${w}s" "$SLIME_GEN_FILE"
    printf '%s[%d;1H%s%s%s%*s%s%s' "$e" "$VIM_STL" "$STL" "$left" "$lc" "$fill" '' "$rp" "$NC"
}

# Put the terminal cursor on the cursor line's first non-blank character.
slime_view_cursor() {
    local e=$'\x1b' k c
    for (( k=slime_vtop; k<slime_vtop+VIM_ROWS && k<${#SLIME_VV_KIND[@]}; k++ )); do
        if [[ "${SLIME_VV_KIND[k]}" == L ]] && (( ${SLIME_VV_NUM[k]} == slime_vcur )); then
            c=$slime_vcol
            if (( c < 1 )); then c=1; fi
            printf '%s[%d;%dH' "$e" $(( k - slime_vtop + 1 )) $(( VIM_GUT + c ))
            return 0
        fi
    done
    return 0
}

slime_view_draw() {
    local r
    for (( r=1; r<=VIM_ROWS; r++ )); do slime_view_row "$r" $(( slime_vtop + r - 1 )); done
    slime_view_status
}

# slime_view_scroll TOP: scroll down to view row TOP one row at a time, as CTRL-E does. The text rows
# are the terminal's scroll region (set when the view opens), so each step is one newline and one row.
slime_view_scroll() {
    local target=$1 e=$'\x1b' tick="${SLIME_SCROLL_TICK:-0.03}" k
    cursor_hide
    _vim_cmdline ""   # the "FILE" NL, NB message goes once the view moves
    while (( slime_vtop < target )); do
        slime_vtop=$(( slime_vtop + 1 ))
        printf '%s[%d;1H\n' "$e" "$VIM_ROWS"
        slime_view_row "$VIM_ROWS" $(( slime_vtop + VIM_ROWS - 1 ))
        # vim keeps the cursor on screen: when its line scrolls off the top, it moves to the top line.
        for (( k=0; k<slime_vtop; k++ )); do
            if [[ "${SLIME_VV_KIND[k]}" == L ]] && (( ${SLIME_VV_NUM[k]} == slime_vcur )); then
                slime_vcur=${SLIME_VV_NUM[slime_vtop]}; slime_vcol=${SLIME_VV_COL[slime_vtop]}
                break
            fi
        done
        slime_view_status
        nap "$tick"
    done
    return 0
}

# slime_view_scroll_to LINE: scroll down until real line LINE is the top row.
slime_view_scroll_to() {
    local k
    for (( k=0; k<${#SLIME_VV_KIND[@]}; k++ )); do
        if (( ${SLIME_VV_NUM[k]} == $1 )); then slime_view_scroll "$k"; return 0; fi
    done
    die "slime_view_scroll_to: line $1 isn't in the view"
}

# slime_label "N N ..." → slime_lbl: the box label, "▶ line 495" or "▶ lines 403, 420–421".
slime_label() {
    local n a=-1 b=-1 out="" count=0
    for n in $1 -1; do
        if (( a >= 0 && n != b + 1 )); then
            if (( a == b )); then out+="${out:+, }${a}"; else out+="${out:+, }${a}–${b}"; fi
            a=-1
        fi
        if (( n < 0 )); then break; fi
        count=$(( count + 1 ))
        if (( a < 0 )); then a=$n; fi
        b=$n
    done
    if (( count == 1 )); then slime_lbl="▶ line ${out}"; else slime_lbl="▶ lines ${out}"; fi
}

# slime_view_callout "N N ..." PARAGRAPH...: like vim_callout, for real line numbers: mark the lines
# with ▶ and bold numbers, scroll down if needed so they sit above the box (the last one just above
# it, as vim_callout places them), show the box for its reading time, then remove it.
slime_view_callout() {
    local marks="$1" p h avail first=-1 last=-1 k n=${#SLIME_VV_KIND[@]}
    shift
    local -a lines=()
    for p in "$@"; do _wrap $(( COLS - 4 )) "$p"; lines+=("${_WL[@]}"); done
    h=$(( ${#lines[@]} + 2 ))
    avail=$(( VIM_ROWS - h ))
    for (( k=0; k<n; k++ )); do
        if [[ "${SLIME_VV_KIND[k]}" == L && " ${marks} " == *" ${SLIME_VV_NUM[k]} "* ]]; then
            if (( first < 0 )); then first=$k; fi
            last=$k
        fi
    done
    if (( first < 0 )); then die "slime_view_callout: none of lines ${marks} is in the view"; fi
    if (( last - slime_vtop + 1 > avail )); then slime_view_scroll $(( last - avail + 1 )); fi
    if (( first < slime_vtop )); then die "slime_view_callout: lines ${marks} don't fit above a ${h}-row box"; fi
    slime_label "$marks"
    slime_vcur=${SLIME_VV_NUM[first]}; slime_vcol=${SLIME_VV_COL[first]}
    cursor_hide
    nap "$CALLOUT_LEAD"
    slime_vmark=" ${marks} "
    slime_view_draw
    _vim_box "$h" "$slime_lbl" "${lines[@]}"
    read_hold_at "$CALLOUT_WPM" "$CALLOUT_FLOOR" "$@"
    slime_vmark=""
    slime_view_draw
    slime_view_cursor
    cursor_show
}

# slime_view_open LINE: type "vim +LINE FILE", then show the file from LINE with vim's "FILE" NL, NB.
slime_view_open() {
    local line=$1 e=$'\x1b' k n=${#SLIME_VV_KIND[@]}
    show_command "vim +${line} ${SLIME_GEN_FILE}"
    slime_view_hl
    slime_vtop=-1
    for (( k=0; k<n; k++ )); do
        if (( ${SLIME_VV_NUM[k]} == line )); then slime_vtop=$k; break; fi
    done
    if (( slime_vtop < 0 )); then die "slime_view_open: line ${line} isn't in the view"; fi
    slime_vcur=$line; slime_vcol=${SLIME_VV_COL[slime_vtop]}
    VIM_ACTIVE=1   # so the runner leaves the alternate screen if the demo stops inside the view
    cursor_hide
    printf '%s[?1049h%s[H%s[2J%s[1;%dr' "$e" "$e" "$e" "$e" "$VIM_ROWS"
    slime_view_draw
    _vim_cmdline "\"${SLIME_GEN_FILE}\" ${SLIME_GEN_NLINES}L, ${SLIME_GEN_NBYTES}B"
    slime_view_cursor
    cursor_show
    nap "$VIM_OPEN_HOLD"
}

# slime_view_close: type :q (nothing changed, so vim exits without a message) and go back to the shell.
slime_view_close() {
    local e=$'\x1b' cmd=":q" k
    nap "$VIM_KEY_PAUSE"
    _vim_cmdline ""
    for (( k=0; k<${#cmd}; k++ )); do
        printf '%s' "${cmd:k:1}"
        nap "$VIM_KEY_CHAR"
    done
    nap "$VIM_KEY_PAUSE"
    printf '%s[r%s[?1049l' "$e" "$e"
    VIM_ACTIVE=0
    cursor_show
}

# The code, part by part: generate's turn loop, then reward_func.
slime_code() {
    local -a slime_vh=()
    local slime_vtop=0 slime_vmark="" slime_vcur=0 slime_vcol=1 slime_lbl=""
    slime_view_open "$SLIME_GEN_LN"
    slime_view_callout "$SLIME_MARK_C1" \
        "Slime calls ${FUNC}generate${NC} once per trajectory. Every turn goes to the Dynamo frontend (DYNAMO_ROLLOUT_URL)."
    slime_view_callout "$SLIME_MARK_C2" \
        "The turn loop: up to ${NUM}${BOLD}${SLIME_MAX_TURNS}${NC} turns. Each sends the whole transcript so far as token IDs, with logprobs for training."
    slime_view_callout "$SLIME_MARK_C3" \
        "One streaming request through Dynamo. The model writes until it calls the tool (stopping at ${SLIME_STOP}) or answers."
    slime_view_callout "$SLIME_MARK_C4" \
        "Append the reply. ${BOLD}trainable=True${NC}: training learns from these tokens."
    slime_view_callout "$SLIME_MARK_C5" \
        "${FUNC}execute_predictions${NC} runs the code in the sandbox and wraps its output in <interpreter> tags. An answer ends the loop."
    slime_view_callout "$SLIME_MARK_C6" \
        "Append the sandbox output with ${BOLD}trainable=False${NC}: context for the next turn, not the model's own words."
    slime_view_scroll_to "$SLIME_REWARD_LN"
    slime_view_callout "$SLIME_MARK_R" \
        "${FUNC}reward_func${NC}: ${OK}+1${NC} if the final \boxed{} answer matches the label. Otherwise −1 + ${SLIME_REWARD_STEP} × (tool calls − ${SLIME_REWARD_FREE}), at most ${SLIME_REWARD_CAP}. Slime calls it right after each generate, so scoring is part of the rollout."
    slime_view_close
}

beat_05() {
    local title="Step 4: Set Up the Training Side with Slime"
    beat_header "🧩" "$title"
    narrate "Slime, THUDM's RL training framework, runs the training side. Setting it up takes two pieces:" \
            "the task code (this step), which makes and scores trajectories, and a RayJob (Step 5) whose entrypoint is" \
            "python3 train.py <args>: a declarative ray job submit, as the DGD's engines run python3 -m sglang.launch_server."
    card "Who writes generate?" \
         "Built into Slime: a ${FUNC}generate${NC} that sends one request per trajectory, enough for single-turn tasks." \
         "Multi-turn tool use needs its own. Slime's ReTool example ships one: ${SLIME_EXAMPLE_PATH}." \
         "Your own multi-turn task or tool: write or adapt one. This kit runs a patched copy (PATCH comments): every turn streams through Dynamo, plus bug fixes."
    narrate "The kit's copy, from generate:"
    slime_code
    narrate "Slime imports both functions by name, so they ship as a ConfigMap; Step 5 mounts it on PYTHONPATH:"
    show_command 'kubectl apply -n $NAMESPACE -f manifests/functions.yaml'
    printf '%s\n' "$SLIME_CM_APPLY_OUT"
    nap "$PAUSE_MED"
}
