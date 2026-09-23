<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# vLLM-Omni video demo — chat summary and handoff

**Demo date:** September 23, 2026 (America/New_York).
**Last successful cluster qualification:** September 22, 2026.

## Files on this branch

- [vllm-omni-t2v-dgd.yaml](vllm-omni-t2v-dgd.yaml): Wan2.1 text-to-video.
- [vllm-omni-i2v-dgd.yaml](vllm-omni-i2v-dgd.yaml): Wan2.2 image-to-video.

These are the **actual successful serving configurations**, with only DGD names
and test labels changed for the demo. They are environment-specific: namespace
`default`, CPU pool `nodepool1`, H100 pool `ndh100pool`, existing PVC
`pvc-lustre-dynamo` with subpath `dynamo-cache`, and secret reference
`hf-token-secret`. The model cache is mounted **read-only**, and
`HF_HUB_OFFLINE=1` requires complete cached model snapshots.

**Each DGD requests one H100 80 GB. Run them sequentially for a one-GPU budget.**
Nothing was deployed while preparing these files. Reconfirm the target context,
namespace, available resources and demo budget before starting a new run.

## What happened in this chat

Two independent defects affected the original Dynamo 1.4.2 T2V/I2V reproductions:

1. The video handler dropped `response_format`, so `b64_json` requests returned a
   pod-local file URL. The test image incorporates the exact forwarding fix from
   Dynamo PR **#14844**.
2. FFmpeg 8.1.2, built with `--disable-x86asm`, corrupted RGB-to-YUV conversion.
   The image includes a validated backport of upstream FFmpeg commit
   `62285be0096319310d7012bbab1739db79926c4d`.

The codec was first reproduced and fixed in isolation. The three required
controls then passed on **native Intel Xeon Platinum 8480C** on the cluster:
stock default reproduced corruption, stock with MMXEXT disabled passed, and
patched default passed **without** disabling CPU features. All 12 CLI/helper
controls passed; maximum patched interior channel error was **2/255**.

The rebuilt qualification image was pushed to Docker Hub, then both DGDs were
run sequentially against the existing **Dynamo operator 1.4.2**, with a matching
1.4.2 frontend. All eight generation requests returned HTTP 200 and completed
status: base64 smoke, base64 visual, explicit URL, and omitted/default format for
each workflow. All four client-decoded base64 MP4s passed independent full-frame
decoding, dimension, frame-count, frame-rate, VP9 and YUV420p checks.

| Workflow | Model | Visual run | Plumbing smoke |
|---|---|---|---|
| T2V | `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | 50 steps, 33 frames | 4 steps, 9 frames |
| I2V | `Wan-AI/Wan2.2-TI2V-5B-Diffusers` | 50 steps, 33 frames | 4 steps, 9 frames |

Both used **832×480, 16 fps, seed 42**. Visual clips last **2.0625 seconds**;
smoke clips last **0.5625 seconds**. Worker logs confirmed `50/50` steps. These
were correctness checks, **not performance benchmarks**.

## Use this exact image

```text
docker.io/jont828/dynamo-ffmpeg-color:1.4.2-validation-20260922-1b72cc6dfbbf
```

Immutable digest, already pinned in both DGDs:

```text
sha256:1b72cc6dfbbf610ba6e1135832c9cb702f7e6ddca2d57ddfc01e7e67e066980c
```

The frontend is pinned to 1.4.2 amd64 digest
`sha256:4d6435ad3893487e7e5a8751eb2381199464f80248284bb98c5e0998f09a91c0`.

**Keep `runtimeVersionOverride: "1.4.2"` on both components.** The operator
rejected the first server dry-run without it because digest-pinned images have
no parseable version tag. Keep `--enforce-eager` as tested.

This is a **release-derived qualification image**, based on Dynamo `v1.4.2`
source `2ecbdfdf192c69c02c6d21e931d20d3b4a0bb64a`, with rebuilt FFmpeg/libvpx and
the bounded handler fix. Unchanged Rust/CUDA/engine wheels were inherited:
Dynamo/runtime 1.4.2, vLLM 0.26.0, vLLM-Omni 0.26.0rc1, Torch 2.11.0+cu130.
**This demo branch's current source checkout is not that image's build baseline.**
Do not substitute stock 1.4.2 or rebuild unrelated current source with old release
dependencies and assume it has been qualified. Full canonical image CI and other
architectures were not tested.

## Demo caveats to say out loud

- T2V sampled frames show a dog moving on a beach, without the earlier
  magenta/banding corruption.
- I2V retains the red apple and white/gray scene, but the apple appears
  **flatter/wider than the square input**. This is an undiagnosed
  proportion/conditioning concern, not a general model-quality pass.
- The four-step videos are **plumbing smokes**, not quality demonstrations.
- Explicit URL and default-format requests returned expected `file://` URLs
  because storage is `file:///tmp/dynamo_media`. Those URLs are **pod-local**;
  externally accessible media storage was not configured or validated.
- For the live demo, request **`response_format: "b64_json"`** and decode the
  response on the client. Do not depend on recovering a file from the worker pod.

## Live-run outline

The validated context was `h100`; verify that this still names the intended
cluster. The commands below create resources only when an operator runs them.
Use `create`, not `apply`, to avoid overwriting an existing same-name demo.

```bash
kubectl --context h100 get namespace default
kubectl --context h100 -n default get pvc pvc-lustre-dynamo
kubectl --context h100 -n default get secret hf-token-secret -o name

kubectl --context h100 -n default create --dry-run=server -f vllm-omni-t2v-dgd.yaml
kubectl --context h100 -n default create -f vllm-omni-t2v-dgd.yaml
kubectl --context h100 -n default get pods -l demo=vllm-omni,video-mode=t2v -w
```

Wait for both pods to be ready. Allow several minutes for image/model loading;
do not wait until presentation time to begin. In a separate terminal:

```bash
kubectl --context h100 -n default port-forward svc/vllm-omni-t2v-demo-frontend 8000:8000
```

Confirm the expected model appears at `GET http://127.0.0.1:8000/v1/models`.
Send `POST /v1/videos` with this visual-run request:

```json
{
  "model": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers",
  "prompt": "A dog running on a beach at sunrise",
  "size": "832x480",
  "response_format": "b64_json",
  "nvext": {
    "num_inference_steps": 50,
    "num_frames": 33,
    "fps": 16,
    "seed": 42
  }
}
```

Save it as a request file outside the tracked source and send it with
`curl --max-time 1200 --fail-with-body -H 'Content-Type: application/json' --data-binary @request.json http://127.0.0.1:8000/v1/videos -o response.json`.
Check HTTP success, `status=completed`, no error, and nonempty
`data[0].b64_json`; decode with `base64.b64decode(..., validate=True)` to an MP4.

After the T2V demo, stop port-forwarding and remove **only the DGD you created**:

```bash
kubectl --context h100 -n default delete dynamographdeployment vllm-omni-t2v-demo --wait=true
kubectl --context h100 -n default wait --for=delete pod -l demo=vllm-omni,video-mode=t2v --timeout=180s
```

Then repeat with `vllm-omni-i2v-dgd.yaml` and service
`vllm-omni-i2v-demo-frontend`. Use model
`Wan-AI/Wan2.2-TI2V-5B-Diffusers` and prompt:
“The red apple slowly turns toward the camera while the white table and
background remain stable”. Add:

- `input_reference`: `"data:image/png;base64," + base64.b64encode(input_png_bytes).decode()`.
- `nvext.guidance_scale: 1.0`, `nvext.boundary_ratio: 0.875`, and
  `nvext.guidance_scale_2: 1.0`.
- Keep the visual-run step/frame/fps/seed values above.

Use the **valid original PNG** below, not the launcher's illustrative invalid
PNG. Cleanup is the same with `vllm-omni-i2v-demo` and `video-mode=i2v`.
Do not delete shared controllers, PVCs, secrets or existing workloads.

## Existing videos, exact requests and deeper evidence

Per the requested minimal branch scope, videos/artifacts were **not copied into
this branch**. They remain on this Mac in the separate validation worktree:

```text
/Users/jonathan/.codex/worktrees/ffmpeg-color-validation/dynamo/
  ffmpeg-cluster-qualification-report.md
  ffmpeg-color-validation-worklog.md
  artifacts/ffmpeg-color-validation/cluster-20260922T2127Z/
    outputs/t2v-visual/video-1.mp4
    outputs/i2v-visual/video-1.mp4
    outputs/t2v-smoke/video-1.mp4
    outputs/i2v-smoke/video-1.mp4
    requests/i2v-input.png
    requests/t2v-visual.json
    requests/i2v-visual.json
    videos-and-previews.zip
```

The `requests/` files are ready-to-send captured requests; the I2V JSON already
embeds the correct image. Each output folder also contains response JSON, HTTP
headers, validation results and a contact sheet. The ZIP is the pre-recorded
fallback for the presentation. These absolute paths are local-machine pointers,
not portable Git assets; copy the ZIP separately if presenting on another machine.

Original I2V input: 1024×1024 RGB PNG, 587310 bytes, SHA256
`d3b77ff73294c127e24382076bfcbb9b269dd2d278372e71692c903cfe04aedd`.

The full report includes binary/source hashes, native control commands, model
snapshot revisions and deployment ledgers. Source changes remain separately on
`codex/ffmpeg-color-validation`; they were not indiscriminately merged into this
newer demo branch. Related public trackers: Dynamo **#14667 / #14668** and
response-format PR **#14844**. No PR was published or tracker closed in this chat.

## State at handoff

All September 22 validation Jobs/DGDs, owned pods/services/Grove resources and
local inspection containers were removed. Recorded allocation was **0.179856
GPU-hours**, maximum one H100 concurrently. The shared operator, cache and
pre-existing workloads were left untouched. `docs/diffusion-dgd-examples` was
not changed. A new live demo needs a newly confirmed resource/time budget.
