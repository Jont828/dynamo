#!/usr/bin/env bash
# One-time cache setup, run by k8s/setup-job.yaml (a Kubernetes Job; no kubectl exec). CPU only.
# Fills the cache volume (mounted at /cache) with what the RayJobs download otherwise:
#   1. Qwen/Qwen3-0.6B (CI's smoke model) and Qwen/Qwen2.5-1.5B (the demo's), in $HF_HOME
#   2. OpenMathInstruct-2's train_1M split, loaded and formatted by NeMo RL's own dataset class,
#      so the training run finds every step cached
# It also prints what the image carries. The Job runs on the image itself, so it pre-pulls it onto
# the node the demo will use. Idempotent: a re-run skips finished downloads.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
MODELS="${MODELS:-Qwen/Qwen3-0.6B Qwen/Qwen2.5-1.5B}"
step() { printf '\n[setup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

step "1/3 what the image carries"
echo "image built from NeMo RL commit ${NEMO_RL_COMMIT:-unknown}"
for bin in etcd nats-server; do
    printf '%-12s %s\n' "$bin" "$("${NEMO_RL_DYNAMO_BIN_DIR:-/opt/dynamo/bin}/$bin" --version 2>&1 | head -1)"
done
versions() {  # versions <label> <python> <package>...
    local label="$1" python="$2"
    shift 2
    "$python" - "$label" "$@" <<'EOF'
import importlib.metadata as md
import sys

label, pkgs = sys.argv[1], sys.argv[2:]
found = []
for pkg in pkgs:
    try:
        found.append(f"{pkg} {md.version(pkg)}")
    except md.PackageNotFoundError:
        found.append(f"{pkg} -")
print(f"{label:<16} " + ", ".join(found))
EOF
}
versions "driver venv" "$NEMO_RL_PYTHON" nemo-rl ray torch transformers datasets
versions "vLLM workers" "$VLLM_WORKER_VENV/bin/python" vllm ai-dynamo ai-dynamo-runtime torch nvidia-nccl-cu13 ray
versions "Megatron worker" "$MEGATRON_WORKER_VENV/bin/python" megatron-core megatron-bridge transformer-engine torch nvidia-nccl-cu13

step "2/3 models"
for model in $MODELS; do
    "$NEMO_RL_PYTHON" - "$model" <<'EOF'
import sys

from huggingface_hub import snapshot_download

path = snapshot_download(sys.argv[1])
print(f"{sys.argv[1]}: {path}")
EOF
done

step "3/3 OpenMathInstruct-2 (train_1M), formatted and split the way the demo config loads it"
cd "$NEMO_RL_DIR"
"$NEMO_RL_PYTHON" - <<'EOF'
import textwrap

from nemo_rl.data.datasets.response_datasets.openmathinstruct2 import OpenMathInstruct2Dataset

# grpo_math_1B.yaml: data.train.dataset_name OpenMathInstruct-2, split_validation_size 0.05, seed 42.
data = OpenMathInstruct2Dataset(split="train_1M", split_validation_size=0.05, seed=42)
print(f"OpenMathInstruct-2 train_1M: {len(data.dataset):,} training / {len(data.val_dataset):,} held-out problems")
# math_hf_data_processor sends the problem inside examples/prompts/cot.txt, through the chat template.
with open("examples/prompts/cot.txt") as fh:
    template = fh.read()
row = data.dataset[0]
print("First training prompt, as the model sees it (before the chat template):")
for line in template.format(row["messages"][0]["content"]).strip().splitlines():
    print(textwrap.fill(line, width=110, initial_indent="  │ ", subsequent_indent="  │ ") if line else "  │")
print(f"  expected answer: {row['messages'][1]['content']}  (the reward is 1 when the \\boxed{{}} answer matches)")
EOF

step "cache contents"
# huggingface_hub 1.x keeps large files in a shared $HF_HOME/hub/blobs store; -L follows the snapshot links.
du -shL "$HF_HOME"/hub/models--*/snapshots/* 2>/dev/null || true
du -sh "$HF_HOME"/hub "$HF_HOME"/datasets 2>/dev/null || true
step "done: the cache is ready for k8s/rayjob-smoke.yaml and k8s/rayjob.yaml"
