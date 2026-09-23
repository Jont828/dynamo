#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Narrated demo-magic (https://github.com/paxtonhare/demo-magic) walkthrough of
# vllm-omni-audio.md: deploy the Qwen3-TTS DGD, wait for Ready, generate speech,
# and play it. Narration types itself; ENTER types each command, ENTER again runs it.
#
#   ./vllm-omni-audio-demo.sh                        # present against the pre-deployed DGD
#   SIMULATE_DEPLOY=false ./vllm-omni-audio-demo.sh  # really apply and wait for Ready
#   ./vllm-omni-audio-demo.sh -d -n                  # rehearse: no typing, no pauses (-w5 auto-advances)
#   ./vllm-omni-audio-demo.sh cleanup                # delete the DGD and release the GPU
#
# By default the deploy is simulated: kubectl apply is typed with its usual output but
# not run, so preflight requires the DGD to be deployed, Ready, and identical to the
# manifest (kubectl diff). The Ready wait runs for real and returns at once, and the
# request goes to the existing DGD. With SIMULATE_DEPLOY=false, an identical Ready DGD
# is reused (no cold start).
# Needs kubectl, curl, jq, python3, and pv for simulated typing (brew install pv).
# Env overrides: KUBE_CONTEXT, NAMESPACE, LOCAL_PORT, PLAYER, TTS_INPUT, SIMULATE_DEPLOY,
# and DEMO_MAGIC (a local demo-magic.sh instead of the pinned, verified download).

KUBE_CONTEXT="${KUBE_CONTEXT:-h100}"
NAMESPACE="${NAMESPACE:-default}"
# Each *-demo.sh here defaults to its own port so several can run at once.
LOCAL_PORT="${LOCAL_PORT:-8014}"
PLAYER="${PLAYER:-afplay}"
TTS_INPUT="${TTS_INPUT:-Hey, this is generated using NVIDIA Dynamo.}"
SIMULATE_DEPLOY="${SIMULATE_DEPLOY:-true}"

MANIFEST=vllm-omni-audio.yaml
DGD=vllm-omni-audio-sep18
MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
SELECTOR="nvidia.com/dynamo-graph-deployment-name=${DGD}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${REPO_ROOT}/runs/vllm-omni-audio-demo"
PF_LOG="${OUT_DIR}/port-forward.log"

DEMO_MAGIC_COMMIT=142f0e70c6242456f166aaa75dd3de829ab7fe73
DEMO_MAGIC_SHA256=c949dcfa64b491a3e5a80569dd41c901963c06703a94c80a670f151d2fbead05

die() {
  printf '\033[0;31mERROR: %s\033[0m\n' "$*" >&2
  exit 1
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
for tool in kubectl curl jq python3 "${PLAYER%% *}"; do
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
fi

jq -n --arg input "$TTS_INPUT" --arg model "$MODEL" \
  '{input: $input, model: $model, voice: "vivian", language: "English", data_source: "b64_json", response_format: "wav"}' \
  >"${OUT_DIR}/request.json" || die "could not write request.json"

pf_pid=
stop_port_forward() {
  if [[ -n "$pf_pid" ]]; then kill "$pf_pid" 2>/dev/null; fi
}
trap stop_port_forward EXIT

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

narrate "Text-to-speech on NVIDIA Dynamo with vLLM-Omni" \
  "Model: ${MODEL}" \
  "One DynamoGraphDeployment: a CPU frontend plus one GPU worker"

narrate "The worker launches vLLM-Omni with audio output:"
pe "sed -n '/command:/,/--enforce-eager/p' ${MANIFEST}"

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
pe "kubectl wait --for=condition=Ready dgd/${DGD} --timeout=20m"
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
rm -f headers.txt output.wav output-playback.wav
narrate "Request speech from the OpenAI-style /v1/audio/speech endpoint"
pe "jq . request.json"
if [[ "$cold" == true ]]; then
  narrate "The first request after a cold start includes a one-time warmup (~45 s in testing)"
fi
pe "time curl -sS --fail-with-body --max-time 240 \\
  http://127.0.0.1:${LOCAL_PORT}/v1/audio/speech \\
  -H 'Content-Type: application/json' --data-binary @request.json \\
  --dump-header headers.txt -o output.wav"
grep -qE '^HTTP/[^ ]+ 200' headers.txt 2>/dev/null && grep -qi '^content-type: *audio/wav' headers.txt ||
  die "speech request failed; inspect ${OUT_DIR}/headers.txt and output.wav"

narrate "Finalize the streamed WAV header and check that the audio is not silent"
python3 - <<'PY' || die "audio check failed; the raw response is ${OUT_DIR}/output.wav"
import array
import math
import sys
import wave

count = nonzero = sum_squares = peak = 0
with wave.open("output.wav", "rb") as source:
    channels = source.getnchannels()
    rate = source.getframerate()
    width = source.getsampwidth()
    if width != 2 or source.getcomptype() != "NONE":
        raise SystemExit("Expected uncompressed PCM16 WAV")
    with wave.open("output-playback.wav", "wb") as dest:
        dest.setnchannels(channels)
        dest.setsampwidth(width)
        dest.setframerate(rate)
        while True:
            data = source.readframes(4096)
            if not data:
                break
            dest.writeframesraw(data)
            samples = array.array("h", data)
            if sys.byteorder != "little":
                samples.byteswap()
            count += len(samples)
            nonzero += sum(value != 0 for value in samples)
            sum_squares += sum(value * value for value in samples)
            peak = max(peak, max((abs(value) for value in samples), default=0))

if not count:
    raise SystemExit("FAIL: no audio samples")
duration = count / (rate * channels)
rms = math.sqrt(sum_squares / count)
print(f"{rate} Hz, {channels} channel(s), {duration:.2f} seconds")
print(f"Nonzero samples: {nonzero}; RMS: {rms:.2f}; peak: {peak}")
if duration <= 0.08 or nonzero == 0:
    raise SystemExit("FAIL: empty/silent or initial-chunk-only audio")
print("PASS: non-silent audio saved to output-playback.wav")
PY

narrate "Play it"
pe "${PLAYER} output-playback.wav"
p ""

echo "dgd/${DGD} is still running on ${KUBE_CONTEXT}:${NAMESPACE}; audio is in ${OUT_DIR}."
echo "Release the GPU with: $0 cleanup"
