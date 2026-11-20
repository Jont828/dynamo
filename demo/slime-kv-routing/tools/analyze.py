"""Parse the Slime + Dynamo runs and compare routing modes.

Usage:
  python -I analyze.py run <run-dir>              # one run -> <run-dir>/summary.json
  python -I analyze.py compare <out.md> <label=run-dir>...

A run dir holds driver.log, frontend.log, worker-0.log, worker-1.log,
rayjob.yaml, and frontend-metrics-{before,after}.txt (tools/run_phase.sh
writes them). For older runs that saved only some of these, the missing
parts are reported as None.
"""

import ast
import json
import math
import pathlib
import re
import statistics
import sys
from datetime import datetime, timezone

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
ISO = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z")


def clean(line: str) -> str:
    return ANSI.sub("", line)


def ts(line: str):
    m = TS.search(line)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def dict_after(line: str, marker: str) -> dict:
    return ast.literal_eval(line.split(marker, 1)[1].strip())


def parse_driver(path: pathlib.Path) -> dict:
    out = {
        "rollout_perf": {},
        "rollout": {},
        "train": {},
        "train_perf": {},
        "sync_secs": [],
        "sync_end": [],
        "events": {},
    }
    if not path.exists():
        return out
    for raw in path.read_text(errors="replace").splitlines():
        line = clean(raw)
        t = ts(line)
        m = re.search(r"rollout_metrics\.py:\d+ - perf (\d+): ", line)
        if m:
            out["rollout_perf"][int(m.group(1))] = (t, dict_after(line, m.group(0)))
            continue
        m = re.search(r"train_metric_utils\.py:\d+ - perf (\d+): ", line)
        if m:
            out["train_perf"][int(m.group(1))] = (t, dict_after(line, m.group(0)))
            continue
        m = re.search(r"train_metric_utils\.py:\d+ - rollout (\d+): ", line)
        if m:
            out["rollout"][int(m.group(1))] = (t, dict_after(line, m.group(0)))
            continue
        m = re.search(r"model\.py:\d+ - step (\d+): ", line)
        if m:
            out["train"][int(m.group(1))] = (t, dict_after(line, m.group(0)))
            continue
        m = re.search(r"Timer update_weights end \(elapsed: ([0-9.]+)s\)", line)
        if m:
            out["sync_secs"].append(float(m.group(1)))
            out["sync_end"].append(t)
            continue
        if "Detected external SGLang engines" in line and t:
            out["events"].setdefault("engines_detected", t)
        elif "Router launched at" in line and t:
            out["events"].setdefault("router_launched", t)
        elif re.search(r"Job '[^']+' succeeded", line):
            out["events"]["succeeded_line"] = re.search(r"Job '([^']+)' succeeded", line).group(1)
        if t:
            out["events"]["last_ts"] = t
            out["events"].setdefault("first_ts", t)
    return out


def parse_selected(path: pathlib.Path) -> dict:
    """Routing decisions from the frontend log."""
    res = {"modes": {}, "per_worker": {}, "times": [], "workers": [], "cached_blocks": []}
    if not path.exists():
        return res
    for raw in path.read_text(errors="replace").splitlines():
        if "Selected worker" not in raw:
            continue
        line = clean(raw)
        mode = re.search(r'router_mode="?([a-z-]+)"?', line)
        wid = re.search(r"worker_id=(\d+)", line)
        t = ISO.search(line)
        mode = mode.group(1) if mode else "?"
        wid = wid.group(1) if wid else "?"
        res["modes"][mode] = res["modes"].get(mode, 0) + 1
        res["per_worker"][wid] = res["per_worker"].get(wid, 0) + 1
        res["workers"].append(wid)
        res["times"].append(datetime.fromisoformat(t.group(1)).replace(tzinfo=timezone.utc) if t else None)
        cb = re.search(r"effective_cached_blocks=([0-9.]+)", line)
        res["cached_blocks"].append(float(cb.group(1)) if cb else None)
    return res


def bursts(times, workers, gap=1.0):
    """Split routing decisions into rollouts: a gap of more than `gap` seconds starts a new burst."""
    groups = []
    last = None
    for t, w in zip(times, workers):
        if t is None:
            continue
        if last is None or (t - last).total_seconds() > gap:
            groups.append({})
        groups[-1][w] = groups[-1].get(w, 0) + 1
        last = t
    return groups


def parse_engine(path: pathlib.Path) -> dict:
    res = {"prefill_new": 0, "prefill_cached": 0, "prefill_batches": 0, "reloads": 0, "generate_200": 0}
    if not path.exists():
        return {k: None for k in res}
    for raw in path.read_text(errors="replace").splitlines():
        line = clean(raw)
        m = re.search(r"Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+)", line)
        if m:
            seqs, new, cached = (int(x) for x in m.groups())
            # Skip the readiness probe's 1-token generations.
            if seqs == 1 and new + cached <= 8:
                continue
            res["prefill_batches"] += 1
            res["prefill_new"] += new
            res["prefill_cached"] += cached
        elif 'POST /update_weights_from_disk HTTP/1.1" 200' in line:
            res["reloads"] += 1
        elif 'POST /generate HTTP/1.1" 200' in line:
            res["generate_200"] += 1
    return res


def parse_prom(path: pathlib.Path) -> dict:
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


def prom_delta(before: dict, after: dict, name: str, want=None) -> float | None:
    total, seen = 0.0, False
    for (n, labels), v in after.items():
        if n != name:
            continue
        if want and not all((k, val) in labels for k, val in want.items()):
            continue
        seen = True
        total += v - before.get((n, labels), 0.0)
    return total if seen else None


def hist_quantile(before: dict, after: dict, name: str, q: float, want=None) -> float | None:
    buckets = {}
    for (n, labels), v in after.items():
        if n != f"{name}_bucket":
            continue
        if want and not all((k, val) in labels for k, val in want.items()):
            continue
        le = dict(labels).get("le")
        d = v - before.get((n, labels), 0.0)
        buckets[le] = buckets.get(le, 0.0) + d
    if not buckets:
        return None
    pts = sorted(((math.inf if le == "+Inf" else float(le)), c) for le, c in buckets.items())
    total = pts[-1][1]
    if total <= 0:
        return None
    target = q * total
    prev_le, prev_c = 0.0, 0.0
    for le, c in pts:
        if c >= target:
            if math.isinf(le):
                return prev_le
            frac = (target - prev_c) / (c - prev_c) if c > prev_c else 0.0
            return prev_le + frac * (le - prev_le)
        prev_le, prev_c = le, c
    return None


def _ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def rayjob_times(path: pathlib.Path):
    """RayJob created -> finished, and the Ray job's own submit -> finish."""
    if not path.exists():
        return None, None, None, None
    import yaml

    status = (yaml.safe_load(path.read_text()) or {}).get("status") or {}
    info = status.get("rayJobInfo") or {}
    return (_ts(status.get("startTime")), _ts(status.get("endTime")),
            _ts(info.get("startTime")), _ts(info.get("endTime")))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def analyze_run(rd: pathlib.Path, driver_name="driver.log", frontend_name="frontend.log") -> dict:
    drv = parse_driver(rd / driver_name)
    sel = parse_selected(rd / frontend_name)
    w0 = parse_engine(rd / "worker-0.log")
    w1 = parse_engine(rd / "worker-1.log")
    before = parse_prom(rd / "frontend-metrics-before.txt")
    after = parse_prom(rd / "frontend-metrics-after.txt")
    start, end, ray_start, ray_end = rayjob_times(rd / "rayjob.yaml")
    steps = sorted(drv["rollout_perf"])
    per_step = []
    for k in steps:
        rp = drv["rollout_perf"][k][1]
        ro = drv["rollout"].get(k, (None, {}))[1]
        tr = drv["train"].get(k, (None, {}))[1]
        tp = drv["train_perf"].get(k, (None, {}))[1]
        per_step.append(
            {
                "step": k,
                "rollout_time": rp.get("perf/rollout_time"),
                "prefix_cache_hit_rate": rp.get("rollout/prefix_cache_hit_rate"),
                "avg_cached_tokens_per_sample": rp.get("rollout/avg_cached_tokens_per_sample"),
                "response_len_mean": rp.get("rollout/response_len/mean"),
                "response_len_max": rp.get("rollout/response_len/max"),
                "tokens_per_gpu_per_sec": rp.get("perf/tokens_per_gpu_per_sec"),
                "longest_sample_tokens_per_sec": rp.get("perf/longest_sample_tokens_per_sec"),
                "raw_reward": ro.get("rollout/raw_reward"),
                "total_len_mean": ro.get("rollout/total_lengths"),
                "logprob_diff": tr.get("train/train_rollout_logprob_abs_diff"),
                "grad_norm": tr.get("train/grad_norm"),
                "update_weights_time": tp.get("perf/update_weights_time"),
                "train_wait_time": tp.get("perf/train_wait_time"),
                "actor_train_time": tp.get("perf/actor_train_time"),
                "step_time": tp.get("perf/step_time"),
            }
        )
    ev = drv["events"]
    rel = (lambda t: round((t - start).total_seconds()) if (t and start) else None)
    timeline = {
        "engines_detected": rel(ev.get("engines_detected")),
        "first_sync_end": rel(drv["sync_end"][0]) if drv["sync_end"] else None,
        "rollout_lines": [rel(drv["rollout"][k][0]) for k in sorted(drv["rollout"])],
        "train_lines": [rel(drv["train"][k][0]) for k in sorted(drv["train"])],
        "sync_ends": [rel(t) for t in drv["sync_end"][1:]],
        "last_driver_ts": rel(ev.get("last_ts")),
        "job_end": rel(end),
    }
    later = [s for s in per_step if s["step"] >= 1]
    ttft_want = None
    summary = {
        "run_dir": str(rd),
        "rayjob_start": start.isoformat() if start else None,
        "rayjob_end": end.isoformat() if end else None,
        "job_secs": (end - start).total_seconds() if (start and end) else None,
        # The Ray job alone: submit -> finish. Excludes Ray head scheduling and the
        # submitter Pod's image pull, which is not pinned to the measured node.
        "ray_job_secs": (ray_end - ray_start).total_seconds() if (ray_start and ray_end) else None,
        "succeeded_job": ev.get("succeeded_line"),
        "steps": per_step,
        "sync_secs": drv["sync_secs"],
        "timeline": timeline,
        "routing": {
            "requests": len(sel["workers"]),
            "modes": sel["modes"],
            "per_worker": sel["per_worker"],
            "bursts": bursts(sel["times"], sel["workers"]),
            "mean_effective_cached_blocks": mean(sel["cached_blocks"]),
        },
        "engines": {"worker-0": w0, "worker-1": w1},
        "frontend": {
            "requests": prom_delta(before, after, "dynamo_request_plane_roundtrip_ttft_seconds_count"),
            "ttft_mean": None,
            "ttft_p50": hist_quantile(before, after, "dynamo_request_plane_roundtrip_ttft_seconds", 0.5, ttft_want),
            "ttft_p90": hist_quantile(before, after, "dynamo_request_plane_roundtrip_ttft_seconds", 0.9, ttft_want),
            "router_kv_hit_rate_mean": None,
        },
        "agg": {
            "rollout_time_mean_all": mean([s["rollout_time"] for s in per_step]),
            "rollout_time_mean_1_9": mean([s["rollout_time"] for s in later]),
            "rollout_time_sum": sum(s["rollout_time"] or 0 for s in per_step),
            "hit_rate_mean": mean([s["prefix_cache_hit_rate"] for s in per_step]),
            "cached_tokens_per_sample_mean": mean([s["avg_cached_tokens_per_sample"] for s in per_step]),
            "tokens_per_gpu_per_sec_mean": mean([s["tokens_per_gpu_per_sec"] for s in per_step]),
            "longest_sample_tok_s_mean_1_9": mean([s["longest_sample_tokens_per_sec"] for s in later]),
            "step_time_mean_1_9": mean([s["step_time"] for s in later]),
            "update_weights_mean_1_9": mean([s["update_weights_time"] for s in later]),
            "logprob_diff_min": min((s["logprob_diff"] for s in per_step if s["logprob_diff"] is not None), default=None),
            "logprob_diff_max": max((s["logprob_diff"] for s in per_step if s["logprob_diff"] is not None), default=None),
            "reward_mean": mean([s["raw_reward"] for s in per_step]),
            "response_len_mean": mean([s["response_len_mean"] for s in per_step]),
        },
    }
    s = prom_delta(before, after, "dynamo_request_plane_roundtrip_ttft_seconds_sum")
    c = summary["frontend"]["requests"]
    if s is not None and c:
        summary["frontend"]["ttft_mean"] = s / c
    hs = prom_delta(before, after, "dynamo_component_router_kv_hit_rate_sum")
    hc = prom_delta(before, after, "dynamo_component_router_kv_hit_rate_count")
    if hs is not None and hc:
        summary["frontend"]["router_kv_hit_rate_mean"] = hs / hc
    eng_new = [e["prefill_new"] for e in (w0, w1) if e["prefill_new"] is not None]
    eng_cached = [e["prefill_cached"] for e in (w0, w1) if e["prefill_cached"] is not None]
    summary["agg"]["engine_prefill_new_tokens"] = sum(eng_new) if eng_new else None
    summary["agg"]["engine_prefill_cached_tokens"] = sum(eng_cached) if eng_cached else None
    return summary


def fmt(x, nd=3, pct=False):
    if x is None:
        return "–"
    if pct:
        return f"{100 * x:.1f}%"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def compare(out_path: pathlib.Path, labeled_dirs: list[str]) -> None:
    rows = []
    for item in labeled_dirs:
        label, d = item.split("=", 1)
        js = pathlib.Path(d) / "summary.json"
        rows.append((label, json.loads(js.read_text())))
    lines = [
        "| Run | Requests per engine | Prefix-cache hit rate | Cached tokens / sample | Rollout time, steps 1–9 (s) | Step time, steps 1–9 (s) | TTFT p50 / p90 (ms) | Engine prefill tokens | RayJob (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, s in rows:
        pw = "/".join(str(v) for v in sorted(s["routing"]["per_worker"].values(), reverse=True)) or "–"
        a, f = s["agg"], s["frontend"]
        ttft = (
            f"{1000 * f['ttft_p50']:.0f} / {1000 * f['ttft_p90']:.0f}"
            if f.get("ttft_p50") is not None and f.get("ttft_p90") is not None
            else "–"
        )
        lines.append(
            f"| {label} | {pw} | {fmt(a['hit_rate_mean'], pct=True)} | {fmt(a['cached_tokens_per_sample_mean'], 1)} | "
            f"{fmt(a['rollout_time_mean_1_9'], 2)} | {fmt(a['step_time_mean_1_9'], 2)} | {ttft} | "
            f"{fmt(a['engine_prefill_new_tokens'])} | {fmt(s['job_secs'], 0)} |"
        )
    out_path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def _stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {
        "n": len(xs),
        "mean": statistics.fmean(xs),
        "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0,
        "median": statistics.median(xs),
        "min": min(xs),
        "max": max(xs),
    }


def phase(out_path: pathlib.Path, run_dirs: list[str]) -> None:
    """Pool steps 1-9 of several runs of one configuration."""
    runs = [json.loads((pathlib.Path(d) / "summary.json").read_text()) for d in run_dirs]
    steps = [st for r in runs for st in r["steps"]]
    later = [st for st in steps if st["step"] >= 1]
    before_after = [
        (parse_prom(pathlib.Path(d) / "frontend-metrics-before.txt"), parse_prom(pathlib.Path(d) / "frontend-metrics-after.txt"))
        for d in run_dirs
    ]
    # Pool the TTFT histogram deltas across runs.
    pooled_b, pooled_a = {}, {}
    for b, a in before_after:
        for key, v in a.items():
            pooled_a[key] = pooled_a.get(key, 0.0) + v
            pooled_b[key] = pooled_b.get(key, 0.0) + b.get(key, 0.0)
    ttft_n = prom_delta(pooled_b, pooled_a, "dynamo_request_plane_roundtrip_ttft_seconds_count")
    ttft_s = prom_delta(pooled_b, pooled_a, "dynamo_request_plane_roundtrip_ttft_seconds_sum")
    out = {
        "runs": [r["run_dir"] for r in runs],
        "job_secs": _stats([r["job_secs"] for r in runs]),
        "ray_job_secs": _stats([r.get("ray_job_secs") for r in runs]),
        "hit_rate_all_steps": _stats([st["prefix_cache_hit_rate"] for st in steps]),
        "hit_rate_steps_1_9": _stats([st["prefix_cache_hit_rate"] for st in later]),
        "cached_tokens_per_sample": _stats([st["avg_cached_tokens_per_sample"] for st in steps]),
        "rollout_time_1_9": _stats([st["rollout_time"] for st in later]),
        "rollout_time_step0": _stats([st["rollout_time"] for st in steps if st["step"] == 0]),
        "longest_sample_tok_s_1_9": _stats([st["longest_sample_tokens_per_sec"] for st in later]),
        "tokens_per_gpu_per_sec_1_9": _stats([st["tokens_per_gpu_per_sec"] for st in later]),
        "response_len_max_1_9": _stats([st["response_len_max"] for st in later]),
        "response_len_mean": _stats([st["response_len_mean"] for st in steps]),
        "step_time_1_9": _stats([st["step_time"] for st in later]),
        "update_weights_1_9": _stats([st["update_weights_time"] for st in later]),
        "logprob_diff": _stats([st["logprob_diff"] for st in steps]),
        "raw_reward": _stats([st["raw_reward"] for st in steps]),
        "engine_prefill_new_tokens": _stats([r["agg"]["engine_prefill_new_tokens"] for r in runs]),
        "engine_prefill_cached_tokens": _stats([r["agg"]["engine_prefill_cached_tokens"] for r in runs]),
        "requests": [r["routing"]["requests"] for r in runs],
        "per_worker": [sorted(r["routing"]["per_worker"].values(), reverse=True) for r in runs],
        "router_modes": [r["routing"]["modes"] for r in runs],
        "burst_imbalance": _stats(
            [abs(max(b.values()) - min(b.values())) if len(b) == 2 else (max(b.values()) if b else None)
             for r in runs for b in r["routing"]["bursts"]]
        ),
        "bursts_multiple_of_8": sum(
            1 for r in runs for b in r["routing"]["bursts"] if all(v % 8 == 0 for v in b.values())
        ),
        "bursts_total": sum(len(r["routing"]["bursts"]) for r in runs),
        "ttft_mean": (ttft_s / ttft_n) if (ttft_s is not None and ttft_n) else None,
        "ttft_p50": hist_quantile(pooled_b, pooled_a, "dynamo_request_plane_roundtrip_ttft_seconds", 0.5),
        "ttft_p90": hist_quantile(pooled_b, pooled_a, "dynamo_request_plane_roundtrip_ttft_seconds", 0.9),
        "reloads": [[r["engines"]["worker-0"]["reloads"], r["engines"]["worker-1"]["reloads"]] for r in runs],
    }
    out_path.write_text(json.dumps(out, indent=2, default=str))

    def ms(k):
        v = out[k]
        return "–" if not v else f"{v['mean']:.3f} ± {v['stdev']:.3f} (n={v['n']})"

    for k in ["job_secs", "ray_job_secs", "hit_rate_all_steps", "hit_rate_steps_1_9", "cached_tokens_per_sample", "rollout_time_1_9",
              "rollout_time_step0", "longest_sample_tok_s_1_9", "tokens_per_gpu_per_sec_1_9", "response_len_max_1_9",
              "step_time_1_9", "update_weights_1_9", "logprob_diff", "raw_reward", "engine_prefill_new_tokens",
              "engine_prefill_cached_tokens", "burst_imbalance"]:
        print(f"{k:32s} {ms(k)}")
    print(f"{'ttft mean/p50/p90 (ms)':32s} "
          + " / ".join("–" if out[k] is None else f"{1000 * out[k]:.0f}" for k in ["ttft_mean", "ttft_p50", "ttft_p90"]))
    print(f"{'requests per worker':32s} {out['per_worker']}  modes {out['router_modes']}")
    print(f"{'bursts split in multiples of 8':32s} {out['bursts_multiple_of_8']} of {out['bursts_total']}")
    print(f"{'reloads per engine':32s} {out['reloads']}")


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "run":
        rd = pathlib.Path(sys.argv[2])
        driver = sys.argv[3] if len(sys.argv) > 3 else "driver.log"
        frontend = sys.argv[4] if len(sys.argv) > 4 else "frontend.log"
        s = analyze_run(rd, driver, frontend)
        (rd / "summary.json").write_text(json.dumps(s, indent=2, default=str))
        a = s["agg"]
        print(
            f"{rd.name}: job {fmt(s['job_secs'], 0)}s (ray job {fmt(s['ray_job_secs'], 0)}s), requests {s['routing']['requests']} {s['routing']['per_worker']}, "
            f"hit {fmt(a['hit_rate_mean'], pct=True)}, rollout(1-9) {fmt(a['rollout_time_mean_1_9'], 2)}s, "
            f"step(1-9) {fmt(a['step_time_mean_1_9'], 2)}s, logprob {fmt(a['logprob_diff_min'], 4)}-{fmt(a['logprob_diff_max'], 4)}, "
            f"reloads {s['engines']['worker-0']['reloads']}/{s['engines']['worker-1']['reloads']}"
        )
    elif cmd == "compare":
        compare(pathlib.Path(sys.argv[2]), sys.argv[3:])
    elif cmd == "phase":
        phase(pathlib.Path(sys.argv[2]), sys.argv[3:])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
