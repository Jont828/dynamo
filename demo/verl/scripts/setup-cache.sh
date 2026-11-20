#!/usr/bin/env bash
# One-time cache setup, run by k8s/setup-job.yaml (a Kubernetes Job; no kubectl exec).
# Fills the cache volume (mounted at /cache) with everything the RayJob needs:
#   1. verl + recipe at the pinned commits, and a wheelhouse of the packages they add to the
#      Dynamo image, including flash-attn compiled for H100 (build-env.sh, ~15 min the first time)
#   2. accelerate 1.13, which fixes 7B model loading under transformers 5 (apply-accelerate-fix.sh)
#   3. Qwen2.5-7B-Instruct and the GSM8K train/test splits in verl's format
# Idempotent: finished steps are skipped, so re-running the Job is cheap.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
MODEL_REPO="${MODEL_REPO:-Qwen/Qwen2.5-7B-Instruct}"
MODEL_DIR="$MODELS_DIR/${MODEL_REPO##*/}"
step() { printf '\n[setup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

step "1/3 verl, verl-recipe and the package wheelhouse"
if [ -s "$ENV_DIR/delta.txt" ] && grep -q '^accelerate==1.13' "$ENV_DIR/delta.txt"; then
    echo "already built: $(grep -E 'commit' "$ENV_DIR/BUILD_INFO.txt" | tr '\n' ' ')"
    bash "$SCRIPT_DIR/pod-bootstrap.sh"
else
    bash "$SCRIPT_DIR/build-env.sh"
    step "2/3 accelerate 1.13"
    bash "$SCRIPT_DIR/apply-accelerate-fix.sh"
fi

step "3/3 model and data"
if [ ! -f "$MODEL_DIR/.complete" ]; then
    python3 - "$MODEL_REPO" "$MODEL_DIR" <<'EOF'
import sys
from huggingface_hub import snapshot_download

snapshot_download(sys.argv[1], local_dir=sys.argv[2], allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"])
EOF
    touch "$MODEL_DIR/.complete"
fi
if [ ! -s "$DATA_DIR/gsm8k/test.parquet" ]; then
    python3 "$VERL_DIR/examples/data_preprocess/gsm8k.py" --local_save_dir "$DATA_DIR/gsm8k"
fi

step "cache contents"
grep -E '^(built_at|image|verl_recipe_commit|verl_commit)=' "$ENV_DIR/BUILD_INFO.txt"
python3 - <<'EOF'
import importlib.metadata as md
import textwrap

pkgs = ("ai-dynamo", "vllm", "torch", "transformers", "accelerate", "ray", "flash-attn", "TransferQueue", "verl")
text = "installed by the RayJob head at startup: " + ", ".join(f"{p} {md.version(p)}" for p in pkgs)
print(textwrap.fill(text, width=110, subsequent_indent="  "))
if md.version("ray") != "2.60.0":
    print(f"WARNING: ray {md.version('ray')} != 2.60.0: update rayVersion and the submitter image in k8s/rayjob.yaml")
EOF
echo
du -sh "$MODEL_DIR" "$DATA_DIR/gsm8k" "$WHEELHOUSE"
python3 - "$DATA_DIR/gsm8k" <<'EOF'
import sys
import textwrap

import pandas as pd

train = pd.read_parquet(f"{sys.argv[1]}/train.parquet")
test = pd.read_parquet(f"{sys.argv[1]}/test.parquet")
print(f"\nGSM8K: {len(train):,} train / {len(test):,} test prompts. First training prompt:")
row = train.iloc[0]
print(textwrap.fill(row.prompt[0]["content"], width=110, initial_indent="  ", subsequent_indent="  "))
print(f"  ground truth: {row.reward_model['ground_truth']}")
EOF
step "done: the cache is ready for k8s/rayjob.yaml"
