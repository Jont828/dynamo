<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Diffusion demos from PR #14660

The portable templates from PR #14660 are on this branch under `examples/backends/*/deploy/`. The root-level DGDs below are cluster-specific copies for the `h100` cluster. Each serving spec equals the manifest that passed live testing on **September 10, 2026** against the Dynamo 1.4.2 operator with matched 1.4.2 images; only the DGD name and namespace changed. All four passed a server-side dry-run on September 22; nothing was deployed.

## Every demo on this branch

| Demo | Manifest | DGD | Model | Endpoint | Runbook |
|---|---|---|---|---|---|
| SGLang text-to-image | [sglang-flux-image-dgd.yaml](sglang-flux-image-dgd.yaml) | `sglang-flux-image-demo` | `black-forest-labs/FLUX.1-schnell` | `/v1/images/generations` | This page |
| SGLang diffusion LLM | [sglang-llada-dgd.yaml](sglang-llada-dgd.yaml) | `sglang-llada-demo` | `inclusionAI/LLaDA2.0-mini-preview` | `/v1/chat/completions` | This page |
| TensorRT-LLM text-to-image | [trtllm-flux-image-dgd.yaml](trtllm-flux-image-dgd.yaml) | `trtllm-flux-image-demo` | `black-forest-labs/FLUX.2-klein-4B` | `/v1/images/generations` | This page |
| vLLM-Omni text-to-image | [vllm-omni-image-dgd.yaml](vllm-omni-image-dgd.yaml) | `vllm-omni-image-demo` | `Qwen/Qwen-Image` | `/v1/images/generations` | This page |
| vLLM-Omni text-to-speech | [vllm-omni-audio.yaml](vllm-omni-audio.yaml) | `vllm-omni-audio-sep18` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | `/v1/audio/speech` | [vllm-omni-audio.md](vllm-omni-audio.md) |
| vLLM-Omni text-to-video | [vllm-omni-t2v-dgd.yaml](vllm-omni-t2v-dgd.yaml) | `vllm-omni-t2v-demo` | `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | `/v1/videos` | [vllm-omni-demo-handoff.md](vllm-omni-demo-handoff.md) |
| vLLM-Omni image-to-video | [vllm-omni-i2v-dgd.yaml](vllm-omni-i2v-dgd.yaml) | `vllm-omni-i2v-demo` | `Wan-AI/Wan2.2-TI2V-5B-Diffusers` | `/v1/videos` | [vllm-omni-demo-handoff.md](vllm-omni-demo-handoff.md) |

For audio, use the existing cluster DGD (September 18 nightly pair), not the PR's `agg_omni_audio.yaml`. The PR has no video templates. Each DGD requests one H100 and pins its own tested images, so do not swap images between rows.

## Images for the four new demos

| Component | Image | Pod image ID on September 10 |
|---|---|---|
| Frontend, all four | `nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.4.2` | `sha256:80589311c904b82927ae95703c1bc59c49eb7ac10d25925c0e457260da915bc7` |
| SGLang workers | `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.4.2` | `sha256:3692c0c6a1ae23045f48384a09b539dcb9347ffa66ead5d0426aa90e85865012` |
| TensorRT-LLM worker | `nvcr.io/nvidia/ai-dynamo/tensorrtllm-runtime:1.4.2` | `sha256:386a803a04134a11bf6230a9ce56cfdda679b006b88dcecf63b9982f6523b43e` |
| vLLM-Omni worker | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.4.2` | `sha256:b23ce9e87c413725024ddfd2e06c1e7da220eb48f8a93037f0b01d564b54bd13` |

The release tags carry a parseable version, so these DGDs need no `runtimeVersionOverride`. Keep `shareProcessNamespace: true` on the FLUX.1 worker; without it, SGLang 0.5.16 in 1.4.2 kills its diffusion scheduler.

## Prerequisites

These use the same cluster resources as [vllm-omni-audio.md](vllm-omni-audio.md#prerequisites): context `h100`, namespace `default`, CPU pool `agentpool: nodepool1`, GPU pool `agentpool: ndh100pool`, secret `hf-token-secret`, and PVC `pvc-lustre-dynamo` mounted read-write at `/model-cache` (subpath `dynamo-cache`, `HF_HOME=/model-cache`). All four models were cached there during testing. You need `kubectl`, `curl`, and `jq`; the image steps use macOS `open`.

On September 22, the GPU pool had 32 schedulable H100s with 3 requested. These DGDs were already running in `default`: `flux-1-schnell-sglang-agg-h100`, `sample-diffusion-agg`, `sglang-llada-agg`, and `vllm-omni-audio-sep18`. The demo names avoid all of them. `sglang-llada-agg` matches the serving spec in [sglang-llada-dgd.yaml](sglang-llada-dgd.yaml), so you can demo LLaDA through `svc/sglang-llada-agg-frontend` without using another GPU.

## 1. Deploy and expose the frontend

In **terminal 1**, choose one demo:

```bash
export KUBE_CONTEXT=h100 NAMESPACE=default
export MANIFEST=sglang-flux-image-dgd.yaml DGD=sglang-flux-image-demo
# export MANIFEST=sglang-llada-dgd.yaml      DGD=sglang-llada-demo
# export MANIFEST=trtllm-flux-image-dgd.yaml DGD=trtllm-flux-image-demo
# export MANIFEST=vllm-omni-image-dgd.yaml   DGD=vllm-omni-image-demo

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" create --dry-run=server -f "$MANIFEST" &&
  kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" create -f "$MANIFEST"

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" wait \
  --for=condition=Ready "dgd/$DGD" --timeout=30m

kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" port-forward \
  --address=127.0.0.1 "svc/$DGD-frontend" 8000:8000
```

`create` refuses to overwrite a same-name DGD. Readiness took 1 to 7 minutes on September 10, and cold image pulls take longer, so deploy before presenting. If the wait times out, list the pods with `kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" get pods -l "nvidia.com/dynamo-graph-deployment-name=$DGD"`, then inspect the worker's events and logs. To run several demos at once, give each port-forward its own local port and change `8000` in its requests.

## 2. Send the request

In **terminal 2**, from the repository root, confirm that the model is listed:

```bash
curl -sS --fail-with-body http://127.0.0.1:8000/v1/models | jq -r '.data[].id'
```

Then run the matching request. Outputs go under the git-ignored `runs/diffusion-demo/` directory. Always request `b64_json`: workers write media to pod-local `file:///tmp/dynamo_media`, so URL responses are not reachable from your machine.

**SGLang FLUX.1-schnell** (1024x1024; about 17 s when tested):

```bash
OUT=runs/diffusion-demo/sglang-flux-image-demo && mkdir -p "$OUT"
curl -sS --fail-with-body --max-time 600 http://127.0.0.1:8000/v1/images/generations \
  -H 'Content-Type: application/json' -o "$OUT/response.json" --data-binary @- <<'JSON'
{"model": "black-forest-labs/FLUX.1-schnell", "prompt": "A red apple on a white table",
 "size": "1024x1024", "response_format": "b64_json", "nvext": {"num_inference_steps": 4}}
JSON
```

**SGLang LLaDA 2.0** (about 4 s when tested; prints the answer):

```bash
curl -sS --fail-with-body --max-time 600 http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' --data-binary @- <<'JSON' | jq -r '.choices[0].message.content'
{"model": "inclusionAI/LLaDA2.0-mini-preview", "temperature": 0, "max_tokens": 256,
 "messages": [{"role": "user", "content": "Explain in three sentences why the sky appears blue."}]}
JSON
```

**TensorRT-LLM FLUX.2-klein-4B** (256x256; about 6 s when tested):

```bash
OUT=runs/diffusion-demo/trtllm-flux-image-demo && mkdir -p "$OUT"
curl -sS --fail-with-body --max-time 600 http://127.0.0.1:8000/v1/images/generations \
  -H 'Content-Type: application/json' -o "$OUT/response.json" --data-binary @- <<'JSON'
{"model": "black-forest-labs/FLUX.2-klein-4B", "prompt": "A red apple on a white table, studio lighting",
 "size": "256x256", "response_format": "b64_json", "nvext": {"num_inference_steps": 10, "seed": 42}}
JSON
```

**vLLM-Omni Qwen-Image** (512x512; about 3 s when tested):

```bash
OUT=runs/diffusion-demo/vllm-omni-image-demo && mkdir -p "$OUT"
curl -sS --fail-with-body --max-time 600 http://127.0.0.1:8000/v1/images/generations \
  -H 'Content-Type: application/json' -o "$OUT/response.json" --data-binary @- <<'JSON'
{"model": "Qwen/Qwen-Image", "prompt": "A red apple on a white table, studio lighting",
 "size": "512x512", "response_format": "b64_json", "nvext": {"num_inference_steps": 20, "seed": 42}}
JSON
```

For the three image demos, check the response, then save and open the PNG:

```bash
jq -e '.error == null and (.data[0].b64_json | length > 0)' "$OUT/response.json" >/dev/null &&
  jq -r '.data[0].b64_json' "$OUT/response.json" | base64 --decode > "$OUT/output.png" &&
  file "$OUT/output.png" && open "$OUT/output.png"
```

If a check fails, read `$OUT/response.json` for the error. September 10 results: FLUX.1 and FLUX.2-klein returned coherent red-apple images. The 20-step Qwen-Image passed visual review, but its 4-step smoke was blurry. LLaDA returned a coherent answer in four sentences rather than three. Timings are smoke-test observations through a port-forward, not benchmarks. Other prompts should work, but other sizes and step counts were not tested.

## 3. Clean up

Stop the port-forward with **Ctrl+C**, then in terminal 1 delete only the DGD you created:

```bash
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" delete "dgd/$DGD" --wait=true --timeout=5m
kubectl --context "$KUBE_CONTEXT" -n "$NAMESPACE" wait --for=delete pod \
  -l "nvidia.com/dynamo-graph-deployment-name=$DGD" --timeout=3m
```

Keep the shared PVC, secret, namespace, operator, and pre-existing DGDs listed above.

> [!NOTE]
> The LLaDA worker runs `--trust-remote-code` against an unpinned Hugging Face revision while the pod holds `HF_TOKEN`. That is an open review comment on the PR, and it also covers the audio worker. These files keep the tested spec unchanged.
