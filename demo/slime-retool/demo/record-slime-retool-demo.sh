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
# Record slime-retool-demo.sh (the ReTool + KV-aware routing replay) to an
# asciicast at 100x25 and embed the dark fern-code palette, the same way the
# Slime overview's demo and the first KV demo are recorded.
#
# Usage:
#   python3 -I fill_demo.py --sample N          # writes slime-retool-demo.sh
#   ./record-slime-retool-demo.sh               # -> ./slime-retool-demo.cast
#   ./record-slime-retool-demo.sh out.cast 25 100
#
# Requires: asciinema (3.x), python3.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-${DIR}/slime-retool-demo.cast}"
ROWS="${2:-25}"
COLS="${3:-100}"

# Without a terminal (for example, when an agent or CI records it), asciinema
# must run headless.
HEADLESS=""
[[ -t 1 ]] || HEADLESS="--headless"

# Run from the script directory so the cast header records a relative command.
case "$OUT" in /*) ;; *) OUT="${PWD}/${OUT}" ;; esac
cd "$DIR"
asciinema rec --overwrite ${HEADLESS} \
  --window-size "${COLS}x${ROWS}" \
  --idle-time-limit 2 \
  --command "bash slime-retool-demo.sh" \
  "$OUT"

python3 "${DIR}/apply-fern-code-theme.py" "$OUT"
echo "Recorded + themed: $OUT (${COLS}x${ROWS})"
