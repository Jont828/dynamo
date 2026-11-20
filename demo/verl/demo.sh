#!/usr/bin/env bash
# Narrated demo, Kubernetes-native version: GRPO training of Qwen2.5-7B-Instruct on GSM8K with verl and
# the verl-recipe Dynamo rollout backend, submitted as a KubeRay RayJob. Only kubectl apply / get /
# logs / wait / delete: no kubectl exec. Designed to be recorded:
#
#   asciinema rec --headless --window-size 120x42 -c ./demo.sh recordings/verl-dynamo-rayjob.raw.cast
#   python3 tools/pace_cast.py recordings/verl-dynamo-rayjob.raw.cast recordings/verl-dynamo-rayjob.cast
#
# Prerequisite (one-time, ~20 min): kubectl apply -k . && kubectl apply -f k8s/setup-job.yaml
# Knobs: KEEP_RAYJOB=1 (don't delete the finished RayJob), FAST=1 (no typing effect or pauses).
set -euo pipefail
cd "$(dirname "$0")"

NS=default
JOB=verl-grpo-demo
IMAGE=nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1
KEEP_RAYJOB="${KEEP_RAYJOB:-0}"
TOTAL=9
if [ "${FAST:-0}" = 1 ]; then CHAR_DELAY=0; WORD_DELAY=0; PAUSE=0; else CHAR_DELAY=0.02; WORD_DELAY=0.07; PAUSE=2.5; fi

E=$'\e'
BOLD="$E[1m" DIM="$E[2m" ITAL="$E[3m" RST="$E[0m"
GRN="$E[32m" YEL="$E[33m" BLU="$E[34m" MAG="$E[35m" CYN="$E[36m"
RULE="$(printf '━%.0s' $(seq 1 116))"

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

rayjob_field() { kubectl -n "$NS" get rayjob "$JOB" -o jsonpath="{.status.$1}" 2>/dev/null || true; }

follow_logs() {  # stream the submitter's log; reattach without repeating lines if the stream drops
    local log printed=0 state
    log="$(mktemp)"
    while :; do
        kubectl -n "$NS" logs -f "job/$JOB" --pod-running-timeout=10m 2>/dev/null \
            | awk -v skip="$printed" 'NR > skip { print; fflush() }' | tee -a "$log" || true
        printed="$(wc -l < "$log" | tr -d ' ')"
        state="$(rayjob_field jobDeploymentStatus)"
        case "$state" in Complete|Failed|"") break ;; esac
        sleep 5
    done
    rm -f "$log"
}

# ── Intro ────────────────────────────────────────────────────────────────────────────────
[ "${FAST:-0}" = 1 ] || clear
cat <<EOF

  ${BOLD}verl + NVIDIA Dynamo on Kubernetes: an RL training loop as a KubeRay RayJob${RST}
  ${DIM}GRPO fine-tuning of Qwen2.5-7B-Instruct on GSM8K math problems, with Dynamo serving every rollout${RST}

EOF
say "verl's entrypoint is a Python module, python3 -m verl.trainer.main_ppo. It calls ray.init(): in a bare pod that starts a private Ray cluster inside the process, and as a Ray job it joins the cluster it was submitted to. The Dynamo backend from verl-recipe then starts a Dynamo frontend with a KV-aware router and four vLLM engines inside that Ray cluster, next to the trainer."
cat <<EOF

  ${DIM}┌─────────────────────────────── RayJob ${RST}${BOLD}verl-grpo-demo${RST}${DIM} (KubeRay) ─────────────────────────────────┐${RST}
  ${DIM}│${RST}  ${CYN}submitter Job${RST}  ── ray job submit ──►  ${CYN}Ray head pod${RST} · one node · 8× H100                        ${DIM}│${RST}
  ${DIM}│${RST}  kubectl logs -f job/verl-grpo-demo      ├─ job driver: python3 -m verl.trainer.main_ppo        ${DIM}│${RST}
  ${DIM}│${RST}                                          ├─ FSDP actor + reference policy, GPUs 0–7             ${DIM}│${RST}
  ${DIM}│${RST}                                          └─ ${MAG}Dynamo${RST}: frontend + KV router, 4 vLLM engines (TP 2) ${DIM}│${RST}
  ${DIM}│${RST}  ${CYN}cache PVC${RST}: verl build, model, data and run logs, filled once by a setup Job                    ${DIM}│${RST}
  ${DIM}└─────────────────────────────────────────────────────────────────────────────────────────────────┘${RST}
EOF
say "KubeRay's RayJob wraps that in one manifest. KubeRay creates the Ray cluster, waits until it is healthy, submits verl as a Ray job, streams the job's output into a Kubernetes Job's logs, and deletes the cluster when training ends. Everything below is kubectl apply, get, logs, wait and delete. Steps marked 'one-time' were already done on this cluster; for those we show what you would run on a fresh one."

# ── 1 ────────────────────────────────────────────────────────────────────────────────────
banner 1 "How many GPUs?" "every time"
say "The cluster has 4 H100 nodes with 8 GPUs each, and other work may be running on them."
run "kubectl config current-context"
run "python3 tools/gpu_free.py --image $IMAGE"
say "One node is enough. For a 7B model, FSDP shards the actor and reference policy over all 8 GPUs, and the 4 vLLM engines share those same GPUs: they sleep while the trainer updates and wake up with the new weights. More nodes would only add rollout throughput. The cache is an Azure Disk that attaches to whichever node the run lands on."

# ── 2 ────────────────────────────────────────────────────────────────────────────────────
banner 2 "Cluster prerequisites" "one-time · already in place"
say "Three things: GPUs advertised to Kubernetes, the KubeRay operator, and a storage class for the cache. A Hugging Face token secret is optional. The Dynamo Kubernetes operator is not needed: the recipe runs Dynamo inside the Ray cluster, because the engines take new weights from the trainer over CUDA IPC on the same GPUs."
run "kubectl get nodes -l kubernetes.azure.com/agentpool=ndh100pool -o custom-columns='NODE:.metadata.name,GPUS:.status.allocatable.nvidia\.com/gpu,VM:.metadata.labels.node\.kubernetes\.io/instance-type'"
run "kubectl get deploy -A -l app.kubernetes.io/name=kuberay-operator -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'"
run "kubectl get crd rayjobs.ray.io rayclusters.ray.io"
would "On a fresh AKS cluster you would run (not executed here):" \
    "helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace" \
    "helm install kuberay-operator kuberay/kuberay-operator -n kuberay --create-namespace --version 1.7.1" \
    "kubectl create secret generic hf-token-secret --from-literal=HF_TOKEN=hf_...   # optional"

# ── 3 ────────────────────────────────────────────────────────────────────────────────────
banner 3 "Scripts and cache volume" "every time · idempotent"
say "One kustomization creates what every run shares: a ConfigMap with the helper scripts, and the cache volume. The scripts are the environment build, the training launcher, a log watcher and a results report."
run "grep -vE '^\s*#' kustomization.yaml | grep -E 'name:|resources|k8s/|scripts/(setup|rayjob|run-demo|watch|report)'"
run "kubectl apply -k ."
run "kubectl -n $NS get pvc verl-dynamo-cache"

# ── 4 ────────────────────────────────────────────────────────────────────────────────────
banner 4 "Fill the cache: a setup Job" "one-time · ~20 min · already done"
say "The Dynamo image has Dynamo and vLLM 0.28, the engine version the recipe is tested with, but not verl. A one-time Kubernetes Job installs verl and the recipe at their pinned commits, builds flash-attn for H100, collects every added package as wheels, and downloads the model and GSM8K. It also upgrades accelerate to 1.13, because the image's 1.12 breaks loading 7B models under transformers 5."
would "On a fresh cluster you would run, once (not executed here):" \
    "kubectl apply -f k8s/setup-job.yaml" \
    "kubectl wait --for=condition=complete job/verl-dynamo-setup --timeout=90m"
run "kubectl -n $NS get job verl-dynamo-setup"
say "Its log ends with what is in the cache. Every prompt asks for the final answer after '####'. The reward is 1 when that number matches the ground truth, and the grader is strict: '#### 71' counts, '#### \$71' does not."
run "kubectl -n $NS logs job/verl-dynamo-setup | sed -n '/cache contents/,\$p'"

# ── 5 ────────────────────────────────────────────────────────────────────────────────────
banner 5 "The RayJob manifest" "every time"
say "The RayJob has three parts. The entrypoint is the command Ray runs as the job driver. The cluster spec is one head pod on the Dynamo image with all 8 GPUs, the cache and the scripts. The Dynamo image ships without Ray, so before KubeRay's own ray start the pod installs Ray, verl and the other packages from the cached wheels, in about a minute. The lifecycle fields delete the cluster when the job ends and cap the whole run at one hour."
run "grep -nE '^  (entrypoint|submissionMode|shutdownAfterJobFinishes|ttlSecondsAfterFinished|activeDeadlineSeconds):|^ +(image|command|rayVersion|nvidia\.com/gpu|claimName|workerGroupSpecs):' k8s/rayjob.yaml"
say "The entrypoint wraps verl's main_ppo with the recipe's V1 colocate preset and GRPO: 64 questions per step, 8 sampled answers each, 15 steps, and a test on the held-out split before training and every 5 steps. Dynamo options need a '++' prefix, because the preset doesn't declare them."
run "grep -E -- '^ +.*(--config-name|adv_estimator|rollout\.name|router_mode|free_engine|tensor_model|rollout\.n=)' scripts/run-demo-train.sh"

# ── 6 ────────────────────────────────────────────────────────────────────────────────────
banner 6 "Submit the RayJob" "every time"
if kubectl -n "$NS" get rayjob "$JOB" >/dev/null 2>&1; then
    kubectl -n "$NS" delete rayjob "$JOB" --wait=true >/dev/null
fi
run "kubectl apply -f k8s/rayjob.yaml"
say "KubeRay now creates a RayCluster with one head pod. Its status moves from Initializing to Running once the head is healthy and the submitter Job has started."
run "kubectl -n $NS wait --for=jsonpath='{.status.jobDeploymentStatus}'=Running rayjob/$JOB --timeout=15m"
run "kubectl -n $NS get rayjob $JOB"
CLUSTER="$(rayjob_field rayClusterName)"
say "KubeRay created a RayCluster with one head pod, and once the head was healthy, a submitter Job on a small Ray image that runs ray job submit."
run "kubectl -n $NS get raycluster,pods,jobs -o wide | grep -E '^NAME|$JOB' | cut -c1-118"

# ── 7 ────────────────────────────────────────────────────────────────────────────────────
banner 7 "Watch the loop" "every time · 4–6 min startup, then ~20 s per step"
say "The submitter Job's log is the job driver's output. First, Ray and 8 trainer workers load the model. Then the recipe brings up Dynamo: etcd, NATS, the frontend, and 4 vLLM engines that register with the router. Waiting is shortened in this recording; the timestamps show real elapsed time."
say "Each training step prints one row: the mean reward over its 512 answers, the share the grader can parse, rollout time on Dynamo, training time, the time to push new weights into the engines, and how closely the engines' token probabilities match the trainer's (1.0 means identical). The results report follows at the end."
show "kubectl -n $NS logs -f job/$JOB"
follow_logs

# ── 8 ────────────────────────────────────────────────────────────────────────────────────
banner 8 "Status and teardown" "every time"
say "The outcome is recorded on the RayJob itself. KubeRay then deletes the Ray cluster, which frees the GPUs and takes every Dynamo and vLLM process with it, so nothing is left holding GPU memory."
run "kubectl -n $NS get rayjob $JOB"
run "kubectl -n $NS wait --for=delete raycluster/$CLUSTER --timeout=5m"
run "kubectl -n $NS get raycluster,pods,jobs | grep -E '^NAME|$JOB'"
run "python3 tools/gpu_free.py --image $IMAGE"

# ── 9 ────────────────────────────────────────────────────────────────────────────────────
banner 9 "Clean up" "every time"
say "Logs, rollouts and validation answers stay on the cache volume under /cache/runs. Deleting the RayJob removes the submitter Job too, so the same manifest can be applied again for the next run."
if [ "$KEEP_RAYJOB" = 1 ]; then
    printf '  %s✓%s KEEP_RAYJOB=1: leaving rayjob/%s in place\n' "$GRN" "$RST" "$JOB"
else
    run "kubectl -n $NS delete rayjob $JOB"
fi

checklist() { printf '  %s✓%s %-71s %s%s%s\n' "$GRN" "$RST" "$1" "$DIM" "$2" "$RST"; }
printf '\n  %sWhat it took%s\n' "$BOLD" "$RST"
checklist "1 node with 8 free GPUs" "every time"
checklist "GPU operator, KubeRay operator, a storage class, HF token secret" "one-time, already in place"
checklist "kubectl apply -k .  (scripts ConfigMap + cache PVC)" "every time, idempotent"
checklist "kubectl apply -f k8s/setup-job.yaml  (verl, flash-attn, model, data)" "one-time, ~20 min"
checklist "kubectl apply -f k8s/rayjob.yaml + kubectl logs -f job/$JOB" "every time, ~15 min"
echo
