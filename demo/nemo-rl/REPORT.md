# NeMo RL + Dynamo on AKS with KubeRay: report

Run on the `h100` AKS cluster, 2026-11-12/13 (UTC). The kit and recording are in this directory; `image/NOTES.md`
has the working notes behind the timeline below. The job and run logs this report cites (`runs/…`) weren't copied
into this repo.

**Verdict: PASS.** NeMo RL's new Dynamo path runs unchanged as one KubeRay RayJob. NeMo RL CI's own Dynamo
overlay test passes on 2 GPUs. The demo config with two engines, a layout upstream CI doesn't run, trained 25 GRPO
steps, and Dynamo's KV router routed all 13,312 of its rollouts; 98% went to an engine already holding part of the
prompt. One caveat: on 1 of the 25 steps, `gen_kl_error` was 0.00209, just over the 0.002 CI requires of its
2-step test.

## What was tested

NeMo RL's new Dynamo path, `policy.generation.dynamo: true` from
[NVIDIA-NeMo/RL#4905](https://github.com/NVIDIA-NeMo/RL/pull/4905) (merged 2026-11-11): NeMo RL's driver starts
etcd, NATS and `dynamo.frontend` (KV router) itself, and NeMo RL's own vLLM engines register with that frontend
through `dynamo.common.backend`. Everything ran as KubeRay RayJobs, one per run, with no `kubectl exec`, no Dynamo
operator and no DynamoGraphDeployment.

## Versions

| | |
|---|---|
| NeMo RL | `d51b2842e87403b4d1fbd008d28cd52694ced487` (main, 2026-11-11, #4882), 2 commits after #4905 (`f488a0c6`) |
| Image | `docker.io/jont828/nemo-rl:main-d51b2842-dynamo`, index `sha256:f1008ba75c64a7a8a02aa9a74f11b76f052e9ac7e4dd2106c11d8c373e7b6fc4` (linux/amd64 manifest `sha256:adbedcb5f3b41463753b03b1cdc83b547f249a63897ddb7b89106a845c10cd36`); 61 layers, 17.28 GB compressed, largest layer 10.38 GB |
| Driver venv | nemo-rl 0.8.0+d51b2842e, Ray 2.58.0, torch 2.13.0+cu130, transformers 5.16.1, datasets 5.0.1 |
| vLLM worker venv | vLLM 0.29.0, ai-dynamo 1.5.1, ai-dynamo-runtime 1.5.1, NCCL (nvidia-nccl-cu13) 2.30.7, Ray 2.58.0 |
| Megatron worker venv | megatron-core 0.20.0+3e95097b4, Megatron-Bridge 0.7.0+7e9dcd60, TransformerEngine 2.18.0, NCCL 2.30.7 |
| Dynamo services | etcd v3.5.21, nats-server v2.11.6 (`BUILD_DYNAMO=1`, `/opt/dynamo/bin`) |
| KubeRay | operator v1.7.1 (`quay.io/kuberay/operator:v1.7.1`); RayJob `K8sJobMode`; submitter `rayproject/ray:2.58.0-py313` |
| Cluster | 4 × Standard_ND96isr_H100_v5 (8 × H100 each), Kubernetes v1.34.8 |

## Choosing the NeMo RL commit

Checked 2026-11-11 23:32 and again 2026-11-12 20:25:

- No scheduled nightly after #4905 ran the Dynamo job: the `f488a0c6` nightly (2026-11-11) failed in
  `L0_Unit_Tests_Other` and the CI quality check, and the `defdaef4` nightly (2026-11-12) was cancelled. The last
  nightly that passed `L1_Functional_Tests_Dynamo` was 2026-11-07, before #4905.
- Main's push runs do run `L1_Functional_Tests_Dynamo`. It passed at `f488a0c6` (the #4905 merge; that run failed
  elsewhere) and at `d51b2842` (run 43055697255, which passed as a whole). That job ran both Dynamo overlays on a
  2-GPU runner with one engine; its log is where the expected evidence lines come from.
- `d51b2842` is 2 commits ahead of `f488a0c6`. By 2026-11-12 main was 2 commits further (#5130, PPO; #4861,
  TRT-LLM refit), neither touching Dynamo, the Dockerfile, `uv.lock` or `pyproject.toml`, so the build stayed on
  `d51b2842`.
- No NeMo RL release after v0.8.0 (2026-11-06), which still ships the removed `backend: dynamo` path.

## Image build

In-cluster BuildKit v0.34.2 (buildx `kubernetes` driver, one CPU-only pod with 64 CPU / 384 GiB on an H100 node,
namespace `buildkit`, removed afterwards), pushed with the Mac's Docker Hub login. `image/build-image.sh` is CI's
invocation (`.github/workflows/cicd-main.yml`: `--target release`, `--build-context nemo-rl=.`, `BUILD_DYNAMO=1`,
`MAX_JOBS=4`) plus `SKIP_SGLANG_BUILD=1`, `SKIP_TRTLLM_BUILD=1`, `STRIP_UV_CACHE=1`, `NVTE_CUDA_ARCHS=90` and
`CUSTOM_SETUP_FNAME=` (no Apptainer).

- 1 h 38 min end to end (2026-11-12 20:26 → 22:04). The dependency layer took 62.5 min: a cold build compiles
  DeepEP, DeepGEMM, the TransformerEngine torch extension, fast-hadamard-transform, FlashMLA, mamba-ssm,
  flash-attn 2.8.1 and causal-conv1d, which CI restores from its uv-cache image. Export and push took 22.6 min
  (12.4 min uploading layers); no 502 this time.
- The image is 17.3 GB compressed, against 44.8 GB for the public v0.8.0 image (largest layer 10.4 GB against
  20.8 GB), because SGLang, TRT-LLM and the uv cache are left out.

## Runs

All on node `aks-ndh100pool-82423760-vmss000000` or `…vmss000003`, namespace `nemo-rl-demo`, one RayJob at a time.

| Run | What | Result | Apply → end |
|---|---|---|---|
| setup Job | pull the image, cache both Qwen models and OpenMathInstruct-2 | Complete | 23 min (12 min image pull) |
| smoke-1 | CI's overlay test, 2 GPUs | FAILED: `tests/functional/grpo_dp_single_controller.sh: No such file` (problem 1) | 13 min (11 min image pull on a new node) |
| smoke-2 | CI's overlay test, unchanged, 2 GPUs | **SUCCEEDED**: every CI check passed | 3 min 38 s |
| validate-2engine | `k8s/rayjob.yaml` with `STEPS=5`, `NCCL_DEBUG=INFO`, 3 GPUs | **SUCCEEDED**: report PASS | 6 min 33 s |
| recorded demo | `./demo.sh` (`k8s/rayjob.yaml`, `STEPS=25`), 3 GPUs | **SUCCEEDED**: report PASS, with one `gen_kl_error` warning | 16 min 34 s |

### Smoke: NeMo RL CI's Dynamo overlay, 2 GPUs

`tests/functional/grpo_dp_single_controller.sh ++policy.generation.dynamo=true`, the first test of
`L1_Functional_Tests_Dynamo.sh`, run unchanged under `ray job submit`: Qwen/Qwen3-0.6B, 2 prompts × 4 samples,
2 steps, 1 Megatron trainer GPU and 1 vLLM engine. Log: `runs/smoke-2.log`.

- NeMo RL attached to KubeRay's cluster: `Connected to existing Ray cluster: {… 'GPU': 2.0 …}`. The driver ran
  under `ray job submit` with `RAY_ADDRESS` set, and nothing in NeMo RL needed changing for that.
- CI's metric checks, all PASS: `max(gen_kl_error)` 0.000647 (< 0.002), `max(max_seq_mult_prob_error)` 1.0218,
  no sequences masked for logprob error, clamped probability ratios at 1.0.
- `check_dynamo_overlay.sh`: `[Dynamo] frontend http://<pod IP>:3032/v1 routes to 1 engine(s)`,
  "Requests the Dynamo frontend routed: 16", engine publishes KV events: True,
  "KV events the router applied: 392.0", and no etcd, NATS or frontend process left after shutdown.
- Steps took 13.5 s and 5.1 s; each refit took 0.08 s over a 2-rank NCCL group.

### Two engines: the demo config, 3 GPUs

`k8s/rayjob.yaml` with `STEPS=5` and `NCCL_DEBUG=INFO`: Qwen/Qwen2.5-1.5B, 32 prompts × 16 samples per step,
1 trainer GPU and 2 engines. #4905's PR says upstream never ran this case ("both CI runners have 2 GPUs, and
two engines need 3"). Logs: `runs/validate-2engine.log` (job) and `runs/grpo-validate-2engine.run.log` (full).

| Check | Evidence |
|---|---|
| Both engines registered | `[Dynamo] frontend http://<pod IP>:3032/v1 routes to 2 engine(s)`; both `VllmAsyncGenerationWorker`s log `[Dynamo] engine publishes KV events: True` |
| KV router used both engines | 3,072 `Selected worker router_mode="kv"` lines: 1,517 for worker `…780`, 1,555 for worker `…781` |
| KV events applied | `[Dynamo] router applied KV events: {… "ok": 62783.0 …}`, every error status 0 |
| Prefix reuse | 2,876 of 3,072 `route request completed` lines (94%) have `overlap_blocks` > 0; 5,964 blocks in total |
| Engines match the trainer | `gen_kl_error` 0.00062–0.00064 (CI's limit is 0.002); `token_mult_prob_error` 1.019–1.020 |
| Refit | 5 refits, 0.11 s each on average, over NCCL 2.30.7 in all three processes, channels `via P2P/CUMEM` |
| Time | first step 51.5 s, then ~30 s (training 13.6 s; the trainer never waited for rollouts) |
| Reward | 0.061, 0.059, 0.072, 0.073, 0.059 (see problem 5) |

### Recorded demo: 25 steps, 3 GPUs

`./demo.sh` under `asciinema rec`, run `grpo-20261113-001238` on `vmss000000`, with `k8s/rayjob.yaml` as it is in
the kit (`STEPS=25`, two engines). The job's live view, report and evidence are in the recording
(`recordings/nemo-rl-dynamo-rayjob.public.cast`, redacted, also at
[asciinema.org/a/GHfS1N9o4d9goBqj](https://asciinema.org/a/GHfS1N9o4d9goBqj), unlisted). The full run log,
`runs/grpo-20261113-001238.run.log` (94,809 lines), isn't in this repo.

| Check | Evidence |
|---|---|
| Both engines registered | `[Dynamo] frontend http://<pod IP>:3032/v1 routes to 2 engine(s)`; both engines log `[Dynamo] engine publishes KV events: True` |
| KV router used both engines | 13,312 `Selected worker router_mode="kv"` lines: 6,763 for worker `…837` and 6,549 for worker `…835`. That is 26 batches of 512: generation may run one version ahead (`async_rl.max_lookahead_versions: 1`), so the batch for a 26th step was done when training stopped at step 25 |
| KV events applied | `[Dynamo] router applied KV events: {… "ok": 270223.0 …}`, every error status 0 |
| Prefix reuse | 13,066 of 13,312 `route request completed` lines (98%) have `overlap_blocks` > 0; 26,300 blocks in total |
| Clean shutdown | frontend, NATS and etcd stopped at +15:28; no Dynamo process left afterwards (`pgrep`) |
| Engines match the trainer | `gen_kl_error` 0.00060–0.00143 on 24 steps, and 0.00209 on step 11, the step where the reward went from 0.145 to 0.260; `token_mult_prob_error` 1.012–1.026 |
| Refit | 25 refits, 0.11 s each on average |
| Time | 16 min 34 s from apply (00:11:44) to `SUCCEEDED` (00:28:18). The driver started 54 s after apply, after the head pod, `ray start` and the submitter pod, which included a 27 s pull of `rayproject/ray:2.58.0-py313`. Training process 15 min 31 s: etcd, NATS and the frontend at +0:52–0:53, both engines registered at +1:32, first step at +2:16 taking 46 s, then 30.1 s per step (13.7 s training; the trainer never waited for rollouts). The Megatron checkpoint was already converted, by validate-2engine |
| Reward | 0.072 at step 1 → 0.305 at step 25; first 3 steps mean 0.080, last 3 mean 0.278; highest 0.343 (step 19). `truncation_rate` 0.60–0.68 on steps 1–8, 0.44–0.54 on steps 23–25 |

The `gen_kl_error` on step 11 is the only number here outside a limit NeMo RL's CI uses, and CI applies that limit
to a 2-step run of a smaller model. These runs don't show whether it comes from the Dynamo path or from 25 steps
of this config. A `dynamo: false` run of the same config would tell; it was not run.

## Timeline (UTC)

| When | What |
|---|---|
| 2026-11-11 23:32 | Upstream checks; NeMo RL `d51b2842` chosen (re-checked 2026-11-12 20:25) |
| 2026-11-12 20:24–22:04 | Image build in a BuildKit pod on the cluster, 1 h 38 min, then pushed; the builder and its `buildkit` namespace were removed at 22:04:30 |
| 20:41 | `kubectl apply -k .`: namespace, scripts ConfigMap and cache PVC |
| 22:04–22:29 | Setup Job, re-pinned to `vmss000000` after KAITO taints appeared on `vmss000001` and `vmss000003`: 12 min image pull, then 11 min for models and dataset |
| 22:29–23:38 | Nothing ran. My wait loop for the setup Job never fired (zsh doesn't word-split `$var`), so its completion went unnoticed for an hour |
| 23:38–23:52 | smoke-1: another team's 8-GPU pod had taken `vmss000000`, so re-pinned to `vmss000003`; 11 min image pull, then failed on the missing `tests/functional/` (problem 1) |
| 23:53–23:57 | smoke-2, with CI's scripts restored from the ConfigMap: SUCCEEDED |
| 23:58–2026-11-13 00:05 | validate-2engine, 5 steps on `vmss000000`, free again: SUCCEEDED |
| 00:05–00:08 | Run log copied off the volume (`scripts/fetch-run.sh`); evidence Job checked; the first recording attempt died at `clear` (problem 7) |
| 00:09:11–00:29:23 | Recording: `demo.sh` steps 1–9. RayJob applied 00:11:44 and SUCCEEDED 00:28:18; evidence Job; RayJob and evidence Job deleted |

## Left on the cluster

Namespace `nemo-rl-demo`, which holds:

- the cache PVC `nemo-rl-dynamo-cache` (512 Gi, Bound), kept as asked;
- the ConfigMap `nemo-rl-dynamo-demo-scripts`;
- the completed setup Job `nemo-rl-dynamo-setup` and its pod. Step 4 of `demo.sh` reads that Job's log.

No RayJob, RayCluster or GPU pod is left, and the `buildkit` namespace is gone. `kubectl delete -k .` removes it
all, including the cache disk.

## Problems and fixes

| # | Problem | Fix here | Who could fix it upstream |
|---|---|---|---|
| 1 | NeMo RL's image has no `tests/functional/`: `.dockerignore` lists `tests/functional/*/`, which Docker cleans to `tests/functional/*`, so CI's Dynamo overlay can't run from the image (CI mounts a checkout) | Ship CI's two scripts unchanged in the ConfigMap and restore them before the run | NeMo RL: if only the per-test output directories were meant, `tests/functional/*/` could become a narrower pattern, or document that functional tests need the source tree |
| 2 | No public image with the new path: release images predate #4905, nightly CI images are private | Build it (1 h 38 min cold) and push to Docker Hub | NeMo RL: a public nightly or the next release would remove the build |
| 3 | `datasets` downloads and prepares all four OpenMathInstruct-2 splits (25 GB, ~10 min) for `split="train_1M"` | Do it once in the setup Job | NeMo RL: `data_files` for the requested split would load 0.64 GB instead of 12.6 GB |
| 4 | Dynamo 1.5.1's KV router logs no `effective_cached_blocks`; the kit's brief expected it | Count `overlap_blocks` on `route request completed` instead | None needed; noted for whoever compares with newer Dynamo |
| 5 | The demo config's reward starts near 0.07. With `max_total_sequence_length: 512` (prompt plus answer), 60–68% of answers hit the limit in the first steps, and 44–66% of prompt groups score the same on all 16 answers, so they carry no signal | Narrated as is. Over 25 steps the reward still rose to ~0.3 as fewer answers were cut off | NeMo RL (optional): a longer limit in the 1B example would cut off fewer answers |
| 6 | Other teams took or tainted GPU nodes mid-work (`benchmark.kaito.sh/asym-reserved` on vmss000001/3; an 8-GPU pod on vmss000000) | Re-pinned by hostname; no foreign taint tolerated | Cluster coordination |
| 7 | Kit bugs found by the runs: a stale `--pid` flag killed the live view in validate-2engine; per-row routing counts were noisy because Ray forwards actor output asynchronously; `TERM` unset killed the first recording at `clear` | Fixed in the kit (running totals; `TERM=xterm-256color` for headless recording) | Kit only |

October's NCCL failure did not recur. That run paired NeMo RL's venvs with a separately locked `/opt/dynamo_venv`
and needed `NCCL_P2P_DISABLE=1`; on the new path the engine runs in NeMo RL's own vLLM venv, both sides load
NCCL 2.30.7, and the refit uses NVLink peer-to-peer (`P2P/CUMEM`) with default settings.
