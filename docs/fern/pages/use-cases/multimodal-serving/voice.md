---
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
title: Voice Pipelines
subtitle: Run streaming speech models and cascaded voice agents with Dynamo
---

NVIDIA Dynamo serves voice workloads through OpenAI-compatible endpoints. Use
separate automatic speech recognition (ASR), LLM, and text-to-speech (TTS) workers
for a cascaded pipeline, or a vLLM-Omni model for audio input and output.

## Endpoints

| Stage | Endpoint | Output |
| --- | --- | --- |
| Streaming ASR | `/v1/realtime` WebSocket with `session.type="transcription"` | Transcript events |
| LLM | `/v1/chat/completions` with `stream: true` | Text deltas |
| TTS | `/v1/audio/speech` | Audio chunks for supported models and formats |
| Audio-in/audio-out model | `/v1/realtime` WebSocket with `session.type="realtime"` | Text and audio events |

## Run a Pipeline

- **Nemotron Speech cascade on Kubernetes:** Follow the
  [deployment and smoke-test instructions](https://github.com/ai-dynamo/dynamo/tree/main/examples/nemotron_speech_cascaded_pipeline#deploy-on-kubernetes).
  The example deploys Nemotron ASR and Magpie TTS Speech NIMs with Dynamo adapters,
  plus a Nemotron LLM served by vLLM. ASR, LLM, and TTS run in separate pods.
  Follow the example's runtime-image requirements; it currently requires
  compatible source-built Dynamo images, not the published 1.5.0 images.
- **Separate vLLM/vLLM-Omni speech workers:** Use the
  [Qwen3-ASR realtime transcription launcher](https://github.com/ai-dynamo/dynamo/blob/main/examples/backends/vllm/launch/agg_asr_realtime.sh)
  and [Qwen3-TTS launcher](https://github.com/ai-dynamo/dynamo/blob/main/examples/backends/vllm/launch/agg_omni_audio.sh)
  to exercise each endpoint independently before connecting them to an LLM.
- **Audio-in/audio-out with vLLM-Omni:** Run the
  [Qwen3-Omni realtime example](https://github.com/ai-dynamo/dynamo/blob/main/examples/backends/vllm/launch/agg_omni_realtime.sh).
  The launcher prints a client command that streams an audio file and saves the
  response audio. Each committed utterance is handled independently; this path
  does not preserve multi-turn conversation history.

The local launchers require a configured
[vLLM backend environment](../../developer-guide/knowledge-base/modular-components/backends/vllm/overview.md#installation),
with vLLM-Omni installed for the Omni examples.

For a cascaded voice agent, connect an application such as Pipecat to the three
stage endpoints. The application owns conversation history, turn detection,
interruptions, and orchestration; Dynamo serves and routes each stage. See
[external orchestration](https://github.com/ai-dynamo/dynamo/tree/main/examples/nemotron_speech_cascaded_pipeline#external-orchestration)
for client integration details. The deployment smoke test checks speech endpoints,
not a complete browser voice agent.

## Speech Recognition on Kubernetes

Two DynamoGraphDeployment manifests serve Qwen3-ASR. For the request and response
formats and the local launchers, see
[Speech Recognition (ASR)](../../developer-guide/knowledge-base/modular-components/backends/vllm/multimodal.md#speech-recognition-asr)
in the vLLM multimodal guide.

| Manifest | Endpoint | Worker mode |
| --- | --- | --- |
| [`agg_asr.yaml`](https://github.com/ai-dynamo/dynamo/blob/main/examples/backends/vllm/deploy/agg_asr.yaml) | `/v1/chat/completions` with an `audio_url` content part | `--enable-multimodal` |
| [`agg_asr_realtime.yaml`](https://github.com/ai-dynamo/dynamo/blob/main/examples/backends/vllm/deploy/agg_asr_realtime.yaml) | `/v1/realtime` WebSocket with `session.type="transcription"` | `--realtime` |

A `--realtime` worker cannot also run with `--enable-multimodal`, so each manifest
serves one endpoint. Deploy both to serve both endpoints.

### Audio Transfer Between Pods

In `agg_asr.yaml`, the frontend and worker run in separate pods, which do not share
`/dev/shm`. The default shared-memory transfer of processed audio cannot reach the
worker, so the worker transcribes the prompt without the audio and returns
`language None<asr_text>`. The frontend's transfer settings decide where the audio
is processed:

- **`DYNAMO_DISABLE_NIXL_MM=1`, set in the manifest:** The frontend forwards the
  audio URL, and the worker loads and processes the audio itself.
- **`DYNAMO_MM_TRANSFER=nixl`:** The frontend sends the processed audio to the
  worker over NIXL, which spares the worker a second pass over the audio. To use
  it, remove `DYNAMO_DISABLE_NIXL_MM` from the frontend and set
  `DYNAMO_MM_TRANSFER=nixl` there.
- **`Qwen/Qwen3-ASR-1.7B-hf` behind the default Rust frontend:** The worker always
  loads the audio, so neither setting applies.

> [!IMPORTANT]
> The frontend checks `DYNAMO_DISABLE_NIXL_MM` before it selects a transfer mode.
> Setting `DYNAMO_MM_TRANSFER=nixl` while `DYNAMO_DISABLE_NIXL_MM=1` remains has no
> effect.

### Image Requirements

The Dynamo vLLM runtime images lack two packages that these manifests can need:

- **PyAV**, for `agg_asr.yaml` to accept audio at a rate other than the model's
  16 kHz. The frontend runs the Python chat processor and resamples audio too, so
  run both components from the image.
- **`scipy`**, which the `agg_asr_realtime.yaml` worker uses to resample the 24 kHz
  stream to 16 kHz. Without it, each session ends with an empty transcript.

A `pip install` in a running pod does not survive a restart, so bake the packages
into an image and set it as each component's `image`:

```dockerfile
FROM nvcr.io/nvidia/ai-dynamo/vllm-runtime:x.y.z
# Audio at sample rates other than 16 kHz (agg_asr.yaml)
RUN python -m dynamo.common.utils.install_media_decoders vllm
# Streaming transcription (agg_asr_realtime.yaml)
RUN pip install 'scipy>=1.14,<2'
```

See [Additional Media Decoders](additional-media-decoders.md#kubernetes) for what the
installer changes.

## Planned Work

[Incremental text input](https://github.com/ai-dynamo/dynamo/issues/14476) is under
development to overlap ASR with LLM prefill. Session recovery across worker
failures is a further goal. Neither capability is provided by the examples above.
