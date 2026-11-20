# Working notes for REPORT.md (timeline, UTC)

## Upstream checks
- 2026-11-11 23:32: NeMo RL main = 27fac5f3d6bd (#5130). Scheduled nightlies: f488a0c6 (2026-11-11) failed in
  L0_Unit_Tests_Other + CI quality check; its jobs list has no Dynamo job. Last nightly success 479735d7 (2026-11-07).
- Main *push* runs of cicd-main.yml do run L1_Functional_Tests_Dynamo:
  - f488a0c6 (#4905 merge, run 42982911339): L1_Functional_Tests_Dynamo success 12:02-12:10 (run failed elsewhere)
  - d51b2842 (#4882, run 43055697255): L1_Functional_Tests_Dynamo success 18:10-18:17, whole run success.
    Job 130708807115 on nemo-ci-aws-gpu-x2: 2 overlays (grpo_dp_single_controller, ..._reload_refit), both PASS.
    Evidence lines: "[Dynamo] frontend ... routes to 1 engine(s)", 16 "route request completed", 
    "router applied KV events ... ok: 392", gen_kl_error 0.00062 / 0.00058 max.
  - 27fac5f3: in progress at check time.
- compare f488a0c6...d51b2842: ahead by 2 (e6aae2a7 #3726 DSpark, d51b2842 #4882 SC reward_shaping).
- No NeMo RL release after v0.8.0 (2026-11-06).
- 2026-11-12 20:25 re-check: main defdaef4 (2 commits after d51b2842: #5130 PPO filtering, #4861 TRT-LLM refit; no
  Dynamo/Dockerfile/uv.lock/pyproject change). Nightly at defdaef4 cancelled. Kept d51b2842.
- Format notes from CI log (ai-dynamo 1.5.1): "Selected worker router_mode="kv" request_id=... worker_id=N
  worker_type=decode dp_rank=0 logit=... host_pinned_blocks=0 disk_blocks=0" -- no effective_cached_blocks field.
  "route request completed ... overlap_blocks=N ... worker_id=N" carries the cached-prefix count.

## Build
- 2026-11-12 20:24: namespace buildkit + buildx kubernetes builder nemo-rl-builder (BuildKit v0.34.2), pinned to
  vmss000000 (971 GB free), 64 CPU / 384 GiB. Azure Policy warning about moby/buildkit image (warn only).
- 20:26: build started (image/build-image.sh): CI args (target release, nemo-rl=., BUILD_DYNAMO=1, MAX_JOBS=4) +
  SKIP_SGLANG_BUILD, SKIP_TRTLLM_BUILD, STRIP_UV_CACHE, NVTE_CUDA_ARCHS=90, CUSTOM_SETUP_FNAME= (no apptainer).
- Cold build: compiles deep-ep, deep-gemm, TE torch 2.18 (~3.6 min), fast-hadamard, mamba-ssm, causal-conv1d,
  flash-attn 2.8.1, flash-mla from source (CI seeds these from its uv-cache image).

## Cluster
- 20:41: kubectl apply -k . (ns nemo-rl-demo, cm, pvc Pending/WaitForFirstConsumer). All 4 manifests pass
  server-side dry run. Istio injector needs ns/pod labels; none set.

## Build result
- 22:04:00 pushed docker.io/jont828/nemo-rl:main-d51b2842-dynamo. Total 1 h 38 min (20:26 -> 22:04).
  - index sha256:f1008ba75c64a7a8a02aa9a74f11b76f052e9ac7e4dd2106c11d8c373e7b6fc4 (linux/amd64 + attestation)
  - image manifest sha256:adbedcb5f3b41463753b03b1cdc83b547f249a63897ddb7b89106a845c10cd36
  - 61 layers, 17.28 GB compressed, largest 10.38 GB (then 2.69, 1.78 GB). v0.8.0 public: 44.8 GB / 20.8 GB.
  - step times: hermetic dependency RUN (#28) 3753 s (62.5 min, cold: compiled deep-ep, deep-gemm, TE torch,
    fast-hadamard, flash-mla, mamba-ssm, flash-attn 2.8.1, causal-conv1d); uv lock check 272 s; release
    prefetch 123 s; export + push 1354 s (layers 743.6 s). No 502.
- 22:04:30 builder removed, buildkit namespace deleted.

## Cluster, continued
- 22:04: setup Job Pending: vmss000001 and vmss000003 now carry `benchmark.kaito.sh/asym-reserved=gptoss-asym-20261112:NoSchedule`
  (another team's KAITO benchmark, 4 GPUs each in use); vmss000002 still has the remediator taint. Not tolerated.
  Re-pinned every manifest to vmss000000 (8 free GPUs, 837 GB disk free after the builder left).
- 22:05:33 setup Job pod scheduled; PVC pvc-9da5b583-... provisioned + attached in ~15 s; image pull started 22:05:57.
- 22:18:16-22:29:42 setup script (after a 12 min image pull, 22:06:10-22:18:16): versions printed; models fetched in
  1 min; OpenMathInstruct-2: datasets 5.0.1 downloaded all 32+3+6+14 parquet files and prepared all four splits
  (25 GB in $HF_HOME/datasets) for split="train_1M"; map + split cached. Job Complete, 23 min.
  - my wait loop never fired: zsh doesn't word-split "$ns" -> use functions/bash for these loops.
  - du on hub/models--* showed 11M/4.3M: huggingface_hub 1.32 keeps big files in a shared hub/blobs store
    (hub = 17 GB). Weights are cached. Fixed the report to du -L the snapshots.
- 23:38:30 smoke RayJob submitted: head Pending, "Insufficient nvidia.com/gpu": vmss000000 now runs
  saturn-sregym-stratus/qwen38-flash-next (8 GPUs, since 22:48). KAITO asym-reserved taints gone from
  vmss000001/000003 (8 free each). All nodes zone "0", PV zone "" -> disk can follow.
- 23:39:30 re-pinned to vmss000003; smoke resubmitted; disk attached in ~10 s; image pull started 23:39:40.
- smoke-1 (23:39:30-23:52:26, FAILED rc 127): head pod pulled the image 23:39:40-~23:51:30; KubeRay's ray start
  worked with the image as is; ray job submit OK; driver saw "1 node(s), 2 GPUs, 24 CPUs", RAY_ADDRESS=<pod IP>:6379.
  Then `bash: tests/functional/grpo_dp_single_controller.sh: No such file or directory`: NeMo RL's .dockerignore
  has `tests/functional/*/`, which Docker cleans to `tests/functional/*` -> no tests/functional files in the
  image (CI runs tests from a mounted checkout). Fix: vendor the 2 scripts unchanged (blob hashes match
  d51b2842: de4656dc grpo_dp_single_controller.sh, 42a2b381 check_dynamo_overlay.sh) in the ConfigMap and
  install them into /opt/nemo-rl/tests/functional/ before running.
- smoke-2 submitted 23:53:43 (image now cached on vmss000003).
- smoke-2 (23:53:43-23:57:21, SUCCEEDED, 3 min 38 s): head container started 13 s after apply (image cached),
  submitter 13 s later. "Connected to existing Ray cluster" (RAY_CLUSTER_EXTERNAL). etcd/NATS on the pod IP,
  engine publishes KV events True, frontend routes to 1 engine, 2 steps (13.45 s, 5.13 s; refit 0.08 s; refit
  communicator world_size=2), CI metric checks all PASS (gen_kl_error max 0.000647, max_seq_mult_prob_error
  1.0218), overlay: 16 routed, 392 KV events applied, no leftovers. No NCCL_P2P_DISABLE needed (NCCL_DEBUG unset,
  transport not logged). Local copy: runs/smoke-2.log.
- 23:58:28 two-engine validation submitted: demo rayjob.yaml with NCCL_DEBUG=INFO STEPS=5
  RUN_NAME=grpo-validate-2engine. vmss000000 free again at that time.
- validate-2engine (23:58:28-00:05:01, SUCCEEDED, 6 min 33 s; training process 5 min 57 s incl. the one-time
  Megatron conversion of Qwen2.5-1.5B): "Connected to existing Ray cluster"; both engines vLLM 0.29.0
  registered, KV events True; "frontend ... routes to 2 engine(s)"; 3,072 routed (1,517/1,555), 2,876 (94%)
  with overlap_blocks>0, 5,964 overlap blocks; router applied 62,783 KV events, 0 errors; gen_kl_error
  0.00062-0.00064; token_mult_prob_error 1.019-1.020; 5 refits 0.11 s avg; NCCL 2.30.7 x3, refit channels
  "via P2P/CUMEM" (72 lines) -> P2P works, no NCCL_P2P_DISABLE. Steps: 51.5 s then ~30 s (train ~13.6 s,
  gen wait 0 s: async look-ahead). Reward 0.061/0.059/0.072/0.073/0.059: truncation_rate 0.61-0.68 at the
  config's max_total_sequence_length 512 (prompt ~110 tokens, mean response ~330), zero_variance_fraction
  0.50-0.63. My watcher died at start (stale --pid flag; fixed). Report + evidence Job verified.
  Local: runs/validate-2engine.log (job log), runs/grpo-validate-2engine.run.log (full run.log).
- Live view: switched the routing columns to running totals (per-row deltas were 741/795 then 0/0 because Ray
  forwards actor lines asynchronously while the frontend writes directly).
- Recorded run: STEPS=25, re-pinned to vmss000000 (free again, image cached by the setup Job, gpu_free's pick).
  Recording started 00:08:42.
- Recording (00:09:11-00:29:23, 20.2 min; the first attempt at 00:08:42 died at `clear`, TERM unset): demo.sh
  steps 1-9. RayJob nemo-rl-grpo-demo applied 00:11:44, SUCCEEDED 00:28:18 (16 min 34 s). Run
  grpo-20261113-001238: driver up ~54 s after apply (incl. a 26.6 s pull of rayproject/ray:2.58.0-py313 for
  the submitter); training process 15 min 31 s: Dynamo services at +0:52, both engines registered +1:32, frontend
  routes to 2 engines +1:33, Megatron ready +1:46 (checkpoint already converted by validate-2engine), loop
  +2:16, step 1 46.0 s then 30.1 s/step (train 13.7 s, gen wait 0 s). 25/25 steps, report PASS.
  Reward 0.072 -> 0.305 (first 3 mean 0.080, last 3 0.278, max 0.343 at step 19). truncation_rate 0.60-0.68
  steps 1-8, 0.44-0.54 steps 23-25; zero_variance_fraction 0.44-0.66 steps 1-8, 0.28-0.41 steps 23-25.
  13,312 routed = 26 x 512 (one batch of look-ahead past step 25): 6,763/6,549; 13,066 (98%) overlap_blocks>0,
  26,300 blocks; 270,223 KV events applied, 0 errors; services stopped, no leftovers. gen_kl_error
  0.00060-0.00143 except step 11 = 0.00209 (> CI's 0.002; report marks it "!", a warning); token_mult_prob_error
  1.012-1.026; 25 refits 0.11 s. Evidence Job + teardown in the recording; RayJob and evidence Job deleted.
  Paced cast 9 min 17 s; public cast: pod IPs, frontend URL and node-pool ID masked, scan clean.
  Local: runs/grpo-20261113-001238.run.log (94,809 lines, via scripts/fetch-run.sh after the recording ended).
- Left on the cluster: ns nemo-rl-demo with PVC nemo-rl-dynamo-cache (512 Gi, kept), ConfigMap
  nemo-rl-dynamo-demo-scripts, the completed setup Job (demo.sh step 4 reads its log). No RayJob/RayCluster.
  buildkit namespace already gone.
