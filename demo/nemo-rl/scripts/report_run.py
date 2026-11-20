#!/usr/bin/env python3
"""Print the demo's results for one finished run directory, from its run.log.

1. The Dynamo checks NeMo RL's CI runs on its Dynamo overlay (tests/functional/check_dynamo_overlay.sh):
   engines registered, requests routed, KV-cache events published and applied, no services left over.
2. How the KV router spread the requests over the engines, and how often it found part of the prompt
   already cached on the engine it picked.
3. How closely the engines' token probabilities matched the trainer's, and the refit time.
4. The training reward per step, and the time per step.
Exits 1 when a check fails, so the RayJob reports FAILED.

Usage: python3 report_run.py RUN_DIR
"""

import json
import pathlib
import re
import statistics
import subprocess
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from watch_run import (  # noqa: E402
    ENGINE_KV_RE,
    ENGINE_START_RE,
    KV_APPLIED_RE,
    Routing,
    Steps,
    bar,
    clean,
)

B, D, G, Y, R, C, X = "\x1b[1m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[0m"
ROUTES_TO_RE = re.compile(r"\[Dynamo\] frontend \S+ routes to (\d+) engine")
# CI's threshold for the overlay's token agreement (tests/functional/grpo_dp_single_controller.sh).
GEN_KL_LIMIT = 0.002


def section(title):
    print(f"\n{B}{C}── {title}{X}")


def check(ok, text, warn=False):
    """Print one check. A failed check with warn=True prints '!' and doesn't fail the run."""
    ok = bool(ok)
    mark = f"{G}✓{X}" if ok else (f"{Y}!{X}" if warn else f"{R}✗{X}")
    print(f"  {mark} {text}")
    return ok or warn


def leftover_services():
    """The processes check_dynamo_overlay.sh looks for after shutdown (same pgrep pattern)."""
    pattern = "[d]ynamo.frontend|/opt/dynamo/bin/[e]tcd|/opt/dynamo/bin/[n]ats-server"
    try:
        result = subprocess.run(["pgrep", "-af", pattern], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def main():
    run_dir = pathlib.Path(sys.argv[1]).resolve()
    log = run_dir / "run.log"
    status_file = run_dir / "STATUS"
    status = status_file.read_text().strip() if status_file.exists() else "missing"
    config_file = run_dir / "config.txt"
    config = dict(re.findall(r"^(\w+)=(.*)$", config_file.read_text(), re.M)) if config_file.exists() else {}
    expected_steps = int(config.get("steps") or 0)
    expected_engines = int(config.get("engines") or 1)

    routing, steps = Routing(), Steps()
    routes_to, kv_publishing, applied, model = 0, [], {}, None
    for raw in log.read_text(errors="replace").splitlines() if log.exists() else []:
        line = clean(raw)
        routing.feed(line)
        steps.feed(line)
        if m := ROUTES_TO_RE.search(line):
            routes_to = int(m[1])
        if m := ENGINE_KV_RE.search(line):
            kv_publishing.append(m[1] == "True")
        if m := KV_APPLIED_RE.search(line):
            applied = json.loads(m[1])
        if model is None and (m := ENGINE_START_RE.search(line)):
            model = m[2]
    rows = steps.rows
    print(f"{B}Results for {run_dir.name}{X}  {D}({config.get('config', '?')}, {config.get('gpus', '?')} GPUs){X}")

    section("Dynamo checks, the ones NeMo RL's CI runs on its Dynamo overlay")
    ok = True
    ok &= check(routes_to >= expected_engines,
                f"engines registered: the frontend routes to {routes_to} engine(s)")
    ok &= check(routing.completed > 0, f"requests the frontend routed: {routing.completed:,}")
    ok &= check(kv_publishing and all(kv_publishing),
                f"engines publishing KV-cache events: {sum(kv_publishing)} of {len(kv_publishing)}")
    errors = sum(v for k, v in applied.items() if k != "ok")
    ok &= check(applied.get("ok", 0) > 0,
                f"KV-cache events the router applied: {applied.get('ok', 0):,.0f}  {D}(errors: {errors:,.0f}){X}")
    left = leftover_services()
    if left is None:
        check(False, "leftover services: pgrep not available", warn=True)
    else:
        ok &= check(not left, "no Dynamo services left running after shutdown" if not left else f"left running: {left[:3]}")

    total = sum(routing.selected.values())
    section(f"KV-aware routing: {total:,} routing decisions ({', '.join(sorted(routing.modes)) or '-'} mode)")
    for idx, worker in enumerate(routing.order, 1):
        count = routing.selected.get(worker, 0)
        share = count / total if total else 0
        print(f"  engine {idx}  {count:>6,} requests  {G}{bar(share, 30)}{X} {share:5.1%}  {D}worker_id {worker}{X}")
    if routing.completed:
        print(f"  {routing.cached:,} of {routing.completed:,} requests ({routing.cached / routing.completed:.0%}) went to an engine"
              f" that already held part of their prompt in its KV cache;\n"
              f"  {routing.overlap_blocks:,} cached prompt KV blocks found on the chosen engine in total (overlap_blocks)\n"
              f"  {D}(GRPO sends each prompt 16 times; the router sends a prompt's samples to where its prefix is cached){X}")

    section("Do the engines serve exactly the trainer's policy?")
    kl = [r["metrics"]["gen_kl_error"] for r in rows if "gen_kl_error" in r["metrics"]]
    pe = [r["metrics"]["token_mult_prob_error"] for r in rows if "token_mult_prob_error" in r["metrics"]]
    sync = [r["timing"]["weight_sync"] for r in rows if "weight_sync" in r["timing"]]
    if kl:
        ok &= check(max(kl) < GEN_KL_LIMIT,
                    f"gen_kl_error (KL between the engines' and the trainer's token probabilities): "
                    f"{min(kl):.5f} – {max(kl):.5f}  {D}(CI requires < {GEN_KL_LIMIT}){X}", warn=True)
    if pe:
        print(f"  token_mult_prob_error: {min(pe):.4f} – {max(pe):.4f}  {D}(1.0 = identical){X}")
    if sync:
        print(f"  refits (the Megatron trainer's new weights into both engines over NCCL): {len(sync)}, "
              f"{statistics.mean(sync):.2f} s each on average")

    section("Training reward per step (mean over 32 prompts × 16 sampled answers)")
    rewards = [(r["step"], r["metrics"]["reward"]) for r in rows if "reward" in r["metrics"]]
    for step, reward in rewards:
        print(f"  step {step:>3}  {reward:.3f}  {G}{bar(reward, 30)}{X}")
    if len(rewards) >= 2:
        first, last = rewards[0][1], rewards[-1][1]
        k = min(3, len(rewards) // 2)
        head = statistics.mean(r for _, r in rewards[:k])
        tail = statistics.mean(r for _, r in rewards[-k:])
        print(f"  step {rewards[0][0]} → {rewards[-1][0]}: {first:.3f} → {last:.3f} ({last - first:+.3f}); "
              f"mean of the first {k} steps {head:.3f}, of the last {k} {tail:.3f}")
    ok &= check(rows and len(rows) >= expected_steps, f"{len(rows)} of {expected_steps or '?'} training steps completed")

    section("Time")
    if rows:
        def avg(key):
            vals = [r["timing"][key] for r in (rows[1:] or rows) if key in r["timing"]]
            return statistics.mean(vals) if vals else float("nan")
        print(f"  per step (after step 1): {avg('Total step time'):.1f} s; the trainer waited {avg('exposed_generation'):.1f} s "
              f"for rollouts, trained {avg('policy_training'):.1f} s, refit {avg('weight_sync'):.2f} s")
    started = run_dir / "started_at"
    if started.exists() and status_file.exists():
        wall = int(status_file.stat().st_mtime - int(started.read_text().strip()))
        print(f"  training process, startup included: {wall // 60} min {wall % 60:02d} s")

    section("Verdict")
    ok &= check(status == "done rc=0", f"training process exit: {status}")
    print(f"  {G + 'RESULT: PASS' if ok else R + 'RESULT: FAIL'}{X}")
    if rewards:
        parts = [f"{len(rows)} GRPO steps of {model or 'the policy'} on OpenMathInstruct-2",
                 f"training reward {rewards[0][1]:.3f} → {rewards[-1][1]:.3f}",
                 f"{routing.completed:,} rollouts routed by Dynamo's KV router over {len(routing.order)} vLLM engines"]
        print()
        print(textwrap.fill("; ".join(parts) + ".", width=114, initial_indent=f"{B}Bottom line:{X} ", subsequent_indent="  "))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
