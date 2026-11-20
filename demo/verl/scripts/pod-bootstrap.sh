#!/usr/bin/env bash
# Runs at pod start: re-install the package delta recorded by build-env.sh, offline. The container
# layer resets on every restart; verl and the recipe are imported from the shared checkout (PYTHONPATH).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

if [ ! -s "$ENV_DIR/delta.txt" ] || [ ! -d "$VERL_DIR/recipe/dynamo" ]; then
    echo "[bootstrap] $ENV_DIR/delta.txt or $VERL_DIR/recipe/dynamo missing: run build-env.sh first" >&2
    exit 1
fi
python3 -m pip install --quiet --no-index --no-deps --find-links "$WHEELHOUSE" -r "$ENV_DIR/delta.txt"

python3 - <<'EOF'
import os

import flash_attn  # noqa: F401
import verl
import recipe.dynamo.register  # noqa: F401

assert verl.__file__.startswith(os.environ["VERL_DIR"]), f"verl imported from {verl.__file__}"
EOF
echo "[bootstrap] ok: $(hostname) gpus=$(nvidia-smi -L | wc -l)"
