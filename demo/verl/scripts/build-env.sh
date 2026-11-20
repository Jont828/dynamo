#!/usr/bin/env bash
# One-time environment build for the Dynamo 1.5.1 re-check. Runs inside the test pod
# (image: vllm-runtime:1.5.1, root). Follows the recipe's documented install
# (install_verl.sh --method git) and RECORDS each problem from the 1.4.2 report in
# $ENV_DIR/facts.txt instead of silently working around it. Idempotent.
#
# Deliberately NOT applied here: the accelerate upgrade (problem 3 in the 1.4.2 report),
# so a 7B run can show whether the image still needs it. apply-accelerate-fix.sh does that.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export HF_HOME="${HF_HOME:-/shared/verl-dynamo/hf-home}"
# The image's HOME is not writable for pip's cache when running as root.
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$SHARED_ROOT/pip-cache}"
FACTS="$ENV_DIR/facts.txt"

log() { printf '\n[build-env %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
fact() { printf '%s\n' "$*" | tee -a "$FACTS"; }

mkdir -p "$SRC_DIR" "$WHEELHOUSE" "$ENV_DIR" "$RUNS_DIR" "$KITPY_DIR"

pkg_versions() {
    python3 - <<'EOF'
import importlib.metadata as md
for p in ("ai-dynamo", "ai-dynamo-runtime", "vllm", "vllm-omni", "torch", "transformers", "accelerate",
          "numpy", "scipy", "ray", "tensordict", "flash-attn", "TransferQueue", "verl"):
    try:
        print(f"{p}=={md.version(p)}")
    except md.PackageNotFoundError:
        print(f"{p}==MISSING")
EOF
}

# Problem 3's failure mode: accelerate < 1.13 forwards transformers 5's `_is_hf_initialized`
# parameter attribute into nn.Parameter() during meta-device init (untied-embedding models).
accelerate_canary() {
    python3 - <<'EOF' 2>&1 | tail -1
import torch
from accelerate import init_empty_weights

param = torch.nn.Parameter(torch.zeros(1))
param._is_hf_initialized = True
try:
    with init_empty_weights():
        torch.nn.Linear(1, 1).weight = param
    print("OK")
except TypeError as e:
    print(f"FAIL TypeError: {e}")
EOF
}

if ! python3 -m pip show verl >/dev/null 2>&1; then
    log "Recording the pristine image"
    : > "$FACTS"
    python3 -m pip freeze --all --exclude-editable | LC_ALL=C sort > "$ENV_DIR/freeze.base.txt"
    pkg_versions > "$ENV_DIR/versions.base.txt"
    fact "image=$DYNAMO_IMAGE"
    fact "python=$(python3 --version 2>&1) pip=$(python3 -m pip --version | cut -d' ' -f2)"
    sed 's/^/base: /' "$ENV_DIR/versions.base.txt" | tee -a "$FACTS"
    fact "base: git=$(command -v git || echo MISSING)"
    fact "base: etcd=$(command -v etcd || echo MISSING) nats-server=$(command -v nats-server || echo MISSING)"
    fact "base: nvcc=$("$CUDA_HOME/bin/nvcc" --version 2>/dev/null | sed -n 's/.*release \([0-9.]*\).*/\1/p')"
    for h in cusparse.h cusolverDn.h cublas_v2.h; do
        fact "base: $h=$([ -f "$CUDA_HOME/include/$h" ] && echo present || echo MISSING)"
    done
    fact "base: flash_attn import=$(python3 -c 'import flash_attn; print(flash_attn.__version__)' 2>&1 | tail -1)"
    # Problem 0: the guide's verl pin (d82d2777) imports this at import time in verl/utils/vllm/vllm_fp8_utils.py.
    fact "base: old-pin import FusedMoE=$(python3 -c 'from vllm.model_executor.layers.fused_moe.layer import FusedMoE; print("OK")' 2>&1 | tail -1)"
    fact "base: accelerate canary=$(accelerate_canary)"
    fact "base: scipy.sparse import=$(python3 -c 'import scipy.sparse; print("OK")' 2>&1 | tail -1)"
else
    log "verl already installed; keeping the pristine-image facts"
fi

if ! python3 -c 'import importlib.metadata as m, sys; sys.exit(m.version("vllm") != "0.28.0")'; then
    echo "ERROR: expected vLLM 0.28.0 (ai-dynamo 1.5.x); got $(python3 -c 'import importlib.metadata as m; print(m.version("vllm"))')" >&2
    exit 1
fi

# Problem 1: the recipe installer clones with git.
if ! command -v git >/dev/null; then
    log "Installing git (absent from the image)"
    apt-get update -y -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends git ca-certificates
fi

log "Checking out verl-recipe@$VERL_RECIPE_COMMIT"
if [ ! -d "$SRC_DIR/verl-recipe/.git" ]; then
    git clone --quiet "$VERL_RECIPE_REPO" "$SRC_DIR/verl-recipe"
fi
git -C "$SRC_DIR/verl-recipe" fetch --quiet origin "$VERL_RECIPE_COMMIT"
git -C "$SRC_DIR/verl-recipe" checkout --quiet --force "$VERL_RECIPE_COMMIT"
grep -q "^COMMIT=$VERL_COMMIT\$" "$SRC_DIR/verl-recipe/dynamo/REQUIRED_VERL.txt" || {
    echo "ERROR: REQUIRED_VERL.txt no longer selects $VERL_COMMIT; update env.sh" >&2
    exit 1
}

if ! python3 -m pip show verl >/dev/null 2>&1; then
    log "Dry run: what 'pip install -e verl' changes in the image"
    if [ ! -d "$VERL_DIR/.git" ]; then
        git clone --quiet https://github.com/verl-project/verl.git "$VERL_DIR"
    fi
    git -C "$VERL_DIR" checkout --quiet "$VERL_COMMIT"
    if python3 -m pip install --quiet --dry-run --report "$ENV_DIR/verl-install-dryrun.json" -e "$VERL_DIR" >/dev/null; then
        python3 - "$ENV_DIR/verl-install-dryrun.json" "$ENV_DIR/freeze.base.txt" <<'EOF' | tee -a "$FACTS"
import json
import sys

base = {}
for line in open(sys.argv[2]):
    name, sep, ver = line.strip().partition("==")
    if sep:
        base[name.lower().replace("_", "-")] = ver
for item in json.load(open(sys.argv[1])).get("install", []):
    name = item["metadata"]["name"].lower().replace("_", "-")
    if name != "verl":
        print(f"verl-install: {name} {base.get(name, '(new)')} -> {item['metadata']['version']}")
EOF
    else
        fact "verl-install: dry run FAILED (see pip output above)"
    fi
fi

log "Installing pinned verl with the recipe installer (pip install -e, resolves verl's deps)"
(cd "$SRC_DIR/verl-recipe" && ./install_verl.sh --recipe dynamo --method git --dest "$VERL_DIR" --yes)
test "$(git -C "$VERL_DIR" rev-parse HEAD)" = "$VERL_COMMIT"

log "Moving the nested recipe checkout to the reviewed snapshot"
test -e "$VERL_DIR/recipe/.git" || { echo "ERROR: $VERL_DIR/recipe is not an initialized submodule" >&2; exit 1; }
git -C "$VERL_DIR/recipe" fetch --quiet origin "$VERL_RECIPE_COMMIT"
git -C "$VERL_DIR/recipe" checkout --quiet --force "$VERL_RECIPE_COMMIT"
test -f "$VERL_DIR/recipe/dynamo/register.py"

log "Installing TransferQueue $TRANSFER_QUEUE_VERSION (the recipe README's V1-trainer requirement)"
python3 -m pip install --quiet "TransferQueue==$TRANSFER_QUEUE_VERSION"

log "Ensuring flash-attn (verl's default attn_implementation is still flash_attention_2)"
if python3 -c "import flash_attn" 2>/dev/null; then
    echo "flash-attn already importable"
else
    torch_ver="$(python3 -c 'import torch; print(torch.__version__)')"
    old_whl="$(compgen -G "$OLD_WHEELHOUSE/flash_attn-$FLASH_ATTN_VERSION-*.whl" | head -1 || true)"
    if [ "$torch_ver" = "2.11.0+cu130" ] && [ -n "$old_whl" ]; then
        echo "reusing the 1.4.2 build's wheel (built against the same torch $torch_ver): $old_whl"
        cp -n "$old_whl" "$WHEELHOUSE/"
    elif ! compgen -G "$WHEELHOUSE/flash_attn-$FLASH_ATTN_VERSION-*.whl" >/dev/null; then
        log "Building flash-attn $FLASH_ATTN_VERSION for sm_90 against torch $torch_ver"
        if [ ! -f "$CUDA_HOME/include/cusparse.h" ] || [ ! -f "$CUDA_HOME/include/cusolverDn.h" ]; then
            cuda_dash="$("$CUDA_HOME/bin/nvcc" --version | sed -n 's/.*release \([0-9]*\)\.\([0-9]*\).*/\1-\2/p')"
            apt-get update -y -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
                "cuda-libraries-dev-$cuda_dash" "cuda-cudart-dev-$cuda_dash"
        fi
        python3 -m pip install --quiet packaging psutil ninja einops
        CUDA_HOME="$CUDA_HOME" FLASH_ATTN_CUDA_ARCHS=90 TORCH_CUDA_ARCH_LIST=9.0 \
        MAX_JOBS="${MAX_JOBS:-24}" NVCC_THREADS="${NVCC_THREADS:-2}" \
            python3 -m pip wheel --no-build-isolation --no-deps -w "$WHEELHOUSE" "flash-attn==$FLASH_ATTN_VERSION"
    fi
    python3 -m pip install --quiet --no-deps "$(compgen -G "$WHEELHOUSE/flash_attn-$FLASH_ATTN_VERSION-*.whl" | head -1)"
fi

cp "$SCRIPT_DIR/verl_dynamo_kitlog.py" "$KITPY_DIR/"

log "Recording the post-install state"
pkg_versions > "$ENV_DIR/versions.final.txt"
sed 's/^/final: /' "$ENV_DIR/versions.final.txt" | tee -a "$FACTS"
fact "final: scipy.sparse import=$(python3 -c 'import scipy.sparse; print("OK")' 2>&1 | tail -1)"
fact "final: vllm SamplingParams import=$(python3 -c 'from vllm import SamplingParams; print("OK")' 2>&1 | tail -1)"
fact "final: dynamo.vllm.main import=$(python3 -c 'import dynamo.vllm.main; print("OK")' 2>&1 | tail -1)"
fact "final: accelerate canary=$(accelerate_canary)"
python3 -m pip check > "$ENV_DIR/pip-check.txt" 2>&1 || true
sed 's/^/pip-check: /' "$ENV_DIR/pip-check.txt" | tee -a "$FACTS"

log "Recording the package delta a restarted pod re-installs offline"
python3 -m pip freeze --all --exclude-editable | LC_ALL=C sort > "$ENV_DIR/freeze.final.txt"
LC_ALL=C comm -13 "$ENV_DIR/freeze.base.txt" "$ENV_DIR/freeze.final.txt" \
    | { grep -v -i -E '^verl( @ |==)|^-e |^#' || true; } > "$ENV_DIR/delta.txt"
cat "$ENV_DIR/delta.txt"
python3 -m pip wheel --quiet --no-deps -w "$WHEELHOUSE" -r "$ENV_DIR/delta.txt"

log "Import checks (Dynamo hooks the recipe patches, recipe registration, log shim)"
PYTHONPATH="$VERL_DIR:$KITPY_DIR" VERL_USE_EXTERNAL_MODULES="$EXTERNAL_MODULES" python3 - <<'EOF'
import importlib.metadata as md
import shutil

import flash_attn
import numpy as np
from vllm import SamplingParams  # noqa: F401

import dynamo.vllm.main as dyn_main

assert hasattr(dyn_main, "setup_vllm_engine") and hasattr(dyn_main, "worker"), "recipe engine-capture hooks missing"
import verl  # noqa: F401  (imports VERL_USE_EXTERNAL_MODULES)
import recipe.dynamo.dynamo_vllm_rollout  # noqa: F401
import recipe.dynamo.register  # noqa: F401

print("numpy", np.__version__, "| flash_attn", flash_attn.__version__, "| TransferQueue", md.version("TransferQueue"),
      "| etcd", shutil.which("etcd"), "| nats", shutil.which("nats-server"))
EOF

{
    echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "image=$DYNAMO_IMAGE"
    echo "verl_recipe_commit=$(git -C "$VERL_DIR/recipe" rev-parse HEAD)"
    echo "verl_commit=$(git -C "$VERL_DIR" rev-parse HEAD)"
    cat "$ENV_DIR/versions.final.txt"
} > "$ENV_DIR/BUILD_INFO.txt"
cat "$ENV_DIR/BUILD_INFO.txt"
log "Done"
