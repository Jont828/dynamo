# Slime tutorial with KV-aware routing: h100, Dynamo 1.6.1

The run outputs this report cites (`runs/…`: logs, metrics scrapes, summaries, and the comparison pages) weren't copied into this repo; the kit's README says how to recreate them.

| | |
|---|---|
| **Date** | 2026-11-14, 03:13–04:15 UTC |
| **Cluster** | AKS `h100`, namespace `slime-kv`. Every GPU Pod pinned to `aks-ndh100pool-82423760-vmss000003`, which had all three images cached. 3 GPUs per run (2 engines, 1 Ray head). |
| **Tutorial** | Docs PR #18569 (`docs/slime-rl-integration` at `d75e893bb2`). The manifests were extracted from the pages and match `tests/deploy/slime/` in PR #18618. |
| **Images** | `dynamo-frontend:1.6.1`, `sglang-runtime:1.6.1` (SGLang 0.5.21, FA3 attention), `slimerl/slime:nightly-dev-20261027a-cu130`. Platform 1.6.1, KubeRay 1.7.1. |
| **Workload** | Qwen3-0.6B, GRPO on GSM8K, 10 steps of 64 samples (8 prompts × 8), disk weight sync. Every run trains on the same prompts in the same order (Slime's `rollout_seed` 42). |
| **"The first run"** | Run 7 from 2026-11-05, which the overview's demo replays (`../slime/`), and the e2e test's run 2 from 2026-11-12. Both use round-robin. |
| **Added 2026-11-19** | Section 5: KV against round-robin, measured the way the [ReTool comparison](../slime-retool/REPORT.md#5-round-robin-baseline-is-kv-routing-faster) was. Same runs; nothing was rerun. |

## 1. The short version

- **KV-aware routing works with the tutorial.** It takes two frontend env vars (`DYN_ROUTER_MODE=kv`,
  `DYN_ROUTER_PREDICTED_TTL_SECS=5`) and two engine flags (`--page-size 16`, `--kv-events-config`); the
  rest of the tutorial is unchanged. Both sidecars found their engine's ZMQ KV-event stream, every request
  in the 9 KV-routed runs was routed in KV mode, and all 15 runs passed the tutorial's checks (640 requests,
  11 weight reloads per engine, logprob gap 0.009–0.013).
- **It doesn't make this tutorial faster.** Three RayJobs per configuration, same node, same prompts:

  | | Round-robin (tutorial) | KV-aware (documented settings) |
  |---|---|---|
  | Prefix-cache hit rate | 28.7% | 28.5% |
  | Rollout time, steps 1–9 | 1.48 s | 1.50 s |
  | Training step, steps 1–9 | 5.96 s | 5.90 s |
  | Ray job, submit to finish | 137 s | 137 s |

- **It doesn't save prefill either, unlike ReTool.** Measured the way the ReTool comparison was
  (section 5), KV routing and round-robin on the same engines computed the same prompt tokens: 41,424
  against 41,605 per run (×1.00), where ReTool's multi-turn rollouts showed ×1.85. On warm engines,
  requests finished at the same pace under both routers.
- **Why routing can't help here:**
  1. **Siblings arrive together.** Slime submits all 64 requests of a rollout at once, and the frontend
     routed each rollout's 64 within 7–105 ms. Each engine prefills its share in about 5 batches, and
     SGLang doesn't share a prefix between requests in the same batch, so a sample only reuses a sibling's
     prefix if the sibling was prefilled in an earlier batch. Where a sibling lands matters much less than
     when it arrives.
  2. **The default router splits sibling groups anyway.** A sibling's cached prefix is worth about
     5 blocks. The first sibling makes its engine look busier than that, so the router sends later
     siblings to the other engine. At least 26 of 30 KV rollouts split a prompt's samples across engines.
  3. **Prefill is a small part of a rollout.** Median time to first token was 38–41 ms in every
     configuration, against 1.5 s rollouts whose length is set by the longest sample's decode (up to
     1,024 tokens). Perfect prefix reuse could save at most a few percent of rollout time, and rollout
     is about a quarter of each 5.9 s step.
- **The KV prerequisite costs decode speed.** With `--page-size 16`, SGLang 0.5.21 (FA3) decoded each
  sequence about 9% slower at every batch size than the tutorial's default page size of 1: about
  525 against 575 tokens/s with one to sixteen sequences running. A fresh page-16 engine showed the same
  rate, so the page size causes it, not one engine. Ten-step rollout times hide it because they vary
  with the longest response: per token of the longest sample, page-16 rollouts took 2.39–2.50 ms against
  2.19 ms.
- **Tuning the router for affinity didn't help.** Two attempts to keep each prompt's samples on one
  engine (overlap credit 16, then credit 3, each with prefill load scale 4) still split prompts in 26 and
  21 of 30 rollouts, and the hit rate stayed at 29%. Credit 16 backfired: every GSM8K prompt shares a
  16-token system-prompt block, which then earned enough credit to make the router ignore prefill cost.
- **Other speedups ("if I missed any"):** none of the routing-related ones. A step spends about 1.7 s
  syncing weights through Lustre, 1.5 s generating, 0.5 s training, and about 2 s in Slime overhead, and
  each job spends about 85 s starting Ray and Megatron and doing the first sync before step 0. The big
  lever is NCCL weight sync, which is blocked on 1.6.x by #16245. That is not a Dynamo setting.
- **For the reviewer's routing question:** round-robin is the right default for this tutorial, and now
  there's a measurement behind it. KV-aware routing pays off when requests reuse long prefixes across
  time: multi-turn rollouts that resend their history, or long shared prompts arriving over time. This
  tutorial has neither.

## 2. What ran

1. **Setup, once** (tutorial manifests, unchanged): a static Lustre PV on a fresh sub-dir `slime-kv/` for
   the `slime-data` claim, the setup Job (69 s with the image cached), the `slime-functions` ConfigMap, and
   the two engine Services.
2. **Five configurations**, each run three times with `tools/run_phase.sh`. The DGD is deployed once, then
   the tutorial's RayJob runs three times against it, deleting it between runs:

   | Phase | Frontend | Engines | Order |
   |---|---|---|---|
   | `kv` | `DYN_ROUTER_MODE=kv`, `DYN_ROUTER_PREDICTED_TTL_SECS=5` | `--page-size 16`, `--kv-events-config '{"publisher":"zmq","endpoint":"tcp://*:5557","topic":""}'` | 1st, fresh engines |
   | `affinity` | `kv` + `DYN_ROUTER_KV_OVERLAP_SCORE_CREDIT=16`, `DYN_ROUTER_PREFILL_LOAD_SCALE=4` | the `kv` engines | 2nd, frontend restarted |
   | `control` | round-robin | the `kv` engines | 3rd, frontend restarted |
   | `rr` | round-robin (the tutorial's DGD) | tutorial defaults (page size 1, no KV events) | 4th, fresh engines |
   | `affinity2` | `kv` + overlap credit 3, prefill load scale 4 | page 16 + KV events | 5th, fresh engines |

   The `kv` settings are the ones from [KV-Aware Load Balancing for RL Rollouts](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/use-cases/reinforcement-learning/routing.md)
   and the SGLang sidecar's `agg_kv_router.yaml` (v1.6.1). Slime checks external engines' `/server_info`, but
   not `page_size`, so the training command didn't change.
3. **Controls:** every GPU Pod on one node with cached images (`tools/pin.py`), the same RayJob and prompts,
   and both engines registered before training (two `DynamoWorkerMetadata` objects with model cards; for
   the KV phases, a `kv-events` channel too). Each run's logs and a frontend metrics scrape before and after
   are in `runs/<phase>/run<N>/`.

## 3. Results

Pooled over the three runs of each configuration (mean ± standard deviation across steps):

| Configuration | Prefix-cache hit rate | Rollout time, steps 1–9 | Step time, steps 1–9 | Weight sync, steps 1–9 | Ray job | TTFT p50 / p90 |
|---|---|---|---|---|---|---|
| Round-robin, tutorial DGD | 28.7% ± 11.6 | 1.48 s ± 0.43 | 5.96 s ± 0.49 | 1.67 s ± 0.14 | 137 s | 39 / 96 ms |
| KV-aware, documented settings | 28.5% ± 13.7 | 1.50 s ± 0.48 | 5.90 s ± 0.53 | 1.67 s ± 0.12 | 137 s | 40 / 190 ms |
| KV-aware, affinity, credit 16 | 29.4% ± 14.1 | 1.42 s ± 0.47 | 5.85 s ± 0.48 | 1.69 s ± 0.14 | 136 s | 38 / 50 ms |
| KV-aware, affinity, credit 3 | 29.2% ± 17.4 | 1.42 s ± 0.43 | 5.81 s ± 0.47 | 1.67 s ± 0.11 | 136 s | 41 / 198 ms |
| Round-robin on the KV engines (control) | 28.2% ± 10.5 | 1.51 s ± 0.48 | 5.91 s ± 0.51 | 1.67 s ± 0.12 | 137 s | 38 / 50 ms |

- **Time to first token:** the first RayJob on freshly started engines pays a warm-up in every mode
  (`rr` run 1: p90 212 ms; `kv` run 1: p90 363 ms and a 2.9 s rollout 0; `affinity2` run 1 likewise). Later
  runs sit at 37–39 ms median and 48–60 ms p90 in every configuration, so the p90 column differs only by
  whether a phase started on fresh engines.
- **Decode rate per sequence** (engine `Decode batch` lines, median by running batch size):

  | Engines | 1 running | 2–4 | 5–16 | 17+ |
  |---|---|---|---|---|
  | Page size 1 (`rr`) | 578 tok/s | 568 | 575 | 445 |
  | Page size 16 (`kv`, `affinity`, `control`: one pair of engines) | 524–529 | 518–528 | 521–532 | 410–415 |
  | Page size 16, fresh engines (`affinity2`) | 531 | 544 | 532 | 413 |

- **Routing:** round-robin split every rollout 32/32, so every prompt's samples went 4/4. KV routing split
  runs 336/304, 325/315, and 322/318; 26 of 30 rollouts had a per-engine count that isn't a multiple of 8,
  which means at least one prompt was split. Credit 16: 26 of 30. Credit 3: 21 of 30.
- **Prefill batches** (`tools/first_batch.py`): each engine prefilled a rollout in a median of 5–6 batches.
  The largest batch carried a median of 34–38% of the engine's requests, with 28–36% of its prompt tokens
  cached, and 20–31% of requests sat in batches with no cached token at all, in every configuration.

Per run, against the first run:

| Run | Hit rate | Rollout 1–9 | Step 1–9 | Ray job | Requests per engine | Logprob gap | Reloads |
|---|---|---|---|---|---|---|---|
| Run 7 (2026-11-05, demo) | 26.2% | 1.25 s | 5.35 s | – | 320/320 | 0.0099–0.0121 | 11/11 |
| e2e run 2 (2026-11-12) | 25.9% | 1.35 s | 5.70 s | 135 s | 320/320 | 0.0094–0.0121 | 11/11 |
| `rr` run 1 / 2 / 3 | 28.3 / 29.5 / 28.3% | 1.59 / 1.46 / 1.39 s | 6.23 / 5.89 / 5.77 s | 140 / 136 / 136 s | 320/320 | 0.0094–0.0123 | 11/11 |
| `kv` run 1 / 2 / 3 | 24.8 / 23.9 / 36.7% | 1.57 / 1.39 / 1.54 s | 6.10 / 5.74 / 5.87 s | 140 / 135 / 136 s | 336/304, 325/315, 322/318 | 0.0090–0.0131 | 11/11 |
| `affinity` run 1 / 2 / 3 | 29.4 / 25.6 / 33.1% | 1.35 / 1.53 / 1.40 s | 5.67 / 5.96 / 5.93 s | 134 / 138 / 137 s | 336/304, 352/288, 335/305 | 0.0093–0.0122 | 11/11 |
| `control` run 1 / 2 / 3 | 28.3 / 28.6 / 27.6% | 1.58 / 1.31 / 1.65 s | 6.04 / 5.68 / 6.00 s | 137 / 136 / 137 s | 320/320 | 0.0093–0.0122 | 11/11 |
| `affinity2` run 1 / 2 / 3 | 23.4 / 31.5 / 32.6% | 1.61 / 1.28 / 1.36 s | 6.12 / 5.63 / 5.67 s | 140 / 132 / 135 s | 349/291, 324/316, 326/314 | 0.0091–0.0126 | 11/11 |

Today's round-robin steps are about 0.6 s slower than run 7's under the same configuration: weight sync
alone takes 0.17 s longer (1.67 s against 1.5 s). The comparison therefore uses today's round-robin runs as
the baseline, not run 7.

## 4. Why the router can't move these numbers

- **Arrival:** the frontend logged each rollout's 64 routing decisions within 7–60 ms with KV routing
  (median 45 ms) and 50–105 ms with round-robin. Rollout 0 shows the effect most clearly: in `kv` run 1,
  the engines prefilled 35 and 27 requests in one batch each, with 0 cached tokens. In later rollouts the
  requests spread over more batches, and only the later batches hit the cache.
- **Cost function (`lib/kv-router/src/scheduling/selector/reference.rs` at v1.6.1):** a worker's logit is
  `prefill_load_scale × max(0, raw_prefill_blocks − credit × overlap_blocks) + decode_blocks`, where
  `raw_prefill_blocks` includes the worker's whole prefill backlog. A GSM8K prompt is about 90 tokens,
  5 full blocks, so a sibling's credit is about 5 blocks. The first sibling adds about 6 blocks of backlog
  to its engine, so the next sibling often costs less on the other engine. The logs show it: siblings
  priced at the cached rate (decode load only), interleaved with groups split across engines.
- **The system prompt:** Slime's GSM8K prompts start with the same system prompt ("You are a helpful
  assistant. Please put the answer within \boxed{}."), one shared 16-token block. With credit 16 that
  single block earns 16 blocks of credit, more than any new prompt's prefill, so the router stopped
  distinguishing prompts by cache and balanced on decode load alone.
- **Upper bound:** each engine prefills about 2,100 new tokens per rollout. Time to first token, which
  includes prefill and queueing, was a median of 38–41 ms. Removing all prefill would shorten a 1.5 s
  rollout by at most a few percent, and only if the longest sample's own prefill shrank.

## 5. KV vs round-robin, measured as in the ReTool comparison

The [ReTool comparison](../slime-retool/REPORT.md#5-round-robin-baseline-is-kv-routing-faster) found
that KV routing nearly halved the prompt tokens the engines had to compute (round-robin computed ×1.85 as
many) without making rollouts faster. This section applies its measurements to the runs above: prompt
tokens prefilled, how fast each rollout's requests finished (from the frontend log's per-request times),
and the engines' generation rate at equal load. [`tools/compare_runs.py`](tools/compare_runs.py) produced
the tables (`runs/compare-kv-rr.md` and `.json`), and
`runs/compare-kv-rr.html` charts them with the ReTool numbers alongside.

The main pair is `kv` against `control`, round-robin on the same engines, as in ReTool. `rr`, the
tutorial's own engines with page size 1, is the third column. Every run sent the same 58,176 prompt
tokens, step by step.

**Prompt tokens**, per run, mean of the three runs:

| Configuration | Served from cache | Prompt tokens prefilled | Runs 1 / 2 / 3 | vs KV-aware |
|---|---|---|---|---|
| KV-aware (`kv`) | 28.8% | 41,424 | 43,504 / 44,016 / 36,752 | |
| Round-robin, same engines (`control`) | 28.5% | 41,605 | 41,488 / 41,360 / 41,968 | ×1.00 |
| Round-robin, tutorial engines (`rr`) | 29.0% | 41,304 | 41,421 / 40,942 / 41,548 | ×1.00 |
| KV, credit 16 (`affinity`) | 29.4% | 41,056 | 41,264 / 43,136 / 38,768 | ×0.99 |
| KV, credit 3 (`affinity2`) | 29.5% | 41,024 | 44,432 / 39,632 / 39,008 | ×0.99 |
| ReTool, for contrast: KV / round-robin | 79.0% / 59.7% | 5.66M / 10.45M | | ×1.85 |

Step by step, round-robin prefilled a mean of 18 tokens more than KV routing, out of about 4,100 (paired
over the same step of the same-numbered run: t 0.13, more in 13 of 30 steps). The engines' own prefill
logs give the same totals in every run. KV routing's totals vary more from run to run than round-robin's.
"Served from cache" weights by tokens, as in the ReTool comparison; section 3's 28.5% and 28.7% average
the per-step rates instead.

**Why ReTool's savings don't carry over:**
- In ReTool the savings came from follow-up turns. Each one resends its trajectory's whole history, and KV
  routing sent 61% of them back to the engine that already held it, against 24% for round-robin. GSM8K
  has no follow-ups: each sample is one prompt of about 91 tokens.
- A GSM8K prompt can reuse two things: the shared system prompt, which every engine caches after its
  first request whatever the router does, and the identical prompts of the question's 7 other samples.
  Round-robin alternates between the two engines, so each engine already gets about half of each
  question's samples. The most KV routing could save is the second engine's copy of each question:
  8 questions × about 91 tokens, about 730 tokens per step or 12.5% of prompt tokens (an estimate of the
  upper bound).
- It doesn't save even that, because of timing (section 4). A rollout's 64 requests reach the frontend
  within about 50 ms, each engine prefills them in about 5 batches, and samples prefilled in the same
  batch can't reuse each other's prompt. The router counts a sibling it sent a moment earlier as already
  cached on that engine (predict-on-route, `DYN_ROUTER_PREDICTED_TTL_SECS`). Going by the
  `overlap_blocks` it logged for each request, it expected 76% of prompt tokens to be cached on the
  engines it picked. The engines served 29%.

**Did requests finish sooner?** Times use runs 2 and 3, steps 1–9: 18 rollouts and 1,152 requests per
configuration. Run 1 is left out because freshly started engines ran slower for their first 2–5 rollouts.
The requests of KV run 1 reached the halfway mark at a mean 579 ms per step, against 491 and 493 ms in
runs 2 and 3, and credit-3 run 1 (also on fresh engines) at 572 ms. Round-robin on the KV engines never
ran on fresh engines, so pooling run 1 would credit round-robin with KV's warm-up: with run 1 in, round-robin
looked 31–34 ms faster from the 25th to the 75th percentile.

Mean per step of the time from a rollout's first request until a share of its 64 requests had finished:

| Share finished | KV-aware | Round-robin, same engines | Round-robin − KV (t; steps slower) | Round-robin, tutorial engines | Tutorial − KV (t; steps slower) |
|---|---|---|---|---|---|
| 25% | 382 ms | 384 ms | +2 ms (+0.28; 11 of 18) | 366 ms | −17 ms (−2.41; 7 of 18) |
| 50% | 492 ms | 490 ms | −1 ms (−0.17; 6 of 18) | 450 ms | −42 ms (−6.12; 2 of 18) |
| 75% | 624 ms | 619 ms | −6 ms (−0.44; 8 of 18) | 576 ms | −48 ms (−4.36; 2 of 18) |
| 90% | 831 ms | 813 ms | −19 ms (−0.78; 8 of 18) | 761 ms | −71 ms (−2.75; 4 of 18) |
| 95% | 995 ms | 952 ms | −44 ms (−0.63; 9 of 18) | 887 ms | −108 ms (−1.34; 7 of 18) |
| 100%: the last request | 1,401 ms | 1,416 ms | +15 ms (+0.17; 8 of 18) | 1,360 ms | −41 ms (−0.52; 7 of 18) |
| Slime's rollout time | 1,466 ms | 1,479 ms | +14 ms (+0.15; 8 of 18) | 1,426 ms | −40 ms (−0.51; 7 of 18) |

Differences are paired over the same step of the same-numbered run, which had the same prompts. t is the
mean difference over its standard error, so values between −2 and 2 can't be told apart from zero.

- **Same engines, either router:** requests finish at the same pace at every percentile. Time to first
  token is 38 ms at the median and 49 ms at the 90th percentile under both, and the engines generate at
  the same rate at equal batch size (table in `runs/compare-kv-rr.md`).
- **The tutorial's engines (page size 1):** the median request finished 42 ms (8.5%) sooner than with KV
  routing, in 16 of 18 steps. That's the page-size-16 decode cost from section 3, which comes with turning
  KV routing on, and it's bigger than anything the routing itself changed. Rollout time hides it (t −0.51)
  because each rollout waits for its longest response, whose length varies. Per token of that response,
  rollouts took 2.47 ms on the page-16 engines under either router and 2.23 ms on the tutorial's.
- **Whole job:** the Ray job took 135.5 s with KV routing, 136.5 s with round-robin on the same engines,
  and 136.0 s on the tutorial's engines (means of runs 2 and 3).

**Compared with ReTool:** the same answer on time, the opposite on tokens. KV routing saves prefill when
requests come back to an engine that already computed a long prefix, as multi-turn rollouts do. GSM8K's
samples arrive all at once, before any of them is cached.

## 6. The recording

`demo/slime-rl-demo-kv.public.cast` (100×25, about 2 minutes 15 seconds; the MP4, GIF and `.standalone.html`
player page made from it weren't copied into this repo). It has the same structure, palette,
and replay approach as the overview's `slime-rl-demo.cast`. `slime-demo-kv.sh` replays KV run 1 with the
waits fast-forwarded:

1. Stage the model and data (setup Job, 69 s).
2. Serve rollouts with KV-aware routing: the four settings (real `grep` output of the manifest), the apply,
   and both engines ready at 73–74 s.
3. The custom functions, unchanged.
4. The RayJob, unchanged.
5. Ten steps, now with each rollout's prefix-cache hit rate, then the three-run comparison of hit rates.
6. Verify: `router_mode="kv"` decisions per engine (304 / 336) and 11 reloads per engine.
7. A closing card comparing round-robin and KV-aware routing over three runs each.

Re-record with `demo/fill_demo.py runs` (fills the comparison numbers into `slime-demo-kv.template.sh`),
then `demo/record-slime-demo-kv.sh`.

## 7. Caveats

- **Small and short:** 0.6B model, ~90-token prompts, 8 samples per prompt, 2 engines, 10 steps, 3 runs per
  configuration. Rollout time varies by ±0.45 s per step with the longest response, so differences under
  about 0.15 s in pooled rollout time are noise.
- **One node, one day.** Run 7 (Nov 5) was faster under the same configuration, likely from Lustre load.
- **Router internals inferred from logs.** The frontend logs a cost per decision and, on the request's
  route span, the overlap it expected on the chosen engine (`overlap_blocks`, used in section 5). It
  doesn't log which requests are siblings, so the claims about sibling splits rest on per-engine counts
  and logged costs.
- **Fresh engines run slower at first.** The first RayJob on newly started engines (`kv`, `affinity2`, and
  `rr` run 1) ran slower for its first 2–5 rollouts. Section 3 pools all three runs, which is why its TTFT
  p90 column differs by phase; section 5 times only runs 2 and 3.
- **Not tried:** SGLang's `lpm` schedule policy (it only reorders the queue, and a rollout's prefill fits one
  batch's budget, so it shouldn't change batching here), larger page sizes, more engines, multi-turn rollouts
  (since tried with ReTool, `../slime-retool/`), and NCCL weight sync.

## 8. Left on the cluster

Namespace `slime-kv` with PVC `slime-data` (staged model and data), the `slime-setup` Job, the
`slime-functions` ConfigMap, and the engine Services; PV `pv-lustre-slime-kv` (Retain, Lustre sub-dir
`slime-kv/`). No DGD, RayJob, or GPU is held. Cleanup commands are in [README.md](README.md#cleanup).
