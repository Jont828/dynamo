# Slime ReTool on Dynamo: KV-aware routing vs round-robin (h100, Dynamo 1.6.1)

The run outputs this report cites (`runs/…`: logs, metrics scrapes, summaries, and the comparison pages) weren't copied into this repo; the kit's README says how to recreate them.

| | |
|---|---|
| **Date** | KV-aware run: 2026-11-18, 22:13–23:11 UTC. Round-robin baseline: 2026-11-19, 00:36–01:11 UTC |
| **Cluster** | AKS `h100`, namespace `slime-retool`, every Pod on `aks-ndh100pool-82423760-vmss000003` (all three images cached). 8 GPUs: 4 for the engines, 4 for the trainer. |
| **Images** | `slimerl/slime:nightly-dev-20261027a-cu130` (Slime 0.4.0, Ray 2.59.0), `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.1` (SGLang 0.5.21), `nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.6.1`. Platform 1.6.1, KubeRay 1.7.1. |
| **Workload** | Slime's ReTool example, patched as the [README](README.md#changes-from-upstream-retool-and-why) lists: Qwen3-4B (`font-info/qwen3-4b-sft-SGLang-RL`), GRPO on DAPO-Math-17k, 10 steps of 256 trajectories (32 problems × 8 samples), up to 16 requests per trajectory, 18,432 tokens at most, disk weight sync. |
| **Routing** | `run1`: `DYN_ROUTER_MODE=kv`, `DYN_ROUTER_PREDICTED_TTL_SECS=5`; router defaults otherwise (overlap credit 1, prefill load scale 1, temperature 0). `rr1`: `DYN_ROUTER_MODE=round-robin` ([`manifests/dgd-rr.yaml`](manifests/dgd-rr.yaml)). Both on the same engines: `--page-size 16` and ZMQ KV events. |
| **Runs** | Two RayJobs, identical apart from the router: `run1` (KV-aware) and `rr1` (round-robin). No repeats. A direct smoke test came first. |

## 1. The short version

- **KV-aware routing didn't make this workload measurably faster.** The round-robin baseline ran the same
  RayJob on the same node and engines, with the same problems in every step. Trajectories finished at the
  same pace: half of each step's 256 trajectories were done after a mean of 18.7 s with KV routing and
  18.8 s with round-robin, and the times for 25% to 95% done agree to within 4.1%, in both directions. The
  summed rollout time was 6% lower with KV routing (985 s against 1,047 s), but that whole difference sits
  in each step's last trajectory. Its time differed between the runs by −35 s to +42 s per step, for
  reasons unrelated to routing: a single 18,000-token response, or a run of 20 s sandbox timeouts
  (section 5).
- **On the cache side, it did what it promises.** The engines served 79.0% of prompt tokens from their
  prefix cache with KV routing and 59.7% with round-robin, so they computed 5.66M prompt tokens instead of
  10.45M. 61% of follow-up turns went back to the engine that held their history, against 24% with
  round-robin (chance alone gives 1 in 4).
- **Why the cache savings don't show up as speed:** prefill is a small part of this workload. A step
  waits for its slowest trajectory, and in the KV run those spent 938 s generating output and 13 s
  waiting for their first tokens, over the 10 steps. Recomputing a history is cheap: a follow-up's median
  time to first token was 140 ms with KV routing and 141 ms with round-robin. The extra prefill does slow
  generation a little: with round-robin, follow-up requests took a median 1.55 s against 1.50 s, and
  where the engines' batches were comparable, they generated 3–4% slower. That is smaller than the
  run-to-run swing of the slowest trajectory.
- **ReTool runs through Dynamo 1.6.1 with either router.** Each run took about 30 min for 10 GRPO steps
  (29 min 14 s with KV routing, 30 min 7 s with round-robin): 2,560 trajectories and about 12,400
  requests, 4.8 per trajectory. Every request was matched to the engine that served it. Half the answers
  were correct, and the trainer's logprobs differed from the engines' by about 0.01 per step, as in the
  Slime tutorial. It took patches to ReTool, most of them bugs that hit any user of the example (see the
  [README](README.md#changes-from-upstream-retool-and-why)).
- **Even with KV routing, 39% of follow-ups moved to another engine.** The router knew where each history
  was cached, but 1.6.1's default cost also weighs engine load. The four engines' loads typically
  differed by more (median 145 blocks of 16 tokens) than a turn's cached history was worth (median 114
  blocks; section 6). Keeping more turns on their engine would save more prefill. Given the baseline, it
  probably wouldn't save time on this workload.
- **ReTool as shipped has bugs that would have changed this run.** Dynamo 1.6.x rejects its non-streaming
  requests; its prompt template is doubled; it sets no stop string at the tool call; its import check
  refuses 20% of the SFT data's code; every code run blocks the rollout loop; and code in a fenced `<code>`
  block, the SFT data's format, is a SyntaxError (this model mostly used `<tool_call>` JSON instead).

## 2. What ran

| UTC | Event |
|---|---|
| 22:13 | Namespace, static Lustre PV and PVC ([`manifests/storage.yaml`](manifests/storage.yaml)); inspection Pod for reading the image and `/data` |
| 22:27:32–22:29:21 | Setup Job (109 s): downloads 42 s (8.3 GB checkpoint, 10 MB dataset), `torch_dist` conversion 52 s (7.6 GB), directories |
| 22:35:29–22:37:00 | DGD applied → Ready (91 s). All 4 engines registered a model card and a `kv-events` channel; each sidecar found its engine's ZMQ KV-event source with `block_size=16` |
| 22:37:30 | Direct smoke test (section 3) |
| 22:40:24 | RayJob created (`runs/run1/rayjob.applied.yaml`) |
| 22:41:16 | Slime detected the 4 external engines |
| 22:42:45 | Initial weight sync done (12.8 s); rollout 0 starts |
| 22:44:12–23:09:09 | 10 steps (section 4) |
| 23:09:38 | RayJob `SUCCEEDED` (Ray job `slime-retool-8wmvp`: 22:40:43–23:09:31) |
| 23:10:40, 23:11:06 | Logs saved; RayJob, then DGD deleted |
| **2026-11-19** | **Round-robin baseline** |
| 00:36 | Inspection Pod recreated (the run script copies the turn logs through it) |
| 00:37:06–00:38:37 | Round-robin DGD ([`manifests/dgd-rr.yaml`](manifests/dgd-rr.yaml)) applied → Ready (91 s). All 4 engines registered a model card and a `kv-events` channel, as before. 8 test requests through the frontend went to worker-0, 1, 2, 3, 0, 1, 2, 3, each logged with `router_mode=RoundRobin` |
| 00:39:35 | RayJob created (`runs/rr1/rayjob.applied.yaml`: run1's, with the turn logs in `/data/turn-logs/rr1`) |
| 00:40:26 | Slime detected the 4 external engines |
| 00:41:56 | Initial weight sync done (13.1 s); rollout 0 starts |
| 00:43:30–01:09:18 | 10 steps (section 5) |
| 01:09:42 | RayJob `SUCCEEDED` (Ray job `slime-retool-t82fs`: 00:39:53–01:09:38) |
| 01:10:38, 01:11:02 | Logs saved; RayJob, then DGD deleted; inspection Pod deleted |

Each engine reloaded the weights 11 times per run: once before the first rollout and once after each
training step. No engine retracted a request for lack of KV memory (peak usage of each engine's
418,176-token pool: 19–22% with KV routing, 21–24% with round-robin), no turn needed a retry, and no engine
or frontend logged an error or warning.

## 3. Smoke test (go/no-go)

Before the RayJob, [`tools/smoke_direct.py`](tools/smoke_direct.py) ran the patched `generate()` from a Pod
against the frontend: 2 problems × 4 samples, on the SFT weights
(`runs/smoke-direct/`).

| Check | Result |
|---|---|
| First rendered prompt | 384 tokens; role tags `system` (with `# Tools`), `user`, `assistant`; not doubled. Upstream's would be 389 tokens with 5 role tags, starting `<|im_start|>user<|im_start|>user` |
| Turns happen | 37 requests for 8 trajectories (4.6 each). 28 stopped at `</tool_call>` and 9 at end-of-text. Sandbox results: 19 ok, 6 errors, 1 timeout, 1 refused |
| Later turns hit the cache | On turns 2+, cached tokens were a median 100% of the previous request (e.g. 3,280 of 3,293) |
| Logprobs | 29,131 for 29,131 output tokens; no misaligned sample |

So: go. Three of the 8 trajectories moved to another engine at turn 2, each landing where only the shared
prompt was cached. Each followed a sandbox run of about 0.03 s. That pointed at KV-event timing; the full
run showed load matters more (section 6).

## 4. Per step (KV-aware run)

"Follow-ups on the same engine": requests after a trajectory's first that went to the engine that served
its previous request. Reward: +1 right; wrong is −1 + 0.05 × (tool calls − 2), at most −0.6. Times are Slime's:
rollout, then training (reference logprobs and the actor update), then the weight sync after the step.

| Step | Rollout | Train | Weight sync | Step | Requests per trajectory (max) | Prefix-cache hit rate | Cached / computed prompt tokens | Follow-ups on the same engine | Correct answers | Mean reward | Response tokens, mean / max | Requests per engine (0 / 1 / 2 / 3) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 77 s | 48 s | 10.6 s | 148 s | 4.63 (16) | 77.1% | 1.86M / 0.55M | 60% | 45% | -0.05 | 2,348 / 11,400 | 314 / 266 / 284 / 321 |
| 1 | 116 s | 37 s | 9.6 s | 174 s | 4.85 (16) | 78.0% | 1.87M / 0.53M | 62% | 53% | +0.11 | 2,540 / 18,109 | 313 / 330 / 324 / 274 |
| 2 | 114 s | 38 s | 9.7 s | 176 s | 5.35 (16) | 81.8% | 2.72M / 0.60M | 63% | 45% | -0.04 | 2,692 / 17,705 | 348 / 357 / 348 / 317 |
| 3 | 92 s | 38 s | 9.8 s | 152 s | 4.81 (16) | 78.8% | 2.17M / 0.58M | 62% | 50% | +0.04 | 2,681 / 12,128 | 310 / 292 / 278 / 351 |
| 4 | 98 s | 37 s | 9.2 s | 156 s | 4.33 (16) | 79.3% | 1.98M / 0.52M | 64% | 48% | -0.02 | 2,653 / 15,471 | 292 / 290 / 243 / 283 |
| 5 | 95 s | 39 s | 9.3 s | 154 s | 5.30 (16) | 79.5% | 2.44M / 0.63M | 62% | 43% | -0.07 | 2,748 / 14,056 | 359 / 340 / 328 / 330 |
| 6 | 107 s | 35 s | 9.2 s | 164 s | 4.40 (16) | 75.6% | 1.69M / 0.55M | 59% | 58% | +0.19 | 2,441 / 18,090 | 278 / 301 / 288 / 260 |
| 7 | 66 s | 33 s | 9.4 s | 119 s | 4.60 (16) | 77.4% | 1.81M / 0.53M | 58% | 54% | +0.12 | 2,210 / 10,095 | 303 / 329 / 313 / 233 |
| 8 | 113 s | 37 s | 9.3 s | 174 s | 4.91 (16) | 79.7% | 2.21M / 0.56M | 60% | 55% | +0.14 | 2,551 / 18,071 | 372 / 306 / 285 / 295 |
| 9 | 107 s | 39 s | 9.3 s | 169 s | 5.26 (16) | 80.9% | 2.58M / 0.61M | 60% | 50% | +0.05 | 2,757 / 16,694 | 327 / 363 / 356 / 301 |
| **Mean** | 99 s | 38 s | 9.5 s | 159 s | 4.84 | 79.0% (all) | 21.3M / 5.7M (sum) | 61% (all) | 50% | +0.05 | 2,562 | 3216 / 3174 / 3047 / 2965 (sum) |

Rollout time tracks the longest trajectory, which the step waits for: the two fastest rollouts (66 and 77 s)
had the shortest longest trajectories (10,095 and 11,400 tokens), and the steps whose longest ran past 17,000
tokens took 107–116 s. Training took 33–48 s and each weight sync 9.2–10.6 s.

## 5. Round-robin baseline: is KV routing faster?

Run `rr1` repeats `run1` with one change: the frontend routes round-robin
([`manifests/dgd-rr.yaml`](manifests/dgd-rr.yaml)). It used the same node, the same four engines (still with
page size 16, still publishing KV events), and the same RayJob. It also saw the same problems in every step:
all 320 prompt groups match between the runs, because `--rollout-shuffle` shuffles with a fixed seed
(`--rollout-seed`, default 42). Sampling isn't seeded, so each run drew its own responses and the
trajectories differ. [`tools/compare_runs.py`](tools/compare_runs.py)
produced every table here (`runs/compare-kv-rr.md` and `.json`), and
`runs/compare-kv-rr.html` charts them.

**Run totals.**

| | KV-aware (`run1`) | Round-robin (`rr1`) | Round-robin vs KV |
|---|---|---|---|
| RayJob, created to finished | 29 min 14 s | 30 min 7 s | +53 s (+3.0%) |
| Rollout, sum of 10 steps | 985 s | 1,047 s | +62 s (+6.2%) |
| Training, sum | 383 s | 369 s | −13 s |
| Weight syncs, sum of 11 | 108.2 s | 119.6 s | +11.4 s |
| Requests (per trajectory) | 12,402 (4.84) | 12,489 (4.88) | +0.7% |
| Output tokens | 6.56M | 6.37M | −2.9% |
| Prefix-cache hit rate | 79.0% | 59.7% | −19.3 points |
| Prompt tokens prefilled | 5.66M | 10.45M | ×1.85 |
| Follow-ups on the previous request's engine | 61% | 24% | |
| Correct answers | 1,286 (50.2%) | 1,273 (49.7%) | |

Weight syncs run with no requests in flight, so their extra second per sync isn't routing. Training time
depends on the responses, not the router. The engines' own prefill logs agree with the turn logs on the
prefilled totals in both runs.

**Did trajectories finish sooner?** For each step: the time from its first request until a given share of
its 256 trajectories had finished, summed over the 10 steps.

| Share finished | KV-aware | Round-robin | Round-robin vs KV | Steps where round-robin was slower |
|---|---|---|---|---|
| 25% | 130.7 s | 126.3 s | −3.4% | 4 of 10 |
| 50% | 186.9 s | 187.7 s | +0.4% | 5 of 10 |
| 75% | 293.0 s | 294.9 s | +0.7% | 4 of 10 |
| 90% | 479.9 s | 460.1 s | −4.1% | 4 of 10 |
| 95% | 592.4 s | 589.0 s | −0.6% | 5 of 10 |
| 100%: the rollout | 982.2 s | 1,044.2 s | +6.3% | 6 of 10 |

Up to 95%, the two routers finish trajectories at the same pace, and neither wins consistently. Only the
last trajectory of each step differs. That difference averages +6 s per step but ranges from −35 s to +42 s,
so it can't be told apart from zero. (The 100% row comes from the turn logs, a few seconds short of Slime's
rollout time.)

**Per step.** "Half finished" and "90% finished" are times from the step's first request.

| Step | Rollout, KV / RR | Longest response (tokens), KV / RR | Half finished, KV / RR | 90% finished, KV / RR | Prefilled, KV / RR | Hit rate, KV / RR |
|---|---|---|---|---|---|---|
| 0 | 77 / 85 s | 11,400 / 10,838 | 18.6 / 19.5 s | 42.8 / 45.6 s | 0.55M / 1.04M | 77.1% / 57.6% |
| 1 | 116 / 118 s | 18,109 / 17,940 | 19.5 / 17.8 s | 45.3 / 38.4 s | 0.53M / 0.99M | 78.0% / 61.4% |
| 2 | 114 / 105 s | 17,705 / 14,584 | 20.6 / 21.4 s | 47.4 / 53.2 s | 0.60M / 1.23M | 81.8% / 60.3% |
| 3 | 92 / 134 s | 12,128 / 18,076 | 18.2 / 19.2 s | 52.5 / 51.3 s | 0.58M / 1.15M | 78.8% / 59.1% |
| 4 | 98 / 130 s | 15,471 / 18,060 | 16.9 / 18.7 s | 50.0 / 50.6 s | 0.52M / 1.00M | 79.3% / 57.4% |
| 5 | 95 / 81 s | 14,056 / 11,294 | 18.0 / 19.1 s | 58.0 / 48.9 s | 0.63M / 1.05M | 79.5% / 59.2% |
| 6 | 107 / 107 s | 18,090 / 17,647 | 19.7 / 18.9 s | 41.0 / 40.9 s | 0.55M / 0.98M | 75.6% / 59.8% |
| 7 | 66 / 73 s | 10,095 / 10,497 | 16.3 / 16.3 s | 40.0 / 42.6 s | 0.53M / 0.94M | 77.4% / 60.8% |
| 8 | 113 / 142 s | 18,071 / 17,901 | 18.6 / 17.8 s | 44.6 / 42.6 s | 0.56M / 1.01M | 79.7% / 61.6% |
| 9 | 107 / 72 s | 16,694 / 10,832 | 20.5 / 18.9 s | 58.2 / 46.0 s | 0.61M / 1.05M | 80.9% / 59.9% |

What decided the large per-step differences, from each step's last trajectory:
- Step 3 (+42 s with round-robin): one 18,076-token response, 133 s of generation. The KV run's longest
  response that step was 12,128 tokens.
- Step 4 (+32 s): 18,006 generated tokens over 3 requests, against 15,471 for the KV run's last trajectory.
- Step 8 (+29 s): the last trajectory used all 16 requests and spent 80 s in four 20 s sandbox timeouts.
  Step 0's (+7 s) spent 60 s in three.
- Step 9 (−35 s): the KV run's last trajectory generated 16,694 tokens; round-robin's longest response
  that step was 10,832.

**Per request.**

| Requests | KV: count | KV: time to first token, median / 90th percentile | KV: prefilled, median | RR: count | RR: time to first token, median / 90th percentile | RR: prefilled, median |
|---|---|---|---|---|---|---|
| First of a trajectory | 2,560 | 865 / 1,665 ms | 115 | 2,560 | 850 / 1,450 ms | 116 |
| Follow-up, same engine | 6,016 | 118 / 921 ms | 37 | 2,428 | 126 / 950 ms | 35 |
| Follow-up, other engine | 3,826 | 157 / 912 ms | 840 | 7,501 | 143 / 976 ms | 829 |
| All follow-ups | 9,842 | 140 / 917 ms | 87 | 9,929 | 141 / 968 ms | 537 |

A follow-up that lands on another engine recomputes its history, a median of about 830 tokens. In each
run, that made its median time to first token 17–39 ms longer than for a follow-up that stayed. A whole
follow-up request took a median 1.50 s with KV routing and 1.55 s with round-robin, mostly generating its
output (median 131 tokens).

**The engines' generation rate.** SGLang logs every 40th decode step with the number of running requests
and the generation rate. The rate covers any prefill the engine ran between decode steps, so extra prefill
shows up as a lower rate. Grouped by running requests:

| Running requests | KV: log intervals | KV: tokens/s per request | KV: context per request | RR: log intervals | RR: tokens/s per request | RR: context per request | RR vs KV |
|---|---|---|---|---|---|---|---|
| 1–8 | 7,024 | 187.1 | 8,733 | 6,687 | 182.8 | 8,240 | −2.3% |
| 9–16 | 1,769 | 135.1 | 4,275 | 1,520 | 134.0 | 4,077 | −0.8% |
| 17–32 | 1,412 | 119.9 | 2,553 | 1,364 | 116.2 | 2,538 | −3.1% |
| 33–48 | 1,067 | 119.3 | 1,300 | 1,027 | 114.1 | 1,309 | −4.4% |
| 49–64 | 615 | 141.5 | 578 | 754 | 150.0 | 518 | +6.1% |
| 65–96 | 108 | 156.2 | 239 | 16 | 161.7 | 188 | +3.5% |

Where batch size and context per request match (17–48 running requests), round-robin engines generated
3–4% slower. At 1–8 running requests, the tail of each rollout where the slowest trajectory finishes,
they were 2% slower despite 6% shorter contexts. With 49 or more running requests, early in each rollout,
round-robin's contexts were shorter and it was faster.

**So why no speedup?**
1. The rollout waits for the slowest trajectory, and that trajectory spends almost all its time
   generating. Over the 10 steps, the last trajectories generated for 938 s and waited 13 s for first
   tokens in the KV run (873 s and 18 s with round-robin). Prefill savings can only shorten the second
   number. The first depends on how long the longest response runs, which changes from run to run.
2. Recomputing a history costs tens of milliseconds per request, against 1.5 s for a typical follow-up.
3. Round-robin's extra 4.8M prefilled tokens did slow generation by a few percent (the table above). That
   shows in per-request times (1.55 s against 1.50 s at the median) but is lost in the swing of trajectory
   and rollout times.

**What it would take to see an effect of this size** (an estimate from these runs' spread): the
half-finished time varies by about ±1.2 s per step between the runs, so a 3% difference (0.6 s) would need
about 20 steps per router to show clearly: two more runs of each. Total rollout time, with its ±23 s per
step, would need hundreds of steps.

**Where KV routing should matter more** (an expectation, not tested): wherever prefill is a bigger share of
the engines' time, such as long histories with short replies per turn (for example, agent loops that append
large tool outputs), bigger models, or prefill-bound engines. KV routing also has a cost this comparison
doesn't show. It needs `--page-size 16`, which the first experiment measured at about 9% slower decoding
than SGLang's default page size 1 on the tutorial's Qwen3-0.6B (not measured for this model). Both runs
here used page size 16.

**The single-turn tutorial, measured the same way**
([`../slime-kv-routing/REPORT.md`](../slime-kv-routing/REPORT.md#5-kv-vs-round-robin-measured-as-in-the-retool-comparison)):
on GSM8K, KV routing saved no prompt tokens at all (41,424 against 41,605 per run, ×1.00). Each sample
there is one short prompt, and a question's 8 samples arrive together, before any of them is cached. The
savings here come from follow-up turns, which GSM8K doesn't have.

## 6. Routing and cache reuse (KV-aware run)

**Where the prefilled tokens went.**

| Requests | Count | Prompt tokens | Prefilled (not cached) | Median prefilled per request |
|---|---|---|---|---|
| First request of a trajectory | 2,560 | 0.87M | 0.40M (46%) | – |
| Follow-up, same engine | 6,016 (61%) | 17.40M | 0.32M (1.9%) | 37 |
| Follow-up, moved | 3,826 (39%) | 8.71M | 4.94M (57%) | 840 |
| All | 12,402 | 26.98M | 5.66M (21%) | |

A first request shares 224 tokens (the system prompt with the tool spec, and DAPO's instruction) with every
other problem, and its whole prompt with its 7 siblings. 2,080 of the 2,560 first requests found exactly those
224 tokens cached, 467 found nothing (the engines flush their caches at every weight sync), and only 5 found a
sibling's whole prompt: all 256 of a step's first requests arrive at once, so siblings prefill side by side
before any copy is cached, as in the first experiment.

**Moves by turn.** 903 of 2,504 second requests moved (36%), 749 of 1,905 third requests (39%), 566 of
1,406 fourth (40%), and about the same at every later turn. Within a step, the share that stayed was 58–64%.

**Why turns move: what the router saw.** Each request's `route request completed` line in the frontend log
carries `overlap_blocks`, the cache overlap the router credited on the engine it chose, and its `Selected
worker` line carries that engine's cost (`logit`, in 16-token blocks). For 1.6.1's default policy
(`lib/kv-router/src/scheduling/selector/reference.rs`), the cost of an engine is

    max(0, pending prefill + this prompt − credited overlap) + in-flight decode blocks

and the lowest cost wins (temperature 0, overlap credit 1, prefill load scale 1).

- **The router knew the history.** For follow-ups that stayed, it credited a median of 124 blocks against
  128 actually cached and 128 in the previous request. Its credit always covered the previous request's
  prompt, and in 80% of cases its output too. When it didn't include the output, the turn had been sent a
  median 0.042 s after the previous one ended, against 0.150 s otherwise. SGLang 0.5.21 publishes KV events
  after each prefill batch and every 40th decode step (`--decode-log-interval`), so a turn's output can reach
  the router about a second after the turn ends.
- **Moved turns gave up their history for lower load.** On the engine they moved to, the router credited a
  median of 23 blocks, the shared prompt, against a history of 112.
- **Load differences were bigger than histories.** The chosen engine's cost was a median of 1,494 blocks,
  almost all in-flight decode load. Among decisions within ±1 s of each follow-up, the four engines' lowest
  costs spread a median of 145 blocks, while the median follow-up's history was 114 blocks. The spread
  exceeded the history in 61% of follow-ups. An engine more loaded than another by more than a turn's
  history loses the turn.
- **Events flowed.** Over the run the router applied 34,017 `stored`, 4,125 `removed`, and 88 `cleared` KV
  events, with 1,932 duplicate-store warnings. The 88 match the engines' cache flushes: each engine logged
  22, two per weight sync.

**Requests per engine:** 3,216 / 3,174 / 3,047 / 2,965 (round-robin: 3,123 / 3,122 / 3,122 / 3,122).

**What would keep more turns home (not tried):**
- A higher prefill load scale. By the cost formula, a turn stays when its engine's extra decode load is
  smaller than `prefill_load_scale` × (the prefill it would redo elsewhere), roughly its history minus the
  shared prompt: about 90 blocks at the median, against a median load spread of 145. At scale 2, that margin
  doubles (an estimate from the formula, not measured). In 1.6.1 it's `prefill_load_scale` in a
  `DYN_ROUTER_POLICY_CONFIG` file; the `DYN_ROUTER_PREFILL_LOAD_SCALE` env var still works but is deprecated.
- Raising the overlap credit instead would do little: a turn that stays already pays almost no prefill at
  credit 1 (124 of about 128 blocks credited), and the prefill term is clamped at zero, so more credit mainly
  makes other engines look cheaper for the shared prompt. In the first experiment, credit 16 made the router
  ignore cache entirely for the same reason.
- If all follow-ups had stayed and prefilled like the ones that did (1.9%), the run would have prefilled
  about 0.9M tokens instead of 5.7M (an estimate). The round-robin baseline suggests that would save
  little time: nearly doubling the prefill, to 10.45M tokens, didn't measurably slow the rollouts
  (section 5).

## 7. Turns, the sandbox, and rewards

Figures for the KV-aware run; the last bullet gives the round-robin run's.

- **Requests per trajectory:** 1: 56, 2: 599, 3: 499, 4: 381, 5: 286, 6: 191, 7: 151, 8: 103, 9: 57,
  10: 45, 11: 34, 12: 27, 13: 18, 14: 13, 15: 7, 16: 93 (the limit; 19 of these made 16 tool calls).
  7 trajectories were cut at the 18,432-token cap: 6 while generating, 1 by a sandbox output.
- **How requests ended:** `</tool_call>` 9,392, end-of-text 2,964, `</code>` 40, length 6. When it calls
  the tool, the checkpoint uses Qwen's `<tool_call>` JSON format, not `<code>`.
- **Sandbox:** 9,222 code runs: 7,071 ran cleanly (77%), 2,010 returned an error such as a NameError
  (22%), 122 were refused (1.3%), 19 timed out at 20 s. Another 702 requests had neither code nor an answer,
  and got ReTool's "invalid action" reminder. Runs took a median 0.11 s (90th percentile 0.47 s); the
  longest wait for a sandbox slot was 1.05 s.
- **Answers:** 1,286 of 2,560 correct (50%). Mean reward −0.07 to +0.19 per step. Ten steps don't show a
  trend; the point of the run is the serving pattern.
- **The round-robin run behaved the same way:** 103 trajectories reached the 16-request limit and 8 were
  cut at the token cap; requests ended at `</tool_call>` 9,465 times, end-of-text 3,003, `</code>` 14,
  length 7; 9,315 code runs (7,257 clean, 1,939 errors, 94 refused, 25 timeouts at 20 s), median 0.11 s;
  the longest wait for a sandbox slot was 0.74 s; 1,273 of 2,560 answers correct (50%).

## 8. The trajectory in the demo

Sample 958, step 3: "How many positive integers have a square less than $10^7$?" (label 3162), picked with
`tools/trajectory.py runs/run1 --pick` for a one-line question, short code, a correct answer, and all turns
on one engine.

| Request | Engine | Prompt | Cached | Router credit | Output | Code → sandbox output |
|---|---|---|---|---|---|---|
| 1 | worker-0 | 264 | 224 (85%) | 16 blocks | 417 | `math.sqrt(10**7)` → 3162.2776601683795 |
| 2 | worker-0 | 719 | 672 (93%) | 42 blocks | 184 | 3162² → 9998244 |
| 3 | worker-0 | 929 | 896 (96%) | 56 blocks | 51 | 3163² → 10004569 |
| 4 | worker-0 | 1,007 | 976 (97%) | 61 blocks | 543 | `math.floor(math.sqrt(10**7))` → 3162 |
| 5 | worker-0 | 1,568 | 1,536 (98%) | 62 blocks | 129 | `Answer: \boxed{3162}`, reward +1 |

Turns 2–5 prefilled 31–47 new tokens each: the sandbox output (18–38 tokens) plus the unfilled end of a
16-token block. On request 5, the router credited 62 blocks while the engine held 96: turn 4's 543 output
tokens hadn't been reported yet, and the turn stayed anyway. This trajectory is typical of the 61% that
stayed, not of the run as a whole.

## 9. The recording

[`demo/slime-retool-demo.public.cast`](demo/slime-retool-demo.public.cast): 100×25, 2 min 51 s, the dark fern-code
palette embedded, like the earlier Slime demos. It passed through `tools/redact_cast.py`, which found nothing to
mask. The MP4, GIF (982×582) and `.standalone.html` player page made from it weren't copied into this repo.
`slime-retool-demo.sh` replays the run with the waits
fast-forwarded; `fill_demo.py` wrote it from the saved run, so every number and string on screen comes from
`runs/`. Beats:

1. Intro: what ReTool is and what KV-aware routing does for it.
2. Stage the model and data: the storage and setup Job, 109 s fast-forwarded, and the Job's last log lines.
3. The DGD: real `grep` output of the four KV settings, the apply, the Pods' recorded status changes
   (SchedulingGated, ContainerCreating, Running, Ready at 90 s), and each sidecar's KV-event line.
4. The ReTool functions: an excerpt of the patched turn loop (lines checked against the file).
5. The RayJob: the flags that point Slime at Dynamo and at ReTool, and the head and submitter Pods.
6. One trajectory, sample 958, turn by turn (section 8), from `tools/trajectory.py`'s real output.
7. The ten steps, on the driver log's timestamps: rollout time and requests per trajectory, hit rate and
   the share of follow-ups that stayed, training time, weight sync.
8. A closing card: trajectories and requests, tokens served from cache, follow-ups that stayed and moved,
   requests per engine, and a line saying this shows reuse, not a speedup.

The demo replays the KV-aware run only. The round-robin baseline (section 5) bears out its closing line:
reuse, but no measurable speedup. Not uploaded. To re-record, see the
[README](README.md#re-record-the-demo).

The 120×32 team demo in [`demo/team/`](demo/team/) (12 min 26 s, 11 chapters) covers the AKS stack,
each manifest, one trajectory, the ten steps, and the round-robin comparison as terminal bar charts.
Its redacted cast is uploaded, unlisted: https://asciinema.org/a/4SshnT6HOWZgvVgL (2026-11-19).

## 10. Caveats

- **One run per router, no repeats.** The baseline settles the speed question only down to the
  run-to-run noise. Between the runs, the half-finished time per step varied by about ±1.2 s and the
  rollout time by ±23 s, so an effect of a few percent can't be ruled out (section 5). Within the KV run,
  stayed and moved turns aren't a fair comparison: moved turns went to less loaded engines.
- **Same problems, different responses.** Each run sampled its own answers. The round-robin run generated
  2.9% fewer output tokens, and its slowest trajectories differed (section 5).
- **Page size held at 16.** Both runs used the page size KV routing needs, so the comparison isolates
  routing. It leaves out page size 16's own decode cost against SGLang's default (section 5).
- **Router internals from INFO logs.** The frontend logs the chosen engine's cost and credited overlap,
  not the alternatives'. The load-spread figure compares decisions within ±1 s, an approximation of each
  engine's cost at the moment of a decision.
- **Patched example.** The ReTool here differs from upstream as the README lists. The model saw a
  well-formed prompt and a working sandbox, which upstream's would not have given it, so turn counts and
  rewards aren't comparable with upstream reports such as Slime issue #1951.
- **Small sample of training:** 10 of upstream's 3,000 steps.

## 11. Left on the cluster

Namespace `slime-retool`: PVC `slime-data` (the checkpoint, its `torch_dist` copy, the dataset, and both
runs' turn logs under `/data/turn-logs/run1` and `/data/turn-logs/rr1`; the weight syncs leave nothing in
`/data/weight-updates`), the completed `slime-retool-setup` Job, ConfigMap `slime-retool-functions`, and the
four engine Services. PV `pv-lustre-slime-retool` (Retain, Lustre sub-dir `slime-retool/`). No DGD,
RayJob, inspection Pod, or GPU is held. Cleanup commands are in the [README](README.md#cleanup).

