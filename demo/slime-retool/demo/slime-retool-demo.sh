#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# ============================================================================
# Slime ReTool + Dynamo KV-aware routing — cluster-free replay of one real run:
# Slime trains Qwen3-4B with GRPO on ReTool (multi-turn math with a Python
# sandbox) in a KubeRay RayJob, while a Dynamo DynamoGraphDeployment serves
# every turn of every rollout and routes it by KV-cache overlap and load.
# The waits are fast-forwarded, so the run plays in about three minutes.
# ============================================================================
#
# NOTHING here touches a cluster: no kubectl, no GPUs. Every "❯ ..." line is
# echoed as a prompt string, followed by output saved from the run.
#
# slime-retool-demo.template.sh is the source. fill_demo.py replaces the
# "@DATA@" line below with values read from the saved run (../runs/setup,
# ../runs/deploy, ../runs/run1: the setup Job log, the Pod snapshots, the
# driver log, the turn logs joined to the frontend's routing log, and real
# grep output of the kit's manifests) and writes slime-retool-demo.sh.
# Every number and string on screen comes from there. Frames between two
# recorded states of a fast-forwarded wait are interpolated.
#
# Keep every line under 100 columns: the recording is 100x25.
#
# Deterministic: no randomness, fixed strings -> identical every recording.
# Runs on the macOS system bash (3.2).
#
# Record with:
#   python3 -I fill_demo.py && ./record-slime-retool-demo.sh
#
set -euo pipefail

# =============================================================================
# Timing knobs — tune total length here (target ~3 min)
# =============================================================================
AGE_TICK="${AGE_TICK:-0.04}"         # per-second frame while the setup Job or the engines start
FAST_TICK="${FAST_TICK:-0.03}"       # per-second frame while the training log has nothing new
STEP_TICK="${STEP_TICK:-0.022}"      # per-second frame during the 10 training steps
TURN_PAUSE="${TURN_PAUSE:-1.6}"      # pause after each turn of the trajectory
PAUSE_SHORT="${PAUSE_SHORT:-1.0}"
PAUSE_MED="${PAUSE_MED:-1.2}"
PAUSE_LONG="${PAUSE_LONG:-1.8}"      # static card hold (intro)
CLOSING_HOLD="${CLOSING_HOLD:-8}"    # whole seconds on the closing card before the loop restarts
CHAR_SPEED="${CHAR_SPEED:-0.011}"    # delay per typed chunk
FILE_CHUNK="${FILE_CHUNK:-4}"        # chars per chunk while typing a file body (commands type 1 at a time)
NAR_PAUSE="${NAR_PAUSE:-0.85}"       # pause before each narration line
CMD_PAUSE="${CMD_PAUSE:-0.7}"        # linger after a command finishes "typing"
PROMPT_PAUSE="${PROMPT_PAUSE:-0.6}"  # linger on an empty prompt before typing

# =============================================================================
# Palette: each escape picks a syntax role, not a hue
# =============================================================================
# The Slime Integration overview plays this recording with TerminalDemo's
# "fern-code" theme, which colors each ANSI slot the way Fern's code blocks
# color that role, in the site's light and dark mode. The two modes give some
# roles different hues: command names are purple in light mode and yellow in
# dark mode. apply-fern-code-theme.py embeds the dark colors for other players.
RULE='\033[0;30m'     # 0  step header rules
PROP='\033[0;31m'     # 1  Python attribute names
STR='\033[0;32m'      # 2  quoted strings; success
CMD='\033[0;33m'      # 3  command names
FUNC='\033[0;34m'     # 4  shell builtins and Python function names
DECL='\033[0;35m'     # 5  declaration keywords: export, async, def
OP='\033[0;36m'       # 6  operators and control keywords: | = for do import return
NAR='\033[0;37m'      # 7  narration and comments
DIM='\033[0;90m'      # 8  progress and fast-forward notes
NUM='\033[0;91m'      # 9  numbers called out in narration; in-progress status
ARG='\033[0;92m'      # 10 unquoted arguments and flags
PARAM='\033[0;93m'    # 11 Python parameters
CALLARG='\033[0;94m'  # 12 Python call arguments
QUOTE='\033[0;95m'    # 13 quote marks
PUNCT='\033[0;96m'    # 14 punctuation
PROMPT='\033[0;97m'   # 15 the prompt
BOLD='\033[1m'
NC='\033[0m'
OK="$STR"             # success marks share the string green
STATUS="$NUM"         # in-progress status shares the number color

# Table column formats, padded the way kubectl pads them (widest value + 3).
CRD_FMT='%-36s%s'
POD_FMT='%-32s%-8s%-10s%-11s%s'
DGD_FMT='%-15s%-8s%-10s%s'

# =============================================================================
# Helpers (same behavior as hero-demo.sh)
# =============================================================================
clear_screen() { printf '\033[H\033[2J\033[3J'; }

pause() { sleep "${1:-2}"; }

# Hold the screen for N seconds. The recording and the player cap idle gaps,
# so emit an invisible event (show cursor) every second to keep a longer hold.
hold() {
    local i
    for (( i=0; i<$1; i++ )); do
        sleep 1
        printf '\033[?25h'
    done
}

# Narration. Each line appears on its own after a beat.
narrate() {
    local line
    for line in "$@"; do
        pause "$NAR_PAUSE"
        echo -e "${NAR}# ${line}${NC}"
    done
}

# The cursor stays visible while text is typed and hides during in-place redraws.
cursor_show() { printf '\033[?25h'; }
cursor_hide() { printf '\033[?25l'; }

# -----------------------------------------------------------------------------
# Syntax highlighting. hl_bash and hl_python split one line into segments the
# way Fern's code blocks color bash and Python, appending each segment's text
# to SEG_TXT and its role's escape to SEG_COL.
# -----------------------------------------------------------------------------
SEG_TXT=()
SEG_COL=()
seg_reset() { SEG_TXT=(); SEG_COL=(); }
seg() { SEG_COL+=("$1"); SEG_TXT+=("$2"); }

# Bash state that carries across the lines of one command: inside "...",
# inside $( ) within it, whether the next word is a command name, and a role
# that the next word takes after for or export.
HB_DQ=0
HB_SUB=0
HB_CMD=1
HB_NEXT=""
hl_bash_reset() { HB_DQ=0; HB_SUB=0; HB_CMD=1; HB_NEXT=""; }

# Color one word of a command.
hl_bash_word() {
    local w="$1"
    if [[ "$HB_NEXT" == "var" ]]; then
        HB_NEXT=""
        seg "$NC" "$w"
        return 0
    fi
    if [[ "$HB_NEXT" == "assign" && "$w" == *=* ]]; then
        HB_NEXT=""
        seg "$NC" "${w%%=*}"; seg "$OP" "="; seg "$NC" "${w#*=}"
        return 0
    fi
    case "$w" in
        for) seg "$OP" "$w"; HB_CMD=0; HB_NEXT=var; return 0 ;;
        in|done) seg "$OP" "$w"; return 0 ;;
        do) seg "$OP" "$w"; HB_CMD=1; return 0 ;;
        export) seg "$DECL" "$w"; HB_CMD=0; HB_NEXT=assign; return 0 ;;
    esac
    if (( HB_CMD )); then
        HB_CMD=0
        case "$w" in
            echo|cd|test) seg "$FUNC" "$w" ;;
            *) seg "$CMD" "$w" ;;
        esac
    else
        seg "$ARG" "$w"
    fi
}

hl_bash() {
    local s="$1" n=${#1} i=0 j c
    while (( i < n )); do
        c=${s:i:1}
        if [[ "$c" == " " ]]; then
            j=$i
            while [[ "${s:j:1}" == " " ]]; do j=$((j + 1)); done
            seg "$NC" "${s:i:j-i}"; i=$j
            continue
        fi
        if (( HB_DQ && !HB_SUB )); then
            # Inside "...": variables, $( to open a command, or string text.
            if [[ "$c" == '"' ]]; then
                seg "$QUOTE" '"'; HB_DQ=0; i=$((i + 1))
            elif [[ "$c" == '$' && "${s:i+1:1}" == "(" ]]; then
                seg "$QUOTE" '$('; HB_SUB=1; HB_CMD=1; i=$((i + 2))
            elif [[ "$c" == '$' ]]; then
                j=$((i + 1))
                while [[ "${s:j:1}" =~ [A-Za-z0-9_] ]]; do j=$((j + 1)); done
                seg "$NC" "${s:i:j-i}"; i=$j
            else
                j=$i
                while (( j < n )) && [[ "${s:j:1}" != '"' && "${s:j:1}" != '$' ]]; do j=$((j + 1)); done
                seg "$STR" "${s:i:j-i}"; i=$j
            fi
            continue
        fi
        case "$c" in
            '"') seg "$QUOTE" '"'; HB_DQ=1; HB_CMD=0; i=$((i + 1)); continue ;;
            "'")
                j=$((i + 1))
                while (( j < n )) && [[ "${s:j:1}" != "'" ]]; do j=$((j + 1)); done
                seg "$QUOTE" "'"; seg "$STR" "${s:i+1:j-i-1}"; seg "$QUOTE" "'"
                HB_CMD=0; i=$((j + 1))
                continue ;;
            '|') seg "$OP" '|'; HB_CMD=1; i=$((i + 1)); continue ;;
            ';') seg "$PUNCT" ';'; HB_CMD=1; i=$((i + 1)); continue ;;
            ')') seg "$QUOTE" ')'; HB_SUB=0; i=$((i + 1)); continue ;;
            '\') seg "$NC" '\'; i=$((i + 1)); continue ;;
            '$')
                j=$((i + 1))
                while [[ "${s:j:1}" =~ [A-Za-z0-9_] ]]; do j=$((j + 1)); done
                seg "$NC" "${s:i:j-i}"; HB_CMD=0; i=$j
                continue ;;
        esac
        # A word runs to the next space, quote, operator, or variable.
        j=$i
        while (( j < n )); do
            case "${s:j:1}" in ' '|'"'|"'"|'|'|';'|')'|'$') break ;; esac
            j=$((j + 1))
        done
        hl_bash_word "${s:i:j-i}"
        i=$j
    done
}

# Color one line of Python. Each line of the demo's file is self-contained.
hl_python() {
    local s="$1" n=${#1} i=0 j c w prev next role
    local import_line=0 after_def=0 params=0 depth=0
    case "$s" in "import "*|"from "*) import_line=1 ;; esac
    while (( i < n )); do
        c=${s:i:1}
        if [[ "$c" == " " ]]; then
            j=$i
            while [[ "${s:j:1}" == " " ]]; do j=$((j + 1)); done
            seg "$NC" "${s:i:j-i}"; i=$j
            continue
        fi
        if [[ "$c" == '"' || "$c" == "'" ]]; then
            j=$((i + 1))
            while (( j < n )) && [[ "${s:j:1}" != "$c" ]]; do j=$((j + 1)); done
            seg "$QUOTE" "$c"; seg "$STR" "${s:i+1:j-i-1}"; seg "$QUOTE" "$c"
            i=$((j + 1))
            continue
        fi
        if [[ "$c" =~ [A-Za-z_] ]]; then
            j=$i
            while [[ "${s:j:1}" =~ [A-Za-z0-9_] ]]; do j=$((j + 1)); done
            w=${s:i:j-i}
            next=${s:j:1}
            prev=""
            (( i > 0 )) && prev=${s:i-1:1}
            case "$w" in
                import|from|as|return|await|if|else|for|in|with) role=$OP ;;
                async|def|class) role=$DECL; [[ "$w" == def ]] && after_def=1 ;;
                *)
                    if (( after_def )); then
                        role=$FUNC; after_def=0; params=1
                    elif (( params == 2 )); then
                        role=$PARAM
                    elif [[ "$prev" == "." ]] && (( !import_line )); then
                        if [[ "$next" == "(" ]]; then role=$FUNC; else role=$PROP; fi
                    elif [[ "$next" == "(" ]]; then
                        role=$FUNC
                    elif (( depth > 0 )); then
                        role=$CALLARG
                    else
                        role=$NC
                    fi ;;
            esac
            seg "$role" "$w"; i=$j
            continue
        fi
        case "$c" in
            '(')
                if (( params == 1 )); then params=2; else depth=$((depth + 1)); fi
                seg "$PUNCT" "$c" ;;
            ')')
                if (( params == 2 )); then params=0; else depth=$((depth - 1)); fi
                seg "$PUNCT" "$c" ;;
            '=') seg "$OP" "$c" ;;
            *) seg "$PUNCT" "$c" ;;
        esac
        i=$((i + 1))
    done
}

# Type the segments, sleeping after every $1 characters (default 1).
type_segments() {
    local chunk="${1:-1}" k i t count=0
    cursor_show
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do
        t=${SEG_TXT[$k]}
        printf '%b' "${SEG_COL[$k]}"
        for (( i=0; i<${#t}; i++ )); do
            printf '%s' "${t:i:1}"
            count=$((count + 1))
            if (( count % chunk == 0 )); then sleep "$CHAR_SPEED"; fi
        done
    done
    printf '%b' "$NC"
}

# Print the segments at once.
print_segments() {
    local k
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do
        printf '%b%s' "${SEG_COL[$k]}" "${SEG_TXT[$k]}"
    done
    printf '%b' "$NC"
}

# A shell command: draw the prompt, linger, type the command, linger.
# $2 = optional comment printed after the command.
show_command() {
    cursor_show
    printf '%b ' "${PROMPT}❯${NC}"
    pause "$PROMPT_PAUSE"
    seg_reset; hl_bash_reset; hl_bash "$1"
    type_segments
    if [[ -n "${2:-}" ]]; then
        printf '%b' "${NAR}${2}${NC}"
    fi
    printf '\n'
    cursor_hide
    pause "$CMD_PAUSE"
}

# A command that spans several lines: the first line follows the prompt and
# each continuation line is typed below it.
show_command_lines() {
    cursor_show
    printf '%b ' "${PROMPT}❯${NC}"
    pause "$PROMPT_PAUSE"
    hl_bash_reset
    local line
    for line in "$@"; do
        seg_reset; hl_bash "$line"
        type_segments
        printf '\n'
        cursor_hide
    done
    pause "$CMD_PAUSE"
}

# The same prompt line, printed at once. Used to re-pin a command after a hard cut.
print_command() {
    printf '%b ' "${PROMPT}❯${NC}"
    seg_reset; hl_bash_reset; hl_bash "$1"
    print_segments
    printf '%b\n' "${2:+${NAR}${2}${NC}}"
}

# Step header with two leading blank lines to separate it from the prior beat.
step_header() {
    echo ""
    echo ""
    step_header_bars "$1" "$2"
}

# Header with no leading blank lines, for watch beats that pin it to the top row.
step_header_bars() {
    local emoji="$1" title="$2"
    echo -e "${RULE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${BOLD}${emoji} ${title}${NC}"
    echo -e "${RULE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# "Create a file": type `vim <file>` at the prompt, then type the file body.
type_file() {
    local file="$1" body="$2" line
    cursor_show
    printf '%b ' "${PROMPT}❯${NC}"
    pause "$PROMPT_PAUSE"
    seg_reset; hl_bash_reset; hl_bash "vim ${file}"
    type_segments
    printf '\n'
    cursor_hide
    pause "$CMD_PAUSE"
    while IFS= read -r line; do
        seg_reset; hl_python "$line"
        type_segments "$FILE_CHUNK"
        printf '\n'
        cursor_hide
    done <<< "$body"
    pause "$CMD_PAUSE"
}

# kubectl's AGE format (HumanDuration): seconds below 2 minutes, then XmYs.
# Sets AGE_STR instead of printing, so redraw loops don't spawn subshells.
fmt_age() {
    local s="$1"
    if (( s < 120 )); then
        AGE_STR="${s}s"
    elif (( s % 60 == 0 )); then
        AGE_STR="$(( s / 60 ))m"
    else
        AGE_STR="$(( s / 60 ))m$(( s % 60 ))s"
    fi
}

# In-place redraw: move the cursor up N lines so the next frame overwrites the
# last one. Every line is printed with el, which erases the old line first.
move_up() { printf '\033[%dA' "$1"; }
el() { printf '\033[K%b\n' "$1"; }

# =============================================================================
# Canned data from the recorded run (generated by fill_demo.py)
# =============================================================================
# Filled by fill_demo.py from ../runs (see its docstring for each source).
SETUP_SECS=109
SETUP_DL_T=43
SETUP_CONVERT_T=95
SETUP_MODEL='font-info/qwen3-4b-sft-SGLang-RL'
SETUP_DATA='zhuzilin/dapo-math-17k'
SETUP_TAIL='8.3G	/data/models/qwen3-4b-sft
7.6G	/data/models/qwen3-4b-sft_torch_dist
setup end 2026-11-18T22:29:18Z'
STORAGE_OUT='namespace/slime-retool created
persistentvolume/pv-lustre-slime-retool created
persistentvolumeclaim/slime-data created'
DATA_ROWS='17,398'
DGD_READY_T=91
KV_GREP_OUT='          - name: DYN_ROUTER_MODE
            value: kv
          - name: DYN_ROUTER_PREDICTED_TTL_SECS
            value: '"'"'5'"'"'
--
          - --page-size
          - '"'"'16'"'"'
          - --kv-events-config
          - '"'"'{"publisher":"zmq","endpoint":"tcp://*:5557","topic":""}'"'"''
DGD_APPLY_OUT='service/slime-sglang-engine-0 created
service/slime-sglang-engine-1 created
service/slime-sglang-engine-2 created
service/slime-sglang-engine-3 created
dynamographdeployment.nvidia.com/slime-sglang created'
WATCH_FROM_T=4
GENERATE_EXCERPT='for turn in range(TOOL_CONFIGS["max_turns"]):
    current_token_ids = prompt_tokens_ids + response_token_ids
    payload = {
        "input_ids": current_token_ids,
        "sampling_params": per_turn_sampling_params,
        "return_logprob": True,
        "rid": f"retool-{sample.index}-t{turn}-{uuid.uuid4().hex[:8]}",
    }
    output, timing = await _stream_generate(args, url, payload)
    ...
    response_token_ids += cur_response_token_ids
    next_obs, done, tool = await execute_predictions(cur_response)
    if done:
        break
    obs_tokens_ids = state.tokenizer(next_obs, add_special_tokens=False)["input_ids"]
    response_token_ids += obs_tokens_ids'
RAYJOB_GREP_OUT='    --rollout-external-engine-addrs slime-sglang-engine-0:30000 slime-sglang-engine-1:30000 
    --update-weight-transport disk
    --custom-update-weight-post-write-path weight_permissions.make_weights_readable
    --rollout-stop '"'"'</code>'"'"' '"'"'</tool_call>'"'"'
    --custom-generate-function-path generate_with_retool.generate
    --custom-rm-path generate_with_retool.reward_func'
RAYJOB_PODS_OUT='NAME                            READY   STATUS    RESTARTS   AGE
slime-retool-sgkxz              1/1     Running   0          2s
slime-retool-h4z4h-head-fwcws   1/1     Running   0          16s'
ENGINES_T=52
FIRST_SYNC_T=141
FIRST_SYNC_SECS='12.8'
DONE_T=1754
JOB_ID='slime-retool-8wmvp'
TRAJ_CMD='python3 tools/trajectory.py runs/run1 --sample 958 2 1'
TRAJ_HEAD='step 3 · sample 958 · 5 requests, all on worker-0 · answer 3162 = label 3162, reward +1.0'
TRAJ_NOTE1='Turns 2-5 found 93-98% of their prompt in worker-0'"'"'s cache and prefilled 31-47 new tokens each:'
TRAJ_NOTE2='the sandbox output (18-38 tokens) plus the tail that didn'"'"'t fill a 16-token cache block.'
TOTAL_TRAJ='2,560'
TOTAL_REQ='12,402'
RPT_ALL='4.8'
CACHED_ALL='21.3'
PROMPT_ALL='27.0'
HIT_ALL='79'
STAY_ALL='61'
REUSE_STAYED='98'
MOVED_ALL='39'
MOVED_RECOMPUTED='4.9'
COMPUTED_ALL='5.7'
ROUTED_PER_ENGINE='3,216 / 3,174 / 3,047 / 2,965'
POD_NAMES=('slime-sglang-0-frontend-cwzgh' 'slime-sglang-0-worker-0-qnggq' 'slime-sglang-0-worker-1-vwd6q' 'slime-sglang-0-worker-2-d52gk' 'slime-sglang-0-worker-3-xqkd8')
POD_CREATED_T=('4' '4' '4' '4' '5')
TR_T=('6' '6' '6' '6' '6' '10' '10' '10' '10' '10' '14' '14' '14' '14' '14' '25' '90' '90' '90' '90')
TR_I=('0' '1' '2' '3' '4' '0' '1' '2' '3' '4' '0' '1' '2' '3' '4' '0' '1' '2' '3' '4')
TR_S=('SchedulingGated' 'SchedulingGated' 'SchedulingGated' 'SchedulingGated' 'SchedulingGated' 'ContainerCreating' 'ContainerCreating' 'ContainerCreating' 'ContainerCreating' 'ContainerCreating' 'Running' 'Running' 'Running' 'Running' 'Running' 'Running' 'Running' 'Running' 'Running' 'Running')
TR_R=('0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '0/1' '1/1' '1/1' '1/1' '1/1' '1/1')
SIDECAR_LINES=('discovered SGLang ZMQ KV-event sources sources=1 block_size=16' 'discovered SGLang ZMQ KV-event sources sources=1 block_size=16' 'discovered SGLang ZMQ KV-event sources sources=1 block_size=16' 'discovered SGLang ZMQ KV-event sources sources=1 block_size=16')
ROLLOUT_T=('228' '413' '588' '740' '897' '1050' '1218' '1340' '1508' '1675')
TRAIN_T=('276' '451' '627' '779' '935' '1089' '1254' '1373' '1546' '1715')
SYNC_T=('288' '461' '637' '790' '945' '1099' '1264' '1383' '1556' '1725')
ROLL_SECS=('77' '116' '114' '92' '98' '95' '107' '66' '113' '107')
RPT=('4.6' '4.8' '5.4' '4.8' '4.3' '5.3' '4.4' '4.6' '4.9' '5.3')
HIT=('77' '78' '82' '79' '79' '80' '76' '77' '80' '81')
STAY=('60' '62' '63' '62' '64' '62' '59' '58' '60' '60')
TRAIN_SECS=('48' '37' '38' '38' '37' '39' '35' '33' '37' '39')
SYNC_SECS=('10.6' '9.6' '9.7' '9.8' '9.2' '9.3' '9.2' '9.4' '9.3' '9.3')
TRAJ_Q=('Q: How many positive integers have a square less than $10^7$?')
TRAJ_LINES=('turn 1 → worker-0 · prompt 264 · cached 224 (85%) · wrote 417 tokens, then a tool call' '   │ import math' '   │ sqrt_10_7 = math.sqrt(10**7)' '   │ … 1 more line' '   ⇢ Square root of 10^7: 3162.2776601683795' 'turn 2 → worker-0 · prompt 719 · cached 672 (93%) · wrote 184 tokens, then a tool call' '   │ n = 3162' '   │ square_n = n ** 2' '   │ … 1 more line' '   ⇢ Square of 3162: 9998244' 'turn 3 → worker-0 · prompt 929 · cached 896 (96%) · wrote 51 tokens, then a tool call' '   │ n = 3163' '   │ square_n = n ** 2' '   │ … 1 more line' '   ⇢ Square of 3163: 10004569' 'turn 4 → worker-0 · prompt 1,007 · cached 976 (97%) · wrote 543 tokens, then a tool call' '   │ import math' '   │ n_max = math.floor(math.sqrt(10**7))' '   │ … 1 more line' '   ⇢ Maximum n: 3162' 'turn 5 → worker-0 · prompt 1,568 · cached 1,536 (98%) · wrote 129 tokens: Answer: \boxed{3162}')
TRAJ_TURN_START=('0' '5' '10' '15' '20')
MILESTONES=('52' '141' '228' '413' '588' '740' '897' '1050' '1218' '1340' '1508' '1675' '276' '451' '627' '779' '935' '1089' '1254' '1373' '1546' '1715' '288' '461' '637' '790' '945' '1099' '1264' '1383' '1556' '1725' '1754')

# Table column formats, padded the way kubectl pads them (widest value + 3).
POD_FMT='%-34s%-8s%-10s%-11s%s'
DGD_FMT='%-15s%-8s%-10s%s'

# =============================================================================
# Beat 0 — Intro (loop-safe start frame)
# =============================================================================
beat0_intro() {
    clear_screen
    step_header "🚀" "Let's train Qwen3-4B on ReTool with Slime and Dynamo's KV-aware router!"
    local arrow="${NC}${BOLD} → ${NC}"
    pause "$NAR_PAUSE"
    printf '%b# Each step: Dynamo serves the rollouts%b' "$NAR" "$NC"; sleep 0.5
    printf '%b' "$arrow"; sleep 0.5
    printf '%bSlime trains on them%b' "$NAR" "$NC"; sleep 0.5
    printf '%b' "$arrow"; sleep 0.5
    printf '%bthe new weights sync back.%b\n' "$NAR" "$NC"
    narrate "ReTool is multi-turn: the model writes Python partway through a math solution, a sandbox" \
            "runs it, and the whole transcript goes back to the model as the next request." \
            "KV-aware routing sends each turn to the engine that already caches that transcript." \
            "One node, ${NUM}${BOLD}8 GPUs${NC}${NAR}: 4 SGLang engines in Dynamo, a 4-GPU Megatron-LM trainer on KubeRay."
    pause "$PAUSE_LONG"
}

# =============================================================================
# Beat 1 — Stage the model and data on a shared volume
# =============================================================================
SETUP_H=2
setup_render() {  # seconds since the Job started
    local t="$1" status
    fmt_age "$t"
    if (( t < SETUP_DL_T )); then
        status="Downloading ${SETUP_MODEL} and ${SETUP_DATA} from Hugging Face..."
    elif (( t < SETUP_CONVERT_T )); then
        status="Converting the checkpoint to torch_dist on 1 GPU (rotary base 5,000,000)..."
    else
        status="Creating /data/weight-updates and /data/turn-logs..."
    fi
    el "   ${STATUS}▸ ${status}${NC}"
    el "  ${DIM}⏩ fast-forwarding · ${AGE_STR} elapsed${NC}"
}

beat1_stage_data() {
    step_header "📦" "Step 1: Stage the Model and Data"
    narrate "Slime publishes a ReTool checkpoint: Qwen3-4B fine-tuned to call a Python tool mid-solution." \
            "With ${DATA_ROWS} DAPO-Math problems, it goes on a ReadWriteMany volume that every Pod mounts."
    echo ""
    show_command 'export NAMESPACE=slime-retool'
    show_command 'kubectl apply -f manifests/storage.yaml'
    printf '%s\n' "$STORAGE_OUT"
    pause "$PAUSE_SHORT"
    show_command 'kubectl apply -n $NAMESPACE -f manifests/setup-job.yaml'
    echo "job.batch/slime-retool-setup created"
    show_command 'kubectl wait -n $NAMESPACE --for=condition=complete job/slime-retool-setup'

    cursor_hide
    local t first=1
    for (( t=0; t<=SETUP_SECS; t++ )); do
        if (( first )); then first=0; else move_up "$SETUP_H"; fi
        setup_render "$t"
        sleep "$AGE_TICK"
    done
    move_up "$SETUP_H"
    el "job.batch/slime-retool-setup condition met"
    printf '\033[K'
    cursor_show
    pause "$PAUSE_SHORT"

    show_command 'kubectl logs -n $NAMESPACE job/slime-retool-setup --tail=3'
    printf '%s\n' "$SETUP_TAIL"
    echo ""
    narrate "SGLang serves the Hugging Face checkpoint. Megatron-LM starts from the torch_dist copy."
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 2 — Dynamo serves the rollouts: a DGD with four SGLang engines
# =============================================================================
# WATCH_H must equal the number of lines pods_render() emits: header + 5 Pods +
# blank + status + blank + progress = 10. Each Pod's status and READY follow the
# transitions recorded in ../runs/deploy/pods-watch.txt (TR_* arrays, by time).
WATCH_H=10
pods_render() {  # seconds since the DGD was applied
    local t="$1" i j line n=0
    local -a st rd
    for i in 0 1 2 3 4; do st[$i]="Pending"; rd[$i]="0/1"; done
    for (( j=0; j<${#TR_T[@]}; j++ )); do
        if (( ${TR_T[$j]} <= t )); then
            st[${TR_I[$j]}]=${TR_S[$j]}
            rd[${TR_I[$j]}]=${TR_R[$j]}
        fi
    done
    printf -v line "$POD_FMT" "NAME" "READY" "STATUS" "RESTARTS" "AGE"
    el "$line"
    for i in 0 1 2 3 4; do
        fmt_age $(( t - ${POD_CREATED_T[$i]} ))
        [[ "${rd[$i]}" == "1/1" ]] && n=$((n + 1))
        printf -v line "$POD_FMT" "${POD_NAMES[$i]}" "${rd[$i]}" "${st[$i]}" "0" "$AGE_STR"
        el "$line"
    done
    el ""
    fmt_age "$t"
    if (( n == 5 )); then
        el "   ${OK}✅ All 5 Pods ready: all four engines can generate${NC}"
    else
        el "   ${STATUS}▸ Starting the frontend and four engines (Qwen3-4B from /data, CUDA graphs)${NC} — ${n}/5 ready"
    fi
    el ""
    el "  ${DIM}⏩ fast-forwarding · ${AGE_STR} elapsed${NC}"
}

beat2_serve_rollouts() {
    local title="Step 2: Serve Rollouts with KV-Aware Routing"
    step_header "🚀" "$title"
    narrate "A DynamoGraphDeployment (DGD) runs a Dynamo frontend and four SGLang engines, one GPU each." \
            "Two frontend settings and two engine flags turn on KV-aware routing:"
    echo ""
    show_command "grep -m 4 -A1 -E '^ +- (name: DYN_ROUTER|--page-size|--kv-events)' manifests/dgd.yaml"
    printf '%s\n' "$KV_GREP_OUT"
    echo ""
    narrate "Each engine publishes KV-cache events; Dynamo's sidecar relays them to the frontend's router."
    echo ""
    show_command 'kubectl apply -n $NAMESPACE -f manifests/engine-services.yaml -f manifests/dgd.yaml'
    printf '%s\n' "$DGD_APPLY_OUT"
    pause "$PAUSE_SHORT"

    local cmd='watch kubectl get pods -n $NAMESPACE -l dynamo.nvidia.com/slime-role'
    local n1="Fast-forwarding while the engines load the model..."
    echo ""
    narrate "$n1"
    echo ""
    show_command "$cmd"

    clear_screen
    step_header_bars "🚀" "$title"
    echo -e "${NAR}# ${n1}${NC}"
    echo ""
    print_command "$cmd"
    echo ""
    cursor_hide
    local t first=1
    for (( t=WATCH_FROM_T; t<=DGD_READY_T + 2; t++ )); do
        if (( first )); then first=0; else move_up "$WATCH_H"; fi
        pods_render "$t"
        sleep "$AGE_TICK"
    done
    cursor_show
    pause "$PAUSE_SHORT"

    show_command 'kubectl get dgd -n $NAMESPACE slime-sglang'
    printf "${DGD_FMT}\n" "NAME" "READY" "BACKEND" "AGE"
    printf "${DGD_FMT}\n" "slime-sglang" "True" "sglang" "${DGD_READY_T}s"
    echo ""
    show_command_lines 'for w in 0 1 2 3; do kubectl logs -n $NAMESPACE -l dynamo.nvidia.com/slime-role=worker-$w \' \
                       "    | grep -o 'discovered SGLang ZMQ KV-event sources.*block_size=16'; done"
    printf '%s\n' "${SIDECAR_LINES[@]}"
    echo ""
    narrate "Every engine streams its cache contents to the router in 16-token blocks."
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 3 — ReTool's rollout function, one stream per turn
# =============================================================================
beat3_functions() {
    step_header "🧩" "Step 3: ReTool's Multi-Turn Rollout, Streamed through Dynamo"
    narrate "Slime loads ReTool's generate function. One trajectory: up to 16 requests, one per code run."
    echo ""
    type_file "generate_with_retool.py" "$GENERATE_EXCERPT"
    echo ""
    narrate "Every request resends the whole transcript as token IDs. The engine that served the last turn" \
            "already holds all of it except the sandbox output, so routing there skips the re-prefill."
    narrate "This kit's copy streams each turn (Dynamo 1.6 serves /generate as a stream), stops at" \
            "</tool_call>, fixes the sandbox, and logs every turn. The README lists each change."
    echo ""
    show_command 'kubectl apply -n $NAMESPACE -f manifests/functions.yaml'
    echo "configmap/slime-retool-functions created"
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 4 — Slime trains in a KubeRay RayJob
# =============================================================================
beat4_rayjob() {
    step_header "🧠" "Step 4: Train with Megatron-LM on KubeRay"
    narrate "A RayJob runs Slime's train.py with ReTool's arguments: Megatron-LM on 4 GPUs (TP 2 × CP 2)." \
            "These flags point Slime at the Dynamo engines and at the ReTool functions:"
    echo ""
    show_command_lines "grep -E '^ +--(rollout-ext|custom-|rollout-stop|update-weight-t)' manifests/rayjob.yaml \\" \
                       "    | cut -c1-92"
    printf '%s\n' "$RAYJOB_GREP_OUT"
    echo ""
    narrate "${NUM}${BOLD}32 problems × 8 samples${NC}${NAR} per step. GRPO scores each answer against its 7 siblings'."
    echo ""
    show_command 'kubectl apply -n $NAMESPACE -f manifests/rayjob.yaml'
    echo "rayjob.ray.io/slime-retool created"
    echo ""
    show_command "kubectl get pods -n \$NAMESPACE | grep -E 'NAME|slime-retool-'"
    printf '%s\n' "$RAYJOB_PODS_OUT"
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 5 — One real trajectory, turn by turn
# =============================================================================
beat5_trajectory() {
    step_header "🔍" "Step 5: One Trajectory, Turn by Turn"
    narrate "Every turn was logged with its tokens, code, and sandbox output; the frontend logged its engine."
    echo ""
    show_command "$TRAJ_CMD"
    local i k start end
    pause "$PAUSE_SHORT"
    printf '%s\n' "$TRAJ_HEAD"
    printf '%s\n' "${TRAJ_Q[@]}"
    pause "$PAUSE_SHORT"
    for (( k=0; k<${#TRAJ_TURN_START[@]}; k++ )); do
        start=${TRAJ_TURN_START[$k]}
        if (( k + 1 < ${#TRAJ_TURN_START[@]} )); then end=${TRAJ_TURN_START[$((k + 1))]}; else end=${#TRAJ_LINES[@]}; fi
        for (( i=start; i<end; i++ )); do
            printf '%s\n' "${TRAJ_LINES[$i]}"
        done
        pause "$TURN_PAUSE"
    done
    echo ""
    narrate "${TRAJ_NOTE1}" "${TRAJ_NOTE2}"
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 6 — The RL loop: rollout → train → sync, 10 times
# =============================================================================
# LOOP_H MUST equal the number of lines loop_render() emits: 2 milestone lines +
# blank + header + 10 steps + blank + status = 16. With the 8 pinned lines above
# it, that fills 24 of 25 rows.
LOOP_H=16

pad() { printf -v "$1" "%-${2}s" "$3"; }

loop_milestones() {  # t
    local t="$1"
    if (( t >= ENGINES_T )); then
        el "  ${OK}✓${NC} Slime attached to the 4 Dynamo engines (slime-sglang-engine-0..3:30000)"
    else
        el "  ${DIM}▸ Ray starts Slime's driver and the Megatron-LM trainer on the head Pod...${NC}"
    fi
    if (( t >= FIRST_SYNC_T )); then
        el "  ${OK}✓${NC} Initial weight sync: v1 → 4 engines in ${FIRST_SYNC_SECS}s; rollouts start from the trainer's weights"
    elif (( t >= ENGINES_T )); then
        el "  ${DIM}▸ Loading the torch_dist checkpoint, then syncing it to the engines...${NC}"
    else
        el ""
    fi
}

loop_row() {  # step t
    local k="$1" t="$2" start c0 c1 c2 c3 sync
    local r_txt="" r_col="$NC" h_txt="" h_col="$NC" t_txt="" t_col="$NC" s_txt="" s_col="$NC"
    if (( k == 0 )); then start=$FIRST_SYNC_T; else start=${SYNC_T[$((k - 1))]}; fi
    if (( t < start )); then
        el ""
        return 0
    fi
    if (( t >= ${ROLLOUT_T[$k]} )); then
        r_txt="${ROLL_SECS[$k]} s, ${RPT[$k]} requests each"
        h_txt="${HIT[$k]}% hit, ${STAY[$k]}% stayed"
    else
        r_txt="multi-turn rollouts..."; r_col="$DIM"
    fi
    if (( t >= ${TRAIN_T[$k]} )); then
        t_txt="${TRAIN_SECS[$k]} s"
    elif (( t >= ${ROLLOUT_T[$k]} )); then
        t_txt="training..."; t_col="$DIM"
    fi
    sync="v$((k + 2))"
    if (( t >= ${SYNC_T[$k]} )); then
        s_txt="${sync} → 4 engines ${SYNC_SECS[$k]} s"; s_col="$OK"
    elif (( t >= ${TRAIN_T[$k]} )); then
        s_txt="writing ${sync}..."; s_col="$DIM"
    fi
    printf -v c0 '  %-6s' "$k"
    pad c1 28 "$r_txt"
    pad c2 24 "$h_txt"
    pad c3 10 "$t_txt"
    el "${c0}${r_col}${c1}${NC}${h_col}${c2}${NC}${t_col}${c3}${NC}${s_col}${s_txt}${NC}"
}

loop_status() {  # t
    local t="$1"
    fmt_age "$t"
    if (( t >= DONE_T )); then
        el "  ${OK}✅ Job '${JOB_ID}' succeeded · ${AGE_STR} after the RayJob started${NC}"
    else
        el "  ${DIM}⏩ fast-forwarding · ${AGE_STR} elapsed${NC}"
    fi
}

loop_render() {  # t
    local t="$1" k header
    loop_milestones "$t"
    el ""
    printf -v header '  %-6s%-28s%-24s%-10s%s' "STEP" "ROLLOUT (256 trajectories)" "PREFIX CACHE" "TRAIN" "WEIGHT SYNC (disk)"
    el "${BOLD}${header}${NC}"
    for (( k=0; k<10; k++ )); do
        loop_row "$k" "$t"
    done
    el ""
    loop_status "$t"
}

EVENTS=0
count_events() {  # t
    local t="$1" m n=0
    for m in "${MILESTONES[@]}"; do
        if (( m <= t )); then n=$((n + 1)); fi
    done
    EVENTS=$n
}

beat6_rl_loop() {
    local title="Step 6: Rollout → Train → Sync, 10 Times"
    local cmd='kubectl logs -n $NAMESPACE -f job/slime-retool'
    local note="   # condensed, with the turn log's routing view"
    local n1="Each step: 256 multi-turn trajectories through Dynamo → one training step → new weights."

    step_header "🔁" "$title"
    narrate "$n1"
    echo ""
    show_command "$cmd" "$note"

    clear_screen
    step_header_bars "🔁" "$title"
    echo -e "${NAR}# ${n1}${NC}"
    echo ""
    print_command "$cmd" "$note"
    echo ""
    cursor_hide

    local t first=1 last=-1
    for (( t=30; t<=DONE_T; t++ )); do
        count_events "$t"
        if (( first )); then
            first=0
            loop_render "$t"
        elif (( EVENTS != last )); then
            move_up "$LOOP_H"
            loop_render "$t"
        else
            move_up 1
            loop_status "$t"
        fi
        last=$EVENTS
        if (( t >= FIRST_SYNC_T && t <= ${SYNC_T[9]} )); then
            sleep "$STEP_TICK"
        else
            sleep "$FAST_TICK"
        fi
    done
    cursor_show
    pause "$PAUSE_MED"
    echo ""
    narrate "hit: the share of prompt tokens the engines served from their prefix cache." \
            "stayed: follow-up turns routed to the engine that served the trajectory's previous turn." \
            "The router weighs each turn's cached history against engine load: the other follow-ups" \
            "went to a less loaded engine and recomputed their history there."
    pause "$PAUSE_MED"
}

# =============================================================================
# Beat 7 — Closing card (loop-safe end frame)
# =============================================================================
beat7_closing() {
    step_header "🎉" "Done: 10 GRPO Steps of Multi-Turn ReTool, Every Turn Routed by KV Cache"
    printf "   %-42s%s\n" "Trajectories / requests" "${TOTAL_TRAJ} / ${TOTAL_REQ} (${RPT_ALL} per trajectory)"
    printf "   %-42s%s\n" "Prompt tokens served from the cache" "${CACHED_ALL}M of ${PROMPT_ALL}M (${HIT_ALL}%)"
    printf "   %-42s%s\n" "Follow-up turns on their previous engine" "${STAY_ALL}%, with ${REUSE_STAYED}% of their prompt tokens cached"
    printf "   %-42s%s\n" "Follow-up turns routed elsewhere" "${MOVED_ALL}%, recomputing ${MOVED_RECOMPUTED}M of the ${COMPUTED_ALL}M uncached"
    printf "   %-42s%s\n" "Requests per engine (worker-0/1/2/3)" "${ROUTED_PER_ENGINE}"
    echo ""
    echo -e "   ${NAR}One run, no round-robin baseline: these numbers show cache reuse, not a speedup.${NC}"
    echo ""
    echo -e "   ${PROMPT}${BOLD}Slime trains. Dynamo routes every turn by KV cache. New weights every step. 🔁${NC}"
    echo ""
    hold "$CLOSING_HOLD"
}

# =============================================================================
# Main
# =============================================================================
main() {
    cursor_show
    trap 'cursor_show' EXIT
    beat0_intro
    beat1_stage_data
    beat2_serve_rollouts
    beat3_functions
    beat4_rayjob
    beat5_trajectory
    beat6_rl_loop
    beat7_closing
}

main "$@"
