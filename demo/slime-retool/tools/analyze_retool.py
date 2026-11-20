"""Analyze one ReTool run: per-step timing, cache reuse, turns, rewards, routing, stickiness.

Usage: python -I analyze_retool.py <run-dir> [<deploy-dir>]

<run-dir> is what tools/run_retool.sh writes: driver.stream.log, frontend.stream.log,
worker-N.stream.log, turn-logs/*.jsonl, rayjob.yaml, frontend-metrics-{before,after}.txt.
<deploy-dir> (default <run-dir>/../deploy) holds workers.json, the engine -> worker_id map.

Writes <run-dir>/summary.json and <run-dir>/turns.joined.jsonl (every turn record with
its step and engine), and prints the per-step table in Markdown.

Definitions:
  step            a turn's step is its group index // rollout batch size (32 prompts per
                  step); checked against the turn times and the driver's rollout lines.
  follow-up turn  the 2nd or later request of a trajectory (turn >= 1).
  same engine     a follow-up routed to the engine that served the trajectory's previous
                  request (from the frontend's "Selected worker" line for the turn's rid, or
                  in round-robin mode its "route request completed" line).
  reused          a follow-up whose cached prompt tokens cover at least 90% of the previous
                  request's input + output, i.e. the history was served from the cache.
  hit rate        cached prompt tokens / prompt tokens, summed over requests (Slime's
                  rollout/prefix_cache_hit_rate is the same ratio, summed per sample).
"""

import ast
import bisect
import json
import math
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
ISO = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z")
GROUPS_PER_STEP = 32
REUSE = 0.9


def clean(line):
    return ANSI.sub("", line)


def ts(line):
    m = TS.search(line)
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if m else None


def dict_after(line, marker):
    try:
        return ast.literal_eval(line.split(marker, 1)[1].strip())
    except (ValueError, SyntaxError):
        return {}


def first_existing(rd, *names):
    for n in names:
        p = rd / n
        if p.exists() and p.stat().st_size > 0:
            return p
    return rd / names[0]


def parse_driver(path):
    out = {"rollout_perf": {}, "rollout": {}, "train": {}, "train_perf": {}, "sync": [], "events": {}}
    if not path.exists():
        return out
    for raw in path.read_text(errors="replace").splitlines():
        line = clean(raw)
        t = ts(line)
        for key, pat in [
            ("rollout_perf", r"rollout_metrics\.py:\d+ - perf (\d+): "),
            ("train_perf", r"train_metric_utils\.py:\d+ - perf (\d+): "),
            ("rollout", r"train_metric_utils\.py:\d+ - rollout (\d+): "),
            ("train", r"model\.py:\d+ - step (\d+): "),
        ]:
            m = re.search(pat, line)
            if m:
                out[key][int(m.group(1))] = (t, dict_after(line, m.group(0)))
                break
        else:
            m = re.search(r"Timer update_weights end \(elapsed: ([0-9.]+)s\)", line)
            if m:
                out["sync"].append((t, float(m.group(1))))
            elif "Detected external SGLang engines" in line and t:
                out["events"].setdefault("engines_detected", t)
            elif (m := re.search(r"Job '([^']+)' succeeded", line)):
                out["events"]["succeeded"] = m.group(1)
            if t:
                out["events"].setdefault("first_ts", t)
                out["events"]["last_ts"] = t
    return out


def parse_routing(path):
    """rid -> (worker_id, time, logit, overlap_blocks) from the frontend's routing lines.

    KV mode logs a "Selected worker" line per request (engine, time, cost) and a "route
    request completed" line (overlap_blocks). Round-robin logs only the completed line,
    with router_mode=RoundRobin and worker_id; then time, logit, and overlap are None.
    """
    route = {}
    overlap = {}
    completed = {}
    modes = Counter()
    if not path.exists():
        return route, modes
    for raw in path.read_text(errors="replace").splitlines():
        if "route request completed" in raw:
            line = clean(raw)
            rid = re.search(r"request_id=(retool-\S+)", line)
            if not rid:
                continue
            if m := re.search(r"overlap_blocks=(\d+)", line):
                overlap[rid.group(1)] = int(m.group(1))
            if wid := re.search(r"worker_id=(\d+)", line):
                mode = re.search(r"router_mode=(\w+)", line)
                completed[rid.group(1)] = (wid.group(1), mode.group(1) if mode else "?")
            continue
        if "Selected worker" not in raw:
            continue
        line = clean(raw)
        rid = re.search(r'request_id="([^"]+)"', line)
        wid = re.search(r"worker_id=(\d+)", line)
        if not (rid and wid):
            continue
        mode = re.search(r'router_mode="?([a-z-]+)"?', line)
        logit = re.search(r"logit=([0-9.]+)", line)
        t = ISO.search(line)
        if rid.group(1) not in route:
            modes[mode.group(1) if mode else "?"] += 1
        route[rid.group(1)] = (wid.group(1), t.group(1) if t else None, float(logit.group(1)) if logit else None)
    for rid, (wid, mode) in completed.items():
        if rid not in route:  # round-robin: no "Selected worker" line
            route[rid] = (wid, None, None)
            modes[mode] += 1
    route = {rid: (*v, overlap.get(rid)) for rid, v in route.items()}
    return route, modes


def parse_engine(path):
    res = {"prefill_new": 0, "prefill_cached": 0, "prefill_batches": 0, "reloads": 0, "retracted": 0}
    if not path.exists():
        return res
    for raw in path.read_text(errors="replace").splitlines():
        line = clean(raw)
        m = re.search(r"Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+)", line)
        if m:
            seqs, new, cached = (int(x) for x in m.groups())
            if seqs == 1 and new + cached <= 8:  # readiness probe
                continue
            res["prefill_batches"] += 1
            res["prefill_new"] += new
            res["prefill_cached"] += cached
        elif 'POST /update_weights_from_disk HTTP/1.1" 200' in line:
            res["reloads"] += 1
        elif "retract" in line.lower():
            res["retracted"] += 1
    return res


def parse_prom(path):
    """Prometheus text -> {(name, frozenset(labels)): value}."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([^\s]+)", line)
        if not m:
            continue
        labels = frozenset(re.findall(r'(\w+)="([^"]*)"', m.group(2) or ""))
        try:
            out[(m.group(1), labels)] = float(m.group(3))
        except ValueError:
            pass
    return out


def kv_event_counts(rd):
    """Frontend counters over the run: KV events the router applied, warnings, its hit-rate estimate."""
    before, after = parse_prom(rd / "frontend-metrics-before.txt"), parse_prom(rd / "frontend-metrics-after.txt")
    applied, warnings = Counter(), Counter()
    hit_sum = hit_count = 0.0
    for (name, labels), v in after.items():
        d = v - before.get((name, labels), 0.0)
        lab = dict(labels)
        if name == "dynamo_component_kv_cache_events_applied" and d:
            applied[f"{lab.get('event_type')}/{lab.get('status')}"] += int(d)
        elif name == "dynamo_component_kv_cache_event_warnings" and d:
            warnings[lab.get("warning_kind")] += int(d)
        elif name == "dynamo_component_router_kv_hit_rate_sum":
            hit_sum += d
        elif name == "dynamo_component_router_kv_hit_rate_count":
            hit_count += d
    return {"events_applied": dict(applied), "event_warnings": dict(warnings),
            "router_predicted_hit_rate_mean": hit_sum / hit_count if hit_count else None, "router_decisions": int(hit_count)}


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def load_turns(rd):
    turns, finals = [], []
    for f in sorted((rd / "turn-logs").glob("*.jsonl")):
        lines = f.read_text(errors="replace").splitlines()
        for n, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                if n == len(lines) - 1:  # a line still being written when the file was copied
                    print(f"note: skipped a partial last line in {f.name}", file=sys.stderr)
                    continue
                raise
            (turns if rec.get("kind") == "turn" else finals).append(rec)
    return turns, finals


def router_view(turns, window=1.0):
    """What the router credited, from its own log lines, against what the engines cached.

    Every request's "route request completed" line carries overlap_blocks (the cache
    overlap the router credited on the chosen engine) and its "Selected worker" line the
    chosen engine's cost (logit, in 16-token blocks). The engine-load spread at a decision
    is the gap between the four engines' lowest costs among decisions within +-1 s.
    """
    fu = [t for t in turns if t["turn"] >= 1 and t.get("prev_engine") and t["engine"]
          and t.get("router_overlap") is not None]
    stay = [t for t in fu if t["engine"] == t["prev_engine"]]
    move = [t for t in fu if t["engine"] != t["prev_engine"]]
    med = lambda xs: statistics.median(xs) if xs else None
    by_eng = defaultdict(list)
    for t in turns:
        if t.get("routed_ts") is not None and t.get("logit") is not None:
            by_eng[t["engine"]].append((t["routed_ts"], t["logit"]))
    for v in by_eng.values():
        v.sort()
    keys = {e: [x[0] for x in v] for e, v in by_eng.items()}

    def lowest(e, ts):
        v, i, best = by_eng[e], bisect.bisect_left(keys[e], ts - window), None
        while i < len(v) and v[i][0] <= ts + window:
            best = v[i][1] if best is None else min(best, v[i][1])
            i += 1
        return best

    spread, hist = [], []
    for t in fu:
        costs = [lowest(e, t["routed_ts"]) for e in by_eng]
        if all(c is not None for c in costs) and len(costs) == 4:
            spread.append(max(costs) - min(costs))
            hist.append(t["prev_full"] / 16)
    return {
        "stayed": len(stay),
        "stayed_router_overlap_blocks_p50": med([t["router_overlap"] for t in stay]),
        "stayed_cached_blocks_p50": med([(t["cached_tokens"] or 0) / 16 for t in stay]),
        "stayed_prev_full_blocks_p50": med([t["prev_full"] / 16 for t in stay]),
        "stayed_credit_covers_prev_prompt": (sum(1 for t in stay if t["router_overlap"] >= (t["prev_full"] - t["prev_output"]) // 16 - 1)
                                             / len(stay)) if stay else None,
        "moved": len(move),
        "moved_router_overlap_blocks_p50": med([t["router_overlap"] for t in move]),
        "moved_prev_full_blocks_p50": med([t["prev_full"] / 16 for t in move]),
        "chosen_cost_blocks_p50": med([t["logit"] for t in fu]),
        "engine_cost_spread_blocks_p50": med(spread),
        "followup_history_blocks_p50": med(hist),
        "spread_exceeds_history": (sum(1 for s, h in zip(spread, hist) if s > h) / len(spread)) if spread else None,
    }


def analyze(rd, deploy):
    workers = json.loads((deploy / "workers.json").read_text())
    engine_of = {str(i): w for w, v in workers.items() for i in v["worker_ids"]}
    drv = parse_driver(first_existing(rd, "driver.stream.log", "driver.final.log"))
    route, modes = parse_routing(first_existing(rd, "frontend.stream.log", "frontend.final.log"))
    engines = {w: parse_engine(first_existing(rd, f"{w}.stream.log", f"{w}.final.log")) for w in sorted(workers)}
    turns, finals = load_turns(rd)

    # Join each turn with its engine and step.
    by_sample = defaultdict(list)
    for t in turns:
        r = route.get(t["rid"])
        t["engine"] = engine_of.get(r[0], r[0]) if r else None
        t["routed_at"] = r[1] if r else None
        t["routed_ts"] = datetime.fromisoformat(r[1]).replace(tzinfo=timezone.utc).timestamp() if r and r[1] else None
        t["logit"] = r[2] if r else None
        t["router_overlap"] = r[3] if r else None
        t["step"] = t["group"] // GROUPS_PER_STEP
        by_sample[t["sample"]].append(t)
    for ts_ in by_sample.values():
        ts_.sort(key=lambda t: t["turn"])
        for prev, cur in zip(ts_, ts_[1:]):
            full = prev["input_len"] + prev["output_tokens"]
            cur["prev_engine"] = prev["engine"]
            cur["prev_full"] = full
            cur["prev_output"] = prev["output_tokens"]
            cur["reuse_frac"] = (cur["cached_tokens"] or 0) / full if full else None
            cur["gap_s"] = round(cur["t_start"] - prev["t_end"], 4)
    for f in finals:
        f["step"] = f["group"] // GROUPS_PER_STEP

    steps = sorted({t["step"] for t in turns} | set(drv["rollout_perf"]))
    per_step = []
    for k in steps:
        st = [t for t in turns if t["step"] == k]
        fu = [t for t in st if t["turn"] >= 1 and t.get("prev_engine") is not None and t["engine"]]
        fi = [f for f in finals if f["step"] == k]
        reqs = Counter(t["sample"] for t in st)
        rp = drv["rollout_perf"].get(k, (None, {}))[1]
        ro = drv["rollout"].get(k, (None, {}))[1]
        tp = drv["train_perf"].get(k, (None, {}))[1]
        tr = drv["train"].get(k, (None, {}))[1]
        prompt = sum(t["prompt_tokens"] or 0 for t in st)
        cached = sum(t["cached_tokens"] or 0 for t in st)
        per_step.append({
            "step": k,
            "rollout_time": rp.get("perf/rollout_time"),
            # perf/train_time = reference logprobs + actor training (+ preprocessing).
            "train_time": tp.get("perf/train_time"),
            "actor_train_time": tp.get("perf/actor_train_time"),
            "ref_log_probs_time": tp.get("perf/ref_log_probs_time"),
            # Step k's perf line reports the sync before rollout k; this is the one after step k.
            "weight_sync_time": drv["sync"][k + 1][1] if k + 1 < len(drv["sync"]) else None,
            "rollout_end": rp_t.isoformat() if (rp_t := drv["rollout_perf"].get(k, (None, {}))[0]) else None,
            "train_end": tr_t.isoformat() if (tr_t := drv["train"].get(k, (None, {}))[0]) else None,
            "sync_end": drv["sync"][k + 1][0].isoformat() if k + 1 < len(drv["sync"]) and drv["sync"][k + 1][0] else None,
            "step_time": tp.get("perf/step_time"),
            "slime_hit_rate": rp.get("rollout/prefix_cache_hit_rate"),
            "trajectories": len(reqs),
            "requests": len(st),
            "requests_per_trajectory": len(st) / len(reqs) if reqs else None,
            "requests_per_trajectory_max": max(reqs.values()) if reqs else None,
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "computed_tokens": prompt - cached,
            "hit_rate": cached / prompt if prompt else None,
            "hit_rate_turn0": (sum(t["cached_tokens"] or 0 for t in st if t["turn"] == 0)
                               / max(1, sum(t["prompt_tokens"] or 0 for t in st if t["turn"] == 0))),
            "hit_rate_followups": (sum(t["cached_tokens"] or 0 for t in st if t["turn"] >= 1)
                                   / max(1, sum(t["prompt_tokens"] or 0 for t in st if t["turn"] >= 1))),
            "output_tokens": sum(t["output_tokens"] for t in st),
            "followups": len(fu),
            "same_engine": sum(1 for t in fu if t["engine"] == t["prev_engine"]) / len(fu) if fu else None,
            "reused": sum(1 for t in fu if (t["reuse_frac"] or 0) >= REUSE) / len(fu) if fu else None,
            "moved_turn1": sum(1 for t in fu if t["turn"] == 1 and t["engine"] != t["prev_engine"]),
            "turn1": sum(1 for t in fu if t["turn"] == 1),
            "per_engine": dict(sorted(Counter(t["engine"] for t in st).items(), key=lambda kv: str(kv[0]))),
            "tool_outcomes": dict(Counter(t.get("tool_outcome") or t.get("loop") for t in st)),
            "tool_run_p50": pct([t.get("tool_run_s") for t in st], 0.5),
            "tool_run_max": max((t.get("tool_run_s") or 0 for t in st), default=None),
            "ttft_p50": pct([t.get("ttft_s") for t in st], 0.5),
            "ttft_p50_followup": pct([t.get("ttft_s") for t in fu], 0.5),
            "reward_mean": mean([f["score"] for f in fi]) if fi else ro.get("rollout/raw_reward"),
            "acc": mean([1.0 if f["acc"] else 0.0 for f in fi]),
            "slime_raw_reward": ro.get("rollout/raw_reward"),
            "response_len_mean": rp.get("rollout/response_len/mean"),
            "response_len_max": rp.get("rollout/response_len/max"),
            "trajectory_len_mean": mean([f["response_length"] for f in fi]),
            "truncated": sum(1 for f in fi if f.get("status") == "truncated"),
            "logprob_diff": tr.get("train/train_rollout_logprob_abs_diff"),
            "grad_norm": tr.get("train/grad_norm"),
            "turn_window": [min((t["t_start"] for t in st), default=None), max((t["t_end"] for t in st), default=None)],
        })

    view = router_view(turns)
    fu_all = [t for t in turns if t["turn"] >= 1 and t.get("prev_engine") is not None and t["engine"]]
    moved = [t for t in fu_all if t["engine"] != t["prev_engine"]]
    stayed = [t for t in fu_all if t["engine"] == t["prev_engine"]]
    prompt = sum(t["prompt_tokens"] or 0 for t in turns)
    cached = sum(t["cached_tokens"] or 0 for t in turns)
    summary = {
        "run_dir": str(rd),
        "window": dict(line.split("=", 1) for line in (rd / "window.txt").read_text().split() if "=" in line),
        "router_modes": dict(modes),
        "requests_logged": len(turns),
        "requests_routed": sum(1 for t in turns if t["engine"]),
        "trajectories": len(by_sample),
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "computed_tokens": prompt - cached,
        "hit_rate": cached / prompt if prompt else None,
        "followups": len(fu_all),
        "same_engine": len(stayed) / len(fu_all) if fu_all else None,
        "reused": sum(1 for t in fu_all if (t["reuse_frac"] or 0) >= REUSE) / len(fu_all) if fu_all else None,
        "reused_when_same_engine": (sum(1 for t in stayed if (t["reuse_frac"] or 0) >= REUSE) / len(stayed)) if stayed else None,
        "moved_by_turn": dict(sorted(Counter(t["turn"] for t in moved).items())),
        "followups_by_turn": dict(sorted(Counter(t["turn"] for t in fu_all).items())),
        "gap_p50_moved": pct([t["gap_s"] for t in moved], 0.5),
        "gap_p50_stayed": pct([t["gap_s"] for t in stayed], 0.5),
        "requests_per_trajectory_hist": dict(sorted(Counter(Counter(t["sample"] for t in turns).values()).items())),
        "per_engine": dict(sorted(Counter(t["engine"] for t in turns).items(), key=lambda kv: str(kv[0]))),
        "computed_by_kind": {
            kind: {"requests": len(ts_), "prompt_tokens": sum(t["prompt_tokens"] or 0 for t in ts_),
                   "computed_tokens": sum((t["prompt_tokens"] or 0) - (t["cached_tokens"] or 0) for t in ts_)}
            for kind, ts_ in [("turn0", [t for t in turns if t["turn"] == 0]), ("followup_stayed", stayed),
                              ("followup_moved", moved)]
        },
        "router_view": view,
        "kv_events": kv_event_counts(rd),
        "engines": engines,
        "steps": per_step,
        "sync_secs": [s for _, s in drv["sync"]],
        "sync_ends": [x.isoformat() if x else None for x, _ in drv["sync"]],
        "engines_detected": drv["events"].get("engines_detected").isoformat() if drv["events"].get("engines_detected") else None,
        "driver_first_ts": drv["events"].get("first_ts").isoformat() if drv["events"].get("first_ts") else None,
        "driver_last_ts": drv["events"].get("last_ts").isoformat() if drv["events"].get("last_ts") else None,
        "succeeded": drv["events"].get("succeeded"),
    }
    with open(rd / "turns.joined.jsonl", "w") as f:
        for t in sorted(turns, key=lambda t: (t["step"], t["sample"], t["turn"])):
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    (rd / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def fmt(x, nd=1, pct_=False, unit=""):
    if x is None:
        return "–"
    if pct_:
        return f"{100 * x:.{nd}f}%"
    return f"{x:.{nd}f}{unit}" if isinstance(x, float) else f"{x}{unit}"


def table(s):
    rows = [
        "| Step | Rollout | Train | Weight sync | Step | Requests / trajectory | Hit rate | Cached / computed prompt tokens | Follow-ups on the same engine | Follow-ups that reused their history | Reward | Response length (mean / max) | Requests per engine (0/1/2/3) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for st in s["steps"]:
        pe = st["per_engine"]
        rows.append(
            f"| {st['step']} | {fmt(st['rollout_time'], 0, unit=' s')} | {fmt(st['train_time'], 0, unit=' s')} | "
            f"{fmt(st['weight_sync_time'], 1, unit=' s')} | {fmt(st['step_time'], 0, unit=' s')} | "
            f"{fmt(st['requests_per_trajectory'], 2)} (max {st['requests_per_trajectory_max']}) | {fmt(st['hit_rate'], 1, pct_=True)} | "
            f"{st['cached_tokens'] / 1e6:.2f}M / {st['computed_tokens'] / 1e6:.2f}M | {fmt(st['same_engine'], 0, pct_=True)} | "
            f"{fmt(st['reused'], 0, pct_=True)} | {fmt(st['reward_mean'], 2)} | "
            f"{fmt(st['response_len_mean'], 0)} / {fmt(st['response_len_max'], 0)} | "
            f"{'/'.join(str(pe.get(f'worker-{i}', 0)) for i in range(4))} |"
        )
    return "\n".join(rows)


def main():
    rd = pathlib.Path(sys.argv[1])
    deploy = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else rd.parent / "deploy"
    s = analyze(rd, deploy)
    print(table(s))
    print()
    keys = ["requests_logged", "requests_routed", "trajectories", "router_modes", "hit_rate", "cached_tokens",
            "computed_tokens", "computed_by_kind", "router_view", "kv_events", "followups", "same_engine", "reused",
            "reused_when_same_engine", "followups_by_turn",
            "moved_by_turn", "gap_p50_moved", "gap_p50_stayed", "requests_per_trajectory_hist", "per_engine", "succeeded"]
    for k in keys:
        print(f"{k:28s} {s[k]}")
    for w, e in s["engines"].items():
        print(f"{w:28s} {e}")


if __name__ == "__main__":
    main()
