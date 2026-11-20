# Slime ReTool + Dynamo: KV-aware routing vs round-robin (h100, Dynamo 1.6.1)

Two runs of Slime's ReTool example on the `h100` AKS cluster, with rollouts served by a Dynamo
DynamoGraphDeployment (DGD). One run routes every request by KV-cache overlap and load; the other, a
baseline, routes round-robin on the same engines. Also here: a narrated asciinema replay of the KV run.
Results and analysis: [`REPORT.md`](REPORT.md).

In short (10 GRPO steps per run, about 30 min each on one 8-GPU node, same problems in every step):
KV-aware routing nearly halved the prompt tokens the engines had to compute (5.66M against 10.45M; prefix-cache
hit rate 79% against 60%), but it didn't make the run measurably faster. Trajectories finished at the same pace
under both routers: half of each step's trajectories were done after a mean 18.7 s against 18.8 s. The summed
rollout time was 6% lower with KV routing (985 s against 1,047 s), but the whole difference comes from each
step's single slowest trajectory, which differed by −35 to +42 s per step for reasons unrelated to routing.
This workload's time goes into generating output, not reading prompts.

Two narrated replays of the KV run play without a cluster:

- **Demo** (100×25): `asciinema play demo/slime-retool-demo.public.cast`. Not uploaded.
- **Team demo** (120×32, 12 min 26 s in 11 chapters): `asciinema play demo/team/slime-retool-team-demo.public.cast`,
  or [asciinema.org/a/4SshnT6HOWZgvVgL](https://asciinema.org/a/4SshnT6HOWZgvVgL) (unlisted). It covers the
  problem (ReTool, one trajectory, the RL loop), the AKS stack and Azure Managed Lustre, each step's manifest typed
  in an emulated vim with callouts, one trajectory turn by turn, the 10 steps, and KV-aware routing against the
  round-robin baseline, in terminal bar charts drawn by [`tools/compare_bars.py`](tools/compare_bars.py).

To record them again, see [Re-record the demos](#re-record-the-demos).

**ReTool in plain words.** It's reinforcement learning (RL) on competition math. Qwen3-4B, already
fine-tuned to call a Python tool, solves DAPO-Math-17k problems. Partway through a solution it writes
code. The rollout code (the part of the RL loop that collects the model's attempts) stops generation,
runs the code in a local sandbox, appends the output, and sends the whole transcript back to the model
as the next request. A trajectory, one attempt at one problem, can take up to 16 requests. Only the
final `Answer: \boxed{...}` is graded: +1 if right, -1 if wrong, with a small bonus for using the tool
on a wrong answer. GRPO, the training algorithm, samples 8 answers per problem and pushes the model
toward the ones that scored above their siblings' average.

**Why it suits KV-aware routing.** Each request's prompt is the previous request plus its output plus
the sandbox output. The engine that served the previous request already holds everything except the
sandbox output in its prefix cache (the KV cache that SGLang keeps after a request finishes). Routing
the next turn there skips re-reading the history. The round-robin baseline shows the catch: here,
re-reading a history takes tens of milliseconds, while generating a reply takes a second or more
([REPORT section 5](REPORT.md#5-round-robin-baseline-is-kv-routing-faster)).

## What ran

| Piece | Where | What |
|---|---|---|
| Storage | [`manifests/storage.yaml`](manifests/storage.yaml) | Namespace `slime-retool`; static Lustre PV `pv-lustre-slime-retool` on sub-dir `slime-retool/` (Retain); PVC `slime-data` |
| Setup Job | [`manifests/setup-job.yaml`](manifests/setup-job.yaml) | Downloads `font-info/qwen3-4b-sft-SGLang-RL` (8.3 GB) and `zhuzilin/dapo-math-17k`; converts the checkpoint to `torch_dist` on 1 GPU with `--rotary-base 5000000`; creates `/data/weight-updates` and `/data/turn-logs` |
| DGD | [`manifests/dgd.yaml`](manifests/dgd.yaml), [`manifests/engine-services.yaml`](manifests/engine-services.yaml) | Dynamo frontend with `DYN_ROUTER_MODE=kv`, `DYN_ROUTER_PREDICTED_TTL_SECS=5`, `DYN_SGLANG_ENABLE_GENERATE=1`; four 1-GPU SGLang engines (`worker-0..3`) with `--page-size 16`, ZMQ `--kv-events-config`, and the SGLang sidecar; one Service per engine. The settings of the first experiment's `kv` phase |
| Round-robin DGD | [`manifests/dgd-rr.yaml`](manifests/dgd-rr.yaml) | The baseline: `dgd.yaml` with `DYN_ROUTER_MODE=round-robin` and without `DYN_ROUTER_PREDICTED_TTL_SECS`, which only the KV router reads. The engines are unchanged, page size 16 and KV events included, so the two runs differ only in which engine serves each request |
| Functions | [`functions/`](functions/) → [`manifests/functions.yaml`](manifests/functions.yaml) | Patched `generate_with_retool.py` and `tool_sandbox.py`, and the tutorial's `weight_permissions.py`, in ConfigMap `slime-retool-functions`, mounted on `PYTHONPATH` at `/opt/slime-functions` |
| RayJob | [`manifests/rayjob.yaml`](manifests/rayjob.yaml) | Slime's `train.py` on a 4-GPU Ray head (Megatron-LM, TP 2 × CP 2), 48 CPUs and 400 Gi for the CPU optimizer offload and the sandbox subprocesses; disk weight sync to the four engines |

Images: `slimerl/slime:nightly-dev-20261027a-cu130` (Slime 0.4.0, SGLang 0.5.21 for the trainer's
tooling, Ray 2.59.0), `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.1`,
`nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.6.1`. One node, `aks-ndh100pool-82423760-vmss000003`,
which had all three images cached; every Pod is pinned to it with [`tools/pin.py`](tools/pin.py).

## Changes from upstream ReTool, and why

Upstream is `examples/retool/` in the Slime image, which is identical to THUDM/slime `main` at
`79aa41a8` (2026-11-05). Copies of both are in [`upstream/`](upstream/), read-only. Every code change
is marked `PATCH(...)` in [`functions/`](functions/); `diff -u upstream/image/examples/retool/<file>
functions/<file>` shows each one.

### `generate_with_retool.py`

| Change | Why | Evidence |
|---|---|---|
| **`PATCH(stream)`** Each turn is a streaming `POST /generate` to the Dynamo frontend (`DYNAMO_ROLLOUT_URL`), folded back into the non-streaming result: `meta_info` from the final chunk (`finish_reason`, `prompt_tokens`, `cached_tokens`, `weight_version`), with `output_token_logprobs` rebuilt for every output token. It uses Slime's own `SGLangStreamAccumulator` and HTTP client, like `slime/rollout/sglang_streaming_rollout.py`. `generate.abort_mode = "request"`, and the RayJob keeps `--sglang-incremental-streaming-output`. A failed attempt is retried from scratch under a new request ID, with the same 60-try budget as Slime's `post()`. | Dynamo 1.6.x serves SGLang's token-in `/generate` only as a stream; a non-streaming request gets HTTP 400 (`lib/llm/src/http/service/sglang_generate.rs` at v1.6.1). Upstream ReTool posts non-streaming requests. | Smoke test: 29,131 logprobs for 29,131 output tokens; the run trained with a rollout/training logprob gap of about 0.01 |
| **`PATCH(prompt)`** The RayJob drops `--apply-chat-template`, and `generate()` takes the user question from DAPO-Math's message-list prompt (`_question_text`). ReTool's own template then renders it once. | DAPO-Math-17k stores each prompt as `[{"role": "user", "content": ...}]`. With upstream's `--apply-chat-template`, Slime renders Qwen's chat template first, and ReTool wraps that text in its template again: the model saw `<|im_start|>user<|im_start|>user ...` and an empty assistant turn, 5 role tags in all. | Upstream rendering: 5 `<|im_start|>` tags, 389 tokens. Patched: 3 (system with the tool spec, user, assistant), 384 tokens |
| **`PATCH(prompt)`** Restore the newline after each role tag. | ReTool's Jinja template uses `{%-`/`{{-` whitespace control, which also eats the newline after `<|im_start|>system`, `user`, and the final `assistant`, so the model saw `<|im_start|>userSolve ...`. Qwen's chat template, which the SFT checkpoint was trained on, writes `<|im_start|>user\n`. | Rendered prompts in the smoke test |
| **`--rollout-stop '</code>' '</tool_call>'`** (a trainer flag, no code change) | Upstream sets no stop string, and the SFT data puts the `<interpreter>` output inside the model's own message, so the model could write its own guess at the output instead of pausing for the sandbox. | The checkpoint calls the tool in Qwen's `<tool_call>{"name": "code_interpreter", ...}</tool_call>` format, not `<code>`: in the smoke test, 28 of 37 requests stopped at `</tool_call>` and 9 at end-of-text. Without `</tool_call>` as a stop string, generation would run past the tool call instead of pausing for the sandbox |
| **`PATCH(fences)`** Run the code inside a ```` ```python ```` fence in a `<code>` block, not the fence itself. | All 4,983 code blocks in the ReTool-SFT data are written as ```` <code>```python ...```</code> ````. Upstream passes everything between the `<code>` tags to the sandbox, where the fence is a SyntaxError. verl's ReTool recipe, which this file adapts, strips the fence the same way. | [`tools/sandbox_check.py`](tools/sandbox_check.py) on the first 200 SFT blocks: upstream gave 160 SyntaxErrors and 40 refusals, so no block ran; patched, 161 ran cleanly. (In this run the model mostly used `<tool_call>` JSON, which carries no fence.) |
| **`PATCH(semaphore)`** One acquire of the tool semaphore per code run, in `execute_predictions`, which also times the wait and the run. | Upstream acquires it in `execute_predictions` and again in `tool_sandbox.execute_tool`, so each run held 2 of the 32 permits. With real concurrency (next table) that nesting can deadlock: if 32 callers each hold their first permit, none can get a second. | Code reading (not reproduced); in the run, the longest wait for a permit was 1.05 s, across 9,222 code runs |
| **`PATCH(turnlog)`** One JSON line per request in `/data/turn-logs/<run>/`: times, sample and group, turn, request ID, input length, prompt and cached tokens, output tokens, finish reason, the turn's text, tool action, outcome, wait and run time, code and output (clipped), observation tokens; one `final` line per scored sample. Each request carries `rid=retool-<sample>-t<turn>-<random>`. | For the replay and the analysis. Dynamo uses the body's `rid` as its request ID, so the frontend's `Selected worker` line names the engine for every turn. | 100% of the run's requests joined to a routing decision (REPORT) |
| **`PATCH(reward)`** `reward_func` builds its solution string from the question text, not `sample.prompt + sample.response`. | Without `--apply-chat-template`, `sample.prompt` is a list, and `list + str` raises TypeError: Slime issue #1829, fixed by PR #2120, which is still open. `compute_score` reads only the last 300 characters, so the prefix doesn't change the score. | Smoke test: a right answer scored +1.0 with a list prompt |

**Slime issue #1829:** it affects the image's Slime, because PR #2120 isn't merged and the image's
`reward_func` is unchanged. Upstream as shipped hits it only in AIME evaluation, whose prompts reach
`reward_func` as lists; training prompts are strings because of `--apply-chat-template`. This run
disables evaluation and drops `--apply-chat-template`, and `PATCH(reward)` handles list prompts.

### `tool_sandbox.py`

| Change | Why | Evidence |
|---|---|---|
| **`PATCH(sandbox-nonblocking)`** Run the subprocess in a worker thread (`asyncio.to_thread`), and drop the `gc.collect()` that followed every run. | Upstream calls `Popen().communicate()` inside an `async` function, which blocks the rollout's event loop for the whole run. Every other trajectory's HTTP stream stalls, and code runs happen one at a time whatever the semaphore allows. The post-run `gc.collect()` cleans up the rollout process, but the code ran in a subprocess, and a full collection also holds the event loop. | `sandbox_check.py`: 200 blocks took 4.0 s through upstream's sandbox, one at a time, and 0.8 s patched, with 32 at once |
| **`PATCH(sandbox-semaphore)`** Drop the second acquire (see above). | | |
| **`PATCH(sandbox-timeout)`** `python_timeout` 120 s → 20 s. | A synchronous step waits for its slowest trajectory, so one runaway loop would hold the whole step for two minutes. | |
| **`PATCH(sandbox-imports)`** Read imports the way Python does: `from X import Y` checks `X`; `import a, b.c as d` checks `a` and `b`. Every `import` keyword is still checked. | Upstream matched `import\s+(\w+)` and `from\s+(\w+)` anywhere, so `from math import gcd` was refused as a module named `gcd`, and "from" in a comment refused the next word. It also checked only the first name in `import a, b`. | Upstream refuses 1,007 of 4,983 SFT code blocks (20.2%), for example `Fraction`, `comb`, `gcd`, `'1'` (from "from 1 to 10" in a comment). Patched: 54 (1.1%), mostly scipy and cmath |
| **`PATCH(sandbox-modules)`** Allow `numpy` and `sympy`, which the image ships (numpy 2.3.5, sympy 1.14.0). Nothing else. | The SFT solutions use both: 360 refusals were sympy and 131 numpy. Both import and run under the sandbox's 4 GB address-space limit. | `sandbox_check.py` |

The sandbox is still a regex filter plus a subprocess with a memory limit, not isolation. These
changes don't loosen the filter: every pattern upstream refused is still refused, except false matches
of the import check.

### Trainer arguments

[`manifests/rayjob.yaml`](manifests/rayjob.yaml) uses upstream's `retool_qwen3_4b_rl.sh` arguments,
grouped as upstream groups them. A mechanical diff of the two flag sets shows only these differences:

- **Dropped:** `--colocate`, `--rollout-num-gpus-per-engine 2`, and `--sglang-mem-fraction-static 0.7`
  (the DGD runs the engines); wandb; AIME eval (`--eval-*`); `--save` and `--save-interval`;
  `--apply-chat-template` (see `PATCH(prompt)`).
- **Added:** the Slime tutorial's `--rollout-external-engine-addrs` (the four engine Services),
  `--update-weight-transport disk`, `--update-weight-disk-dir /data/weight-updates`, and
  `--custom-update-weight-post-write-path weight_permissions.make_weights_readable`;
  `--sglang-incremental-streaming-output`; `--rollout-stop '</code>' '</tool_call>'`.
- **Changed:** `--num-rollout 3000` → `10`; paths under `/data`.
- **Kept:** 32 prompts × 8 samples, the GRPO and optimizer arguments (CPU offload), TP 2 / CP 2,
  `--max-tokens-per-gpu 9216` (so a trajectory stops at 2 × 9,216 = 18,432 tokens), and
  `--rotary-base 5000000`. `MODEL_ARGS` come from the image's `scripts/models/qwen3-4B.sh`.

Upstream also sets `NCCL_NVLS_ENABLE` from an NVLink check; this RayJob leaves it at NCCL's default.

## Contents

| Path | What |
|---|---|
| `manifests/` | Storage, setup Job, DGD (`dgd.yaml` KV-aware, `dgd-rr.yaml` round-robin), engine Services, functions ConfigMap (generated), RayJob |
| `functions/` | The patched ReTool files and the weight-permissions hook |
| `upstream/image/`, `upstream/main/` | Read-only copies of the image's ReTool and the Slime files it uses, and of `main`'s ReTool |
| `tools/run_retool.sh` | `deploy [label]` (ConfigMap, Services, the DGD in `$DGD`; checks that all 4 engines registered and advertise `kv-events`), `rayjob <label> <steps>` (runs the RayJob and saves everything), `teardown` |
| `tools/analyze_retool.py` | Per-step table, cache reuse, routing, stickiness; writes `summary.json` and `turns.joined.jsonl`. Joins each turn to its engine from the frontend's `Selected worker` line (KV) or `route request completed` line (round-robin) |
| `tools/compare_runs.py` | KV-aware against round-robin: run totals, latency by request type, each step side by side, and each step's last trajectory |
| `tools/compare_chart.py` | Renders `compare_runs.py`'s JSON as one HTML page of tiles and per-step charts |
| `tools/trajectory.py` | Prints one trajectory turn by turn (`--sample N`) or lists candidates (`--pick`) |
| `tools/smoke_direct.py` | The direct smoke test: runs the patched `generate()` against the frontend from a Pod |
| `tools/sandbox_check.py` | Upstream vs patched sandbox on the ReTool-SFT code blocks |
| `tools/pin.py`, `tools/player_page.py`, `tools/redact_cast.py` | Node pinning, a page that plays a cast, cast redaction (copied from the earlier kits) |
| `demo/` | The demo: `slime-retool-demo.template.sh`, `fill_demo.py` (writes `slime-retool-demo.sh` from the runs), the generated `slime-retool-demo.sh`, `record-slime-retool-demo.sh`, the fern-code theme, and the recording `slime-retool-demo.public.cast` |
| `demo/team/` | The team demo: the words (`beats/`), the data (`fill/`, read from the runs), the helpers (`lib.sh`), `build.sh`, `run.sh`, `check.py`, `record.sh`, read-only cluster snapshots (`snapshots/`), the generated `slime-retool-team-demo.sh`, and the recording `slime-retool-team-demo.public.cast`. [`FRAMEWORK.md`](demo/team/FRAMEWORK.md) documents the helpers and timing knobs |

Not in this repo: `runs/` (296 MB: each deploy's and run's logs, Pod snapshots, metrics scrapes, turn logs,
`summary.json`, and the KV-against-round-robin comparison), the rendered GIF, MP4, HTML pages, and PNG frames,
and the unredacted casts. [Rerun](#rerun) recreates `runs/`.

## Rerun

`h100` is the kubectl context of the cluster these ran on (the name `az aks get-credentials` gives it). For
another cluster, change it below and set `CTX` for `tools/run_retool.sh`.

```bash
cd demo/slime-retool                             # from the repo root
K=$PWD
PY=python3                                       # any Python 3.10+ with PyYAML
export NODE=aks-ndh100pool-82423760-vmss000003   # one node with 8 free GPUs; every Pod is pinned to it
python3 -I ../nemo-rl/tools/gpu_free.py --need 8 --image slimerl/slime:nightly-dev-20261027a-cu130

kubectl --context h100 apply -f manifests/storage.yaml
(cd /tmp && $PY -I $K/tools/pin.py $K/manifests/setup-job.yaml $NODE) | kubectl --context h100 apply -f -
kubectl --context h100 -n slime-retool wait --for=condition=complete job/slime-retool-setup --timeout=30m

kubectl --context h100 apply -f manifests/inspect-pod.yaml   # reads /data for the turn logs; edit its node pin
mkdir -p runs                                                # console output goes to runs/<label>.console.log
bash tools/run_retool.sh deploy deploy 2>&1 | tee runs/deploy.console.log      # the KV-aware DGD -> runs/deploy/
bash tools/run_retool.sh rayjob run1 10 2>&1 | tee runs/run1.console.log       # about 30 min -> runs/run1/
(cd /tmp && $PY -I $K/tools/analyze_retool.py $K/runs/run1 $K/runs/deploy)
bash tools/run_retool.sh teardown
```

The DGD holds 4 GPUs and the RayJob 4 more, all on `$NODE`.

The round-robin baseline is the same sequence with the other DGD, a different label for each step's
output, and a comparison at the end:

```bash
DGD=manifests/dgd-rr.yaml bash tools/run_retool.sh deploy deploy-rr 2>&1 | tee runs/deploy-rr.console.log
bash tools/run_retool.sh rayjob rr1 10 2>&1 | tee runs/rr1.console.log          # -> runs/rr1/
(cd /tmp && $PY -I $K/tools/analyze_retool.py $K/runs/rr1 $K/runs/deploy-rr)
bash tools/run_retool.sh teardown
(cd /tmp && $PY -I $K/tools/compare_runs.py $K/runs/run1 $K/runs/rr1 --names KV RR --out $K/runs/compare-kv-rr.json)
(cd /tmp && $PY -I $K/tools/compare_chart.py $K/runs/compare-kv-rr.json $K/runs/compare-kv-rr.html \
  '{"labels": {"KV": "KV-aware", "RR": "Round-robin"}}')
```

`deploy` refuses a label that already holds a deploy (it would overwrite `workers.json`, which the
analysis needs), and `rayjob` refuses a label that already holds a run. These labels are the recorded runs'
(`deploy`, `run1`, `deploy-rr`, `rr1`), which the replays read; use others to keep a second run apart.

## Re-record the demos

Both demos are replays: their scripts print the saved run's output, so recording them needs no cluster and no
logs. You need [asciinema](https://docs.asciinema.org/getting-started/) 3.x and `python3`. Recording runs in
real time, so do it on an idle machine.

**Demo** (100×25):

```bash
cd demo
./record-slime-retool-demo.sh                # records slime-retool-demo.sh -> slime-retool-demo.cast, themed
python3 -I ../tools/redact_cast.py slime-retool-demo.cast slime-retool-demo.public.cast
```

To change the narration, edit `slime-retool-demo.template.sh` (not `slime-retool-demo.sh`); timing knobs are at
its top. Then `python3 -I fill_demo.py` regenerates `slime-retool-demo.sh` from sample 958 of the KV run
(`--sample N` for another; `../tools/trajectory.py --pick` lists candidates). `fill_demo.py` needs PyYAML and the
saved run, which isn't in this repo: `runs/setup/`, `runs/deploy/`, `runs/deploy.console.log`, and `runs/run1/`
(its RayJob, Pod watch, `summary.json`, and `turns.joined.jsonl`).

**Team demo** (120×32, about 12.5 minutes):

```bash
cd demo/team
./record.sh --no-build        # records the committed slime-retool-team-demo.sh -> slime-retool-team-demo.cast
python3 -I ../../tools/redact_cast.py slime-retool-team-demo.cast slime-retool-team-demo.public.cast
```

`bash slime-retool-team-demo.sh` plays the replay in a 120×32 terminal without recording it (`FAST=1` skips the
pauses), and `python3 -I check.py --no-build` dry-runs it: each chapter's length, lines over 120 columns, lint.
Commit only the `.public.cast`.

To change the words, edit `beats/NN_*.sh`; to change the data, `fill/NN_*.py`. Then `./build.sh` regenerates
`slime-retool-team-demo.sh`, and `./record.sh` without `--no-build` builds before it records. The build reruns
every fill script, and the fill scripts read the recorded run's saved files, which aren't in this repo:
`runs/setup/`, `runs/deploy/`, `runs/deploy.console.log`, `runs/run1/` (147 MB: the driver and engine stream
logs, the turn logs, Pod snapshots, RayJob, and `summary.json`), and `runs/compare-kv-rr.json`. They also read
`manifests/`, `functions/`, `upstream/`, `tools/`, and `REPORT.md`, which are here.

To edit either narration, get the recorded `runs/` folder (296 MB with its logs and turn logs) from Jonathan. A
new run won't drop in as is: `runs/setup/` was captured by hand during setup, and the fill scripts assert the
recorded run's numbers.

## Cleanup

```bash
bash tools/run_retool.sh teardown                       # RayJob and DGD (already done after the run)
kubectl --context h100 -n slime-retool delete pod slime-inspect
kubectl --context h100 delete namespace slime-retool
kubectl --context h100 delete pv pv-lustre-slime-retool # Retain: files stay on Lustre under slime-retool/
```
