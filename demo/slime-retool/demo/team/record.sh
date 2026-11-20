#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Record the team replay with asciinema at 120x32, turn its chapter tokens into
# markers, and embed the dark fern-code palette (v1's apply-fern-code-theme.py).
#
#   ./record.sh                    build.sh, then record slime-retool-team-demo.sh
#                                  -> slime-retool-team-demo.cast
#   ./record.sh --no-build         record the script as it's built
#   ./record.sh 03 04              record "run.sh 03 04" -> tmp/rec-03-04.cast
#   ./record.sh -o OUT.cast [NN..] choose the output file
#   ./record.sh --gif --mp4 ...    also render OUT.gif with agg and OUT.mp4 with ffmpeg
#
# Recording takes real time (the whole replay: about 12 minutes). For a quick
# look at a beat, use check.py --cast instead (see FRAMEWORK.md).
# Requires asciinema 3.x (writes asciicast v3), agg and ffmpeg for --gif/--mp4.
set -euo pipefail

TEAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"   # any Python 3.10+ with PyYAML
COLS=120
ROWS=32
fail() { printf 'record.sh: %s\n' "$*" >&2; exit 1; }

OUT=""
BUILD=1
GIF=0
MP4=0
BEATS=()
while (( $# )); do
    case "$1" in
        -o) OUT="${2:?-o needs a file}"; shift 2 ;;
        --no-build) BUILD=0; shift ;;
        --gif) GIF=1; shift ;;
        --mp4) MP4=1; GIF=1; shift ;;
        -h|--help) sed -n '4,18p' "$0"; exit 0 ;;
        -*) fail "unknown option $1" ;;
        *) BEATS+=("$1"); shift ;;
    esac
done

if (( ${#BEATS[@]} )); then
    CMD="/bin/bash run.sh ${BEATS[*]}"
    TITLE="Slime ReTool + Dynamo on AKS: beats ${BEATS[*]}"
    if [[ -z "$OUT" ]]; then
        mkdir -p "$TEAM_DIR/tmp"
        OUT="$TEAM_DIR/tmp/rec-$(IFS=-; echo "${BEATS[*]}").cast"
    fi
else
    if (( BUILD )); then /bin/bash "$TEAM_DIR/build.sh" || fail "build.sh failed"; fi
    [[ -f "$TEAM_DIR/slime-retool-team-demo.sh" ]] || fail "no slime-retool-team-demo.sh; run build.sh"
    CMD="/bin/bash slime-retool-team-demo.sh"
    TITLE="Slime ReTool + Dynamo on AKS: the team replay"
    [[ -n "$OUT" ]] || OUT="$TEAM_DIR/slime-retool-team-demo.cast"
fi
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
RAW="${OUT%.cast}.raw.cast"

# Without a terminal (an agent or CI recording it), asciinema must run headless.
HEADLESS=""
[[ -t 1 ]] || HEADLESS="--headless"

# Record from the team directory so the cast header holds a relative command.
cd "$TEAM_DIR"
env -u FAST -u DRYRUN asciinema rec --overwrite ${HEADLESS} \
    --window-size "${COLS}x${ROWS}" \
    --idle-time-limit 2 \
    --title "$TITLE" \
    --command "$CMD" \
    "$RAW"

"$PY" -I "$TEAM_DIR/cast_markers.py" "$RAW" "$OUT"
"$PY" -I "$TEAM_DIR/../apply-fern-code-theme.py" "$OUT"
rm -f "$RAW"

if (( GIF )); then
    agg --font-size 16 --fps-cap 15 --idle-time-limit 2 "$OUT" "${OUT%.cast}.gif" >/dev/null 2>&1 \
        || fail "agg couldn't render $OUT"
    echo "rendered ${OUT%.cast}.gif"
fi
if (( MP4 )); then
    ffmpeg -v error -y -i "${OUT%.cast}.gif" -movflags faststart -pix_fmt yuv420p \
        -vf 'scale=trunc(iw/2)*2:trunc(ih/2)*2' "${OUT%.cast}.mp4" || fail "ffmpeg couldn't make the mp4"
    echo "rendered ${OUT%.cast}.mp4"
fi
echo "recorded ${OUT} (${COLS}x${ROWS}). Before sharing it: python3 -I ../../tools/redact_cast.py ${OUT##*/} <public.cast>"
