# shellcheck shell=bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# lib.sh: helpers for the team replay of the Slime GSM8K + Dynamo tutorial run on AKS.
#
# run.sh sources this file; build.sh concatenates it into slime-gsm8k-team-demo.sh.
# FRAMEWORK.md documents every helper and knob. The helpers up to "New helpers" are
# v1's (the ReTool kit's demo/slime-retool-demo.template.sh), ported with two changes: every sleep goes
# through nap, and palette variables hold real escape characters, so text is printed
# with printf '%s' and a backslash in it (\boxed{3162}) prints as written.
#
# Runs on macOS /bin/bash 3.2: no associative arrays, no ${x,,}, no mapfile, and an
# empty array must be expanded as ${a[@]+"${a[@]}"} under set -u.

# =============================================================================
# Screen and locale
# =============================================================================
COLS="${COLS:-120}"
ROWS="${ROWS:-32}"

# Count characters rather than bytes whatever the caller's locale, and keep pattern
# ranges such as [A-Za-z] ASCII-only.
unset LC_ALL 2>/dev/null || true
export LANG=en_US.UTF-8 LC_CTYPE=en_US.UTF-8 LC_COLLATE=C

# =============================================================================
# Timing knobs, in seconds unless noted. Each one can be set from the environment.
# =============================================================================
TIME_SCALE="${TIME_SCALE:-98}"          # percent applied to every nap (100 = as written; 98 for the
                                        # GSM8K demo: its whole pace, 2% faster)
NAP_CHUNK="${NAP_CHUNK:-1}"             # longest silent sleep: longer naps emit an invisible
                                        # keepalive each chunk, so idle-time limits keep holds
NAP_CATCHUP="${NAP_CATCHUP:-1}"         # 1: when the run falls behind its planned time (each nap
                                        # forks sleep, and a loaded machine runs late), cut up to
                                        # 40% from the next holds of 0.2 s or more until it catches up
CHAR_SPEED="${CHAR_SPEED:-0.011}"       # per character of a typed shell command
TYPE_GROUP="${TYPE_GROUP:-4}"           # shell typing: characters drawn per nap (the nap is
                                        # CHAR_SPEED x TYPE_GROUP: the same speed, fewer sleeps)
PROMPT_PAUSE="${PROMPT_PAUSE:-0.5}"     # on an empty prompt before typing
CMD_PAUSE="${CMD_PAUSE:-0.6}"           # after a command is typed
PAUSE_SHORT="${PAUSE_SHORT:-1.0}"
PAUSE_MED="${PAUSE_MED:-1.2}"
PAUSE_LONG="${PAUSE_LONG:-1.8}"
NAR_LEAD="${NAR_LEAD:-0.3}"             # before each narration line appears
NAR_WPM="${NAR_WPM:-240}"               # reading speed (words per minute): narration, cards, headers
CALLOUT_WPM="${CALLOUT_WPM:-220}"       # reading speed for vim callouts, which vanish when done
NAR_FLOOR="${NAR_FLOOR:-1.0}"           # shortest hold after a narration line
NAR_W="${NAR_W:-116}"                   # narrate_wrap line width, without the "# "
HEADER_HOLD="${HEADER_HOLD:-0.8}"       # after step_header (plus its subtitles' reading time)
CARD_LEAD="${CARD_LEAD:-0.4}"           # before a card's top border
CARD_FLOOR="${CARD_FLOOR:-1.5}"         # shortest hold after each card paragraph
CARD_W="${CARD_W:-114}"                 # card width, borders included
CARD_INDENT="${CARD_INDENT:-2}"         # columns left of a card
RULE_W="${RULE_W:-100}"                 # width of step_header's ━ rules
FF_TICK="${FF_TICK:-0.04}"              # per frame of ff_run
VIM_CHAR="${VIM_CHAR:-0.03}"            # per character at normal typing speed (×1)
VIM_GROUP="${VIM_GROUP:-2}"             # vim typing: ticks drawn per nap (the nap is VIM_CHAR x
                                        # VIM_GROUP: the same speed, fewer sleeps)
VIM_FAST="${VIM_FAST:-16}"              # characters per tick at fast speed ("⏩ typing ×16")
VIM_COLLAPSE_X="${VIM_COLLAPSE_X:-40}"  # characters per tick in vim_collapse
VIM_EOL_PAUSE="${VIM_EOL_PAUSE:-0.15}"  # before Enter, at normal speed
VIM_OPEN_HOLD="${VIM_OPEN_HOLD:-1.0}"   # on the empty buffer and "FILE" [New]
VIM_KEY_PAUSE="${VIM_KEY_PAUSE:-0.5}"   # around single keystrokes: i, Esc, Enter
VIM_KEY_CHAR="${VIM_KEY_CHAR:-0.12}"    # per character of :wq
VIM_WRITTEN_HOLD="${VIM_WRITTEN_HOLD:-1.0}"  # on "FILE" [New] NL, NB written
VIM_FOLD="${VIM_FOLD:-1}"               # 1: vim_collapse folds its lines into one row afterwards
VIM_FOLD_PAUSE="${VIM_FOLD_PAUSE:-0.5}" # between the collapsed typing and the fold
VIM_RULER_LIVE="${VIM_RULER_LIVE:-0}"   # 1: the ruler follows every typed tick. 0: it updates
                                        # when a line is done, so a typed line reaches the cast
                                        # unbroken and tools/redact_cast.py can mask its IPs
CALLOUT_LEAD="${CALLOUT_LEAD:-0.3}"     # before a callout appears
CALLOUT_FLOOR="${CALLOUT_FLOOR:-2.5}"   # shortest callout hold (otherwise its reading time)

# =============================================================================
# Palette: each escape picks a syntax role, not a hue (v1's roles, same slots)
# =============================================================================
# apply-fern-code-theme.py maps the 16 slots to the dark fern-code colors; the docs'
# TerminalDemo maps them to the light or dark theme. Real escape characters, so print
# them with printf '%s'.
RULE=$'\033[0;30m'     # 0  step header rules
PROP=$'\033[0;31m'     # 1  Python attribute names; YAML keys
STR=$'\033[0;32m'      # 2  quoted strings; YAML values; success
CMD=$'\033[0;33m'      # 3  command names; Python class names
FUNC=$'\033[0;34m'     # 4  shell builtins and Python function names
DECL=$'\033[0;35m'     # 5  declaration keywords: export, async, def
OP=$'\033[0;36m'       # 6  operators and control keywords: | = for do import return
NAR=$'\033[0;37m'      # 7  narration and comments
DIM=$'\033[0;90m'      # 8  progress and fast-forward notes
NUM=$'\033[0;91m'      # 9  numbers called out in narration; in-progress status; literals
ARG=$'\033[0;92m'      # 10 unquoted arguments and flags
PARAM=$'\033[0;93m'    # 11 Python parameters
CALLARG=$'\033[0;94m'  # 12 Python call arguments
QUOTE=$'\033[0;95m'    # 13 quote marks
PUNCT=$'\033[0;96m'    # 14 punctuation
PROMPT=$'\033[0;97m'   # 15 the prompt
BOLD=$'\033[1m'
NC=$'\033[0m'
OK="$STR"              # success marks share the string green
STATUS="$NUM"          # in-progress status shares the number color

# Roles built from the slots above (no new colors).
LNR="$DIM"                   # vim line numbers
EOB="$FUNC"                  # vim's ~ rows after the end of the buffer
MARK=$'\033[0;1;33m'         # ▶ gutter mark and the marked lines' numbers
STL=$'\033[0;1;7m'           # vim status line: bold, reverse video
FOLDC=$'\033[0;36;40m'       # vim fold row: cyan on the rule slot
POP=$'\033[0;40m'            # callout box: default text on the rule slot
POPB=$'\033[0;37;40m'        # callout border
POPMARK=$'\033[0;1;33;40m'   # callout label (▶ lines A–B)
CARDB="$DIM"                 # card border

# Table column formats from v1 (kubectl pads columns to the widest value + 3).
CRD_FMT='%-36s%s'
POD_FMT='%-34s%-8s%-10s%-11s%s'
DGD_FMT='%-15s%-8s%-10s%s'

# =============================================================================
# Time: nap is the only sleep
# =============================================================================
CURSOR_ON=1
cursor_show() { CURSOR_ON=1; printf '\033[?25h'; }
cursor_hide() { CURSOR_ON=0; printf '\033[?25l'; }

# An invisible output event: re-send the cursor's current visibility. Recorders and
# players cap idle gaps (2 s, 1.5 s), so a long hold needs an event every second.
_keepalive() { if (( CURSOR_ON )); then printf '\033[?25h'; else printf '\033[?25l'; fi; }

_to_us() {  # _to_us SECONDS → _US (integer microseconds)
    local s="$1" int frac
    case "$s" in
        *.*) int="${s%%.*}"; frac="${s#*.}" ;;
        *) int="$s"; frac="" ;;
    esac
    [[ -n "$int" ]] || int=0
    frac="${frac}000000"
    _US=$(( 10#$int * 1000000 + 10#${frac:0:6} ))
}

_us_str() { printf -v _SECS '%d.%06d' $(( $1 / 1000000 )) $(( $1 % 1000000 )); }

# nap SECONDS: sleep (TIME_SCALE percent of it). FAST=1: return at once.
# DRYRUN=1: print an invisible token with the seconds instead, for check.py.
# NAP_CATCHUP=1: _NAP_PLAN adds up the requested time. When the wall clock ($SECONDS,
# whole seconds, so 2 s of slack) is behind it, the next naps give up time until it
# catches up: a hold of 0.2 s or more up to 40% of itself, a typing or animation tick
# up to 60%. Each nap forks sleep and a loaded machine runs late; this keeps a recording
# about as long as check.py's duration (measured: +2.6% at a load average of 3).
_NAP_PLAN=0
_NAP_T0=$SECONDS
nap() {
    local us chunk late cut
    _to_us "${1:-0}"; us=$_US
    if (( TIME_SCALE != 100 )); then us=$(( us * TIME_SCALE / 100 )); fi
    if (( us <= 0 )); then return 0; fi
    if [[ "${FAST:-0}" != 0 ]]; then return 0; fi
    if [[ "${DRYRUN:-0}" != 0 ]]; then
        _us_str "$us"; printf '\033]7770;nap;%s\007' "$_SECS"
        return 0
    fi
    if [[ "$NAP_CATCHUP" != 0 ]]; then
        late=$(( (SECONDS - _NAP_T0) * 1000000 - _NAP_PLAN - 2000000 ))
        _NAP_PLAN=$(( _NAP_PLAN + us ))
        if (( late > 0 )); then
            if (( us >= 200000 )); then cut=$(( us * 2 / 5 )); else cut=$(( us * 3 / 5 )); fi
            if (( cut > late )); then cut=$late; fi
            us=$(( us - cut ))
        fi
    fi
    _to_us "$NAP_CHUNK"; chunk=$_US
    if (( us >= 300000 )); then _keepalive; fi
    while (( us > chunk )); do
        _us_str "$chunk"; sleep "$_SECS"
        _keepalive
        us=$(( us - chunk ))
    done
    _us_str "$us"; sleep "$_SECS"
}

pause() { nap "${1:-2}"; }   # v1 name
hold() { nap "$1"; }         # v1 name: hold the screen for N seconds

# =============================================================================
# Text measurement
# =============================================================================
# _strip_esc TEXT → _S: TEXT without CSI escape sequences.
_strip_esc() {
    local s="$1" out=""
    while [[ "$s" == *$'\033'* ]]; do
        out+="${s%%$'\033'*}"
        s="${s#*$'\033'}"
        s="${s#*[A-Za-z]}"
    done
    _S="$out$s"
}

# Symbols below U+2C00 that terminals draw two columns wide (East Asian Width W or F).
# Characters outside the BMP (most emoji) are wide too; _dwidth checks their length.
WIDE_CHARS='⌚⌛〈〉⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕'
_bytes() { local LC_ALL=C; _B=${#1}; }

# _dwidth TEXT → _W: display columns of TEXT (no escapes in TEXT).
_dwidth() {
    local s="$1" rest k c
    _W=${#s}
    rest="${s//[ -~]/}"
    if [[ -z "$rest" ]]; then return 0; fi
    for (( k=0; k<${#rest}; k++ )); do
        c="${rest:k:1}"
        case "$WIDE_CHARS" in *"$c"*) _W=$(( _W + 1 )); continue ;; esac
        case "$c" in $'\xef\xb8\x8f'|$'\xe2\x80\x8d') _W=$(( _W - 1 )); continue ;; esac
        _bytes "$c"
        if (( _B == 4 )); then _W=$(( _W + 1 )); fi
    done
}

# dwidth TEXT → _W: display columns, escapes ignored.
dwidth() { _strip_esc "$1"; _dwidth "$_S"; }

_rep() {  # _rep CHAR N → _REP
    local n="$2" s=""
    while (( n > 0 )); do s+="$1"; n=$(( n - 1 )); done
    _REP=$s
}

# padr VAR WIDTH TEXT: TEXT padded with spaces to WIDTH display columns.
padr() {
    dwidth "$3"
    local n=$(( $2 - _W ))
    if (( n < 0 )); then n=0; fi
    printf -v "$1" '%s%*s' "$3" "$n" ''
}

# _wrap WIDTH PARAGRAPH → _WL=(lines): greedy word wrap at WIDTH display columns.
# An escape inside the paragraph carries over to the next line.
_wrap() {
    local width="$1" rest="$2" w line="" lw=0 ww sgr="" tail
    _WL=()
    if [[ -z "$rest" ]]; then _WL=(""); return 0; fi
    while [[ -n "$rest" ]]; do
        if [[ "$rest" == *" "* ]]; then w="${rest%% *}"; rest="${rest#* }"; else w="$rest"; rest=""; fi
        if [[ -z "$w" ]]; then continue; fi
        dwidth "$w"; ww=$_W
        if (( lw == 0 )); then
            line="$line$w"; lw=$ww
        elif (( lw + 1 + ww <= width )); then
            line+=" $w"; lw=$(( lw + 1 + ww ))
        else
            _WL+=("$line"); line="$sgr$w"; lw=$ww
        fi
        if [[ "$w" == *$'\033'* ]]; then
            tail="${w##*$'\033'}"
            sgr=$'\033'"${tail%%m*}m"
        fi
    done
    _WL+=("$line")
}

# _words TEXT... → _WORDS: the number of words (runs of non-blanks), escapes ignored, so
# padded columns and double spaces don't count as words.
_words() {
    local IFS=$' \t\n' f=0
    local -a w
    _strip_esc "$*"
    case $- in *f*) f=1 ;; esac
    set -f
    w=($_S)
    if (( ! f )); then set +f; fi
    _WORDS=${#w[@]}
}

# read_hold_at WPM FLOOR TEXT...: hold for TEXT's reading time at WPM words per minute,
# at least FLOOR seconds.
read_hold_at() {
    local wpm="$1" floor="$2" us
    shift 2
    _words "$@"
    us=$(( _WORDS * 60000000 / wpm ))
    _to_us "$floor"
    if (( us < _US )); then us=$_US; fi
    _us_str "$us"
    nap "$_SECS"
}

# read_hold FLOOR TEXT...: the same at NAR_WPM.
read_hold() {
    local floor="$1"
    shift
    read_hold_at "$NAR_WPM" "$floor" "$@"
}

die() { printf 'demo: %s\n' "$*" >&2; exit 1; }

# Helpers that draw on the shell screen stop here if vim is still open.
_shell_only() { if (( ${VIM_ACTIVE:-0} )); then die "$1: vim is open (call vim_close first)"; fi; }

# =============================================================================
# v1 helpers
# =============================================================================
clear_screen() { _shell_only clear_screen; printf '\033[H\033[2J\033[3J'; }

# Narration: each line appears after NAR_LEAD and stays for its reading time.
narrate() {
    local line
    if (( ${VIM_ACTIVE:-0} )); then vim_note "$@"; return 0; fi   # inside vim: a callout
    for line in "$@"; do
        nap "$NAR_LEAD"
        printf '%s\n' "${NAR}# ${line}${NC}"
        read_hold "$NAR_FLOOR" "$line"
    done
}

# narrate_wrap PARAGRAPH...: word-wrap each paragraph to NAR_W columns, then narrate.
narrate_wrap() {
    local p
    local -a lines=()
    for p in "$@"; do
        _wrap "$NAR_W" "$p"
        lines+=("${_WL[@]}")
    done
    narrate ${lines[@]+"${lines[@]}"}
}

# -----------------------------------------------------------------------------
# Syntax highlighting. hl_bash, hl_python, and hl_yaml split one line into
# segments the way Fern's code blocks color bash, Python, and YAML, appending each
# segment's text to SEG_TXT and its role's escape to SEG_COL.
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
                while [[ "${s:j:1}" == [A-Za-z0-9_] ]]; do j=$((j + 1)); done
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
                seg "$QUOTE" "'"; seg "$STR" "${s:i+1:j-i-1}"
                if (( j < n )); then seg "$QUOTE" "'"; fi
                HB_CMD=0; i=$((j + 1))
                continue ;;
            '|') seg "$OP" '|'; HB_CMD=1; i=$((i + 1)); continue ;;
            ';') seg "$PUNCT" ';'; HB_CMD=1; i=$((i + 1)); continue ;;
            ')') seg "$QUOTE" ')'; HB_SUB=0; i=$((i + 1)); continue ;;
            '\') seg "$NC" '\'; i=$((i + 1)); continue ;;
            '$')
                j=$((i + 1))
                while [[ "${s:j:1}" == [A-Za-z0-9_] ]]; do j=$((j + 1)); done
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

# Python. v1's rules, plus state that carries across lines (open parentheses, a
# def's parameter list, a triple-quoted string), comments, numbers, decorators,
# string prefixes, and the rest of the keywords.
HP_TQ=""
HP_DEPTH=0
HP_PARAMS=0
hl_python_reset() { HP_TQ=""; HP_DEPTH=0; HP_PARAMS=0; }

hl_python() {
    local s="$1" n=${#1} i=0 j c w prev next role q3 rest
    local import_line=0 after_def=0 after_class=0 params=$HP_PARAMS depth=$HP_DEPTH
    if [[ -n "$HP_TQ" ]]; then
        if [[ "$s" == *"$HP_TQ"* ]]; then
            rest="${s%%"$HP_TQ"*}"
            if [[ -n "$rest" ]]; then seg "$STR" "$rest"; fi
            seg "$QUOTE" "$HP_TQ"; i=$(( ${#rest} + 3 )); HP_TQ=""
        else
            if [[ -n "$s" ]]; then seg "$STR" "$s"; fi
            return 0
        fi
    fi
    rest="${s#"${s%%[! ]*}"}"
    case "$rest" in "import "*|"from "*) import_line=1 ;; esac
    while (( i < n )); do
        c=${s:i:1}
        if [[ "$c" == " " ]]; then
            j=$i
            while [[ "${s:j:1}" == " " ]]; do j=$((j + 1)); done
            seg "$NC" "${s:i:j-i}"; i=$j
            continue
        fi
        if [[ "$c" == "#" ]]; then
            seg "$NAR" "${s:i}"
            break
        fi
        q3="${s:i:3}"
        if [[ "$q3" == '"""' || "$q3" == "'''" ]]; then
            rest="${s:i+3}"
            seg "$QUOTE" "$q3"
            if [[ "$rest" == *"$q3"* ]]; then
                w="${rest%%"$q3"*}"
                if [[ -n "$w" ]]; then seg "$STR" "$w"; fi
                seg "$QUOTE" "$q3"; i=$(( i + 6 + ${#w} ))
            else
                if [[ -n "$rest" ]]; then seg "$STR" "$rest"; fi
                HP_TQ=$q3; i=$n
            fi
            continue
        fi
        if [[ "$c" == '"' || "$c" == "'" ]]; then
            j=$((i + 1))
            while (( j < n )) && [[ "${s:j:1}" != "$c" ]]; do
                if [[ "${s:j:1}" == '\' ]]; then j=$((j + 1)); fi
                j=$((j + 1))
            done
            if (( j > n )); then j=$n; fi
            seg "$QUOTE" "$c"; seg "$STR" "${s:i+1:j-i-1}"
            if (( j < n )); then seg "$QUOTE" "$c"; fi
            i=$((j + 1))
            continue
        fi
        if [[ "$c" == [0-9] ]]; then
            j=$i
            while (( j < n )) && [[ "${s:j:1}" == [0-9a-zA-Z_.] ]]; do j=$((j + 1)); done
            seg "$NUM" "${s:i:j-i}"; i=$j
            continue
        fi
        w="${s:0:i}"
        if [[ "$c" == "@" && -z "${w// /}" ]]; then
            j=$((i + 1))
            while [[ "${s:j:1}" == [A-Za-z0-9_.] ]]; do j=$((j + 1)); done
            seg "$FUNC" "${s:i:j-i}"; i=$j
            continue
        fi
        if [[ "$c" == [A-Za-z_] ]]; then
            j=$i
            while [[ "${s:j:1}" == [A-Za-z0-9_] ]]; do j=$((j + 1)); done
            w=${s:i:j-i}
            next=${s:j:1}
            prev=""
            if (( i > 0 )); then prev=${s:i-1:1}; fi
            case "$w" in
                import|from|as|return|await|if|elif|else|for|while|in|not|and|or|is|with|try|except|finally|raise|pass|break|continue|lambda|yield|del|global|nonlocal|assert) role=$OP ;;
                async|def|class)
                    role=$DECL
                    if [[ "$w" == def ]]; then after_def=1; fi
                    if [[ "$w" == class ]]; then after_class=1; fi ;;
                None|True|False) role=$NUM ;;
                *)
                    if [[ "$next" == '"' || "$next" == "'" ]] &&
                       [[ "$w" == [fFrRbBuU] || "$w" == [rR][bBfF] || "$w" == [bBfF][rR] ]]; then
                        role=$DECL
                    elif (( after_def )); then
                        role=$FUNC; after_def=0; params=1
                    elif (( after_class )); then
                        role=$CMD; after_class=0
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
    if (( depth < 0 )); then depth=0; fi
    HP_DEPTH=$depth
    HP_PARAMS=$params
    return 0
}

# Type the segments, CHAR_SPEED per character, or CHUNK times faster (default 1). To
# fork fewer sleeps, TYPE_GROUP characters share one nap: same speed, coarser steps.
type_segments() {
    local chunk="${1:-1}" k i t count=0 every tick
    every=$(( chunk * TYPE_GROUP ))
    _to_us "$CHAR_SPEED"; _us_str $(( _US * TYPE_GROUP )); tick=$_SECS
    cursor_show
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do
        t=${SEG_TXT[$k]}
        printf '%s' "${SEG_COL[$k]}"
        for (( i=0; i<${#t}; i++ )); do
            printf '%s' "${t:i:1}"
            count=$((count + 1))
            if (( count % every == 0 )); then nap "$tick"; fi
        done
    done
    printf '%s' "$NC"
}

# Print the segments at once.
print_segments() {
    local k
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do
        printf '%s%s' "${SEG_COL[$k]}" "${SEG_TXT[$k]}"
    done
    printf '%s' "$NC"
}

# A shell command: draw the prompt, linger, type the command, linger.
# $2 = optional comment printed after the command.
show_command() {
    _shell_only show_command
    cursor_show
    printf '%s ' "${PROMPT}❯${NC}"
    nap "$PROMPT_PAUSE"
    seg_reset; hl_bash_reset; hl_bash "$1"
    type_segments
    if [[ -n "${2:-}" ]]; then
        printf '%s' "${NAR}${2}${NC}"
    fi
    printf '\n'
    cursor_hide
    nap "$CMD_PAUSE"
}

# A command that spans several lines: the first line follows the prompt and
# each continuation line is typed below it.
show_command_lines() {
    _shell_only show_command_lines
    cursor_show
    printf '%s ' "${PROMPT}❯${NC}"
    nap "$PROMPT_PAUSE"
    hl_bash_reset
    local line
    for line in "$@"; do
        seg_reset; hl_bash "$line"
        type_segments
        printf '\n'
        cursor_hide
    done
    nap "$CMD_PAUSE"
}

# The same prompt line, printed at once. Used to re-pin a command after a hard cut.
print_command() {
    printf '%s ' "${PROMPT}❯${NC}"
    seg_reset; hl_bash_reset; hl_bash "$1"
    print_segments
    printf '%s\n' "${2:+${NAR}${2}${NC}}"
}

# Step header: two blank lines, the rules and title, optional subtitle lines, then
# a hold of HEADER_HOLD plus the subtitles' reading time.
_rep '━' "$RULE_W"; _RULE_STR=$_REP
step_header() {
    _shell_only step_header
    printf '\n\n'
    step_header_bars "$@"
    if (( $# > 2 )); then
        shift 2
        read_hold "$HEADER_HOLD" "$@"
    else
        nap "$HEADER_HOLD"
    fi
}

# Header with no leading blank lines and no hold, for re-pinning it to the top row.
step_header_bars() {
    local emoji="$1" title="$2" sub
    shift 2
    printf '%s%s%s\n' "$RULE" "$_RULE_STR" "$NC"
    printf '  %s%s %s%s\n' "$BOLD" "$emoji" "$title" "$NC"
    for sub in "$@"; do printf '     %s%s%s\n' "$NAR" "$sub" "$NC"; done
    printf '%s%s%s\n\n' "$RULE" "$_RULE_STR" "$NC"
}

# beat_header EMOJI TITLE [SUBTITLE...]: open a beat. Its chapter marker (named TITLE, so
# the player's chapter list matches the screen), a hard cut, the header on the top row, and
# step_header's hold. A viewer who jumps to the chapter lands on a clean screen.
beat_header() {
    _shell_only beat_header
    chapter "$2"
    clear_screen
    step_header_bars "$@"
    if (( $# > 2 )); then
        shift 2
        read_hold "$HEADER_HOLD" "$@"
    else
        nap "$HEADER_HOLD"
    fi
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
move_up() { if (( $1 > 0 )); then printf '\033[%dA' "$1"; fi; }
el() { printf '\033[K%s\n' "$1"; }

# =============================================================================
# New helpers
# =============================================================================

# chapter TITLE: an invisible marker. record.sh turns it into an asciinema marker
# (a chapter in the player) and removes it from the output. Call it first in a beat.
chapter() { printf '\033]7770;chapter;%s\007' "$1"; }

# stream_lines DELAY LINE...: print lines one at a time, DELAY seconds apart, like
# a log being followed.
stream_lines() {
    local delay="$1" line
    shift
    for line in "$@"; do
        printf '%s\n' "$line"
        nap "$delay"
    done
}

# unsaved_note WHEN: in place of the output of a command that ran before the replayed run and whose
# output was never saved, a dim note saying so (WHEN: when it was applied, for example
# "2026-11-14 03:13"). One wording for every such command; the beat then shows the object's real
# current state from a read-only snapshot.
unsaved_note() {
    local text="applied at setup, ${1} UTC, before this run; its output wasn't saved"
    printf '%s\n' "  ${DIM}(${text})${NC}"
    read_hold "$PAUSE_SHORT" "$text"
}

# card TITLE PARAGRAPH...: a boxed explainer, CARD_W columns wide. Each paragraph is
# word-wrapped and appears after the previous one's reading time. "" is a blank line.
card() {
    local title="$1" p k pad d ind
    shift
    _shell_only card
    local iw=$(( CARD_W - 4 ))
    printf -v ind '%*s' "$CARD_INDENT" ''
    dwidth "$title"
    d=$(( CARD_W - 5 - _W ))
    if (( d < 1 )); then d=1; fi
    _rep '─' "$d"
    nap "$CARD_LEAD"
    printf '%s%s╭─ %s%s%s%s %s╮%s\n' "$ind" "$CARDB" "$NC" "$BOLD" "$title" "$CARDB" "$_REP" "$NC"
    for p in "$@"; do
        _wrap "$iw" "$p"
        for (( k=0; k<${#_WL[@]}; k++ )); do
            dwidth "${_WL[k]}"
            pad=$(( iw - _W ))
            if (( pad < 0 )); then pad=0; fi
            printf '%s%s│%s %s%s%*s %s│%s\n' "$ind" "$CARDB" "$NC" "${_WL[k]}" "$NC" "$pad" '' "$CARDB" "$NC"
        done
        if [[ -n "$p" ]]; then read_hold "$CARD_FLOOR" "$p"; fi
    done
    _rep '─' $(( CARD_W - 2 ))
    printf '%s%s╰%s╯%s\n' "$ind" "$CARDB" "$_REP" "$NC"
}

# -----------------------------------------------------------------------------
# Frames: in-place redraws for fast-forwarded waits. A render function adds lines
# with fl; frame_draw rewrites only the lines that changed since the last frame.
# -----------------------------------------------------------------------------
FRAME=()
_FF_PREV=()
_FF_H=0
fl() { FRAME+=("$1"); }
frame_reset() { FRAME=(); _FF_PREV=(); _FF_H=0; }

frame_draw() {
    local h=${#FRAME[@]} k first=-1 prev=$_FF_H
    while (( h < prev )); do FRAME+=(""); h=$(( h + 1 )); done
    if (( h == 0 )); then return 0; fi
    if (( prev == 0 )); then
        for (( k=0; k<h; k++ )); do el "${FRAME[k]}"; done
    else
        for (( k=0; k<h; k++ )); do
            if (( k >= prev )) || [[ "${FRAME[k]}" != "${_FF_PREV[k]}" ]]; then first=$k; break; fi
        done
        if (( first >= 0 )); then
            move_up $(( prev - first ))
            for (( k=first; k<h; k++ )); do
                if (( k >= prev )) || [[ "${FRAME[k]}" != "${_FF_PREV[k]}" ]]; then
                    el "${FRAME[k]}"
                else
                    printf '\n'
                fi
            done
        fi
    fi
    _FF_PREV=("${FRAME[@]}")
    _FF_H=$h
}

# frame_replace LINE...: replace the last frame with these lines (for example, the
# wait's final output) and clear what is left of it.
frame_replace() {
    local prev=$_FF_H k extra
    move_up "$prev"
    for k in "$@"; do el "$k"; done
    extra=$(( prev - $# ))
    if (( extra > 0 )); then
        for (( k=0; k<extra; k++ )); do el ""; done
        move_up "$extra"
    fi
    frame_reset
}

# ff_run RENDER FROM TO [TICK] [STEP]: call "RENDER t" for t = FROM, FROM+STEP, ...,
# TO and draw each frame in place, TICK seconds apart (default FF_TICK). RENDER adds
# lines with fl; it may set FF_TICK_NOW to change the pause after its frame.
ff_run() {
    local fn="$1" from="$2" to="$3" tick="${4:-$FF_TICK}" step="${5:-1}" t
    frame_reset
    cursor_hide
    t=$from
    while :; do
        FRAME=()
        FF_TICK_NOW=$tick
        "$fn" "$t"
        frame_draw
        if (( t >= to )); then break; fi
        nap "$FF_TICK_NOW"
        t=$(( t + step ))
        if (( t > to )); then t=$to; fi
    done
    cursor_show
}

# ff_elapsed SECONDS: add v1's "⏩ fast-forwarding · 1m49s elapsed" line to the frame.
ff_elapsed() {
    fmt_age "$1"
    fl "  ${DIM}⏩ fast-forwarding · ${AGE_STR} elapsed${NC}"
}

# -----------------------------------------------------------------------------
# YAML: keys, values, comments, list dashes, flow collections, block scalars.
# -----------------------------------------------------------------------------
HY_BLOCK=-1   # column of the key that opened a block scalar (| or >); -1 outside one
hl_yaml_reset() { HY_BLOCK=-1; }
_HY_KEY_RE='^([^]#'"'"'"{}[,&*!|>%@`-][^:#]*):( |$)'
_HY_BLOCK_RE='^([|>][-+0-9]*)( +#.*)?$'
_HY_NUM_RE='^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?$'
_HY_CONST_RE='^(true|false|True|False|TRUE|FALSE|null|Null|NULL|~)$'

_hl_yaml_scalar() {
    if [[ "$1" =~ $_HY_NUM_RE || "$1" =~ $_HY_CONST_RE ]]; then seg "$NUM" "$1"; else seg "$STR" "$1"; fi
}

_hl_yaml_flow() {
    local v="$1" n=${#1} i=0 j c q
    while (( i < n )); do
        c="${v:i:1}"
        case "$c" in
            '{'|'}'|'['|']'|','|':') seg "$PUNCT" "$c"; i=$(( i + 1 )) ;;
            ' ')
                j=$i
                while [[ "${v:j:1}" == " " ]]; do j=$(( j + 1 )); done
                seg "$NC" "${v:i:j-i}"; i=$j ;;
            '"'|"'")
                q=$c; j=$(( i + 1 ))
                while (( j < n )) && [[ "${v:j:1}" != "$q" ]]; do j=$(( j + 1 )); done
                seg "$QUOTE" "$q"; seg "$STR" "${v:i+1:j-i-1}"
                if (( j < n )); then seg "$QUOTE" "$q"; fi
                i=$(( j + 1 )) ;;
            '#')
                if (( i > 0 )) && [[ "${v:i-1:1}" == " " ]]; then
                    seg "$NAR" "${v:i}"; i=$n
                else
                    seg "$STR" "$c"; i=$(( i + 1 ))
                fi ;;
            *)
                j=$i
                while (( j < n )); do
                    case "${v:j:1}" in '{'|'}'|'['|']'|','|':'|' '|'"'|"'") break ;; esac
                    j=$(( j + 1 ))
                done
                _hl_yaml_scalar "${v:i:j-i}"; i=$j ;;
        esac
    done
}

_hl_yaml_value() {
    local v="$1" q j n c pre
    case "$v" in
        '#'*) seg "$NAR" "$v"; return 0 ;;
        '{'*|'['*) _hl_yaml_flow "$v"; return 0 ;;
        \"*|\'*)
            q="${v:0:1}"; n=${#v}; j=1
            while (( j < n )); do
                c="${v:j:1}"
                if [[ "$q" == '"' && "$c" == '\' ]]; then j=$(( j + 2 )); continue; fi
                if [[ "$c" == "$q" ]]; then
                    if [[ "$q" == "'" && "${v:j+1:1}" == "'" ]]; then j=$(( j + 2 )); continue; fi
                    break
                fi
                j=$(( j + 1 ))
            done
            if (( j > n )); then j=$n; fi
            seg "$QUOTE" "$q"; seg "$STR" "${v:1:j-1}"
            if (( j < n )); then
                seg "$QUOTE" "$q"
                pre="${v:j+1}"
                if [[ "$pre" == *"#"* ]]; then
                    seg "$NC" "${pre%%#*}"; seg "$NAR" "#${pre#*#}"
                elif [[ -n "$pre" ]]; then
                    seg "$STR" "$pre"
                fi
            fi
            return 0 ;;
    esac
    pre="${v%% #*}"
    _hl_yaml_scalar "$pre"
    if (( ${#pre} < ${#v} )); then seg "$NAR" "${v:${#pre}}"; fi
}

hl_yaml() {
    local s="$1" rest ind key kc sp
    rest="${s#"${s%%[! ]*}"}"
    ind=$(( ${#s} - ${#rest} ))
    if (( HY_BLOCK >= 0 )); then
        if [[ -z "$rest" ]] || (( ind > HY_BLOCK )); then
            if (( ind > 0 )); then seg "$NC" "${s:0:ind}"; fi
            if [[ -n "$rest" ]]; then seg "$STR" "$rest"; fi
            return 0
        fi
        HY_BLOCK=-1
    fi
    if (( ind > 0 )); then seg "$NC" "${s:0:ind}"; fi
    if [[ -z "$rest" ]]; then return 0; fi
    case "$rest" in
        '#'*) seg "$NAR" "$rest"; return 0 ;;
        ---|...) seg "$PUNCT" "$rest"; return 0 ;;
    esac
    kc=$ind
    while [[ "$rest" == "-" || "$rest" == "- "* ]]; do
        seg "$PUNCT" "-"; rest="${rest:1}"; kc=$(( kc + 1 ))
        sp="${rest%%[! ]*}"
        if [[ -n "$sp" ]]; then seg "$NC" "$sp"; rest="${rest:${#sp}}"; kc=$(( kc + ${#sp} )); fi
    done
    if [[ -z "$rest" ]]; then return 0; fi
    if [[ "$rest" =~ $_HY_KEY_RE ]]; then
        key="${BASH_REMATCH[1]}"
        seg "$PROP" "$key"; seg "$PUNCT" ":"
        rest="${rest:$(( ${#key} + 1 ))}"
        sp="${rest%%[! ]*}"
        if [[ -n "$sp" ]]; then seg "$NC" "$sp"; rest="${rest:${#sp}}"; fi
        if [[ -z "$rest" ]]; then return 0; fi
        if [[ "$rest" =~ $_HY_BLOCK_RE ]]; then
            seg "$OP" "${BASH_REMATCH[1]}"
            if [[ -n "${BASH_REMATCH[2]}" ]]; then seg "$NAR" "${BASH_REMATCH[2]}"; fi
            HY_BLOCK=$kc
            return 0
        fi
    fi
    _hl_yaml_value "$rest"
}

# hl_line LANG TEXT: highlight one line of LANG (yaml, python, bash, or text) into
# SEG_TXT/SEG_COL. hl_reset LANG clears the state that carries across lines.
hl_reset() {
    case "$1" in
        yaml) hl_yaml_reset ;;
        python) hl_python_reset ;;
        bash) hl_bash_reset ;;
    esac
    return 0
}
hl_line() {
    seg_reset
    case "$1" in
        yaml) hl_yaml "$2" ;;
        python) hl_python "$2" ;;
        bash) hl_bash "$2" ;;
        *) if [[ -n "$2" ]]; then seg "$NC" "$2"; fi ;;
    esac
    return 0
}

# =============================================================================
# Vim emulator, on the alternate screen
# =============================================================================
# Layout (ROWS=32): rows 1-30 hold text, row 31 is the status line (file name and
# ruler, reverse video), row 32 the command line (-- INSERT --, messages, :wq).
# Each text row starts with a 2-column sign column (▶ marks) and a 4-column line
# number, like vim with signcolumn=yes and number; long lines soft-wrap.
VIM_ROWS=$(( ROWS - 2 ))
VIM_STL=$(( ROWS - 1 ))
VIM_CMDROW=$ROWS
VIM_GUT=6
VIM_TW=$(( COLS - VIM_GUT ))

VIM_ACTIVE=0
VIM_FILE=""
VIM_LANG=text
VIM_N=0          # lines in the buffer
VIM_TOP=0        # buffer index of the first line on screen
VIM_CROW=1       # cursor screen row
VIM_CCOL=0       # cursor column within the text area (0-based)
VIM_LCOL=0       # characters typed on the cursor's line (for the ruler)
VIM_MOD=0        # buffer changed: the status line shows [+]
VIM_MA=0         # first and last marked line (1-based) while a callout shows
VIM_MB=0
VIM_NOTE=""      # note after -- INSERT -- on the command line
VIM_MODE=normal
VIM_FOUND=0
VB=(); VH=(); VW=(); VFOLD=(); VHID=(); VFNOTE=()
_VT_N=0

_vim_need() { if (( ! VIM_ACTIVE )); then die "$1: call vim_open first"; fi; }

# _vim_rows IDX → _R: screen rows of buffer line IDX (0 inside a closed fold).
_vim_rows() {
    local i=$1
    if (( ${VHID[i]} )); then _R=0; return 0; fi
    if (( ${VFOLD[i]} > 0 )); then _R=1; return 0; fi
    if (( ${VW[i]} <= VIM_TW )); then _R=1; else _R=$(( (${VW[i]} + VIM_TW - 1) / VIM_TW )); fi
}

# _vim_gutter IDX FIRST → _G: the sign and number columns for a row of line IDX.
_vim_gutter() {
    local ln=$(( $1 + 1 )) num
    if (( $2 )); then printf -v num '%3d ' "$ln"; else num='    '; fi
    if (( $2 && VIM_MA > 0 && ln >= VIM_MA && ln <= VIM_MB )); then
        _G="${MARK}▶ ${num}${NC}"
    else
        _G="  ${LNR}${num}${NC}"
    fi
}

# _vim_wrap_line IDX → _WR=(the highlighted line cut into screen rows).
_vim_wrap_line() {
    local i=$1
    if (( ${VW[i]} <= VIM_TW )); then _WR=("${VH[i]}"); return 0; fi
    local h="${VH[i]}" n k=0 c col=0 cur="" sgr="" sq cw
    n=${#h}
    _WR=()
    while (( k < n )); do
        c="${h:k:1}"
        if [[ "$c" == $'\033' ]]; then
            sq="$c"; k=$(( k + 1 ))
            while (( k < n )); do
                c="${h:k:1}"; sq+="$c"; k=$(( k + 1 ))
                if [[ "$c" == [A-Za-z] ]]; then break; fi
            done
            cur+="$sq"; sgr="$sq"
            continue
        fi
        cw=1
        if [[ "$c" != [\ -~] ]]; then _dwidth "$c"; cw=$_W; fi
        if (( col + cw > VIM_TW )); then _WR+=("$cur"); cur="$sgr"; col=0; fi
        cur+="$c"; col=$(( col + cw )); k=$(( k + 1 ))
    done
    _WR+=("$cur")
}

# _vim_draw_line IDX ROW → draws line IDX from screen ROW down (clipped); _R = rows drawn.
_vim_draw_line() {
    local i=$1 row=$2 k txt
    if (( ${VFOLD[i]} > 0 )); then
        _vim_gutter "$i" 1
        printf -v txt '+--%3d lines: %s ' "${VFOLD[i]}" "${VFNOTE[i]}"
        _dwidth "$txt"
        _rep '-' $(( VIM_TW - _W ))
        printf '\033[%d;1H\033[K%s%s%s%s%s' "$row" "$_G" "$FOLDC" "$txt" "$_REP" "$NC"
        _R=1
        return 0
    fi
    _vim_wrap_line "$i"
    for (( k=0; k<${#_WR[@]}; k++ )); do
        if (( row + k > VIM_ROWS )); then break; fi
        _vim_gutter "$i" $(( k == 0 ? 1 : 0 ))
        printf '\033[%d;1H\033[K%s%s%s' $(( row + k )) "$_G" "${_WR[k]}" "$NC"
    done
    _R=$k
}

# Redraw every text row from VIM_TOP: the lines, then ~ rows.
_vim_redraw() {
    local i=$VIM_TOP row=1
    while (( row <= VIM_ROWS && i < VIM_N )); do
        if (( ${VHID[i]} )); then i=$(( i + 1 )); continue; fi
        _vim_draw_line "$i" "$row"
        row=$(( row + _R )); i=$(( i + 1 ))
    done
    if (( VIM_N == 0 )); then
        _vim_gutter 0 1
        printf '\033[1;1H\033[K%s' "$_G"
        row=2
    fi
    while (( row <= VIM_ROWS )); do
        printf '\033[%d;1H\033[K%s~%s' "$row" "$EOB" "$NC"
        row=$(( row + 1 ))
    done
}

# _vim_locate: VIM_CROW/VIM_CCOL for the cursor at the end of the last line.
# VIM_CROW is 0 when that line is above the screen, > VIM_ROWS when below it.
_vim_locate() {
    local i last=$(( VIM_N - 1 )) row=1 k=$VIM_LCOL
    if (( VIM_N == 0 )); then VIM_CROW=1; VIM_CCOL=0; return 0; fi
    while (( last > 0 )) && (( ${VHID[last]} )); do last=$(( last - 1 )); done
    if (( last < VIM_TOP )); then VIM_CROW=0; VIM_CCOL=0; return 0; fi
    for (( i=VIM_TOP; i<last; i++ )); do _vim_rows "$i"; row=$(( row + _R )); done
    if (( ${VFOLD[last]} > 0 )); then VIM_CROW=$row; VIM_CCOL=0; return 0; fi
    if (( k > 0 && k % VIM_TW == 0 )); then
        VIM_CROW=$(( row + k / VIM_TW - 1 )); VIM_CCOL=$VIM_TW
    else
        VIM_CROW=$(( row + k / VIM_TW )); VIM_CCOL=$(( k % VIM_TW ))
    fi
}

_vim_cursor() {
    local c=$(( VIM_GUT + VIM_CCOL + 1 ))
    if (( c > COLS )); then c=$COLS; fi
    if (( VIM_CROW >= 1 && VIM_CROW <= VIM_ROWS )); then printf '\033[%d;%dH' "$VIM_CROW" "$c"; fi
}

# _vim_relpos → _RP: All, Top, Bot, or NN%, the way vim's ruler computes it.
_vim_relpos() {
    local i=$VIM_TOP row=1 above=$VIM_TOP below
    while (( i < VIM_N )); do
        if (( ${VHID[i]} )); then i=$(( i + 1 )); continue; fi
        _vim_rows "$i"
        if (( row + _R - 1 > VIM_ROWS )); then break; fi
        row=$(( row + _R )); i=$(( i + 1 ))
    done
    below=$(( VIM_N - i ))
    if (( below <= 0 )); then
        if (( above == 0 )); then _RP=All; else _RP=Bot; fi
    elif (( above <= 0 )); then
        _RP=Top
    else
        printf -v _RP '%2d%%' $(( above * 100 / (above + below) ))
    fi
}

# _vim_ruler RELPOS → _RS: vim's 18-column ruler, "line,col" then the position.
_vim_ruler() {
    local lc c fill
    if (( VIM_N == 0 )); then
        lc="0,0-1"
    elif (( VIM_LCOL == 0 )); then
        lc="${VIM_N},0-1"
    else
        if [[ "$VIM_MODE" == insert ]]; then c=$(( VIM_LCOL + 1 )); else c=$VIM_LCOL; fi
        lc="${VIM_N},${c}"
    fi
    fill=$(( 18 - ${#lc} - ${#1} ))
    if (( fill < 1 )); then fill=1; fi
    printf -v _RS '%s%*s%s' "$lc" "$fill" '' "$1"
}

_vim_status() {
    local name="$VIM_FILE" left w=$(( COLS - 18 ))
    if (( VIM_MOD )); then name+=" [+]"; fi
    _vim_relpos
    _vim_ruler "$_RP"
    printf -v left "%-${w}s" "$name"
    printf '\033[%d;1H%s%s%s%s' "$VIM_STL" "$STL" "${left:0:w}" "$_RS" "$NC"
}

# The ruler alone, while typing at the end of the buffer; then back to the cursor.
_vim_ruler_live() {
    local rp=All c
    if (( VIM_TOP > 0 )); then rp=Bot; fi
    _vim_ruler "$rp"
    printf '\033[%d;%dH%s%s%s' "$VIM_STL" $(( COLS - 17 )) "$STL" "$_RS" "$NC"
    c=$(( VIM_GUT + VIM_CCOL + 1 ))
    if (( c > COLS )); then c=$COLS; fi
    printf '\033[%d;%dH%s' "$VIM_CROW" "$c" "${1:-}"
}

_vim_cmdline() { printf '\033[%d;1H\033[K%s' "$VIM_CMDROW" "$1"; }

# The mode line: -- INSERT -- and VIM_NOTE; then the cursor goes back to the text.
_vim_set_note() {
    VIM_NOTE="$1"
    _vim_cmdline "${BOLD}-- INSERT --${NC}${VIM_NOTE:+   ${DIM}${VIM_NOTE}${NC}}"
    _vim_cursor
}

# Move the cursor to the start of the next screen row. At the bottom, scroll whole
# lines off the top (as vim does); freed rows below the cursor show ~.
_vim_next_row() {
    local freed=0 r
    if (( VIM_CROW < VIM_ROWS )); then VIM_CROW=$(( VIM_CROW + 1 )); return 0; fi
    while (( freed < 1 && VIM_TOP < VIM_N - 1 )); do
        _vim_rows "$VIM_TOP"; freed=$(( freed + _R ))
        VIM_TOP=$(( VIM_TOP + 1 ))
        while (( VIM_TOP < VIM_N - 1 )) && (( ${VHID[VIM_TOP]} )); do VIM_TOP=$(( VIM_TOP + 1 )); done
    done
    if (( freed < 1 )); then freed=1; fi
    printf '\033[%d;1H' "$VIM_ROWS"
    for (( r=0; r<freed; r++ )); do printf '\n'; done
    VIM_CROW=$(( VIM_ROWS - freed + 1 ))
    for (( r=VIM_CROW+1; r<=VIM_ROWS; r++ )); do printf '\033[%d;1H%s~%s' "$r" "$EOB" "$NC"; done
}

# Append one line to the buffer (no drawing): text, highlight, width, fold fields.
_vim_add_line() {
    local s="$1" k h=""
    hl_line "$VIM_LANG" "$s"
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do h+="${SEG_COL[k]}${SEG_TXT[k]}"; done
    VB[VIM_N]="$s"; VH[VIM_N]="$h"
    _dwidth "$s"; VW[VIM_N]=$_W
    VFOLD[VIM_N]=0; VHID[VIM_N]=0; VFNOTE[VIM_N]=""
    VIM_N=$(( VIM_N + 1 ))
    if (( VIM_N > 999 )); then die "vim: more than 999 lines; the number column is 4 wide"; fi
}

# Type SEG_TXT/SEG_COL at the cursor: CHUNK characters per TICK, leading spaces at
# once (autoindent), wrapping at the text width.
_vim_type_text() {
    local chunk=$1 tick=$2 k i t col ch lead=1 cw
    for (( k=0; k<${#SEG_TXT[@]}; k++ )); do
        t="${SEG_TXT[k]}"; col="${SEG_COL[k]}"
        printf '%s' "$col"
        for (( i=0; i<${#t}; i++ )); do
            ch="${t:i:1}"
            cw=1
            if [[ "$ch" != [\ -~] ]]; then _dwidth "$ch"; cw=$_W; fi
            if (( VIM_CCOL + cw > VIM_TW )); then
                _vim_next_row
                printf '\033[%d;%dH%s' "$VIM_CROW" $(( VIM_GUT + 1 )) "$col"
                VIM_CCOL=0
            fi
            printf '%s' "$ch"
            VIM_CCOL=$(( VIM_CCOL + cw )); VIM_LCOL=$(( VIM_LCOL + 1 ))
            if (( lead )) && [[ "$ch" == " " ]]; then continue; fi
            lead=0
            _VT_N=$(( _VT_N + 1 ))
            if (( _VT_N % chunk == 0 )); then
                if [[ "$VIM_RULER_LIVE" != 0 ]]; then _vim_ruler_live "$col"; fi
                nap "$tick"
            fi
        done
    done
    printf '%s' "$NC"
    _vim_ruler_live ""
}

# Enter (unless the buffer is empty), then type TEXT.
_vim_type_line() {
    local chunk=$1 tick=$2 eol=$3 s="$4"
    s="${s//$'\t'/        }"
    if (( VIM_N > 0 )); then
        if [[ "$eol" != 0 ]]; then nap "$eol"; fi
        _vim_next_row
        if [[ "$eol" == 0 ]]; then   # fast typing counts Enter as a keystroke
            _VT_N=$(( _VT_N + 1 ))
            if (( chunk > 1 && _VT_N % chunk == 0 )); then nap "$tick"; fi
        fi
    fi
    _vim_add_line "$s"
    _vim_gutter $(( VIM_N - 1 )) 1
    printf '\033[%d;1H\033[K%s' "$VIM_CROW" "$_G"
    VIM_CCOL=0; VIM_LCOL=0
    if (( ! VIM_MOD )) && [[ -n "$s" ]]; then
        VIM_MOD=1
        _vim_status
    fi
    # The ruler follows Enter now and the end of the line later; the characters in
    # between reach the output unbroken (see VIM_RULER_LIVE).
    _vim_ruler_live ""
    _vim_type_text "$chunk" "$tick"
}

# vim_open FILE [LANG]: type "vim FILE" at the prompt, open an empty buffer on the
# alternate screen ("FILE" [New]), then press i (-- INSERT --). LANG is yaml, python,
# bash, or text; by default it follows FILE's extension.
vim_open() {
    local file="$1" lang="${2:-}"
    if [[ -z "$lang" ]]; then
        case "$file" in
            *.yaml|*.yml) lang=yaml ;;
            *.py) lang=python ;;
            *.sh|*.bash) lang=bash ;;
            *) lang=text ;;
        esac
    fi
    show_command "vim $file"
    VIM_FILE=$file; VIM_LANG=$lang; VIM_N=0; VIM_TOP=0; VIM_CROW=1; VIM_CCOL=0; VIM_LCOL=0
    VIM_MOD=0; VIM_MA=0; VIM_MB=0; VIM_NOTE=""; VIM_MODE=normal; VIM_FOUND=0
    VIM_COLL_START=-1; VIM_COLL_NOTE=""
    VB=(); VH=(); VW=(); VFOLD=(); VHID=(); VFNOTE=()
    hl_reset "$lang"
    VIM_ACTIVE=1
    cursor_hide
    printf '\033[?1049h\033[H\033[2J\033[1;%dr' "$VIM_ROWS"
    _vim_redraw
    _vim_status
    _vim_cmdline "\"$file\" [New]"
    _vim_cursor
    cursor_show
    nap "$VIM_OPEN_HOLD"
    VIM_MODE=insert
    _vim_status
    _vim_set_note ""
    nap "$VIM_KEY_PAUSE"
}

# vim_type normal|fast|collapse LINE...: append LINEs. normal types one character at a
# time; fast types VIM_FAST characters per tick and shows "⏩ typing ×N" on the command
# line; collapse (between vim_collapse_begin and vim_collapse_end) types VIM_COLLAPSE_X
# per tick. VIM_GROUP ticks share one nap (the same speed, fewer sleeps).
vim_type() {
    local mode="$1" line chunk eol=0 tick
    shift
    _vim_need vim_type
    case "$mode" in
        normal) chunk=$VIM_GROUP; eol=$VIM_EOL_PAUSE ;;
        fast) chunk=$(( VIM_FAST * VIM_GROUP )); _vim_set_note "⏩ typing ×${VIM_FAST}" ;;
        collapse)
            if (( VIM_COLL_START < 0 )); then die "vim_type collapse: call vim_collapse_begin first"; fi
            chunk=$(( VIM_COLLAPSE_X * VIM_GROUP )) ;;
        *) die "vim_type: the mode is normal, fast, or collapse, not '$mode'" ;;
    esac
    _to_us "$VIM_CHAR"; _us_str $(( _US * VIM_GROUP )); tick=$_SECS
    _VT_N=0
    for line in "$@"; do _vim_type_line "$chunk" "$tick" "$eol" "$line"; done
    if [[ "$mode" == fast ]]; then _vim_set_note ""; fi
}

# vim_fold_in NOTE LINE...: add LINEs as one closed fold, "+--  N lines: NOTE", without
# drawing their text (as if pasted, then closed with zc). The buffer and the written size
# stay the real file; the text never reaches the cast. For lines the public cast mustn't
# show, such as an address tools/redact_cast.py would flag. Vim shows a closed fold only
# for 2 lines or more ('foldminlines'), so pass at least 2.
vim_fold_in() {
    local note="$1" start k
    shift
    _vim_need vim_fold_in
    if (( $# < 2 )); then die "vim_fold_in: a closed fold needs 2 lines or more"; fi
    if (( VIM_N > 0 )); then
        if (( VIM_COLL_START < 0 )); then nap "$VIM_EOL_PAUSE"; fi
        _vim_next_row
    fi
    start=$VIM_N
    for k in "$@"; do _vim_add_line "${k//$'\t'/        }"; done
    VFOLD[start]=$#; VFNOTE[start]="$note"
    for (( k=start+1; k<VIM_N; k++ )); do VHID[k]=1; done
    VIM_CCOL=0; VIM_LCOL=0
    _vim_draw_line "$start" "$VIM_CROW"
    if (( ! VIM_MOD )); then VIM_MOD=1; fi
    _vim_status
    _vim_cursor
    if (( VIM_COLL_START < 0 )); then nap "$VIM_FOLD_PAUSE"; fi
}

# Fold buffer lines A..B (0-based) into one row and keep the fold row where the
# cursor was.
_vim_fold() {
    local a=$1 b=$2 note="$3" i used=1 top prev
    VFOLD[a]=$(( b - a + 1 )); VFNOTE[a]="$note"
    for (( i=a+1; i<=b; i++ )); do VHID[i]=1; done
    top=$a
    while (( top > 0 )); do
        prev=$(( top - 1 ))
        while (( prev > 0 )) && (( ${VHID[prev]} )); do prev=$(( prev - 1 )); done
        _vim_rows "$prev"
        if (( used + _R > VIM_CROW )); then break; fi
        used=$(( used + _R )); top=$prev
    done
    VIM_TOP=$top
    _vim_redraw
    _vim_locate
    _vim_status
}

# vim_collapse NOTE LINE...: type LINEs at VIM_COLLAPSE_X characters per tick with
# "⏩ typing ×N · NOTE" on the command line, then (VIM_FOLD=1) fold them into one row,
# "+--180 lines: NOTE". The buffer keeps every line.
vim_collapse() {
    local note="$1"
    shift
    _vim_need vim_collapse
    if (( $# == 0 )); then return 0; fi
    vim_collapse_begin "$note"
    vim_type collapse "$@"
    vim_collapse_end
}

# vim_collapse_begin NOTE ... vim_collapse_end: vim_collapse in parts, so that
# vim_fold_in can add lines in between. Everything added in between becomes one fold.
VIM_COLL_START=-1
VIM_COLL_NOTE=""
vim_collapse_begin() {
    _vim_need vim_collapse_begin
    VIM_COLL_START=$VIM_N; VIM_COLL_NOTE="$1"
    _vim_set_note "⏩ typing ×${VIM_COLLAPSE_X} · $1"
}
vim_collapse_end() {
    _vim_need vim_collapse_end
    if (( VIM_COLL_START < 0 )); then die "vim_collapse_end: no vim_collapse_begin"; fi
    nap "$VIM_FOLD_PAUSE"
    if [[ "$VIM_FOLD" != 0 ]] && (( VIM_N > VIM_COLL_START )); then
        _vim_fold "$VIM_COLL_START" $(( VIM_N - 1 )) "$VIM_COLL_NOTE"
    fi
    VIM_COLL_START=-1
    _vim_set_note ""
}

# vim_find TEXT → VIM_FOUND: the 1-based number of the first buffer line that
# contains TEXT, or 0.
vim_find() {
    local i
    VIM_FOUND=0
    for (( i=0; i<VIM_N; i++ )); do
        if [[ "${VB[i]}" == *"$1"* ]]; then VIM_FOUND=$(( i + 1 )); return 0; fi
    done
    return 0
}

# Scroll (change VIM_TOP) so that lines A..B (0-based) sit in rows 1..AVAIL, with
# B at the bottom and as much context above it as fits. No change if they already do.
_vim_show_range() {
    local a=$1 b=$2 avail=$3 i row=1 first=0 total=0 top
    if (( a >= VIM_TOP )); then
        for (( i=VIM_TOP; i<=b; i++ )); do
            _vim_rows "$i"
            if (( i == a )); then first=$row; fi
            row=$(( row + _R ))
        done
        if (( first >= 1 && row - 1 <= avail )); then return 0; fi
    fi
    for (( i=a; i<=b; i++ )); do _vim_rows "$i"; total=$(( total + _R )); done
    top=$a
    while (( top > 0 )); do
        i=$(( top - 1 ))
        while (( i > 0 )) && (( ${VHID[i]} )); do i=$(( i - 1 )); done
        _vim_rows "$i"
        if (( total + _R > avail )); then break; fi
        total=$(( total + _R )); top=$i
    done
    VIM_TOP=$top
}

# The callout box over the bottom text rows: H rows, an optional label in the top
# border, then the wrapped lines.
_vim_box() {
    local h="$1" label="$2" t d row col=1 iw=$(( COLS - 4 ))
    shift 2
    row=$(( VIM_ROWS - h + 1 ))
    if [[ -n "$label" ]]; then
        _dwidth "$label"
        _rep '─' $(( COLS - 5 - _W ))
        printf '\033[%d;%dH%s╭─ %s%s%s %s╮%s' "$row" "$col" "$POPB" "$POPMARK" "$label" "$POPB" "$_REP" "$NC"
    else
        _rep '─' $(( COLS - 2 ))
        printf '\033[%d;%dH%s╭%s╮%s' "$row" "$col" "$POPB" "$_REP" "$NC"
    fi
    for t in "$@"; do
        row=$(( row + 1 ))
        dwidth "$t"
        d=$(( iw - _W ))
        if (( d < 0 )); then d=0; fi
        t="${t//$'\033[0;'/$'\033[0;40;'}"
        t="${t//$'\033[0m'/$POP}"
        printf '\033[%d;%dH%s│%s %s%*s %s│%s' "$row" "$col" "$POPB" "$POP" "$t" "$d" '' "$POPB" "$NC"
    done
    row=$(( row + 1 ))
    _rep '─' $(( COLS - 2 ))
    printf '\033[%d;%dH%s╰%s╯%s' "$row" "$col" "$POPB" "$_REP" "$NC"
}

# vim_callout FROM TO PARAGRAPH...: pause and explain. Marks buffer lines FROM..TO
# (1-based; 0 0 marks none) with ▶ in the gutter, scrolls if needed so they stay
# visible above the box, shows the paragraphs (word-wrapped) in a box above the
# status line for their reading time, then removes the box. The buffer is unchanged.
vim_callout() {
    local a="$1" b="$2" p top0=$VIM_TOP label="" h avail
    shift 2
    _vim_need vim_callout
    local -a lines=()
    for p in "$@"; do
        _wrap $(( COLS - 4 )) "$p"
        lines+=("${_WL[@]}")
    done
    h=$(( ${#lines[@]} + 2 ))
    avail=$(( VIM_ROWS - h ))
    if (( b < a )); then b=$a; fi
    if (( a > VIM_N || b > VIM_N )); then die "vim_callout: lines $a-$b, but the buffer has $VIM_N"; fi
    if (( a > 0 )); then
        _vim_show_range $(( a - 1 )) $(( b - 1 )) "$avail"
        if (( a == b )); then label="▶ line $a"; else label="▶ lines ${a}–${b}"; fi
    fi
    VIM_MA=$a; VIM_MB=$b
    cursor_hide
    nap "$CALLOUT_LEAD"
    _vim_redraw
    _vim_status
    _vim_box "$h" "$label" "${lines[@]}"
    read_hold_at "$CALLOUT_WPM" "$CALLOUT_FLOOR" "$@"
    VIM_MA=0; VIM_MB=0
    _vim_locate
    if (( VIM_CROW < 1 || VIM_CROW > VIM_ROWS )); then
        VIM_TOP=$top0
        _vim_locate
    fi
    _vim_redraw
    _vim_status
    _vim_set_note "$VIM_NOTE"
    cursor_show
}

# vim_note PARAGRAPH...: a callout that marks no lines.
vim_note() { vim_callout 0 0 "$@"; }

# vim_close: Esc, type :wq, show "FILE" [New] NL, NB written, and return to the
# shell screen as it was before vim_open.
vim_close() {
    local cmd=":wq" k bytes=0
    _vim_need vim_close
    VIM_MODE=normal
    VIM_NOTE=""
    _vim_cmdline ""
    if (( VIM_CCOL > 0 )); then VIM_CCOL=$(( VIM_CCOL - 1 )); fi
    _vim_status
    _vim_cursor
    nap "$VIM_KEY_PAUSE"
    _vim_cmdline ""
    for (( k=0; k<${#cmd}; k++ )); do
        printf '%s' "${cmd:k:1}"
        nap "$VIM_KEY_CHAR"
    done
    nap "$VIM_KEY_PAUSE"
    if (( VIM_N > 0 )); then bytes=$(printf '%s\n' "${VB[@]}" | LC_ALL=C wc -c | tr -d ' '); fi
    cursor_hide
    _vim_cmdline "\"$VIM_FILE\" [New] ${VIM_N}L, ${bytes}B written"
    nap "$VIM_WRITTEN_HOLD"
    printf '\033[r\033[?1049l'
    VIM_ACTIVE=0
    cursor_show
}

# =============================================================================
# Beat runner (run.sh and the built script call demo_main)
# =============================================================================
DEMO_BEAT=""
_demo_exit() {
    local rc=$1
    if (( VIM_ACTIVE )); then printf '\033[r\033[?1049l'; fi
    printf '%s\033[?25h' "$NC"
    if (( rc != 0 )) && [[ -n "$DEMO_BEAT" ]]; then
        printf 'demo: beat %s failed (exit %d)\n' "$DEMO_BEAT" "$rc" >&2
    fi
}

# demo_main NN...: run beat_NN for each NN, in the order given. Each beat starts
# with an invisible beat marker that check.py uses to attribute time and width.
demo_main() {
    local nn
    for nn in "$@"; do
        if ! declare -F "beat_$nn" >/dev/null; then die "no function beat_$nn (beats/${nn}_*.sh defines it)"; fi
    done
    trap '_demo_exit $?' EXIT
    cursor_show
    for nn in "$@"; do
        DEMO_BEAT=$nn
        printf '\033]7770;beat;%s\007' "$nn"
        "beat_$nn"
    done
    DEMO_BEAT=""
}
