# Slime + Dynamo tutorial with KV-aware routing (h100, Dynamo 1.6.1)

The Slime tutorial from docs PR #18569 (`docs/slime-rl-integration`), run unchanged except for the
routing settings, on the `h100` AKS cluster on 2026-11-14 UTC. Five configurations, three RayJobs
each, all on one node with cached images. Results: [`REPORT.md`](REPORT.md). In short: KV-aware routing
works but doesn't speed up this tutorial, and the `--page-size 16` it needs slows decode by about 9%. Unlike
the multi-turn ReTool run in [`../slime-retool/`](../slime-retool/), it doesn't reduce prefill
either ([REPORT section 5](REPORT.md#5-kv-vs-round-robin-measured-as-in-the-retool-comparison)).

| Phase | DGD | Frontend | Engines |
|---|---|---|---|
| `rr` | [`manifests/tutorial/dgd.yaml`](manifests/tutorial/dgd.yaml), the tutorial's own | round-robin (default) | page size 1 (default), no KV events |
| `kv` | [`manifests/kv/dgd.yaml`](manifests/kv/dgd.yaml) | `DYN_ROUTER_MODE=kv`, `DYN_ROUTER_PREDICTED_TTL_SECS=5` | `--page-size 16`, `--kv-events-config` (ZMQ) |
| `affinity` | [`manifests/affinity/dgd.yaml`](manifests/affinity/dgd.yaml) | `kv` plus overlap credit 16 and prefill load scale 4 | as `kv` |
| `affinity2` | [`manifests/affinity2/dgd.yaml`](manifests/affinity2/dgd.yaml) | `kv` plus overlap credit 3 and prefill load scale 4 | as `kv` |
| `control` | [`manifests/control/dgd.yaml`](manifests/control/dgd.yaml) | round-robin | as `kv` |

`kv` follows [KV-Aware Load Balancing for RL Rollouts](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/use-cases/reinforcement-learning/routing.md)
and the SGLang sidecar's [`agg_kv_router.yaml`](https://github.com/ai-dynamo/dynamo/blob/v1.6.1/lib/sidecar/sglang/deploy/agg_kv_router.yaml).
`affinity`, `affinity2`, and `control` are experiments, not recommendations: the affinity phases use two
router settings that 1.6.1 deprecates, and `control` separates the engine-flag changes from the routing
change.

Two narrated replays of these runs play without a cluster:

- **KV demo** (100×25, 2:12): `asciinema play demo/slime-rl-demo-kv.public.cast`. It replays
  KV run 1 and the comparison numbers.
- **Team demo** (120×32, 9:17 in 10 chapters): `asciinema play demo/team/slime-gsm8k-team-demo.public.cast`. It
  replays round-robin run 1 with the tutorial's manifests: the problem (GSM8K, two real answers from the run, the
  reward, the RL loop), the AKS stack and Azure Managed Lustre at the cluster's versions (shown, not reinstalled),
  each step's manifest typed in an emulated vim with callouts, one step from both sides (rollout, grading, the
  GRPO update, and the weight sync as the trainer's write plus the engines' load), and the 10 steps. It shows no
  KV-aware routing. Command output comes from the run's saved logs or from read-only snapshots of the cluster
  (`demo/team/snapshots/`); the two `kubectl apply` commands run at setup, whose output wasn't saved, say so.

To record them again, see [Re-record the demos](#re-record-the-demos).

## Contents

| Path | What |
|---|---|
| `manifests/tutorial/` | The six manifests, extracted from the tutorial pages with `tools/extract_heredocs.py` (identical to `tests/deploy/slime/` in PR #18618) |
| `manifests/{kv,affinity,affinity2,control}/dgd.yaml` | The DGD variants. `diff` each against `manifests/tutorial/dgd.yaml` |
| `manifests/storage.yaml` | Namespace `slime-kv`, static Lustre PV `pv-lustre-slime-kv` (sub-dir `slime-kv/`, Retain), PVC `slime-data` |
| `tools/pin.py` | Adds a `kubernetes.io/hostname` node selector to every GPU Pod template (the measurement control) |
| `tools/run_phase.sh` | Deploys one DGD, checks that both engines registered (and, for KV, advertise `kv-events`), then runs the RayJob N times and saves every log and a frontend metrics scrape before and after each run, in `runs/<phase>/` |
| `tools/analyze.py` | Parses a run (`run`) and pools a phase (`phase`) |
| `tools/first_batch.py` | How each engine batched a rollout's prefills, from the engine logs |
| `tools/chart_page.py` | The comparison chart page: hit rate, rollout time, step time, and generation rate per configuration |
| `tools/compare_runs.py` | KV against round-robin, measured as in the ReTool comparison: prompt tokens prefilled, how fast each rollout's requests finished (frontend log), and generation rate at equal load. Writes `runs/compare-kv-rr.{md,json}` (REPORT section 5) |
| `tools/compare_chart.py` | The chart page for it, with ReTool's cache numbers alongside (`runs/compare-kv-rr.html`) |
| `tools/player_page.py` | A page that plays a cast with asciinema-player 3.17.0, the version the docs' `TerminalDemo` uses |
| `tools/redact_cast.py` | A shareable copy of a recording: masks pod IPs and the AKS node-pool ID, then lists anything else to review |
| `demo/` | The KV demo: `slime-demo-kv.template.sh`, `fill_demo.py` (writes `slime-demo-kv.sh` from the runs), the generated `slime-demo-kv.sh`, `record-slime-demo-kv.sh`, and the recording `slime-rl-demo-kv.public.cast` |
| `demo/team/` | The team demo: the words (`beats/`), the data (`fill/`, read from the runs), the helpers (`lib.sh`), `build.sh`, `run.sh`, `check.py`, `record.sh`, read-only cluster snapshots (`snapshots/`), the generated `slime-gsm8k-team-demo.sh`, and the recording `slime-gsm8k-team-demo.public.cast`. [`FRAMEWORK.md`](demo/team/FRAMEWORK.md) documents the helpers and timing knobs |

Not in this repo: `runs/` (each run's logs, metrics scrapes, and summaries, 53 MB), the rendered GIF, MP4, HTML
pages, and PNG frames, and the unredacted casts. [Rerun](#rerun) recreates `runs/`.

## Rerun

`h100` is the kubectl context of the cluster these ran on (the name `az aks get-credentials` gives it). For
another cluster, change it below and set `CTX` for `tools/run_phase.sh`.

```bash
cd demo/slime-kv-routing                         # from the repo root
K=$PWD
PY=python3                                       # any Python 3.10+ with PyYAML
export NODE=aks-ndh100pool-82423760-vmss000003   # one node with 3 free GPUs; every Pod is pinned to it
kubectl --context h100 apply -f manifests/storage.yaml
(cd /tmp && $PY -I $K/tools/pin.py $K/manifests/tutorial/setup-job.yaml $NODE) \
  | kubectl --context h100 -n slime-kv apply -f -
kubectl --context h100 -n slime-kv wait --for=condition=complete job/slime-setup --timeout=30m
kubectl --context h100 -n slime-kv apply -f manifests/tutorial/slime-functions.yaml -f manifests/tutorial/engine-services.yaml

mkdir -p runs/{kv,affinity,control,rr,affinity2}   # each phase's console output goes to runs/<phase>/phase.log
REQUIRE_KV_EVENTS=1 KEEP_DGD=1 bash tools/run_phase.sh kv manifests/kv/dgd.yaml 3 2>&1 | tee runs/kv/phase.log
ALLOW_UPDATE=1 REQUIRE_KV_EVENTS=1 KEEP_DGD=1 bash tools/run_phase.sh affinity manifests/affinity/dgd.yaml 3 2>&1 | tee runs/affinity/phase.log
ALLOW_UPDATE=1 bash tools/run_phase.sh control manifests/control/dgd.yaml 3 2>&1 | tee runs/control/phase.log
bash tools/run_phase.sh rr manifests/tutorial/dgd.yaml 3 2>&1 | tee runs/rr/phase.log
REQUIRE_KV_EVENTS=1 bash tools/run_phase.sh affinity2 manifests/affinity2/dgd.yaml 3 2>&1 | tee runs/affinity2/phase.log

for p in rr kv affinity affinity2 control; do
  for r in 1 2 3; do (cd /tmp && $PY -I $K/tools/analyze.py run $K/runs/$p/run$r); done
  (cd /tmp && $PY -I $K/tools/analyze.py phase $K/runs/$p/phase-summary.json $K/runs/$p/run{1,2,3})
done

# KV against round-robin, measured as in the ReTool comparison (REPORT section 5)
$PY -I tools/compare_runs.py runs/compare-kv-rr "KV-aware=runs/kv" "Round-robin=runs/control" \
  "Round-robin, tutorial engines=runs/rr" --tokens-only "KV, credit 16=runs/affinity" "KV, credit 3=runs/affinity2"
# The chart puts ReTool's numbers alongside, so it needs that kit's runs/compare-kv-rr.json from its own rerun.
$PY -I tools/compare_chart.py runs/compare-kv-rr.json runs/compare-kv-rr.html ../slime-retool/runs/compare-kv-rr.json \
  '{"labels": {"A": "KV-aware", "B": "Round-robin", "T": "RR, page size 1"}, "lede": "The Slime tutorial on GSM8K: three 10-step runs with KV-aware routing and three with round-robin on the same engines, next to the ReTool comparison. Unlike ReTool, KV routing did not change how many prompt tokens the engines computed, and requests finished at the same pace. Times use runs 2 and 3, on warm engines; tokens use all three runs."}'
```

Each phase takes 8–12 minutes and holds 3 GPUs (2 engines and the Ray head) on `$NODE`.

## Re-record the demos

Both demos are replays: their scripts print the saved run's output, so recording them needs no cluster and no
logs. You need [asciinema](https://docs.asciinema.org/getting-started/) 3.x and `python3`. Recording runs in
real time, so do it on an idle machine.

**KV demo** (100×25, about 2.5 minutes):

```bash
cd demo
./record-slime-demo-kv.sh                 # records slime-demo-kv.sh -> slime-rl-demo-kv.cast, themed
python3 -I ../tools/redact_cast.py slime-rl-demo-kv.cast slime-rl-demo-kv.public.cast
```

To change the narration, edit `slime-demo-kv.template.sh` (not `slime-demo-kv.sh`), then run
`python3 -I fill_demo.py ../runs` to regenerate `slime-demo-kv.sh`. `fill_demo.py` reads only
`runs/rr/run{1,2,3}/summary.json` and `runs/kv/run{1,2,3}/summary.json`, which `tools/analyze.py` writes after a
rerun.

**Team demo** (120×32, about 9.5 minutes):

```bash
cd demo/team
./record.sh --no-build        # records the committed slime-gsm8k-team-demo.sh -> slime-gsm8k-team-demo.cast
python3 -I cast_markers.py --list slime-gsm8k-team-demo.cast      # the 10 chapters
python3 -I ../../tools/redact_cast.py slime-gsm8k-team-demo.cast slime-gsm8k-team-demo.public.cast
```

`bash slime-gsm8k-team-demo.sh` plays the replay in a 120×32 terminal without recording it (`FAST=1` skips the
pauses), and `python3 -I check.py --no-build` dry-runs it: chapter times, lines over 120 columns, lint. Commit
only the `.public.cast`; `./record.sh --gif --mp4` renders the unredacted cast, so use it only for a local look.

To change the words, edit `beats/NN_*.sh`; to change the data, `fill/NN_*.py`. Then `./build.sh` regenerates
`slime-gsm8k-team-demo.sh`, and `./record.sh` without `--no-build` builds before it records. The build reruns
every fill script, and the fill scripts read the recorded run's saved files, which aren't in this repo:

- `runs/setup/`: the setup Job's log and start and end times.
- `runs/rr/` (9.4 MB): the phase log, the applied DGD, Pod snapshots, and run 1's driver, frontend, and engine
  logs, metrics scrape, events, RayJob, and summary.

To edit the narration, get the recorded `runs/` folder (53 MB with its logs) from Jonathan. A new run won't drop
in as is: `runs/setup/` was captured by hand during setup, and the fill scripts assert the recorded run's numbers.
Two fill scripts also read the Slime image's source in `../slime-retool/upstream/image/` (included), and
`fill/08_loop.py` checks Slime's example scripts in a local clone of THUDM/slime when `SLIME_REPO` points at one.

## Cleanup

```bash
kubectl --context h100 delete namespace slime-kv
kubectl --context h100 delete pv pv-lustre-slime-kv    # Retain: the files stay on Lustre under slime-kv/
```
