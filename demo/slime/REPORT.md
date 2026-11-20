# slime + Dynamo on the H100 cluster: KubeRay RayJob + DGD, Dynamo 1.6.1

| | |
|---|---|
| **Date** | 2026-11-04, 19:08–20:01 EST (pod clocks in UTC). Recording uploaded 2026-11-05. |
| **Cluster** | AKS, kube context `h100`, namespace `slime`. 4 × `Standard_ND96isr_H100_v5`; `vmss000002` carries the remediator `NoSchedule` taint, so 24 GPUs were usable. Each run used 3 GPUs. |
| **Compared with** | the earlier `slime-dynamo-aks` kit (not in this repo): Oct 6–7, Dynamo 1.5.1 images, trainer pod plus `kubectl exec`, NCCL weight sync. Verdict there: PARTIAL. |
| **Guide under test** | Dynamo `docs/fern/pages/use-cases/reinforcement-learning/slime.md` and `examples/rl/slime/` (unchanged from v1.5.0 through `main` on 2026-11-04) |
| **Dynamo** | Platform 1.6.1 (Helm; upgraded from 1.5.1 at 17:24 EST that day, outside this session), Grove v0.1.0-alpha.17, KAI v0.17.2. Runs 1–3 used the 1.5.1 runtime images (SGLang 0.5.18); runs 4–7 used the 1.6.1 images (SGLang 0.5.21, torch 2.13.0+cu130, NCCL 2.29.7). |
| **KubeRay** | Operator v1.7.1 (Helm chart 1.7.1, upgraded at 18:37 EST that day, outside this session) |
| **slime** | Image `slimerl/slime:nightly-dev-20261027a-cu130`: same digest (`sha256:2a7437aa771d…`) as `latest-cu130` on 2026-11-03, and the tag in slime `docker/version.txt` at `8a53b680`. Contents: Ray 2.59.0, torch 2.13.0+cu130, slime-patched SGLang 0.5.21, NCCL 2.30.7, safetensors 0.8.0. We used the slime installed in the image, unmodified. |
| **Model / task** | Qwen3-0.6B, GRPO on GSM8K, 10 loops of 64 samples (8 prompts × 8) → 1 train step → weight sync |
| **Kit** | This folder: a verbatim copy of `deploy/aks-dynamo/` from Jont828/slime branch `jont828-slime-dynamo-aks-demo` at `d054e6fd`, plus this report and the recording. Its `README.md` is the full tutorial. The driver and frontend logs this report cites (`runs/…`) and the take-2 recording weren't copied into this repo. |
| **Recording** | [asciinema.org/a/H9rsnRJDYIqWG3CN](https://asciinema.org/a/H9rsnRJDYIqWG3CN) (unlisted), or [`recordings/slime-dynamo-grpo.public.cast`](recordings/slime-dynamo-grpo.public.cast) |

## 1. The short version

- **Verdict: PASS.** slime trained 10 GRPO steps as a KubeRay RayJob. Every rollout was generated through
  the Dynamo frontend, and every policy update reached both Dynamo-managed SGLang engines. The guide's
  checks 1–3 passed in four separate runs, two on 1.5.1 images and two on 1.6.1.
- **What made it work: disk weight sync instead of NCCL.** Disk sync avoids both blockers from October and
  the 1.6.x known issue:
  - the trainer and the engines run different NCCL builds (2.30.7 vs 2.29.7);
  - stock SGLang 0.5.21 rejects slime's NCCL update without a weight-update session
    ([ai-dynamo/dynamo#16245](https://github.com/ai-dynamo/dynamo/issues/16245); its fix PR #16187 was closed
    unmerged).

  Dynamo's known-issues page (on `main`) says of #16245 "No workaround is available yet"; disk sync is one. slime's own
  docs also make disk the default for externally managed engines.
- **Beyond the docs, it needed three fixes, none of them in Dynamo:**
  - **Storage.** We used a Lustre sub-directory volume shared by the trainer and the engines. Mounting the
    filesystem root into DGD pods makes kubelet re-own every file on it.
  - **File permissions.** A 4-line post-write hook. safetensors 0.8.0 writes the weight shards as `0600`
    (owner root), but the engines run as uid 1000. Without the hook the first reload crashed both engines
    (run 1).
  - **KubeRay specifics.** The env vars that `launch-slime.sh` exports must go in the RayJob's runtime env,
    and the Ray head needs a GPU.
- **Small and fast:**
  - **GPUs:** 3 H100s. Two 1-GPU engines are the minimum that shows Dynamo routing; the trainer uses 1 GPU.
  - **Time:** the DGD is Ready in 72 s (images cached); the RayJob takes 2 min 36 s.
  - **Results (runs 3, 4 and 7):** each step's |trainer − rollout logprob| was 0.0094–0.0122. GSM8K reward
    was 0.42–0.81 per step; that's noise, not learning.
- **Recommendation:**
  - Report that it works on [#13933](https://github.com/ai-dynamo/dynamo/issues/13933), the tracking issue.
  - Tell #16245 and the known-issues page that disk sync is a workaround.
  - Open a Dynamo docs issue for the guide's gaps and a slime issue for the `0600` shards.

  Nothing has been posted yet. Drafts are in [section 6](#6-what-to-put-on-github).

## 2. October's problems, re-checked

| October problem | Now | Why |
|---|---|---|
| The guide's `launch-slime.sh` command isn't runnable: no Megatron model, checkpoint, GRPO or optimizer arguments | **Still applies** | The example is unchanged. [`rayjob/train.sh`](rayjob/train.sh) supplies them. |
| NCCL `Message truncated` from mismatched NCCL builds (trainer 2.28.9 vs engine 2.29.7) | **Avoided** | Disk sync. The mismatch remains: 2.30.7 vs 2.29.7. |
| Disk sync needs shared storage, but the example mounts none; mounting the Lustre PVC stalled on recursive `chown` | **Solved** | A static PV with `volumeAttributes.sub-dir: slime-dynamo`, so only the demo's own files (model, data, checkpoint) are re-owned. The same `fsGroup: 1000` and CSI `fsGroupPolicy: File` are behind it ([5.2](#52-dgd-pods-re-own-the-whole-shared-volume)). |
| Transformer Engine `no kernel image is available` on H100 | **Gone** | The newer slime image (`nightly-dev-20261027a`) trains fine. |
| Frontend `/health` listed another DGD's endpoint | **Not reproduced** | In namespace `slime` it listed exactly the two `slime-slime-sglang-*` workers. |
| `--num-rollout 1` can't show a post-update rollout | **Still applies** | The guide is unchanged. We used 10. |
| The README's "make sure the system recovers" contradicts "slime doesn't recover external engines" | **Still applies** | See [4.1](#41-the-guides-six-checks): the engine crash in run 1 behaved as the guide's other sentence says. |
| (New in 1.6.x) First NCCL update fails with HTTP 400 ([#16245](https://github.com/ai-dynamo/dynamo/issues/16245)) | **Avoided** | Disk sync uses only stock SGLang endpoints (`pause_generation`, `flush_cache`, `update_weights_from_disk`, `continue_generation`). |

## 3. What we ran

**Setup, once:**
1. **Storage.** [`storage.yaml`](storage.yaml): a PV/PVC on the Lustre sub-dir `slime-dynamo/`, mounted
   at `/data` everywhere.
2. **Image pre-pull.** [`prepull.yaml`](prepull.yaml).
3. **Model and data.** [`prepare-job.yaml`](prepare-job.yaml) downloads Qwen3-0.6B and GSM8K and
   converts the checkpoint to Megatron `torch_dist`. It takes 90 s on 1 GPU.
4. **Dynamo's scripts.** `launch-slime.sh` and `dynamo_generate.py`, unmodified, go into ConfigMap
   `dynamo-slime-example`.

**Each run:**
1. **Inference.** Apply [`dynamo-dgd.yaml`](dynamo-dgd.yaml): Dynamo's example manifest plus the
   `/data` mount, with the engines loading `/data/Qwen3-0.6B`.
2. **Training.** Apply [`rayjob/`](rayjob/): a head-only RayJob with 1 GPU. Its entrypoint,
   [`train.sh`](rayjob/train.sh), runs the guide's readiness curls, then execs Dynamo's
   `launch-slime.sh` with the full slime arguments and `--update-weight-transport disk`.

| Run | Time (EST) | Images | Config | Result | Evidence |
|---|---|---|---|---|---|
| 1 | 19:17 | 1.5.1 | dapo-math-17k, 3 rollouts | **FAIL** at the first weight reload | Both engines: `FileNotFoundError .../weight_v000001/model-00001-of-00003.safetensors`, HTTP 500, then SGLang exits and the containers restart. `runs/run1-perm-fail-1.5.1.driver.log` |
| 2 | 19:21 | 1.5.1 | + chmod hook | **PASS** in 2 min 3 s, but reward 0.000 on all 192 samples | Qwen3-0.6B can't solve DAPO problems in 1,024 tokens (up to 23% truncated), so the GRPO advantages are all zero. The pipeline is exercised but there's no learning signal. `runs/run2-dapo-1.5.1.driver.log` |
| 3 | 19:26 | 1.5.1 | GSM8K, `--rm-type math`, 10 rollouts | **PASS** in 2 min 36 s | Reward 0.48–0.75; logprob gap 0.0094–0.0122. `runs/run3-gsm8k-1.5.1.driver.log` |
| 4 | 19:40 | **1.6.1** | as run 3 | **PASS** in 2 min 39 s | Engine reports SGLang 0.5.21. Reward 0.42–0.81; logprob gap 0.0096–0.0122. Routing 320/320, 11 reloads per engine. Log not saved. |
| 5 | 19:47 | 1.6.1 | recording, take 1 | **FAIL** (cluster) | The DGD apply was refused: `failed calling webhook "mdynamographdeployment.kb.io" ... no endpoints available`. The Dynamo operator and Grove pods had been recreated at 19:47:14 EST by something outside this session, and the operator was briefly unready. With no engines, `train.sh`'s readiness curl failed (exit 7). [`demo.sh`](demo.sh) now stops on such failures. |
| 6 | 19:52 | 1.6.1 | recording, take 2 | **PASS** in 2 min 35 s | `recordings/run6-take2.cast` |
| 7 | 19:58 | 1.6.1 | recording, take 3 (uploaded) | **PASS** in 2 min 36 s | [`recordings/slime-dynamo-grpo.public.cast`](recordings/slime-dynamo-grpo.public.cast), `runs/run7-recorded-1.6.1.driver.log`, `runs/run7-recorded-1.6.1.frontend.log` |

**Probes along the way:**
- **Head without a GPU.** A CPU-only pod on the slime image can `import train`, but importing the Megatron
  actor fails with `libcuda.so.1: cannot open shared object file`. The driver creates that actor, so the Ray
  head needs a GPU.
- **Text request to the frontend.** A text `POST /generate` returns `400 missing field input_ids`: the
  frontend's native endpoint is token-in only. slime sends token IDs, so this is expected.
- **safetensors file mode.** In the slime image, `save_file` creates `0600` files even with umask `022`; a
  plain `open()` creates `0644`.

## 4. Checks and evidence

### 4.1 The guide's six checks

The checks are from `slime.md`, "Make Sure That the Integration Works".

| # | Check | Result | Evidence (run 7 unless noted) |
|---|---|---|---|
| 1 | One rollout with the selected model and weight transport | ✅ | 10 rollouts of 64 samples, each 0.9–2.5 s |
| 2 | One policy update to both engines | ✅ | `POST /update_weights_from_disk ... 200` 11 times on each engine: the startup sync plus one per step |
| 3 | One rollout after the update | ✅ | Rollouts 1–9 each follow an update; the logprob gap stays at about 0.01 (4.2) |
| 4 | Stop one worker during a run | ⚠️ not deliberate | In run 1, both engines crashed during the first reload |
| 5 | slime stops or reports the failure | ✅ incidentally (run 1) | RayJob `FAILED`; the driver shows `HTTPError: 500 ... /update_weights_from_disk` |
| 6 | Restart slime after Kubernetes replaces the worker | ⚠️ partial | Run 2 passed against the same pods after their containers restarted, which keeps the pod IP and Service. A replaced Pod (new IP) was not tested. |

### 4.2 Proof that rollouts used Dynamo and the synced policy

- **slime is wired to Dynamo.** The driver's argument dump shows:
  - `custom_generate_function_path = dynamo_generate.generate_streaming`;
  - `rollout_external_engine_addrs = ['slime-sglang-engine-0:30000', 'slime-sglang-engine-1:30000']`;
  - `update_weight_transport = disk`.

  slime still launches its own SGLang router, but only as an engine registry.
- **The frontend did the routing.** Its log has one `Selected worker` line per request: 640 lines, 320 for
  each worker ID, all `router_mode="round-robin"`.
- **Each engine served its share through the sidecar.** Each engine logged 321 `POST /generate 200`
  requests: 320 from the rollouts plus 1 more. In run 2 we checked their source: all came from `127.0.0.1`,
  forwarded by that worker's Dynamo sidecar, and none from the trainer's IP.
- **The engines serve the policy that was just synced.** Megatron recomputes the log-probs of every sampled
  token and compares them with what the engines returned: `train/train_rollout_logprob_abs_diff` was
  0.0099–0.0121 on all 10 steps. An engine still on an older policy would show a growing gap. This is weaker
  than verl's stale-step control experiment, which we did not repeat here.

### 4.3 Timing (run 7)

| Phase | Time |
|---|---|
| DGD apply → Ready (images cached; first pull of `sglang-runtime` took about 3.5 min) | 72 s |
| RayJob apply → driver submitted | 19 s |
| Ray, Megatron init, checkpoint load and first sync | 65 s |
| 10 loops (rollout + train + sync) | 61 s |
| Weight sync: 1.2 GB HF checkpoint written to Lustre, then both engines reload | 1.4–1.6 s (2.9 s first) |
| RayJob total | 2 min 36 s |

## 5. Findings

### 5.1 Disk sync: shards are `0600`, engines run as uid 1000

- **What happens:** slime's disk updater writes the shards with safetensors `save_file`. In the image's
  safetensors 0.8.0 that produces `0600` files owned by root, while `config.json` and the other copied files
  stay readable. Dynamo's SGLang workers run as `uid=1000(dynamo)`, so the engine gets `FileNotFoundError`;
  SGLang then exits and kills its whole process tree.
- **Fix used:** `--custom-update-weight-post-write-path slime_dynamo_hooks.make_weights_readable`. It chmods
  the version directory to `0755` and the files in it to `0644`.
- **Owner:** slime, to chmod after `save_file` or document the requirement. Dynamo's guide should mention it
  whenever it suggests disk sync.

### 5.2 DGD pods re-own the whole shared volume

- **Cause:** the Dynamo operator gives DGD pods `securityContext.fsGroup: 1000`, and the Azure Lustre CSI
  driver declares `fsGroupPolicy: File`. Together they make kubelet recursively `chown`/`chmod` the volume on
  every pod start, and a `subPath` doesn't limit it. On the shared filesystem this stalled the pods in
  October, and it changes ownership of other users' files.
- **Fix:** a static PV with `volumeAttributes.sub-dir`. The driver creates the sub-dir on first mount.
- **Owner:** Dynamo docs (the guide and any AKS/Lustre page).

### 5.3 Running the trainer under KubeRay

The Dynamo guide only says to run slime "in the same namespace". As a RayJob, two things matter:

- **Runtime env.** `launch-slime.sh` exports `PYTHONPATH` and `DYNAMO_ROLLOUT_URL`, but Ray workers started by
  the head pod's raylet don't inherit the driver's shell. Both must be in the RayJob's `runtimeEnvYAML`.
  Otherwise `dynamo_generate` can't be imported, or the generator can't find the frontend.
- **GPU on the head.** The driver imports Transformer Engine, which needs `libcuda`, so the head needs a GPU.
  With a 1-GPU trainer the head can host it.

**Owner:** Dynamo docs (a Kubernetes trainer example).

### 5.4 Smaller observations

- **Score centering vs. streaming.** slime's lab and recipes default to `--use-score-centering`, which slime
  rejects with streaming rollouts (`slime/utils/score_centering.py`). The Dynamo generator streams, so drop
  that flag. Found by reading the code, not hit.
- **Smoke-test dataset.** DAPO-Math with a 0.6B model and 1,024 tokens gives zero reward and zero gradient
  signal (run 2). GSM8K with `--rm-type math` gives a working smoke test; slime's own reproducibility example
  uses the same dataset.
- **Example manifest has no namespace.** `kubectl delete -f dynamo-dgd.yaml` without `-n` targets `default`.
- **Startup probes.** DGD workers fail them while SGLang warms up (connection refused, then 503); the DGD
  becomes Ready at about 70 s.
- **Frontend `/generate` is token-in only** (see section 3).

## 6. What to put on GitHub

Nothing has been posted or filed. Search each repository again before filing.

### 6.1 Report that it works: comment on [ai-dynamo/dynamo#13933](https://github.com/ai-dynamo/dynamo/issues/13933)

**Scope.** The tracking issue's checklist items now covered:
- known-good versions (the header table);
- the generation smoke test through the shared endpoint;
- a small end-to-end streaming RL run;
- per-engine control and weight updates (disk transport).

**Results.** The results table from section 3 and the evidence in 4.2.

**Problems and gaps.** The three fixes from section 5, and what's untested: NCCL transport, multiple nodes,
deliberate failure injection, KV routing.

**Reproduction.** Link to the kit, or paste `rayjob.yaml` and `train.sh` in collapsible sections.

### 6.2 Open or comment

1. **ai-dynamo/dynamo#16245 and `known-issues.mdx`:** say that disk sync
   (`--update-weight-transport disk` plus a shared mount) works on 1.6.1 without SGLang sessions, so "No
   workaround is available yet" can become a workaround.
2. **ai-dynamo/dynamo, docs:** `docs(rl): make the slime guide runnable on Kubernetes`. Contents:
   - a complete smoke command, or a pointer to slime's model scripts;
   - `--num-rollout 2`;
   - the shared-storage requirements for disk sync: a mount on the DGD workers, a sub-dir volume on Lustre
     (5.2), and file permissions (5.1);
   - a trainer example as a RayJob, including the runtime env and the head GPU (5.3);
   - removing "make sure the system recovers".
3. **THUDM/slime:** `Disk weight sync writes 0600 safetensors; external engines running as another user
   can't load them`. Include the safetensors 0.8.0 repro from section 3 and suggest chmodding in
   `hf_checkpoint_saver.py` after `save_file`. No existing issue was found.

### 6.3 Don't open

The webhook outage in run 5: an outside operator restart, transient.

## 7. How to reproduce

From this folder (`demo/slime`). The kit's `README.md` has the same steps in more detail.

```bash
DYNAMO=../..               # this repo's root; any ai-dynamo/dynamo checkout works (examples/rl/slime is unchanged since v1.5.0)
kubectl apply -f storage.yaml                       # edit mgs-ip-address/fs name for another Lustre
kubectl apply -f prepull.yaml                       # optional; delete once the pods are Running
kubectl apply -f prepare-job.yaml && kubectl -n slime wait --for=condition=complete job/slime-dynamo-prepare --timeout=30m
kubectl -n slime create configmap dynamo-slime-example \
  --from-file=$DYNAMO/examples/rl/slime/launch-slime.sh --from-file=$DYNAMO/examples/rl/slime/dynamo_generate.py

# Either the narrated demo (deploys, runs, checks, cleans up; about 5 min):
./demo.sh
# or by hand:
kubectl -n slime apply -f dynamo-dgd.yaml && kubectl -n slime wait --for=condition=Ready dgd/slime-sglang --timeout=15m
kubectl apply -k rayjob && kubectl -n slime logs -f job/slime-dynamo
kubectl -n slime get rayjob slime-dynamo            # JOB STATUS: SUCCEEDED
```

Cleanup:

```bash
kubectl -n slime delete rayjob slime-dynamo --ignore-not-found
kubectl -n slime delete -f dynamo-dgd.yaml --ignore-not-found
```

**Left on the cluster:**
- PVC `slime-dynamo-data` with the model, GSM8K and converted checkpoint;
- PV `pv-lustre-slime-dynamo` (Retain);
- Job `slime-dynamo-prepare`;
- ConfigMaps `dynamo-slime-example` and `slime-dynamo-scripts`.

No GPUs are held.

## 8. Caveats

- **Small and short:** 0.6B model, 10 steps, 1-GPU trainer, two 1-GPU engines on one node. Reward moves with
  the 8 prompts per step; this is not a learning curve.
- **Not tested:**
  - NCCL weight sync (blocked by [#16245](https://github.com/ai-dynamo/dynamo/issues/16245) and the NCCL
    build mismatch);
  - delta sync (needs slime's `/pull_weights` patch, which stock SGLang lacks);
  - multiple nodes, multi-GPU engines, KV-aware routing, deliberate failure injection, long runs, and speed
    compared with slime-launched engines.
- **Weaker policy check than verl's:** the evidence that engines serve the synced policy is the
  trainer/rollout logprob gap, not a stale-weights control experiment.
- **Mixed versions in runs 1–3:** they used 1.5.1 runtime images under the 1.6.1 operator; runs 4–7 used
  matching 1.6.1. The platform and KubeRay were upgraded earlier that day, outside this session.
- **Our deviations from a plain install of the guide:**
  - disk transport;
  - a `/data` mount and local model path in the DGD;
  - the chmod hook;
  - the trainer as a RayJob with the full slime arguments.
- **What was not modified:** Dynamo's scripts, slime and the images.
- **Recording redactions:** the uploaded recording shows pod IPs (`10.244.x.x`), AKS node names and the Helm
  install times. It contains no credentials. Unlike verl's `.public.cast`, these weren't masked.
