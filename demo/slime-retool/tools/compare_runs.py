"""Compare two runs of the ReTool RayJob that differ only in routing (KV-aware vs round-robin).

Usage: python -I compare_runs.py <run-a> <run-b> [--names A B] [--out compare.json]

Each run dir needs summary.json and turns.joined.jsonl (from tools/analyze_retool.py), and
rayjob.yaml and worker-N.stream.log (from tools/run_retool.sh). Prints Markdown tables; --out
also writes the numbers.

Definitions, per request from the turn logs unless noted:
  TTFT              send -> first streamed chunk, as ReTool's client measured it.
  decode ms/token   (request time - TTFT) / (output tokens - 1), for requests with >= 16
                    output tokens.
  computed tokens   prompt tokens the engine prefilled: prompt - cached (SGLang's meta_info).
  stayed / moved    a follow-up request on the same / a different engine as the trajectory's
                    previous request.
  finished by pNN   seconds from a step's first request until NN% of its trajectories had
                    finished; p100 is the rollout's last trajectory. Unlike the rollout time,
                    p25-p95 don't hinge on one trajectory.
  last trajectory   the step's trajectory whose final request ended last; the rollout waits
                    for it. Its span runs from the step's first request to that final end:
                    wait before its first request + its requests (TTFT + decode) + the time
                    between its requests (sandbox runs, tokenizing, sending).
  decode at equal load  SGLang's decode log lines (engine side), bucketed by running requests.
"""

import argparse
import json
import math
import pathlib
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime

import yaml

ANSI = re.compile(r"\x1b\[[0-9;]*m")
DECODE = re.compile(r"Decode batch, #running-req: (\d+), #token: (\d+), token usage: [0-9.]+, cuda graph: \w+, "
                    r"gen throughput \(token/s\): ([0-9.]+)")
BATCH_BUCKETS = [(1, 8), (9, 16), (17, 32), (33, 48), (49, 64), (65, 96)]
QUANTILES = (0.25, 0.5, 0.75, 0.9, 0.95, 1.0)


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None


def load(rd):
    s = json.loads((rd / "summary.json").read_text())
    turns = [json.loads(line) for line in (rd / "turns.joined.jsonl").read_text().splitlines() if line.strip()]
    status = yaml.safe_load((rd / "rayjob.yaml").read_text())["status"]
    return s, turns, status


def kind(t):
    if t["turn"] == 0:
        return "first"
    if not t.get("prev_engine") or not t.get("engine"):
        return "unjoined"
    return "stayed" if t["engine"] == t["prev_engine"] else "moved"


def decode_ms_per_token(t):
    if t.get("req_s") is None or t.get("ttft_s") is None or (t.get("output_tokens") or 0) < 16:
        return None
    return 1000 * (t["req_s"] - t["ttft_s"]) / (t["output_tokens"] - 1)


def computed(t):
    return (t.get("prompt_tokens") or 0) - (t.get("cached_tokens") or 0)


def completion(turns_k):
    """Seconds from the step's first request until each share of its trajectories had finished."""
    ends = defaultdict(float)
    start = min(t["t_start"] for t in turns_k)
    for t in turns_k:
        ends[t["sample"]] = max(ends[t["sample"]], t["t_end"] - start)
    done = sorted(ends.values())
    return {f"p{round(q * 100)}": done[min(len(done) - 1, math.ceil(q * len(done)) - 1)] for q in QUANTILES}, done


def decode_by_batch(rd):
    """SGLang's decode log, every 40 decode steps per engine: generated tokens per second per running
    request, bucketed by the number of running requests. The interval includes any prefill batches
    the engine ran in between, so prefill that interrupts decoding shows up as a lower rate."""
    by = defaultdict(list)
    for f in sorted(rd.glob("worker-*.stream.log")):
        for raw in f.read_text(errors="replace").splitlines():
            m = DECODE.search(ANSI.sub("", raw))
            if not m:
                continue
            running, tokens, thr = int(m.group(1)), int(m.group(2)), float(m.group(3))
            if thr < 50:  # the first interval after the engine was idle
                continue
            for lo, hi in BATCH_BUCKETS:
                if lo <= running <= hi:
                    by[f"{lo}-{hi}"].append((thr / running, tokens / running))
                    break
    return {k: {"intervals": len(v), "tok_s_per_seq_p50": statistics.median(x for x, _ in v),
                "context_per_seq_p50": statistics.median(c for _, c in v)} for k, v in by.items()}


def critical(turns_k):
    by = defaultdict(list)
    for t in turns_k:
        by[t["sample"]].append(t)
    start = min(t["t_start"] for t in turns_k)
    last = sorted(max(by.values(), key=lambda ts: max(t["t_end"] for t in ts)), key=lambda t: t["turn"])
    ttft = sum(t.get("ttft_s") or 0 for t in last)
    req = sum(t.get("req_s") or 0 for t in last)
    out_tokens = sum(t["output_tokens"] for t in last)
    return {
        "sample": last[0]["sample"],
        "requests": len(last),
        "output_tokens": out_tokens,
        "computed_tokens": sum(computed(t) for t in last),
        "span_s": last[-1]["t_end"] - start,
        "wait_before_first_s": last[0]["t_start"] - start,
        "ttft_s": ttft,
        "decode_s": req - ttft,
        "decode_ms_per_token": 1000 * (req - ttft) / out_tokens if out_tokens else None,
        "between_requests_s": sum(b["t_start"] - a["t_end"] for a, b in zip(last, last[1:])),
        "moves": sum(1 for a, b in zip(last, last[1:]) if a.get("engine") != b.get("engine")),
    }


def latency(ts):
    return {
        "requests": len(ts),
        "ttft_p50_ms": 1000 * pct([t.get("ttft_s") for t in ts], 0.5) if ts else None,
        "ttft_p90_ms": 1000 * pct([t.get("ttft_s") for t in ts], 0.9) if ts else None,
        "request_p50_s": pct([t.get("req_s") for t in ts], 0.5),
        "decode_ms_per_token_p50": pct([decode_ms_per_token(t) for t in ts], 0.5),
        "prompt_tokens": sum(t.get("prompt_tokens") or 0 for t in ts),
        "computed_tokens": sum(computed(t) for t in ts),
        "computed_p50": pct([computed(t) for t in ts], 0.5),
    }


def run_view(rd, s, turns, status):
    kinds = defaultdict(list)
    for t in turns:
        kinds[kind(t)].append(t)
    by_step = defaultdict(list)
    for t in turns:
        by_step[t["step"]].append(t)
    steps = {}
    pooled = []
    for st in s["steps"]:
        k, tk = st["step"], by_step.get(st["step"], [])
        fu = [t for t in tk if kind(t) in ("stayed", "moved")]
        done_q, done = completion(tk) if tk else ({}, [])
        pooled += done
        steps[k] = {
            "rollout_s": st["rollout_time"],
            "train_s": st["train_time"],
            "sync_s": st["weight_sync_time"],
            "step_s": st["step_time"],
            "requests": len(tk),
            "requests_per_trajectory": st["requests_per_trajectory"],
            "output_tokens": sum(t["output_tokens"] for t in tk),
            "response_len_max": st["response_len_max"],
            "hit_rate": st["hit_rate"],
            "computed_tokens": st["computed_tokens"],
            "same_engine": st["same_engine"],
            "ttft_p50_followup_ms": 1000 * pct([t.get("ttft_s") for t in fu], 0.5) if fu else None,
            "decode_ms_per_token_p50": pct([decode_ms_per_token(t) for t in tk], 0.5),
            "acc": st["acc"],
            "finished_by": done_q,
            "critical": critical(tk) if tk else None,
        }
    info = status.get("rayJobInfo", {})
    created, finished = iso(status.get("startTime")), iso(status.get("endTime"))
    total = lambda key: sum(v[key] for v in steps.values() if v[key] is not None)
    eng_req = Counter(t.get("engine") for t in turns)
    eng_out = Counter()
    eng_comp = Counter()
    for t in turns:
        eng_out[t.get("engine")] += t["output_tokens"]
        eng_comp[t.get("engine")] += computed(t)
    return {
        "router_modes": s["router_modes"],
        "rayjob_s": (finished - created).total_seconds() if created and finished else None,
        "ray_job_s": (iso(info["endTime"]) - iso(info["startTime"])).total_seconds() if info.get("endTime") else None,
        "first_sync_s": s["sync_secs"][0] if s["sync_secs"] else None,
        "rollout_sum_s": total("rollout_s"),
        "train_sum_s": total("train_s"),
        "sync_sum_s": sum(s["sync_secs"]),
        "step_sum_s": total("step_s"),
        "trajectories": s["trajectories"],
        "requests": len(turns),
        "output_tokens": sum(t["output_tokens"] for t in turns),
        "prompt_tokens": s["prompt_tokens"],
        "computed_tokens": s["computed_tokens"],
        "hit_rate": s["hit_rate"],
        "same_engine": s["same_engine"],
        "acc": statistics.fmean(v["acc"] for v in steps.values() if v["acc"] is not None),
        "per_engine_requests": dict(sorted(eng_req.items(), key=lambda kv: str(kv[0]))),
        "per_engine_output_tokens": dict(sorted(eng_out.items(), key=lambda kv: str(kv[0]))),
        "per_engine_computed_tokens": dict(sorted(eng_comp.items(), key=lambda kv: str(kv[0]))),
        "latency": {k: latency(v) for k, v in [("first", kinds["first"]), ("followup", kinds["stayed"] + kinds["moved"]),
                                                ("stayed", kinds["stayed"]), ("moved", kinds["moved"]), ("all", turns)]},
        "unjoined": len(kinds["unjoined"]),
        "engine_logs": s["engines"],
        "decode_by_batch": decode_by_batch(rd),
        # Share of all trajectories finished, by whole seconds since their step's first request.
        "finished_curve": [sum(1 for x in pooled if x <= sec) / len(pooled) for sec in range(0, math.ceil(max(pooled)) + 1)]
        if pooled else [],
        "steps": steps,
    }


def same_problems(ta, tb):
    qa = {t["group"]: t.get("question") for t in ta if t["turn"] == 0}
    qb = {t["group"]: t.get("question") for t in tb if t["turn"] == 0}
    common = sorted(set(qa) & set(qb))
    return sum(1 for g in common if qa[g] == qb[g]), len(common)


def f(x, nd=0, unit="", pct_=False, sign=False):
    if x is None:
        return "–"
    if pct_:
        return f"{100 * x:{'+' if sign else ''}.{nd}f}%"
    return f"{x:{'+' if sign else ''},.{nd}f}{unit}"


def mmss(x):
    return "–" if x is None else f"{int(x // 60)} min {int(round(x % 60))} s"


def rel(a, b):
    return None if not a or b is None else (b - a) / a


def markdown(A, B, na, nb, same):
    out = []
    w = out.append
    w(f"Same problems in the same steps: {same[0]} of {same[1]} prompt groups match.\n")
    w(f"| | {na} | {nb} | {nb} vs {na} |")
    w("|---|---|---|---|")
    rows = [
        ("Router modes in the frontend log", str(A["router_modes"]), str(B["router_modes"]), ""),
        ("RayJob, created to finished", mmss(A["rayjob_s"]), mmss(B["rayjob_s"]), f(B["rayjob_s"] - A["rayjob_s"], 0, " s", sign=True)),
        ("Ray job", mmss(A["ray_job_s"]), mmss(B["ray_job_s"]), f(B["ray_job_s"] - A["ray_job_s"], 0, " s", sign=True)),
        ("Rollout, sum of 10 steps", f(A["rollout_sum_s"], 0, " s"), f(B["rollout_sum_s"], 0, " s"),
         f"{f(B['rollout_sum_s'] - A['rollout_sum_s'], 0, ' s', sign=True)} ({f(rel(A['rollout_sum_s'], B['rollout_sum_s']), 1, pct_=True, sign=True)})"),
        ("Training, sum", f(A["train_sum_s"], 0, " s"), f(B["train_sum_s"], 0, " s"), f(B["train_sum_s"] - A["train_sum_s"], 0, " s", sign=True)),
        ("Weight syncs, sum of 11", f(A["sync_sum_s"], 1, " s"), f(B["sync_sum_s"], 1, " s"), f(B["sync_sum_s"] - A["sync_sum_s"], 1, " s", sign=True)),
        ("Step time, sum", f(A["step_sum_s"], 0, " s"), f(B["step_sum_s"], 0, " s"),
         f"{f(B['step_sum_s'] - A['step_sum_s'], 0, ' s', sign=True)} ({f(rel(A['step_sum_s'], B['step_sum_s']), 1, pct_=True, sign=True)})"),
        ("Requests (per trajectory)", f"{A['requests']:,} ({A['requests'] / A['trajectories']:.2f})",
         f"{B['requests']:,} ({B['requests'] / B['trajectories']:.2f})", f(rel(A["requests"], B["requests"]), 1, pct_=True, sign=True)),
        ("Output tokens", f"{A['output_tokens'] / 1e6:.2f}M", f"{B['output_tokens'] / 1e6:.2f}M", f(rel(A["output_tokens"], B["output_tokens"]), 1, pct_=True, sign=True)),
        ("Prompt tokens", f"{A['prompt_tokens'] / 1e6:.2f}M", f"{B['prompt_tokens'] / 1e6:.2f}M", f(rel(A["prompt_tokens"], B["prompt_tokens"]), 1, pct_=True, sign=True)),
        ("Prefix-cache hit rate", f(A["hit_rate"], 1, pct_=True), f(B["hit_rate"], 1, pct_=True), ""),
        ("Prompt tokens prefilled (computed)", f"{A['computed_tokens'] / 1e6:.2f}M", f"{B['computed_tokens'] / 1e6:.2f}M",
         f"×{B['computed_tokens'] / A['computed_tokens']:.2f}"),
        ("Follow-ups on the previous request's engine", f(A["same_engine"], 0, pct_=True), f(B["same_engine"], 0, pct_=True), ""),
        ("Correct answers", f(A["acc"], 0, pct_=True), f(B["acc"], 0, pct_=True), ""),
    ]
    for r in rows:
        w("| " + " | ".join(r) + " |")
    w("")
    w(f"| Requests | {na}: count | TTFT p50 / p90 | Prefilled, median (sum) | Decode ms/token p50 | {nb}: count | TTFT p50 / p90 | Prefilled, median (sum) | Decode ms/token p50 |")
    w("|---|---|---|---|---|---|---|---|---|")
    names = {"first": "First of a trajectory", "followup": "Follow-ups", "stayed": "Follow-up, same engine",
             "moved": "Follow-up, other engine", "all": "All"}
    for key, label in names.items():
        cells = [label]
        for R in (A, B):
            L = R["latency"][key]
            cells += [f"{L['requests']:,}", f"{f(L['ttft_p50_ms'], 0)} / {f(L['ttft_p90_ms'], 0)} ms",
                      f"{f(L['computed_p50'], 0)} ({L['computed_tokens'] / 1e6:.2f}M)", f(L["decode_ms_per_token_p50"], 2)]
        w("| " + " | ".join(cells) + " |")
    w("")
    w(f"| Step | Rollout {na} / {nb} | Δ | Longest response {na} / {nb} | Rollout per 1k tokens of it | Requests per trajectory | "
      f"Hit rate | Prefilled tokens | Follow-up TTFT p50 | Decode ms/token p50 |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for k in sorted(set(A["steps"]) & set(B["steps"])):
        a, b = A["steps"][k], B["steps"][k]
        per1k = lambda s: s["rollout_s"] / s["response_len_max"] * 1000 if s["rollout_s"] and s["response_len_max"] else None
        w(f"| {k} | {f(a['rollout_s'])} / {f(b['rollout_s'])} s | {f(b['rollout_s'] - a['rollout_s'], 0, ' s', sign=True)} | "
          f"{f(a['response_len_max'])} / {f(b['response_len_max'])} | {f(per1k(a), 2)} / {f(per1k(b), 2)} s | "
          f"{f(a['requests_per_trajectory'], 2)} / {f(b['requests_per_trajectory'], 2)} | "
          f"{f(a['hit_rate'], 1, pct_=True)} / {f(b['hit_rate'], 1, pct_=True)} | "
          f"{a['computed_tokens'] / 1e6:.2f}M / {b['computed_tokens'] / 1e6:.2f}M | "
          f"{f(a['ttft_p50_followup_ms'], 0)} / {f(b['ttft_p50_followup_ms'], 0)} ms | "
          f"{f(a['decode_ms_per_token_p50'], 2)} / {f(b['decode_ms_per_token_p50'], 2)} |")
    w("")
    w(f"Seconds from each step's first request until a share of its 256 trajectories had finished "
      f"({nb} − {na}; p100 is the rollout's last trajectory).\n")
    qs = [f"p{round(q * 100)}" for q in QUANTILES]
    w("| Step | " + " | ".join(f"{q}: {na} / {nb}" for q in qs) + " |")
    w("|---|" + "---|" * len(qs))
    for k in sorted(set(A["steps"]) & set(B["steps"])):
        a, b = A["steps"][k]["finished_by"], B["steps"][k]["finished_by"]
        w(f"| {k} | " + " | ".join(f"{a[q]:.1f} / {b[q]:.1f} ({b[q] - a[q]:+.1f})" for q in qs) + " |")
    cells = ["**Sum**"]
    for q in qs:
        sa = sum(A["steps"][k]["finished_by"][q] for k in A["steps"])
        sb = sum(B["steps"][k]["finished_by"][q] for k in B["steps"])
        cells.append(f"{sa:.1f} / {sb:.1f} ({f(rel(sa, sb), 1, pct_=True, sign=True)})")
    w("| " + " | ".join(cells) + " |")
    cells = [f"**Paired:** mean {nb} − {na} (t, steps where {nb} was slower)"]
    for q in qs:
        d = [B["steps"][k]["finished_by"][q] - A["steps"][k]["finished_by"][q] for k in sorted(set(A["steps"]) & set(B["steps"]))]
        m, sd = statistics.fmean(d), statistics.stdev(d)
        cells.append(f"{m:+.2f} s (t {m / (sd / math.sqrt(len(d))):.2f}, {sum(1 for x in d if x > 0)}/{len(d)})")
    w("| " + " | ".join(cells) + " |")
    w("")
    w("Engines' decode rate at equal load: generated tokens per second per running request, median over "
      "SGLang's decode log intervals (every 40 decode steps), by the number of running requests.\n")
    w(f"| Running requests | {na}: intervals | tok/s per request | context per request | "
      f"{nb}: intervals | tok/s per request | context per request | {nb} vs {na} |")
    w("|---|---|---|---|---|---|---|---|")
    for lo, hi in BATCH_BUCKETS:
        key = f"{lo}-{hi}"
        a, b = A["decode_by_batch"].get(key), B["decode_by_batch"].get(key)
        if not a or not b:
            continue
        w(f"| {key} | {a['intervals']:,} | {a['tok_s_per_seq_p50']:.1f} | {a['context_per_seq_p50']:,.0f} | "
          f"{b['intervals']:,} | {b['tok_s_per_seq_p50']:.1f} | {b['context_per_seq_p50']:,.0f} | "
          f"{f(rel(a['tok_s_per_seq_p50'], b['tok_s_per_seq_p50']), 1, pct_=True, sign=True)} |")
    w("")
    w(f"Last trajectory of each step (the one the rollout waits for): span = wait before its first request + "
      f"its requests' TTFT + decode + time between its requests.\n")
    w(f"| Step | {na}: span | requests, output tokens | TTFT sum | decode (ms/token) | between requests | moves | "
      f"{nb}: span | requests, output tokens | TTFT sum | decode (ms/token) | between requests | moves |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in sorted(set(A["steps"]) & set(B["steps"])):
        cells = [str(k)]
        for R in (A, B):
            c = R["steps"][k]["critical"]
            cells += [f(c["span_s"], 1, " s"), f"{c['requests']}, {c['output_tokens']:,}", f(c["ttft_s"], 2, " s"),
                      f"{f(c['decode_s'], 1, ' s')} ({f(c['decode_ms_per_token'], 2)})", f(c["between_requests_s"], 1, " s"),
                      str(c["moves"])]
        w("| " + " | ".join(cells) + " |")
    w("")
    for R, n in ((A, na), (B, nb)):
        cs = [s["critical"] for s in R["steps"].values() if s["critical"]]
        w(f"- {n}, last trajectories: TTFT {sum(c['ttft_s'] for c in cs):.1f} s and decode {sum(c['decode_s'] for c in cs):.1f} s "
          f"in all ({sum(c['output_tokens'] for c in cs):,} tokens, "
          f"{1000 * sum(c['decode_s'] for c in cs) / sum(c['output_tokens'] for c in cs):.2f} ms/token)")
    for label, key in [("Requests per engine", "per_engine_requests"), ("Output tokens per engine", "per_engine_output_tokens"),
                       ("Prefilled tokens per engine", "per_engine_computed_tokens")]:
        w(f"- {label}: {na} {list(A[key].values())}, {nb} {list(B[key].values())}")
    for R, n in ((A, na), (B, nb)):
        e = R["engine_logs"]
        w(f"- {n}, engines' own prefill logs: {sum(v['prefill_new'] for v in e.values()) / 1e6:.2f}M computed, "
          f"{sum(v['prefill_cached'] for v in e.values()) / 1e6:.2f}M cached, "
          f"{sum(v['prefill_batches'] for v in e.values()):,} prefill batches, "
          f"{sum(v['reloads'] for v in e.values())} weight reloads, {sum(v['retracted'] for v in e.values())} retractions")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a", type=pathlib.Path)
    ap.add_argument("run_b", type=pathlib.Path)
    ap.add_argument("--names", nargs=2, default=None)
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()
    na, nb = args.names or (args.run_a.name, args.run_b.name)
    sa, ta, ra = load(args.run_a)
    sb, tb, rb = load(args.run_b)
    A, B = run_view(args.run_a, sa, ta, ra), run_view(args.run_b, sb, tb, rb)
    same = same_problems(ta, tb)
    print(markdown(A, B, na, nb, same))
    if args.out:
        args.out.write_text(json.dumps({"names": [na, nb], "same_problems": same, na: A, nb: B}, indent=2, default=str))


if __name__ == "__main__":
    main()
