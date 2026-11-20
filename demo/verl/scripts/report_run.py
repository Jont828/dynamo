#!/usr/bin/env python3
"""Print the demo's results for one finished run directory.

Validation accuracy before and after training, the training reward per step, the share of
answers in the graded "#### <number>" format, engine-vs-trainer agreement, weight updates,
KV-cache reuse per engine, timing, and one test question the model fixed. Ends with the
PASS/FAIL verdict from summarize_run.py.

Usage: python3 report_run.py RUN_DIR
"""

import json
import math
import pathlib
import re
import statistics
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from summarize_run import kv_hit_rates, parse_driver  # noqa: E402
from watch_run import FORMAT_RE, VAL_KEY_RE, bar  # noqa: E402

B, D, G, Y, R, C, X = "\x1b[1m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[0m"


def load_jsonl(path):
    rows = []
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def tail_lines(text, n=3, width=104):
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return [ln if len(ln) <= width else ln[: width - 1] + "…" for ln in lines[-n:]]


def question_of(prompt):
    match = re.search(r"user\n(.*?)(?: Let's think step by step|\nassistant)", prompt, re.S)
    return (match.group(1) if match else prompt).strip()


def section(title):
    print(f"\n{B}{C}── {title}{X}")


def main():
    run_dir = pathlib.Path(sys.argv[1]).resolve()
    steps, updates, info, _ = parse_driver(run_dir / "driver.log")
    train_steps = sorted(s for s, m in steps.items() if "critic/score/mean" in m)
    val = {s: v for s, m in steps.items() for k, v in m.items() if VAL_KEY_RE.match(k)}

    print(f"{B}Results for {run_dir.name}{X}")

    section("Held-out accuracy: GSM8K test split (1,319 questions, greedy decoding)")
    if val:
        base = val.get(0)
        for step in sorted(val):
            label = "before training" if step == 0 else f"after step {step}"
            delta = "" if base is None or step == 0 else f"  {G if val[step] >= base else R}{val[step] - base:+.3f}{X}"
            print(f"  {label:<17} {val[step]:.3f}  {G}{bar(val[step], 30)}{X}{delta}")
    else:
        print("  no validation metrics found")

    section("Training reward per step (mean score of 64 prompts × 8 sampled answers)")
    fmt = {}
    for step in train_steps:
        reward = steps[step]["critic/score/mean"]
        rows = load_jsonl(run_dir / "rollouts" / f"{step}.jsonl")
        fmt[step] = sum(bool(FORMAT_RE.search(r.get("output", ""))) for r in rows) / len(rows) if rows else None
        print(f"  step {step:>3}  {reward:.3f}  {G}{bar(reward, 30)}{X}")
    if train_steps:
        first, last = train_steps[0], train_steps[-1]
        r0, r1 = steps[first]["critic/score/mean"], steps[last]["critic/score/mean"]
        print(f"  step {first} → {last}: {r0:.3f} → {r1:.3f} ({r1 - r0:+.3f})")
        if fmt.get(first) is not None and fmt.get(last) is not None:
            print(f"  answers in the format the grader accepts ('#### <number>', no units or $): {fmt[first]:.0%} → {fmt[last]:.0%}")

    section("Is Dynamo serving exactly what the trainer expects?")
    corrs = [steps[s].get("training/rollout_actor_probs_pearson_corr") for s in train_steps]
    corrs = [c for c in corrs if c is not None and not math.isnan(c)]
    if corrs:
        print(f"  engine vs. trainer token-probability correlation: {min(corrs):.4f} – {max(corrs):.4f}\n"
              f"  {D}(1.0 = identical; slightly lower on steps written one update behind, by design of V1's async trainer){X}")
    if updates:
        enters = sorted({c['enter'] for c in updates.values()})
        errors = sum(c["error"] for c in updates.values())
        sync = [steps[s].get("timing_s/update_weights") for s in train_steps]
        sync = [x for x in sync if x is not None]
        print(f"  weight updates: {len(updates)} trainer ranks × {'/'.join(map(str, enters))} pushes into the engines, "
              f"{G if not errors else R}{errors} errors{X}"
              + (f", {statistics.mean(sync):.1f} s each on average" if sync else ""))

    section("KV-aware routing: prompt-prefix cache reuse per engine")
    kv_file = run_dir / "kv-metrics.jsonl"
    rates = kv_hit_rates(kv_file) if kv_file.exists() else {}
    if rates:
        total = sum(q for _, q in rates.values())
        for idx, (target, (rate, queries)) in enumerate(sorted(rates.items()), 1):
            print(f"  engine {idx}  hit rate {rate:5.1%}  {G}{bar(rate, 20)}{X}  {queries / 1e3:7,.0f}k prompt tokens  {D}{target}{X}")
        weighted = sum(r * q for r, q in rates.values() if not math.isnan(r)) / total if total else float("nan")
        print(f"  overall: {weighted:.1%} of prompt tokens served from cache\n"
              f"  {D}(training sends each prompt 8× and the router keeps those samples on the engine that cached it;{X}\n"
              f"  {D} validation sends each test question once, which lowers the overall rate){X}")
    else:
        print("  no engine metrics captured")

    section("Time")
    if train_steps:
        def avg(key):
            vals = [steps[s][key] for s in train_steps[1:] or train_steps if key in steps[s]]
            return statistics.mean(vals) if vals else float("nan")
        print(f"  per step (after step 1): {avg('timing_s/step'):.1f} s = rollout {avg('timing_s/gen'):.1f} s + "
              f"train {avg('timing_s/update_actor'):.1f} s + weight sync {avg('timing_s/update_weights'):.1f} s + other")
    started = run_dir / "started_at"
    status = run_dir / "STATUS"
    if started.exists() and status.exists():
        wall = int(status.stat().st_mtime - int(started.read_text().strip()))
        print(f"  wall clock, including startup, validation and teardown: {wall // 60} min {wall % 60:02d} s")

    val_steps = sorted(val)
    if len(val_steps) >= 2:
        before = {question_of(r["input"]): r for r in load_jsonl(run_dir / "val" / f"{val_steps[0]}.jsonl")}
        after = {question_of(r["input"]): r for r in load_jsonl(run_dir / "val" / f"{val_steps[-1]}.jsonl")}
        fixed = [q for q in before if q in after and before[q].get("score", 0) < 0.5 and after[q].get("score", 0) >= 0.5]
        broke = [q for q in before if q in after and before[q].get("score", 0) >= 0.5 and after[q].get("score", 0) < 0.5]
        section(f"One test question, before and after ({len(fixed)} fixed, {len(broke)} newly wrong)")
        if fixed:
            q = min(fixed, key=lambda k: len(before[k]["output"]) + len(after[k]["output"]))
            print(textwrap.fill(q, width=112, initial_indent=f"  {B}Q:{X} ", subsequent_indent="     "))
            print(f"  {B}expected:{X} {before[q].get('gts', after[q].get('gts', '?'))}")
            for label, row, color in (("before", before[q], R), (f"after step {val_steps[-1]}", after[q], G)):
                print(f"  {color}{label} (score {row.get('score', 0):.0f}){X}, last lines of the answer:")
                for ln in tail_lines(row["output"]):
                    print(f"    {D}│{X} {ln}")

    section("Verdict")
    summary = run_dir / "summary.txt"
    verdict = [ln for ln in summary.read_text().splitlines() if ln.startswith(("RESULT", "WARN", "FAIL"))] if summary.exists() else []
    for ln in verdict:
        color = G if ln == "RESULT: PASS" else (Y if ln.startswith("WARN") else R)
        print(f"  {color}{ln}{X}")
        if "orphaned" in ln:
            print(f"  {D}  known verl-recipe issue on the V1 trainer: vLLM EngineCore processes outlive the run;{X}\n"
                  f"  {D}  run-demo-train.sh already killed them (cleanup-orphans.sh), so the GPUs are free again{X}")
        if "driver traceback" in ln:
            log = (run_dir / "driver.log").read_text(errors="replace")
            if "frontend dispatch failed (request_id=logprob-probe" in log and "logprob channel probe OK" in log:
                print(f"  {D}  includes the startup logprob probe reaching the frontend before its model route was up;{X}\n"
                      f"  {D}  the recipe retried it and the probe passed, so training was unaffected{X}")
    if not verdict:
        print("  summary.txt missing")

    if train_steps:
        train_min = sum(steps[s].get("timing_s/step", 0) for s in train_steps) / 60
        parts = [f"{len(train_steps)} GRPO steps ({train_min:.0f} min of training)"]
        if len(val_steps) >= 2:
            parts.append(f"held-out GSM8K accuracy {val[val_steps[0]]:.3f} → {val[val_steps[-1]]:.3f} "
                         f"({val[val_steps[-1]] - val[val_steps[0]]:+.3f})")
        r0, r1 = steps[train_steps[0]]["critic/score/mean"], steps[train_steps[-1]]["critic/score/mean"]
        parts.append(f"training reward {r0:.3f} → {r1:.3f}")
        print()
        print(textwrap.fill("; ".join(parts) + ".", width=114, initial_indent=f"{B}Bottom line:{X} ", subsequent_indent="  "))


if __name__ == "__main__":
    main()
