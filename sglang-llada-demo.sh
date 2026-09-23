#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Narrated demo-magic (https://github.com/paxtonhare/demo-magic) walkthrough of
# sglang-llada-dgd.yaml: deploy the LLaDA 2.0 diffusion language model DGD, wait for
# Ready, ask it a question, and print the answer. Narration types itself; ENTER types
# each command, ENTER again runs it.
#
#   ./sglang-llada-demo.sh                        # present against the pre-deployed DGD
#   SIMULATE_DEPLOY=false ./sglang-llada-demo.sh  # really apply and wait for Ready
#   ./sglang-llada-demo.sh -d -n                  # rehearse: no typing, no pauses (-w5 auto-advances)
#   ./sglang-llada-demo.sh cleanup                # delete the DGD and release the GPU
#
# By default the deploy is simulated: kubectl apply is typed with its usual output but
# not run, so preflight requires the DGD to be deployed, Ready, and identical to the
# manifest (kubectl diff). The Ready wait runs for real and returns at once, and the
# request goes to the existing DGD. With SIMULATE_DEPLOY=false, an identical Ready DGD
# is reused (no cold start). The DGD holds one H100: on a one-GPU budget, run cleanup
# before the next demo. The cluster's long-running sglang-llada-agg has the same serving
# spec but is a separate DGD; this script neither uses nor deletes it.
# Needs kubectl, curl, jq, python3, and pv for simulated typing (brew install pv).
# Env overrides: KUBE_CONTEXT, NAMESPACE, LOCAL_PORT, PROMPT, SIMULATE_DEPLOY, and
# DEMO_MAGIC (a local demo-magic.sh instead of the pinned, verified download).

KUBE_CONTEXT="${KUBE_CONTEXT:-h100}"
NAMESPACE="${NAMESPACE:-default}"
# Each *-demo.sh here defaults to its own port so several can run at once.
LOCAL_PORT="${LOCAL_PORT:-8012}"
PROMPT="${PROMPT:-Explain in three sentences why the sky appears blue.}"
SIMULATE_DEPLOY="${SIMULATE_DEPLOY:-true}"

MANIFEST=sglang-llada-dgd.yaml
DGD=sglang-llada-demo
MODEL=inclusionAI/LLaDA2.0-mini-preview
SELECTOR="nvidia.com/dynamo-graph-deployment-name=${DGD}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${REPO_ROOT}/runs/sglang-llada-demo"
PF_LOG="${OUT_DIR}/port-forward.log"

DEMO_MAGIC_COMMIT=142f0e70c6242456f166aaa75dd3de829ab7fe73
DEMO_MAGIC_SHA256=c949dcfa64b491a3e5a80569dd41c901963c06703a94c80a670f151d2fbead05

die() {
  printf '\033[0;31mERROR: %s\033[0m\n' "$*" >&2
  exit 1
}

warn() {
  printf '\033[0;33mWARNING: %s\033[0m\n' "$*" >&2
}

# Every kubectl call, including the ones typed on screen, targets this context and namespace.
kubectl() {
  command kubectl --context "$KUBE_CONTEXT" --namespace "$NAMESPACE" "$@"
}

dgd_ready() {
  kubectl get dgd "$DGD" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null
}

cd "$REPO_ROOT" || die "cannot cd to ${REPO_ROOT}"

if [[ "${1:-}" == cleanup ]]; then
  kubectl delete dgd "$DGD" --ignore-not-found --wait=true --timeout=3m || exit
  kubectl wait --for=delete pod -l "$SELECTOR" --timeout=3m >/dev/null 2>&1
  kubectl get pods -l "$SELECTOR"
  exit
fi

[[ "$SIMULATE_DEPLOY" == true || "$SIMULATE_DEPLOY" == false ]] || die "SIMULATE_DEPLOY must be true or false"
for tool in kubectl curl jq python3; do
  type -P "$tool" >/dev/null || die "missing required command: ${tool}"
done

mkdir -p "$OUT_DIR" || die "cannot create ${OUT_DIR}"
if [[ -z "${DEMO_MAGIC:-}" ]]; then
  DEMO_MAGIC="${OUT_DIR}/demo-magic.sh"
  if [[ ! -f "$DEMO_MAGIC" ]]; then
    curl --proto '=https' --tlsv1.2 -fsSL -o "${DEMO_MAGIC}.part" \
      "https://raw.githubusercontent.com/paxtonhare/demo-magic/${DEMO_MAGIC_COMMIT}/demo-magic.sh" &&
      mv "${DEMO_MAGIC}.part" "$DEMO_MAGIC" || die "could not download demo-magic.sh"
  fi
  sha=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$DEMO_MAGIC")
  [[ "$sha" == "$DEMO_MAGIC_SHA256" ]] || die "${DEMO_MAGIC} does not match the pinned SHA256; delete it and rerun"
fi
# demo-magic parses this script's flags: -d (no typing), -n (no pauses), -w<seconds> (auto-advance).
. "$DEMO_MAGIC"
DEMO_PROMPT="${GREEN}➜ ${CYAN}(${KUBE_CONTEXT}:${NAMESPACE}) ${COLOR_RESET}"
TIMEFORMAT='took %1Rs'

# Narration types at twice the command speed and never waits for ENTER.
narrate() {
  local line
  for line in "$@"; do
    TYPE_SPEED="${TYPE_SPEED:+$((TYPE_SPEED * 2))}" NO_WAIT=true p "# ${line}"
  done
}

kubectl get namespace "$NAMESPACE" -o name >/dev/null ||
  die "namespace ${NAMESPACE} not found in context ${KUBE_CONTEXT}"
[[ "$(kubectl get pvc pvc-lustre-dynamo -o jsonpath='{.status.phase}')" == Bound ]] ||
  die "PVC pvc-lustre-dynamo is missing or not Bound"
kubectl get secret hf-token-secret -o name >/dev/null || die "secret hf-token-secret not found"

# Never overwrite a same-name DGD; reuse it only if it already matches the manifest.
cold=true
if [[ -n "$(kubectl get dgd "$DGD" -o name --ignore-not-found)" ]]; then
  diff_out=$(kubectl diff -f "$MANIFEST" 2>&1)
  case $? in
    0) ;;
    1) printf '%s\n' "$diff_out" >&2; die "dgd/${DGD} already exists with a different spec" ;;
    *) printf '%s\n' "$diff_out" >&2; die "could not compare dgd/${DGD} with ${MANIFEST}" ;;
  esac
  [[ "$(dgd_ready)" == True ]] && cold=false
fi
if [[ "$SIMULATE_DEPLOY" == true ]]; then
  [[ "$cold" == false ]] ||
    die "simulating the deploy needs dgd/${DGD} deployed and Ready; deploy it first or set SIMULATE_DEPLOY=false"
else
  kubectl apply --dry-run=server -f "$MANIFEST" >/dev/null || die "server-side dry run of ${MANIFEST} failed"
  # Each demo DGD on this branch holds an H100; on a one-GPU budget this worker stays Pending until they go.
  others=$(kubectl get dgd --no-headers -o custom-columns=:metadata.name 2>/dev/null |
    grep -E '^vllm-omni-|^(sglang|trtllm)-.*-demo$' | grep -vxF "$DGD" | paste -sd ' ' -)
fi

jq -n --arg model "$MODEL" --arg prompt "$PROMPT" '{
  model: $model,
  messages: [{role: "user", content: $prompt}],
  temperature: 0,
  max_tokens: 256
}' >"${OUT_DIR}/request.json" || die "could not write request.json"

pf_pid=
stop_port_forward() {
  if [[ -n "$pf_pid" ]]; then kill "$pf_pid" 2>/dev/null; fi
}
trap stop_port_forward EXIT

[[ -z "$others" ]] ||
  warn "also deployed in ${NAMESPACE}: ${others}. Each holds an H100; on a one-GPU budget, clean up first."
if [[ "$SIMULATE_DEPLOY" == true ]]; then
  echo "Preflight OK (${KUBE_CONTEXT}:${NAMESPACE}). dgd/${DGD} is Ready and matches ${MANIFEST}; the apply is simulated."
elif [[ "$cold" == true ]]; then
  echo "Preflight OK (${KUBE_CONTEXT}:${NAMESPACE}). Cold start: expect several minutes of loading."
else
  echo "Preflight OK (${KUBE_CONTEXT}:${NAMESPACE}). dgd/${DGD} is already deployed and Ready."
fi
echo "Press ENTER to start."
[[ "$NO_WAIT" == true ]] || wait
clear

narrate "Diffusion language model on NVIDIA Dynamo with SGLang" \
  "Model: ${MODEL}" \
  "One DynamoGraphDeployment: a CPU frontend plus one H100 worker"

narrate "Instead of generating one token at a time, LLaDA starts from masked tokens" \
  "and refines them over several steps"
narrate "The worker launches SGLang with a diffusion decoding algorithm:"
pe "sed -n '/command:/,/LowConfidence/p' ${MANIFEST}"

narrate "Deploy it; the Dynamo operator creates the frontend and worker pods"
if [[ "$SIMULATE_DEPLOY" == true ]]; then
  # Typed but not run: preflight proved the live DGD matches the manifest.
  p "kubectl apply -f ${MANIFEST}"
  echo "dynamographdeployment.nvidia.com/${DGD} created"
else
  pe "kubectl apply -f ${MANIFEST}"
fi

if [[ "$cold" == true ]]; then
  narrate "A cold start pulls images and loads the model, which takes several minutes"
fi
pe "kubectl wait --for=condition=Ready dgd/${DGD} --timeout=30m"
[[ "$(dgd_ready)" == True ]] ||
  die "dgd/${DGD} is not Ready; inspect: kubectl --context ${KUBE_CONTEXT} -n ${NAMESPACE} describe pods -l ${SELECTOR}"
pe "kubectl get pods -l ${SELECTOR}"

narrate "Forward the frontend's OpenAI-compatible API to this machine"
p "kubectl port-forward svc/${DGD}-frontend ${LOCAL_PORT}:8000 &"
# Truncate first so a previous run's log can't satisfy the readiness check.
: >"$PF_LOG"
command kubectl --context "$KUBE_CONTEXT" --namespace "$NAMESPACE" port-forward \
  --address 127.0.0.1 "svc/${DGD}-frontend" "${LOCAL_PORT}:8000" >"$PF_LOG" 2>&1 &
pf_pid=$!
for ((i = 0; i < 40; i++)); do
  grep -q '^Forwarding from' "$PF_LOG" && break
  kill -0 "$pf_pid" 2>/dev/null || break
  sleep 0.25
done
grep -m1 '^Forwarding from' "$PF_LOG" || die "port-forward failed: $(cat "$PF_LOG")"

listed=false
for ((i = 0; i < 60; i++)); do
  if curl -fs "http://127.0.0.1:${LOCAL_PORT}/v1/models" |
    jq -e --arg m "$MODEL" 'any(.data[]; .id == $m)' >/dev/null 2>&1; then
    listed=true
    break
  fi
  sleep 2
done
[[ "$listed" == true ]] || die "${MODEL} is not listed at /v1/models"

narrate "The model is discoverable through /v1/models"
pe "curl -s http://127.0.0.1:${LOCAL_PORT}/v1/models | jq -r '.data[].id'"

cd "$OUT_DIR" || die "cannot cd to ${OUT_DIR}"
rm -f response.json
narrate "Ask a question through the standard /v1/chat/completions endpoint"
pe "jq . request.json"
narrate "Greedy decoding with temperature 0 (about 4 s in testing)"
pe "time curl -sS --fail-with-body --max-time 600 \\
  http://127.0.0.1:${LOCAL_PORT}/v1/chat/completions \\
  -H 'Content-Type: application/json' --data-binary @request.json \\
  -o response.json"
jq -e '.choices[0].message.content | length > 0' response.json >/dev/null 2>&1 ||
  die "chat request failed: $(head -c 2000 response.json 2>/dev/null)"

narrate "The answer:"
pe "jq -r '.choices[0].message.content' response.json"
p ""

echo "dgd/${DGD} is still running on ${KUBE_CONTEXT}:${NAMESPACE}; the response is in ${OUT_DIR}."
echo "Release the GPU with: $0 cleanup"
