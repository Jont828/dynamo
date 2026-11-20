"""Compare the tutorial's routing configurations in the terms of the ReTool comparison
(../slime-retool/REPORT.md, section 5): prompt tokens prefilled, how fast each rollout's
requests finished, request times, and the engines' generation rate at equal load.

Usage: python3 -I compare_runs.py <out-prefix> <name>=<phase-dir>... [--tokens-only <name>=<phase-dir>...]

The first phase is the reference (KV-aware). Each phase dir holds run1, run2, run3 from
tools/run_phase.sh, each with summary.json from tools/analyze.py. Writes <out-prefix>.md and
<out-prefix>.json. Phases after --tokens-only appear only in the token table.

Definitions:
  prompt tokens     per step, 64 x (mean total length - mean response length), from Slime's
                    rollout metrics.
  prefilled tokens  prompt tokens minus the cached tokens SGLang reported for each sample
                    (Slime's avg_cached_tokens_per_sample x 64). The engines' own prefill logs
                    give the same totals, run by run (checked below).
  hit rate          cached / prompt tokens, weighted by tokens as in the ReTool comparison.
                    (REPORT.md section 3 averages the per-step rates instead.)
  finished by pNN   ms from a rollout's first request reaching the frontend until NN% of its
                    64 requests had completed (frontend log); p100 is the last request.
  request time      frontend log: "request received" to "route request completed".
  tokens            all runs, all 10 steps.
  timing            runs 2 and 3, steps 1-9 (TIMING_RUNS, TIMED). Freshly started engines ran
                    slower for their first 2-5 rollouts (kv, affinity2 and rr run 1), and the
                    control phase never started fresh engines. Step 0's rollout time also
                    carries Slime's start-up in every run.
  paired            the same step of the same-numbered run: the same prompts.
"""

import importlib.util
import json
import math
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

_spec = importlib.util.spec_from_file_location("analyze", pathlib.Path(__file__).with_name("analyze.py"))
analyze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze)

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)Z")
RID = re.compile(r'request_id="?([0-9a-f-]{36})')
DECODE = re.compile(r"Decode batch, #running-req: (\d+), #token: (\d+), token usage: [0-9.]+, cuda graph: \w+, "
                    r"gen throughput \(token/s\): ([0-9.]+)")
PREFILL = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] Prefill batch, #new-seq: (\d+), "
                     r"#new-token: (\d+), #cached-token: (\d+)")
BATCH_BUCKETS = [(1, 1), (2, 4), (5, 8), (9, 16), (17, 32), (33, 64)]
QUANTILES = (0.25, 0.5, 0.75, 0.9, 0.95, 1.0)
QK = [f"p{round(q * 100)}" for q in QUANTILES]
SAMPLES = 64
TIMED = range(1, 10)
TIMING_RUNS = ("run2", "run3")
CURVE_BIN_MS = 10


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def frontend_requests(path):
    """(received, completed, worker, overlap_blocks) per request, sorted by arrival."""
    rec, done = {}, {}
    for raw in path.read_text(errors="replace").splitlines():
        line = ANSI.sub("", raw)
        m = ISO.match(line)
        if not m:
            continue
        t = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc).timestamp()
        if " request received " in line:
            rec[RID.findall(line)[0]] = t  # the message's id is the generate span's id
        elif "route request completed" in line:
            ids = RID.findall(line)  # http span, generate span, route_request span
            ob = re.search(r"overlap_blocks=(\d+)", line)
            done[ids[1]] = (t, re.search(r"worker_id=(\d+)", line).group(1), int(ob.group(1)) if ob else None)
    reqs = sorted((rec[k], *v) for k, v in done.items() if k in rec)
    return reqs, len(rec), len(done)


def bursts(reqs, gap=1.0):
    out, last = [], None
    for r in reqs:
        if last is None or r[0] - last > gap:
            out.append([])
        out[-1].append(r)
        last = r[0]
    return out


def engine_logs(rd):
    decode, prefill = [], []
    for f in sorted(rd.glob("worker-*.log")):
        groups, last = [], None
        for raw in f.read_text(errors="replace").splitlines():
            line = ANSI.sub("", raw)
            m = DECODE.search(line)
            if m:
                decode.append((int(m.group(1)), int(m.group(2)), float(m.group(3))))
                continue
            m = PREFILL.search(line)
            if not m:
                continue
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            seqs, new, cached = (int(x) for x in m.groups()[1:])
            if seqs == 1 and new + cached <= 8:  # readiness probe
                continue
            if last is None or (t - last).total_seconds() > 2:
                groups.append([])
            groups[-1].append((seqs, new, cached))
            last = t
        prefill += [g for g in groups if sum(s for s, _, _ in g) >= 8]
    return decode, prefill


def run_view(rd):
    s = json.loads((rd / "summary.json").read_text())
    reqs, n_rec, n_done = frontend_requests(rd / "frontend.log")
    bs = bursts(reqs)
    if len(bs) != len(s["steps"]) or any(len(b) != SAMPLES for b in bs):
        raise SystemExit(f"{rd}: {len(bs)} bursts of {[len(b) for b in bs]} requests, expected {len(s['steps'])} of {SAMPLES}")
    steps = {}
    for st, b in zip(s["steps"], bs):
        k = st["step"]
        prompt = round(SAMPLES * (st["total_len_mean"] - st["response_len_mean"]))
        cached = round(SAMPLES * st["avg_cached_tokens_per_sample"])
        t0 = b[0][0]
        done = sorted(c - t0 for _, c, _, _ in b)
        last = max(b, key=lambda r: r[1])
        steps[k] = {
            "rollout_s": st["rollout_time"],
            "step_s": st["step_time"],
            "train_s": st["actor_train_time"],
            "sync_s": st["update_weights_time"],
            "response_len_max": st["response_len_max"],
            "output_tokens": round(SAMPLES * st["response_len_mean"]),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "computed_tokens": prompt - cached,
            "hit_rate": cached / prompt,
            "reward": st["raw_reward"],
            "finished_by": {q: 1000 * done[min(len(done) - 1, math.ceil(qq * len(done)) - 1)] for q, qq in zip(QK, QUANTILES)},
            "done_ms": [1000 * x for x in done],
            "arrival_ms": 1000 * (b[-1][0] - t0),
            "request_ms": [1000 * (c - r) for r, c, _, _ in b],
            "last_wait_ms": 1000 * (last[0] - t0),
            "last_request_ms": 1000 * (last[1] - last[0]),
            "per_worker": sorted(Counter(w for _, _, w, _ in b).values(), reverse=True),
        }
    decode, prefill = engine_logs(rd)
    return {
        "run": rd.name,
        "rayjob_s": s["job_secs"],
        "ray_job_s": s["ray_job_secs"],
        "received": n_rec,
        "completed": n_done,
        "joined": len(reqs),
        "router_modes": s["routing"]["modes"],
        "per_worker": sorted(s["routing"]["per_worker"].values(), reverse=True),
        "engine_prefill_new": s["agg"]["engine_prefill_new_tokens"],
        "engine_prefill_cached": s["agg"]["engine_prefill_cached_tokens"],
        "reloads": [e["reloads"] for e in s["engines"].values()],
        "logprob": [s["agg"]["logprob_diff_min"], s["agg"]["logprob_diff_max"]],
        "overlap_blocks": Counter(ob for *_, ob in reqs if ob is not None),
        "steps": steps,
        "decode": decode,
        "prefill_rollouts": prefill,
    }


def ttft(run_dirs):
    pooled_b, pooled_a = {}, {}
    for d in run_dirs:
        b = analyze.parse_prom(d / "frontend-metrics-before.txt")
        a = analyze.parse_prom(d / "frontend-metrics-after.txt")
        for key, v in a.items():
            pooled_a[key] = pooled_a.get(key, 0.0) + v
            pooled_b[key] = pooled_b.get(key, 0.0) + b.get(key, 0.0)
    name = "dynamo_request_plane_roundtrip_ttft_seconds"
    return {q: 1000 * analyze.hist_quantile(pooled_b, pooled_a, name, qq) for q, qq in (("p50", 0.5), ("p90", 0.9))}


def decode_by_batch(decode):
    """SGLang logs every 40th decode step: tokens/s per running request, by running requests.
    The interval includes any prefill run in between, so extra prefill shows as a lower rate."""
    by = defaultdict(list)
    for running, tokens, thr in decode:
        if thr < 50:  # the first interval after the engine sat idle
            continue
        for lo, hi in BATCH_BUCKETS:
            if lo <= running <= hi:
                by[f"{lo}-{hi}" if hi > lo else f"{lo}"].append((thr / running, tokens / running))
                break
    return {k: {"intervals": len(v), "tok_s_per_seq_p50": statistics.median(x for x, _ in v),
                "context_per_seq_p50": statistics.median(c for _, c in v)} for k, v in by.items()}


def phase_view(name, pdir):
    dirs = sorted(d for d in pdir.glob("run*") if d.is_dir())
    runs = [run_view(d) for d in dirs]
    warm = [r for r in runs if r["run"] in TIMING_RUNS]
    timed = [(r, k, r["steps"][k]) for r in warm for k in TIMED]
    per_run = lambda key, ks=None, rs=runs: [sum(st[key] for k, st in r["steps"].items() if ks is None or k in ks) for r in rs]
    prompt, cached = sum(per_run("prompt_tokens")), sum(per_run("cached_tokens"))
    done = [x for _, _, st in timed for x in st["done_ms"]]
    top = math.ceil(max(done) / CURVE_BIN_MS)
    prefill = [g for r in runs for g in r["prefill_rollouts"]]
    overlap = Counter()
    for r in runs:
        overlap.update(r["overlap_blocks"])
    steps = {}
    for k in sorted(runs[0]["steps"]):
        sts = [r["steps"][k] for r in runs]  # tokens: every run
        wts = [r["steps"][k] for r in warm]  # timing: TIMING_RUNS
        steps[k] = {
            "computed_tokens": [st["computed_tokens"] for st in sts],
            "hit_rate": sum(st["cached_tokens"] for st in sts) / sum(st["prompt_tokens"] for st in sts),
            "rollout_s": [st["rollout_s"] for st in wts],
            "response_len_max": [st["response_len_max"] for st in wts],
            "finished_p50_ms": [st["finished_by"]["p50"] for st in wts],
            "finished_p90_ms": [st["finished_by"]["p90"] for st in wts],
            "finished_p100_ms": [st["finished_by"]["p100"] for st in wts],
        }
    return {
        "name": name,
        "dir": str(pdir),
        "runs": [r["run"] for r in runs],
        "router_modes": [r["router_modes"] for r in runs],
        "per_worker": [r["per_worker"] for r in runs],
        "joined": [f"{r['joined']}/{r['received']}/{r['completed']}" for r in runs],
        "reloads": [r["reloads"] for r in runs],
        "logprob": [min(r["logprob"][0] for r in runs), max(r["logprob"][1] for r in runs)],
        "timing_runs": [r["run"] for r in warm],
        "rayjob_s": statistics.fmean(r["rayjob_s"] for r in warm),
        "ray_job_s": statistics.fmean(r["ray_job_s"] for r in warm),
        "rollout_s_mean": statistics.fmean(st["rollout_s"] for _, _, st in timed),
        "rollout_s_sum_per_run": statistics.fmean(per_run("rollout_s", TIMED, warm)),
        "step_s_mean": statistics.fmean(st["step_s"] for _, _, st in timed),
        "train_s_mean": statistics.fmean(st["train_s"] for _, _, st in timed),
        "sync_s_mean": statistics.fmean(st["sync_s"] for _, _, st in timed),
        "prompt_tokens_per_run": per_run("prompt_tokens"),
        "computed_tokens_per_run": per_run("computed_tokens"),
        "engine_computed_per_run": [r["engine_prefill_new"] for r in runs],
        "engine_cached_per_run": [r["engine_prefill_cached"] for r in runs],
        "output_tokens_per_run": per_run("output_tokens"),
        "hit_rate": cached / prompt,
        "hit_rate_per_run": [c / p for c, p in zip(per_run("cached_tokens"), per_run("prompt_tokens"))],
        "reward": statistics.fmean(st["reward"] for r in runs for st in r["steps"].values()),
        "finished_by_mean_ms": {q: statistics.fmean(st["finished_by"][q] for _, _, st in timed) for q in QK},
        "finished_curve": [sum(1 for x in done if x <= b * CURVE_BIN_MS) / len(done) for b in range(top + 1)],
        "request_ms": {"p50": pct([x for _, _, st in timed for x in st["request_ms"]], 0.5),
                       "p90": pct([x for _, _, st in timed for x in st["request_ms"]], 0.9)},
        "arrival_ms_p50": statistics.median(st["arrival_ms"] for _, _, st in timed),
        "last_wait_ms_p50": statistics.median(st["last_wait_ms"] for _, _, st in timed),
        "last_request_ms_mean": statistics.fmean(st["last_request_ms"] for _, _, st in timed),
        "rollout_ms_per_longest_token": statistics.fmean(1000 * st["rollout_s"] / st["response_len_max"] for _, _, st in timed),
        "ttft_ms": ttft(dirs),
        "ttft_ms_warm": ttft([d for d in dirs if d.name in TIMING_RUNS]),
        "half_finished_by_run_ms": {r["run"]: statistics.fmean(r["steps"][k]["finished_by"]["p50"] for k in TIMED) for r in runs},
        "router_expected_hit_rate": (16 * sum(k * v for k, v in overlap.items()) / prompt) if overlap else None,
        "decode_by_batch": decode_by_batch([x for r in runs for x in r["decode"]]),
        "prefill_batches_per_rollout_p50": statistics.median(len(g) for g in prefill),
        "uncached_request_share": sum(s for g in prefill for s, _, c in g if c == 0) / sum(s for g in prefill for s, _, _ in g),
        "router_overlap_blocks": dict(sorted(overlap.items())),
        "steps": steps,
        "_timed": timed,
        "_all": [(r, k, st) for r in runs for k, st in sorted(r["steps"].items())],
    }


def paired(A, B):
    """B - A over the same step of the same-numbered run: timing over TIMING_RUNS and steps
    1-9, tokens over every run and step."""
    match = lambda xs, ys: [(a, b) for (ra, ka, a), (rb, kb, b) in zip(xs, ys)
                            if ka == kb and ra["run"] == rb["run"]]
    timed, every = match(A["_timed"], B["_timed"]), match(A["_all"], B["_all"])
    out = {}
    for key, get, pairs in [*((q, lambda st, q=q: st["finished_by"][q], timed) for q in QK),
                            ("rollout", lambda st: 1000 * st["rollout_s"], timed),
                            ("computed", lambda st: st["computed_tokens"], every)]:
        d = [get(b) - get(a) for a, b in pairs]
        m, sd = statistics.fmean(d), statistics.stdev(d)
        out[key] = {"n": len(d), "mean": m, "sd": sd, "t": m / (sd / math.sqrt(len(d))) if sd else None,
                    "b_higher": sum(1 for x in d if x > 0)}
    return out


def f(x, nd=0, unit="", sign=False):
    return "–" if x is None else f"{x:{'+' if sign else ''},.{nd}f}{unit}"


def pc(x, nd=1, sign=False):
    return "–" if x is None else f"{100 * x:{'+' if sign else ''}.{nd}f}%"


def rel(a, b):
    return (b - a) / a


def markdown(P, tok_only):
    A, others = P[0], P[1:]
    out = []
    w = out.append
    names = [p["name"] for p in P]
    m = statistics.fmean
    pairs = {B["name"]: paired(A, B) for B in others}
    w("**Prompt tokens.** Per run (10 steps, 640 requests), mean of the three runs.\n")
    w("| Configuration | Prompt tokens | Served from cache | Prefilled (computed) | Prefilled, runs 1 / 2 / 3 | vs " + names[0] + " |")
    w("|---|---|---|---|---|---|")
    for p in P + tok_only:
        c = p["computed_tokens_per_run"]
        w(f"| {p['name']} | {f(m(p['prompt_tokens_per_run']))} | {pc(p['hit_rate'])} | {f(m(c))} | "
          f"{' / '.join(f(x) for x in c)} | ×{m(c) / m(A['computed_tokens_per_run']):.3f} |")
    w("")
    eng_ok = all(p["engine_computed_per_run"] == p["computed_tokens_per_run"] for p in P + tok_only)
    w(f"- The engines' own prefill logs give the same per-run totals: {'yes, in every run' if eng_ok else 'NO, see the JSON'}.")
    for B in others:
        d = pairs[B["name"]]["computed"]
        w(f"- Prefilled tokens per step, {B['name']} − {names[0]} (same step, same-numbered run): mean {d['mean']:+.0f} "
          f"(standard deviation {d['sd']:.0f}, t {d['t']:+.2f}; higher in {d['b_higher']} of {d['n']})")
    for p in P + tok_only:
        if p["router_expected_hit_rate"] is not None:
            ob = p["router_overlap_blocks"]
            n = sum(ob.values())
            w(f"- {p['name']}: the router expected {pc(p['router_expected_hit_rate'])} of prompt tokens to be cached on the "
              f"engine it picked (sum of `overlap_blocks` × 16); the engines served {pc(p['hit_rate'])}. "
              f"`overlap_blocks` per request: " + ", ".join(f"{k}: {v / n:.0%}" for k, v in ob.items()))
    w("")
    tr = "runs " + " and ".join(r.replace("run", "") for r in A["timing_runs"])
    w(f"**Run totals.** Times are from {tr} (warm engines), steps 1–9; tokens and answers from all three runs.\n")
    w("| | " + " | ".join(names) + " |")
    w("|---|" + "---|" * len(P))
    rows = [
        ("Router modes in the frontend log", lambda p: ", ".join(f"{k} {v}" for mm in p["router_modes"] for k, v in mm.items())),
        ("Requests per engine, runs 1 / 2 / 3", lambda p: ", ".join("/".join(map(str, x)) for x in p["per_worker"])),
        ("RayJob, created to finished", lambda p: f(p["rayjob_s"], 1, " s")),
        ("Ray job, submit to finish", lambda p: f(p["ray_job_s"], 1, " s")),
        ("Rollout (Slime), mean per step", lambda p: f(p["rollout_s_mean"], 3, " s")),
        ("Rollout per token of the step's longest response", lambda p: f(p["rollout_ms_per_longest_token"], 2, " ms")),
        ("Training, mean per step", lambda p: f(p["train_s_mean"], 3, " s")),
        ("Weight sync, mean per step", lambda p: f(p["sync_s_mean"], 3, " s")),
        ("Step time, mean", lambda p: f(p["step_s_mean"], 3, " s")),
        ("Output tokens per run", lambda p: f(m(p["output_tokens_per_run"]))),
        ("Correct answers (mean reward)", lambda p: pc(p["reward"])),
        ("Logprob gap, min–max", lambda p: f"{p['logprob'][0]:.4f}–{p['logprob'][1]:.4f}"),
        ("Weight reloads per engine", lambda p: ", ".join("/".join(map(str, x)) for x in p["reloads"])),
        (f"Time to first token p50 / p90, {tr}", lambda p: f"{p['ttft_ms_warm']['p50']:.0f} / {p['ttft_ms_warm']['p90']:.0f} ms"),
        ("Time to first token p50 / p90, all runs", lambda p: f"{p['ttft_ms']['p50']:.0f} / {p['ttft_ms']['p90']:.0f} ms"),
        ("Request time p50 / p90", lambda p: f"{p['request_ms']['p50']:.0f} / {p['request_ms']['p90']:.0f} ms"),
        ("A rollout's 64 requests reached the frontend within (median)", lambda p: f(p["arrival_ms_p50"], 0, " ms")),
        ("Last request of a rollout: its own time (mean)", lambda p: f(p["last_request_ms_mean"], 0, " ms")),
        ("Half the requests finished, mean per step: runs 1 / 2 / 3",
         lambda p: " / ".join(f(v, 0) for v in p["half_finished_by_run_ms"].values()) + " ms"),
        ("Prefill batches per engine per rollout (median, all runs)", lambda p: f(p["prefill_batches_per_rollout_p50"], 0)),
        ("Requests prefilled in a batch with no cached token (all runs)", lambda p: pc(p["uncached_request_share"], 0)),
    ]
    for label, get in rows:
        w(f"| {label} | " + " | ".join(get(p) for p in P) + " |")
    w("")
    w(f"**Did requests finish sooner?** Mean per step ({tr}, steps 1–9) of the time from the rollout's first request "
      "until a share of its 64 requests had completed. Paired: the same step of the same-numbered run, so the same "
      "prompts; t is the mean difference over its standard error.\n")
    head = "| Share finished | " + " | ".join(names)
    for B in others:
        head += f" | {B['name']} − {names[0]} (t; pairs slower)"
    w(head + " |")
    w("|---|" + "---|" * (len(P) + len(others)))
    for q in QK:
        cells = [q.replace("p", "") + "%" + (": the last request" if q == "p100" else "")]
        cells += [f(p["finished_by_mean_ms"][q], 0, " ms") for p in P]
        for B in others:
            d = pairs[B["name"]][q]
            cells.append(f"{d['mean']:+.0f} ms ({d['t']:+.2f}; {d['b_higher']}/{d['n']})")
        w("| " + " | ".join(cells) + " |")
    cells = ["Slime's rollout time", *(f(p["rollout_s_mean"] * 1000, 0, " ms") for p in P)]
    for B in others:
        d = pairs[B["name"]]["rollout"]
        cells.append(f"{d['mean']:+.0f} ms ({d['t']:+.2f}; {d['b_higher']}/{d['n']})")
    w("| " + " | ".join(cells) + " |")
    w("")
    B = others[0]
    w(f"**Per step** ({names[0]} / {B['name']}): times are means of {tr}; tokens are means of all three runs.\n")
    w("| Step | Rollout | Longest response (tokens) | Half finished | 90% finished | Last request | Prefilled tokens | Hit rate |")
    w("|---|---|---|---|---|---|---|---|")
    for k in sorted(A["steps"]):
        a, b = A["steps"][k], B["steps"][k]
        w(f"| {k} | {m(a['rollout_s']):.2f} / {m(b['rollout_s']):.2f} s | "
          f"{m(a['response_len_max']):.0f} / {m(b['response_len_max']):.0f} | "
          f"{m(a['finished_p50_ms']):.0f} / {m(b['finished_p50_ms']):.0f} ms | {m(a['finished_p90_ms']):.0f} / {m(b['finished_p90_ms']):.0f} ms | "
          f"{m(a['finished_p100_ms']):,.0f} / {m(b['finished_p100_ms']):,.0f} ms | "
          f"{m(a['computed_tokens']):,.0f} / {m(b['computed_tokens']):,.0f} | {pc(a['hit_rate'])} / {pc(b['hit_rate'])} |")
    w("")
    w("Step 0's rollout time includes Slime's first-rollout start-up in every run, so the totals leave it out.\n")
    w("**The engines' generation rate at equal load** (all runs): tokens per second per running request, median over "
      "SGLang's decode log intervals (every 40 decode steps), by running requests; context per request in parentheses.\n")
    w("| Running requests | " + " | ".join(f"{p['name']}: intervals | tok/s per request (context)" for p in P) + " |")
    w("|---|" + "---|---|" * len(P))
    for lo, hi in BATCH_BUCKETS:
        key = f"{lo}-{hi}" if hi > lo else f"{lo}"
        cells = [key]
        for p in P:
            d = p["decode_by_batch"].get(key)
            cells += ["–", "–"] if not d else [f"{d['intervals']:,}", f"{d['tok_s_per_seq_p50']:.0f} ({d['context_per_seq_p50']:.0f})"]
        w("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        raise SystemExit(__doc__)
    prefix, rest = pathlib.Path(args[0]), args[1:]
    main_specs, tok_specs = (rest[:rest.index("--tokens-only")], rest[rest.index("--tokens-only") + 1:]) \
        if "--tokens-only" in rest else (rest, [])
    load = lambda specs: [phase_view(n, pathlib.Path(d)) for n, d in (s.split("=", 1) for s in specs)]
    P, T = load(main_specs), load(tok_specs)
    md = markdown(P, T)
    print(md)
    prefix.with_suffix(".md").write_text(md + "\n")
    strip = lambda p: {k: v for k, v in p.items() if not k.startswith("_")}
    prefix.with_suffix(".json").write_text(json.dumps(
        {"names": [p["name"] for p in P], "tokens_only": [p["name"] for p in T],
         "paired": {B["name"]: paired(P[0], B) for B in P[1:]},
         "phases": {p["name"]: strip(p) for p in P + T}}, indent=1, default=str))


if __name__ == "__main__":
    main()
