#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Run some beats of the team replay, for testing.
#
#   ./run.sh              every beat in NN order, except 99 (the framework test)
#   ./run.sh 03 04        just these, in this order
#   FAST=1 ./run.sh 03    no sleeps
#   DRYRUN=1 ./run.sh 03  no sleeps; prints invisible nap tokens (check.py reads them)
#   REFILL=1 ./run.sh 03  rerun the fill script even if its data file is current
#
# Before running, each selected beat's fill script runs if build/NN_slug.data.sh is
# missing or older than fill/NN_slug.py or fill/_common.py. Then run.sh sources
# lib.sh, every build/*.data.sh, and every beats/*.sh, and calls demo_main. A beat
# file or data file with a syntax error stops the run only if its beat is selected;
# otherwise it is skipped with a warning, so one beat's work in progress doesn't
# block the others.
set -euo pipefail

TEAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"   # any Python 3.10+ with PyYAML

warn() { printf 'run.sh: %s\n' "$*" >&2; }
fail() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

# Beats on disk: beats/NN_slug.sh.
ALL=()
for f in "$TEAM_DIR"/beats/[0-9][0-9]_*.sh; do
    [[ -e "$f" ]] || continue
    b="${f##*/}"
    ALL+=("${b%%_*}")
done

# The selection: arguments (3 or 03), or every beat except 99.
SEL=()
if (( $# )); then
    for a in "$@"; do
        [[ "$a" =~ ^[0-9]+$ ]] || fail "beats are numbers (03 or 3), not '$a'"
        printf -v nn '%02d' "$((10#$a))"
        found=0
        for b in ${ALL[@]+"${ALL[@]}"}; do if [[ "$b" == "$nn" ]]; then found=1; fi; done
        (( found )) || fail "no beats/${nn}_*.sh"
        SEL+=("$nn")
    done
else
    for b in ${ALL[@]+"${ALL[@]}"}; do [[ "$b" == 99 ]] || SEL+=("$b"); done
fi
(( ${#SEL[@]} )) || fail "no beats to run (beats/ has no NN_slug.sh files)"

selected() { local s; for s in "${SEL[@]}"; do [[ "$s" == "$1" ]] && return 0; done; return 1; }

# Refresh the selected beats' data.
for nn in "${SEL[@]}"; do
    for f in "$TEAM_DIR"/fill/"$nn"_*.py; do
        [[ -e "$f" ]] || continue
        base="${f##*/}"
        data="$TEAM_DIR/build/${base%.py}.data.sh"
        if [[ "${REFILL:-0}" != 0 || ! -e "$data" || "$f" -nt "$data" || "$TEAM_DIR/fill/_common.py" -nt "$data" ]]; then
            "$PY" -I "$f" >&2 || fail "fill/$base failed; fix it and rerun"
        fi
    done
done

# shellcheck source=lib.sh
source "$TEAM_DIR/lib.sh"

# Source data, then beats. A file that doesn't parse is fatal only for a selected beat.
for f in "$TEAM_DIR"/build/[0-9][0-9]_*.data.sh "$TEAM_DIR"/beats/[0-9][0-9]_*.sh; do
    [[ -e "$f" ]] || continue
    b="${f##*/}"; nn="${b%%_*}"
    if ! /bin/bash -n "$f" 2>/dev/null; then
        if selected "$nn"; then /bin/bash -n "$f"; fail "${f#"$TEAM_DIR"/} has a syntax error"; fi
        warn "skipping ${f#"$TEAM_DIR"/}: syntax error (beat $nn isn't selected)"
        continue
    fi
    # shellcheck disable=SC1090
    source "$f"
done

demo_main "${SEL[@]}"
