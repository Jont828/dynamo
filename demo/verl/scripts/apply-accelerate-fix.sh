#!/usr/bin/env bash
# Problem 3 workaround from the 1.4.2 report: upgrade accelerate past the transformers-5
# `_is_hf_initialized` bug, then refresh the recorded delta so a restarted pod keeps it.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$SHARED_ROOT/pip-cache}"
ACCELERATE_VERSION="${ACCELERATE_VERSION:-1.13.0}"

echo "accelerate before: $(python3 -c 'import accelerate; print(accelerate.__version__)')"
python3 -m pip install --quiet --no-deps "accelerate==$ACCELERATE_VERSION"
echo "accelerate after:  $(python3 -c 'import accelerate; print(accelerate.__version__)')"
python3 - <<'EOF'
import torch
from accelerate import init_empty_weights

param = torch.nn.Parameter(torch.zeros(1))
param._is_hf_initialized = True
with init_empty_weights():
    torch.nn.Linear(1, 1).weight = param
print("accelerate canary: OK")
EOF

python3 -m pip freeze --all --exclude-editable | LC_ALL=C sort > "$ENV_DIR/freeze.final.txt"
LC_ALL=C comm -13 "$ENV_DIR/freeze.base.txt" "$ENV_DIR/freeze.final.txt" \
    | { grep -v -i -E '^verl( @ |==)|^-e |^#' || true; } > "$ENV_DIR/delta.txt"
python3 -m pip wheel --quiet --no-deps -w "$WHEELHOUSE" -r "$ENV_DIR/delta.txt"
echo "applied=$(date -u +%Y-%m-%dT%H:%M:%SZ) accelerate==$ACCELERATE_VERSION" >> "$ENV_DIR/facts.txt"
