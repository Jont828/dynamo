#!/usr/bin/env bash
# Narrated demo, exec-based version (the one in recordings/verl-dynamo-grpo*.cast): GRPO training of
# Qwen2.5-7B-Instruct on GSM8K with verl and the verl-recipe Dynamo rollout backend, on one 8-GPU AKS
# node. Superseded by demo.sh (KubeRay RayJob, no kubectl exec). Designed to be recorded:
#
#   asciinema rec --headless --window-size 120x42 -c ./demo-exec.sh recordings/verl-dynamo-grpo.raw.cast
#   python3 tools/pace_cast.py recordings/verl-dynamo-grpo.raw.cast recordings/verl-dynamo-grpo.cast
#   agg --theme github-dark --font-size 14 --idle-time-limit 30 --fps-cap 15 \
#       recordings/verl-dynamo-grpo.cast recordings/verl-dynamo-grpo.gif
#
# Knobs: STEPS (15), TEST_FREQ (5), RUN_NAME, NODE (auto), KEEP_POD=1 (skip deletion),
#        FAST=1 (no typing effect or pauses).
set -euo pipefail
cd "$(dirname "$0")"

NS=default
POD=verl-dynamo-demo
IMAGE=nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1
STEPS="${STEPS:-15}"
TEST_FREQ="${TEST_FREQ:-5}"
RUN_NAME="${RUN_NAME:-demo-$(date +%Y%m%d-%H%M)}"
RUN_DIR="/shared/verl-dynamo-demo/runs/$RUN_NAME"
KEEP_POD="${KEEP_POD:-0}"
TOTAL=10
if [ "${FAST:-0}" = 1 ]; then CHAR_DELAY=0; WORD_DELAY=0; PAUSE=0; else CHAR_DELAY=0.02; WORD_DELAY=0.07; PAUSE=2.5; fi

E=$'\e'
BOLD="$E[1m" DIM="$E[2m" ITAL="$E[3m" RST="$E[0m"
GRN="$E[32m" YEL="$E[33m" BLU="$E[34m" MAG="$E[35m" CYN="$E[36m"
RULE="$(printf '━%.0s' $(seq 1 116))"

k() { kubectl -n "$NS" "$@"; }

banner() {  # banner N "Title" "tag"
    [ "${FAST:-0}" = 1 ] || clear
    printf '%s%s%s\n' "$BLU" "$RULE" "$RST"
    printf ' %sSTEP %s/%s%s  %s%s%s   %s[%s]%s\n' "$BOLD$BLU" "$1" "$TOTAL" "$RST" "$BOLD" "$2" "$RST" "$DIM" "$3" "$RST"
    printf '%s%s%s\n' "$BLU" "$RULE" "$RST"
}

say() {  # narration, typed a word at a time at reading pace
    local line words w
    echo
    while IFS= read -r line; do
        printf '  %s▌%s %s' "$MAG" "$RST" "$ITAL"
        read -ra words <<< "$line"
        for w in "${words[@]}"; do printf '%s ' "$w"; sleep "$WORD_DELAY"; done
        printf '%s\n' "$RST"
    done < <(printf '%s\n' "$*" | fold -s -w 104)
    sleep "$PAUSE"
}

show() {  # type a command without running it
    local s="$1" i
    printf '\n%s$%s ' "$GRN$BOLD" "$RST"
    for ((i = 0; i < ${#s}; i++)); do printf '%s' "${s:i:1}"; sleep "$CHAR_DELAY"; done
    printf '\n'
}

run() { show "$1"; eval "$1"; }

would() {  # commands for a fresh cluster, shown but not run
    printf '\n  %s┌─ %s%s\n' "$YEL" "$1" "$RST"
    shift
    local line
    for line in "$@"; do printf '  %s│%s %s%s%s\n' "$YEL" "$RST" "$DIM" "$line" "$RST"; done
    printf '  %s└─%s\n' "$YEL" "$RST"
    sleep "$PAUSE"
}

ok() { printf '  %s✓%s %s\n' "$GRN" "$RST" "$*"; }

watch_training() {  # stream the watcher; reattach without repeating lines if kubectl exec drops
    local log printed=0
    log="$(mktemp)"
    while :; do
        k exec "$POD" -- python3 /opt/verl-dynamo-demo/watch_run.py "$RUN_DIR" --skip "$printed" | tee -a "$log" || true
        printed="$(wc -l < "$log" | tr -d ' ')"
        k exec "$POD" -- grep -q '^done' "$RUN_DIR/STATUS" 2>/dev/null && break
        sleep 5
    done
    rm -f "$log"
}

# ── Intro ────────────────────────────────────────────────────────────────────────────────
[ "${FAST:-0}" = 1 ] || clear
cat <<EOF

  ${BOLD}verl + NVIDIA Dynamo: an RL training loop on AKS${RST}
  ${DIM}GRPO fine-tuning of Qwen2.5-7B-Instruct on GSM8K math problems, with Dynamo serving every rollout${RST}

EOF
say "Reinforcement learning for LLMs alternates two jobs. Rollout: the current model writes answers. Training: the answers are scored and the weights updated. verl runs that loop. The Dynamo backend from verl-recipe serves every rollout through a Dynamo frontend with KV-aware routing in front of vLLM engines, and takes the new weights after every update."
cat <<EOF

  ${DIM}┌──────────────────────────── one AKS node · 8× H100 · one pod ──────────────────────────────┐${RST}
  ${DIM}│${RST}  ${CYN}verl trainer${RST} (Ray)                                                                        ${DIM}│${RST}
  ${DIM}│${RST}   ├─ FSDP actor + reference policy, sharded over GPUs 0–7                                  ${DIM}│${RST}
  ${DIM}│${RST}   └─ GRPO loop ──── generate ────► ${MAG}Dynamo frontend${RST}  (OpenAI API + KV-aware router)         ${DIM}│${RST}
  ${DIM}│${RST}                                      ├──► ${MAG}vLLM engine 1${RST}  GPUs 0,1                          ${DIM}│${RST}
  ${DIM}│${RST}        ◄──── new weights (CUDA IPC) ─┼──► ${MAG}vLLM engine 2${RST}  GPUs 2,3                          ${DIM}│${RST}
  ${DIM}│${RST}                                      ├──► ${MAG}vLLM engine 3${RST}  GPUs 4,5                          ${DIM}│${RST}
  ${DIM}│${RST}                                      └──► ${MAG}vLLM engine 4${RST}  GPUs 6,7                          ${DIM}│${RST}
  ${DIM}│${RST}   etcd + NATS: worker discovery and KV-cache events, started by the recipe                 ${DIM}│${RST}
  ${DIM}└────────────────────────────────────────────────────────────────────────────────────────────┘${RST}
EOF
say "Trainer and engines share the GPUs: the engines sleep while the trainer updates, then wake up with the new weights. Ten steps follow. Steps marked 'one-time' were already done on this cluster; for those we show what you would run on a fresh one."

# ── 1 ────────────────────────────────────────────────────────────────────────────────────
banner 1 "How many GPUs? Find a free node" "every time"
say "First, what's free? The cluster has 4 H100 nodes with 8 GPUs each, and other work may be running on them."
run "kubectl config current-context"
run "python3 tools/gpu_free.py --image $IMAGE"
NODE="${NODE:-$(python3 tools/gpu_free.py --image "$IMAGE" --pick)}"
say "One node is enough. For a 7B model, FSDP shards the actor and reference policy over all 8 GPUs, and 4 vLLM engines with tensor parallelism 2 share those same GPUs. The recipe is tested on one node; more nodes would only add rollout throughput. (Smaller models need less: the recipe's smoke tests run a 0.5B model on 1 GPU.)"
say "We take one free, untainted node, preferring one that already caches the Dynamo image: $NODE. The other three stay free."

# ── 2 ────────────────────────────────────────────────────────────────────────────────────
banner 2 "Cluster prerequisites" "one-time · already in place"
say "The cluster needs three things: GPUs advertised to Kubernetes, a ReadWriteMany volume for the model, data and build cache, and optionally a Hugging Face token."
run "kubectl get node $NODE -o custom-columns='NODE:.metadata.name,GPUS:.status.allocatable.nvidia\.com/gpu,VM:.metadata.labels.node\.kubernetes\.io/instance-type'"
run "kubectl -n $NS get pvc pvc-lustre-ray"
run "kubectl -n $NS get secret hf-token-secret -o name"
would "On a fresh AKS cluster you would run (not executed here):" \
    "helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace   # or the NVIDIA device plugin" \
    "# an RWX volume: Azure Managed Lustre via the azurelustre CSI driver, or Azure Files Premium" \
    "kubectl create secret generic hf-token-secret --from-literal=HF_TOKEN=hf_..."
say "Note what is not needed: the Dynamo Kubernetes operator. The recipe starts its own Dynamo frontend, engines, etcd and NATS inside the training pod. This cluster runs the Dynamo platform for other workloads; the demo doesn't touch it."

# ── 3 ────────────────────────────────────────────────────────────────────────────────────
banner 3 "Ship the helper scripts" "every time"
say "The pod uses a few small scripts: the environment build, the training launcher, a log watcher and a results report. A ConfigMap mounts them at /opt/verl-dynamo-demo."
run "ls scripts/"
run "kubectl -n $NS create configmap verl-dynamo-demo-scripts --from-file=scripts/ --dry-run=client -o yaml | kubectl apply -f -"

# ── 4 ────────────────────────────────────────────────────────────────────────────────────
banner 4 "Launch the trainer pod" "every time"
say "One pod holds the whole loop. It runs Dynamo's vllm-runtime 1.5.1 image, which ships vLLM 0.28.0, the engine version the recipe is tested with. It takes all 8 GPUs, mounts the shared volume, and gives Ray and NCCL a 256 GiB /dev/shm."
run "grep -nE '^ +(image|kubernetes\.io/hostname|nvidia\.com/gpu|claimName|sizeLimit):' k8s/pod.yaml"
if k get pod "$POD" >/dev/null 2>&1; then k delete pod "$POD" --wait=true >/dev/null; fi
run "sed 's/__NODE__/$NODE/' k8s/pod.yaml | kubectl apply -f -"
run "kubectl -n $NS wait --for=condition=Ready pod/$POD --timeout=15m"
run "kubectl -n $NS get pod $POD -o wide"

# ── 5 ────────────────────────────────────────────────────────────────────────────────────
banner 5 "Software environment" "one-time · cached on the volume"
say "The image has Dynamo and vLLM but not verl. A one-time build installs verl at the commit the recipe pins, adds the recipe, TransferQueue for verl's V1 trainer, and flash-attn compiled for H100. It also upgrades accelerate to 1.13, because the image's 1.12 breaks loading 7B models under transformers 5. That takes about 15 minutes and lives on the shared volume, so here it is already done."
would "On a fresh cluster you would run, once (not executed here):" \
    "kubectl exec $POD -- bash /opt/verl-dynamo-demo/build-env.sh              # ~15 min" \
    "kubectl exec $POD -- bash /opt/verl-dynamo-demo/apply-accelerate-fix.sh"
say "Each time the pod starts, a bootstrap step reinstalls those packages from the cached wheels in about a minute."
run "kubectl -n $NS logs $POD | grep '\[bootstrap\]'"
run "kubectl -n $NS exec $POD -- grep -E 'commit' /shared/verl-dynamo-151/env/BUILD_INFO.txt"
run "kubectl -n $NS exec $POD -- python3 -c 'import importlib.metadata as m; [print(f\"{p:<15}{m.version(p)}\") for p in (\"ai-dynamo\", \"vllm\", \"torch\", \"transformers\", \"accelerate\", \"flash-attn\", \"TransferQueue\")]'"

# ── 6 ────────────────────────────────────────────────────────────────────────────────────
banner 6 "Model and data" "one-time · cached on the volume"
say "The model and dataset are on the shared volume too: Qwen2.5-7B-Instruct, and GSM8K with 7,473 grade-school math problems for training and 1,319 held out for testing."
run "kubectl -n $NS exec $POD -- ls /shared/verl-dynamo/models /shared/verl-dynamo/data/gsm8k"
would "On a fresh cluster you would run, once (not executed here):" \
    "kubectl exec $POD -- hf download Qwen/Qwen2.5-7B-Instruct --local-dir /shared/verl-dynamo/models/Qwen2.5-7B-Instruct" \
    "kubectl exec $POD -- python3 \$VERL_DIR/examples/data_preprocess/gsm8k.py --local_save_dir /shared/verl-dynamo/data/gsm8k"
say "Every prompt asks for the final answer after '####'. The reward is 1 when that number matches the ground truth and 0 otherwise. The grader is strict: '#### 71' counts, '#### \$71' does not. So the model has to be right and answer in the expected format, and early gains come largely from learning that format."
run "kubectl -n $NS exec $POD -- python3 -c \"import pandas as pd; r = pd.read_parquet('/shared/verl-dynamo/data/gsm8k/train.parquet').iloc[0]; print(r.prompt[0]['content']); print('ground truth:', r.reward_model['ground_truth'])\""

# ── 7 ────────────────────────────────────────────────────────────────────────────────────
banner 7 "Start the RL training loop" "every time"
say "Now the loop itself: verl's main_ppo entry point with the recipe's V1 colocate preset and GRPO. Each step takes 64 questions and the engines write 8 answers to each. Every answer is scored against its group's average, so the model learns from which of its own attempts worked. We run $STEPS steps and test on the held-out split before training and every $TEST_FREQ steps."
run "kubectl -n $NS exec $POD -- grep -E -- '^ +.*(--config-name|adv_estimator|rollout\.name|router_mode|free_engine|tensor_model|rollout\.n=|test_freq)' /opt/verl-dynamo-demo/run-demo-train.sh"
say "Three details matter. The Dynamo options need a '++' prefix, because the preset doesn't declare them. Console logging avoids a Weights & Biases login. And a 55-minute timeout caps the run so it can never hold the node for an hour."
run "kubectl -n $NS exec $POD -- bash -c 'mkdir -p /shared/verl-dynamo-demo/runs && RUN_NAME=$RUN_NAME STEPS=$STEPS TEST_FREQ=$TEST_FREQ setsid -f bash /opt/verl-dynamo-demo/run-demo-train.sh > /shared/verl-dynamo-demo/runs/$RUN_NAME.console.log 2>&1 < /dev/null'"
sleep 3
run "kubectl -n $NS exec $POD -- cat /shared/verl-dynamo-demo/runs/$RUN_NAME.console.log"

# ── 8 ────────────────────────────────────────────────────────────────────────────────────
banner 8 "Watch the loop" "every time · 5–12 min startup, then ~20–30 s per step"
say "Startup takes 5 to 12 minutes, mostly reading the 15 GB model from the shared volume. Ray starts, 8 trainer workers load the model, then the recipe brings up Dynamo: etcd, NATS, the frontend, and 4 vLLM engines that register with the router. Waiting is shortened in this recording; the timestamps show real elapsed time."
say "Each training step prints one row: the mean reward over its 512 answers, the share the grader can parse, rollout time on Dynamo, training time, the time to push new weights into the engines, and how closely the engines' token probabilities match the trainer's (1.0 means identical)."
show "kubectl -n $NS exec $POD -- python3 /opt/verl-dynamo-demo/watch_run.py $RUN_DIR"
watch_training

# ── 9 ────────────────────────────────────────────────────────────────────────────────────
banner 9 "Results" "every time"
say "What to look for: held-out accuracy going up, engine and trainer agreeing, every weight update landing, and the KV-aware router reusing cached prompts."
run "kubectl -n $NS exec $POD -- python3 /opt/verl-dynamo-demo/report_run.py $RUN_DIR"
sleep "$PAUSE"; sleep "$PAUSE"

# ── 10 ───────────────────────────────────────────────────────────────────────────────────
banner 10 "Clean up" "every time"
say "Logs, rollouts and validation answers stay on the shared volume under $RUN_DIR. Deleting the pod frees the node's 8 GPUs."
if [ "$KEEP_POD" = 1 ]; then
    ok "KEEP_POD=1: leaving $POD running"
else
    run "kubectl -n $NS delete pod $POD"
    run "python3 tools/gpu_free.py --image $IMAGE"
fi

checklist() { printf '  %s✓%s %-64s %s%s%s\n' "$GRN" "$RST" "$1" "$DIM" "$2" "$RST"; }
printf '\n  %sWhat it took%s\n' "$BOLD" "$RST"
checklist "1 node with 8 free GPUs" "every time"
checklist "GPU device plugin, RWX shared volume, HF token secret" "one-time, already in place"
checklist "Helper scripts as a ConfigMap; one pod on vllm-runtime:1.5.1" "every time, ~2 min"
checklist "verl + recipe, TransferQueue, flash-attn, accelerate 1.13" "one-time, ~15 min, cached"
checklist "Qwen2.5-7B-Instruct + GSM8K on the volume" "one-time, cached"
checklist "GRPO with Dynamo rollouts: $STEPS steps + validation" "every time, ~15 min"
echo
