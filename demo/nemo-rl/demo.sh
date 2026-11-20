#!/usr/bin/env bash
# Narrated demo: GRPO training of Qwen2.5-1.5B on OpenMathInstruct-2 with NeMo RL, every rollout served
# through NVIDIA Dynamo's frontend and KV-aware router (policy.generation.dynamo: true), submitted as one
# KubeRay RayJob. Only kubectl apply / get / logs / wait / delete: no kubectl exec. Designed to be recorded:
#
#   asciinema rec --headless --window-size 120x42 -c ./demo.sh recordings/nemo-rl-dynamo-rayjob.raw.cast
#   python3 tools/pace_cast.py recordings/nemo-rl-dynamo-rayjob.raw.cast recordings/nemo-rl-dynamo-rayjob.cast
#   python3 tools/redact_cast.py recordings/nemo-rl-dynamo-rayjob.cast recordings/nemo-rl-dynamo-rayjob.public.cast
#
# Prerequisite (one-time, ~20 min): kubectl apply -k . && kubectl apply -f k8s/setup-job.yaml
# Knobs: KEEP_RAYJOB=1 (don't delete the finished RayJob), FAST=1 (no typing effect or pauses).
set -euo pipefail
cd "$(dirname "$0")"

NS=nemo-rl-demo
JOB=nemo-rl-grpo-demo
EVIDENCE=nemo-rl-dynamo-evidence
IMAGE=docker.io/jont828/nemo-rl:main-d51b2842-dynamo
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

excerpt() {  # a file excerpt from inside the image, shown but not run
    printf '\n  %s┌─ %s%s\n' "$CYN" "$1" "$RST"
    shift
    local line
    for line in "$@"; do printf '  %s│%s %s\n' "$CYN" "$RST" "$line"; done
    printf '  %s└─%s\n' "$CYN" "$RST"
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

  ${BOLD}NeMo RL + NVIDIA Dynamo on Kubernetes: an RL training loop as a KubeRay RayJob${RST}
  ${DIM}GRPO fine-tuning of Qwen2.5-1.5B on OpenMathInstruct-2 math problems, with Dynamo routing every rollout${RST}

EOF
say "NeMo RL's entrypoint is a Python script, examples/run_grpo_single_controller.py. With one config switch, policy.generation.dynamo set to true, its driver starts etcd, NATS and Dynamo's frontend with the KV-aware router itself, and NeMo RL's own vLLM engines register with that frontend. Every rollout then goes through the router, which sends each prompt to the engine that already holds most of it in its KV cache."
cat <<EOF

  ${DIM}┌────────────────────────────── RayJob ${RST}${BOLD}nemo-rl-grpo-demo${RST}${DIM} (KubeRay) ───────────────────────────────┐${RST}
  ${DIM}│${RST}  ${CYN}submitter Job${RST}  ── ray job submit ──►  ${CYN}Ray head pod${RST} · one node · 3× H100                        ${DIM}│${RST}
  ${DIM}│${RST}  kubectl logs -f job/nemo-rl-grpo-demo   ├─ driver: run_grpo_single_controller.py               ${DIM}│${RST}
  ${DIM}│${RST}                                          │    └─ ${MAG}Dynamo${RST}: etcd · NATS · frontend + KV router     ${DIM}│${RST}
  ${DIM}│${RST}                                          ├─ GPU 0: Megatron trainer (policy + reference)        ${DIM}│${RST}
  ${DIM}│${RST}                                          └─ GPUs 1–2: two vLLM engines, registered with ${MAG}Dynamo${RST}  ${DIM}│${RST}
  ${DIM}│${RST}  ${CYN}cache PVC${RST}: models, dataset, Megatron checkpoint and run logs, filled once by a setup Job       ${DIM}│${RST}
  ${DIM}└─────────────────────────────────────────────────────────────────────────────────────────────────┘${RST}
EOF
say "KubeRay's RayJob wraps that in one manifest. KubeRay creates the Ray cluster, waits until it is healthy, submits NeMo RL as a Ray job, streams the job's output into a Kubernetes Job's logs, and deletes the cluster when training ends. There is no Dynamo operator and no DynamoGraphDeployment here: Dynamo lives inside the Ray job. Everything below is kubectl apply, get, logs, wait and delete. Steps marked 'one-time' were already done on this cluster; for those we show what you would run on a fresh one."

# ── 1 ────────────────────────────────────────────────────────────────────────────────────
banner 1 "How many GPUs?" "every time"
say "The cluster has 4 H100 nodes with 8 GPUs each, and other work may be running on them."
run "kubectl config current-context"
run "python3 tools/gpu_free.py --need 3 --image $IMAGE"
say "Three GPUs on one node: one for the Megatron trainer and one for each of two vLLM engines, so the router has a choice. Generation can't share the trainer's GPU here: a sleeping engine would stay registered with the frontend and keep getting requests, so NeMo RL rejects colocated generation with Dynamo. The run is pinned to the node whose image cache the setup Job filled."

# ── 2 ────────────────────────────────────────────────────────────────────────────────────
banner 2 "Cluster prerequisites" "one-time · already in place"
say "Three things: GPUs advertised to Kubernetes, the KubeRay operator, and a storage class for the cache. A Hugging Face token secret is optional; the Qwen models are public. The Dynamo Kubernetes operator is not needed: NeMo RL's driver starts Dynamo itself, inside the Ray job."
run "kubectl get nodes -l kubernetes.azure.com/agentpool=ndh100pool -o custom-columns='NODE:.metadata.name,GPUS:.status.allocatable.nvidia\.com/gpu,VM:.metadata.labels.node\.kubernetes\.io/instance-type'"
run "kubectl get deploy -A -l app.kubernetes.io/name=kuberay-operator -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'"
run "kubectl get crd rayjobs.ray.io rayclusters.ray.io"
would "On a fresh AKS cluster you would run (not executed here):" \
    "helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace" \
    "helm install kuberay-operator kuberay/kuberay-operator -n kuberay --create-namespace --version 1.7.1" \
    "kubectl -n $NS create secret generic hf-token-secret --from-literal=HF_TOKEN=hf_...   # optional"

# ── 3 ────────────────────────────────────────────────────────────────────────────────────
banner 3 "Namespace, scripts and cache volume" "every time · idempotent"
say "One kustomization creates what every run shares: the namespace, a ConfigMap with the helper scripts, and the cache volume. The scripts are the cache setup, the job entrypoint, a log watcher and a results report."
run "grep -vE '^\s*#' kustomization.yaml | grep -E 'namespace:|name:|resources|k8s/|scripts/(setup|rayjob|watch|report|evidence)'"
run "kubectl apply -k ."
run "kubectl -n $NS get pvc nemo-rl-dynamo-cache"

# ── 4 ────────────────────────────────────────────────────────────────────────────────────
banner 4 "The image and the cache" "one-time · already done"
say "NeMo RL publishes no nightly images, so this one was built from NeMo RL main with its CI's own build command. ai-dynamo 1.5.1 is part of NeMo RL's vllm extra, so it ships in the vLLM workers' own environment, next to vLLM 0.29. BUILD_DYNAMO=1 only adds the etcd and nats-server binaries. The build ran in a BuildKit pod on this cluster."
would "Built once and pushed (not executed here):" \
    "git clone --recursive https://github.com/NVIDIA-NeMo/RL.git && cd RL && git checkout d51b2842" \
    "docker buildx build --file docker/Dockerfile --target release --build-context nemo-rl=. \\" \
    "    --build-arg BUILD_DYNAMO=1 --build-arg SKIP_SGLANG_BUILD=1 --build-arg SKIP_TRTLLM_BUILD=1 \\" \
    "    --build-arg STRIP_UV_CACHE=1 --tag $IMAGE --push ."
say "A one-time Kubernetes Job, on the same node the run will use, pulled the image there, printed what it carries, and filled the cache: both Qwen models and the OpenMathInstruct-2 split, formatted and split exactly as the training run loads it."
would "On a fresh cluster you would run, once (not executed here):" \
    "kubectl apply -f k8s/setup-job.yaml" \
    "kubectl -n $NS wait --for=condition=complete job/nemo-rl-dynamo-setup --timeout=90m"
run "kubectl -n $NS get job nemo-rl-dynamo-setup"
run "kubectl -n $NS logs job/nemo-rl-dynamo-setup | sed -n '/what the image carries/,/2\/3 models/p' | grep -v '2/3 models'"
say "Each training prompt is a competition-style math problem. The model is asked to reason step by step and put its final answer in a boxed tag; the reward is 1 when that answer matches the reference."
run "kubectl -n $NS logs job/nemo-rl-dynamo-setup | sed -n '/^OpenMathInstruct-2/,/expected answer/p'"

# ── 5 ────────────────────────────────────────────────────────────────────────────────────
banner 5 "The RayJob manifest" "every time"
say "The RayJob has three parts. The entrypoint is the command Ray runs as the job driver. The cluster spec is one head pod on the NeMo RL image with 3 GPUs, the cache and the scripts. The image works with KubeRay as is: its PATH starts with NeMo RL's own environment, so KubeRay's generated ray start runs NeMo RL's Ray 2.58. The lifecycle fields delete the cluster when the job ends and cap the whole run at one hour."
run "grep -nE '^  (entrypoint|submissionMode|shutdownAfterJobFinishes|ttlSecondsAfterFinished|activeDeadlineSeconds):|^ +(image|rayVersion|nvidia\.com/gpu|claimName|workerGroupSpecs):' k8s/rayjob.yaml"
say "The entrypoint runs NeMo RL's own launcher with its own Dynamo example config, and sets only the run's size and where it logs: 3 GPUs, of which 2 serve generation."
run "grep -E -- '--config|gpus_per_node|max_num_steps|log_dir' scripts/rayjob-entrypoint.sh"
say "That config is NeMo RL's SingleController GRPO recipe with Dynamo switched on: 32 math problems per step, 16 sampled answers each. dynamo_cfg is optional; these are its defaults."
excerpt "examples/configs/grpo_math_1B_dynamo_single_controller.yaml (in the image)" \
    "defaults: grpo_math_1B_megatron_single_controller.yaml" \
    "policy:" \
    "  generation:" \
    "    ${BOLD}dynamo: true${RST}" \
    "    dynamo_cfg:" \
    "      startup_timeout_s: 600" \
    "      frontend_args:" \
    "        router_mode: kv        ${DIM}# route each request to the engine holding most of its prefix${RST}"

# ── 6 ────────────────────────────────────────────────────────────────────────────────────
banner 6 "Submit the RayJob" "every time"
if kubectl -n "$NS" get rayjob "$JOB" >/dev/null 2>&1; then
    kubectl -n "$NS" delete rayjob "$JOB" --wait=true >/dev/null
fi
kubectl -n "$NS" delete job "$EVIDENCE" --ignore-not-found --wait=true >/dev/null
run "kubectl apply -f k8s/rayjob.yaml"
say "KubeRay now creates a RayCluster with one head pod. Its status moves from Initializing to Running once the head is healthy and the submitter Job has started."
run "kubectl -n $NS wait --for=jsonpath='{.status.jobDeploymentStatus}'=Running rayjob/$JOB --timeout=15m"
run "kubectl -n $NS get rayjob $JOB"
CLUSTER="$(rayjob_field rayClusterName)"
say "KubeRay created a RayCluster with one head pod, and once the head was healthy, a submitter Job on a small Ray image that runs ray job submit."
run "kubectl -n $NS get raycluster,pods,jobs -o wide | cut -c1-118"

# ── 7 ────────────────────────────────────────────────────────────────────────────────────
banner 7 "Watch the loop" "every time · ~3 min startup, then ~30 s per step"
say "The submitter Job's log is the job driver's output. First NeMo RL joins KubeRay's Ray cluster and loads the data. Then its driver starts Dynamo: etcd, NATS and the frontend. The Megatron trainer and the two vLLM engines load the model, and each engine registers with the frontend. Waiting is shortened in this recording; the timestamps show real elapsed time."
say "Each training step prints one row: the mean reward over its 512 answers, how closely the engines' token probabilities match the trainer's (gen_kl near 0 and prob_err near 1 mean the engines serve exactly the trainer's policy), the step, generation-wait, training and refit times, and the KV router's running totals: requests per engine, and the share that landed on an engine already holding their prompt in its KV cache. Generation runs one step ahead of training, so the trainer barely waits for rollouts. This is a short run of a 1.5B base model: rewards start low, because most of its answers run past the recipe's 512-token limit before they write a boxed answer. The results report follows at the end."
show "kubectl -n $NS logs -f job/$JOB"
follow_logs

# ── 8 ────────────────────────────────────────────────────────────────────────────────────
banner 8 "Dynamo evidence and status" "every time"
say "The outcome is recorded on the RayJob itself. KubeRay then deletes the Ray cluster, which frees the GPUs and takes every Dynamo and vLLM process with it."
run "kubectl -n $NS get rayjob $JOB"
say "The frontend ran inside the head pod and logged to the driver's output, which the run saved on the cache volume. A small Job reads the frontend's own lines back from there: each routing decision names the engine it picked, and each finished request says how many of its prompt's KV blocks were already cached on that engine."
run "kubectl apply -f k8s/evidence-job.yaml"
run "kubectl -n $NS wait --for=condition=complete job/$EVIDENCE --timeout=5m"
run "kubectl -n $NS logs job/$EVIDENCE"

# ── 9 ────────────────────────────────────────────────────────────────────────────────────
banner 9 "Teardown and clean up" "every time"
say "The Ray cluster is gone, so the three GPUs are free again. Logs stay on the cache volume under /cache/runs. Deleting the RayJob removes the submitter Job too, so the same manifest can be applied again for the next run."
run "kubectl -n $NS wait --for=delete raycluster/$CLUSTER --timeout=5m"
run "kubectl -n $NS get raycluster,pods,jobs"
run "python3 tools/gpu_free.py --need 3 --image $IMAGE"
if [ "$KEEP_RAYJOB" = 1 ]; then
    printf '  %s✓%s KEEP_RAYJOB=1: leaving rayjob/%s in place\n' "$GRN" "$RST" "$JOB"
else
    run "kubectl -n $NS delete rayjob $JOB && kubectl -n $NS delete job $EVIDENCE"
fi

checklist() { printf '  %s✓%s %-71s %s%s%s\n' "$GRN" "$RST" "$1" "$DIM" "$2" "$RST"; }
printf '\n  %sWhat it took%s\n' "$BOLD" "$RST"
checklist "1 node with 3 free GPUs" "every time"
checklist "GPU operator, KubeRay operator, a storage class; no Dynamo operator" "one-time, already in place"
checklist "An image built from NeMo RL main with BUILD_DYNAMO=1" "one-time, already pushed"
checklist "kubectl apply -k .  (namespace, scripts ConfigMap, cache PVC)" "every time, idempotent"
checklist "kubectl apply -f k8s/setup-job.yaml  (image pull, models, dataset)" "one-time, ~20 min"
checklist "kubectl apply -f k8s/rayjob.yaml + kubectl logs -f job/$JOB" "every time, ~17 min"
echo
