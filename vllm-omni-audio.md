<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vLLM-Omni audio demo

Deploy Qwen3-TTS with [vllm-omni-audio.yaml](vllm-omni-audio.yaml), send a speech request, and save a playable WAV. Run the commands below from the repository root.

This is the cluster-specific DGD used for the **September 18, 2026** sanity check, not the portable template from PR #14660. Its serving spec is unchanged from the successful deployment. The test returned HTTP 200 and non-silent audio on all three requests (3.68 s, 3.20 s, and 3.20 s), with zero pod restarts.

## Tested images and versions

| Component | Pinned image |
|---|---|
| Frontend | `nvcr.io/nvidia/ai-dynamo/dynamo-frontend-nightly:20260918-a9792db` |
| Worker | `nvcr.io/nvidia/ai-dynamo/vllm-runtime-nightly:20260918-a9792db` |

- Dynamo packages: **1.6.0.dev20260918** on both sides.
- Backend: **vLLM 0.29.0**, **vLLM-Omni 0.29.0rc1**.
- Tested platform Helm chart/operator: **1.4.2**, without an upgrade.
- Both components declare `runtimeVersionOverride: 1.6.0`, describing the runtime image, not the operator.
- Model: `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`; one GPU worker and one CPU frontend.

> [!WARNING]
> These are experimental nightly images pinned to the tested September 18 build, not a claim about today's latest nightly. Keep the frontend and worker on the matched pair. Stock 1.4.2 serving images returned only an initial 80 ms of silence in this case. The test validates this audio workflow, not every feature across mixed operator/runtime versions.

## Prerequisites

The manifest retains the following existing resources from the tested cluster:

| Setting | Value |
|---|---|
| Kubernetes context used below | `h100` |
| Namespace (also set in the YAML) | `default` |
| CPU node selector | `agentpool: nodepool1` |
| GPU node selector | `agentpool: ndh100pool` |
| Existing model-cache PVC | `pvc-lustre-dynamo` |
| Cache subpath / mount | `dynamo-cache` / `/model-cache` |
| Existing Hugging Face secret | `hf-token-secret`, providing `HF_TOKEN` |

Use a configured `kubectl`, `curl`, and Python 3. The cluster needs a working Dynamo operator/CRDs and one available compatible GPU; validation used an H100. No secret values are included here. Change the context and the YAML's namespace, node selectors, or PVC reference if targeting another cluster. These commands do not install or upgrade the platform.

## 1. Deploy and expose the frontend

In **terminal 1**, check the prerequisites. Stop if a required resource is absent or the PVC is not `Bound`:

```bash
export KUBE_CONTEXT=h100
export NAMESPACE=default

kubectl --context "$KUBE_CONTEXT" get namespace "$NAMESPACE"
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get pvc pvc-lustre-dynamo
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get secret hf-token-secret -o name
kubectl --context "$KUBE_CONTEXT" get nodes -L agentpool
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get dgd vllm-omni-audio-sep18 --ignore-not-found
```

If that DGD already exists, inspect it before applying so you do not overwrite another deployment using the same name. The original test deployment was removed after validation.

```bash
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" apply --dry-run=server -f vllm-omni-audio.yaml &&
  kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" apply -f vllm-omni-audio.yaml

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" wait \
  --for=condition=Ready dgd/vllm-omni-audio-sep18 --timeout=20m

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get pods \
  -l nvidia.com/dynamo-graph-deployment-name=vllm-omni-audio-sep18

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" port-forward \
  --address=127.0.0.1 svc/vllm-omni-audio-sep18-frontend 8000:8000
```

Keep the port-forward running. Cold image pulls and model startup took several minutes in the test. If readiness times out, inspect the worker pod's events and logs rather than treating the deployment as ready. If local port 8000 is occupied, forward `18000:8000` and use port 18000 in the requests below.

## 2. Generate the audio

In **terminal 2**, from the repository root, confirm model discovery:

```bash
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/v1/models
```

Check that the response lists `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`. Then replay the exact tested request. Output is written under the git-ignored `runs/` directory:

```bash
mkdir -p runs/vllm-omni-audio-demo

cat > runs/vllm-omni-audio-demo/request.json <<'JSON'
{
  "input": "Hey, this is generated using NVIDIA Dynamo.",
  "model": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
  "voice": "vivian",
  "language": "English",
  "data_source": "b64_json",
  "response_format": "wav"
}
JSON

curl --fail-with-body --silent --show-error --max-time 240 \
  http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  --data-binary @runs/vllm-omni-audio-demo/request.json \
  --dump-header runs/vllm-omni-audio-demo/headers.txt \
  --output runs/vllm-omni-audio-demo/output.wav
```

Expect HTTP 200 and `Content-Type: audio/wav` in `headers.txt`. Despite `data_source: b64_json`, this endpoint returns **raw WAV bytes**, not a JSON/base64 envelope. If curl fails, inspect the saved headers and error body before continuing. The first tested request took about 44 seconds for cold warmup/JIT compilation; later requests took about 2.3 seconds. These are smoke-test observations, not a latency guarantee.

## 3. Validate and play the output

Streaming WAV responses use open-ended length fields. Preserve `output.wav` as the raw HTTP response and finalize the header in a separate playback copy. This Python-only check counts actual samples instead of trusting the streaming header's declared duration:

```bash
python3 - <<'PY'
import array
import math
from pathlib import Path
import sys
import wave

out = Path("runs/vllm-omni-audio-demo")
count = nonzero = sum_squares = peak = 0
with wave.open(str(out / "output.wav"), "rb") as source:
    channels = source.getnchannels()
    rate = source.getframerate()
    width = source.getsampwidth()
    if width != 2 or source.getcomptype() != "NONE":
        raise SystemExit("Expected uncompressed PCM16 WAV")
    with wave.open(str(out / "output-playback.wav"), "wb") as dest:
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
print(f"PASS: non-silent audio saved to {out / 'output-playback.wav'}")
PY
```

The tested outputs were 24 kHz mono PCM16. Their first 80 ms was silent, followed by non-silent audio. Exact duration and bytes can vary. The check above confirms waveform delivery, not the accuracy of the spoken words.

On macOS, play the finalized WAV:

```bash
afplay runs/vllm-omni-audio-demo/output-playback.wav
```

On other systems, open `output-playback.wav` in a WAV-capable player. The playback copy contains the same PCM samples; only its WAV length fields are finalized.

## 4. Clean up

In terminal 1, stop the port-forward with **Ctrl+C**, then release the test GPU:

```bash
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" delete \
  dgd/vllm-omni-audio-sep18 --wait=true --timeout=3m

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get pods \
  -l nvidia.com/dynamo-graph-deployment-name=vllm-omni-audio-sep18
```

Wait for the test pods to disappear. Do not delete the shared PVC, Hugging Face secret, namespace, or platform. The local WAV files remain under `runs/vllm-omni-audio-demo/`.
