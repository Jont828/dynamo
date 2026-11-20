#!/usr/bin/env python3
"""Summarize one verl + Dynamo run directory into pass/fail evidence.

Reads <run_dir>/driver.log, <run_dir>/dynamo-logs/, and <run_dir>/kv-metrics.jsonl
(written by the run-*.sh scripts) and reports the doc's three boundaries:
generation correctness, policy update, and routing/cache behavior.

Usage: python3 summarize_run.py RUN_DIR [--expect-steps N] [--expect-ranks N]
"""

import argparse
import collections
import json
import math
import pathlib
import re
import subprocess
import sys
import time

STEP_RE = re.compile(r"\bstep:(\d+) - (.*)$")
NUMBER_RE = re.compile(r"^(?:np\.float\d*\()?([-+0-9.eE]+|nan|inf|-inf)\)?$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DATALOADER_KILL_RE = re.compile(r"RuntimeError: DataLoader worker \(pid \d+\) is killed by signal: Killed")
# The recipe's `rank` is the rank inside one TP group, so it repeats across replicas;
# key evidence by the Ray worker process instead.
# Recipe 461b830c tagged these `[v4a-4]`; recipe main tags them `[dynamo-vllm]` and logs at DEBUG.
UPDATE_RE = re.compile(r"(?:\((\w+) pid=(\d+)(?:, ip=([^)]+))?\)\s*)?\[(?:v4a-4|dynamo-vllm)\]\[rank=(\d+)\] (.*)$")
POOL_RE = re.compile(r"\[DynamoReplica pool\] ready: server_address=(\S+) logical_replicas=(\d+) workers=(\d+) nodes=(\d+)")
HEALTHY_RE = re.compile(r"frontend healthy: (\d+)/(\d+) workers registered")
SELFTEST_RE = re.compile(r"refit self-test (PASSED|FAILED)")
STEP_KEYS = {
    "timing_s/gen": "gen_s",
    "timing_s/update_actor": "update_actor_s",
    "timing_s/update_weights": "update_wts_s",
    "timing_s/step": "step_s",
    "response_length/mean": "resp_len",
    "critic/score/mean": "score",
    "training/rollout_probs_diff_mean": "p_diff_mean",
    "training/rollout_probs_diff_max": "p_diff_max",
    "training/rollout_actor_probs_pearson_corr": "p_corr",
}


def parse_value(text):
    match = NUMBER_RE.match(text.strip())
    return float(match.group(1)) if match else None


def parse_driver(path):
    steps, updates, info = {}, collections.defaultdict(collections.Counter), collections.defaultdict(list)
    tracebacks = ignored = 0
    previous, last_traceback = "", None
    for raw in path.read_text(errors="replace").splitlines():
        line = ANSI_RE.sub("", raw)
        if "Traceback (most recent call last)" in line:
            # "Exception ignored in ... __del__" tracebacks are teardown noise (e.g. DataLoader workers).
            if "Exception ignored in" in previous:
                ignored += 1
                last_traceback = "ignored"
            else:
                tracebacks += 1
                last_traceback = "counted"
        elif DATALOADER_KILL_RE.search(line) and last_traceback == "counted":
            # Ray teardown SIGKILLs torch DataLoader workers; torch re-raises from its SIGCHLD handler.
            tracebacks -= 1
            ignored += 1
            last_traceback = "ignored"
        previous = line
        if match := STEP_RE.search(line):
            metrics = {}
            for item in match.group(2).split(" - "):
                key, _, raw_value = item.partition(":")
                value = parse_value(raw_value)
                if value is not None:
                    metrics[key.strip()] = value
            steps[int(match.group(1))] = metrics
        if match := UPDATE_RE.search(line):
            _, pid, ip, rank, message = match.groups()
            worker = f"{ip or 'local'}/pid{pid}" if pid else f"rank{rank}"
            if message.startswith("ENTER update_weights"):
                updates[worker]["enter"] += 1
            elif "ERROR" in message or "TIMEOUT" in message:
                updates[worker]["error"] += 1
                info["update_errors"].append(line.strip())
            elif "completed OK" in message or "sender DONE" in message:
                updates[worker]["done"] += 1
            elif message.startswith("kv cache cleared"):
                updates[worker]["cleared"] += 1
        for name, regex in (("pool", POOL_RE), ("healthy", HEALTHY_RE), ("selftest", SELFTEST_RE)):
            if match := regex.search(line):
                info[name].append(match.groups())
        if "PASS: Dynamo validation smoke completed" in line or "PASS: Dynamo V1 colocate_async smoke completed" in line:
            info["smoke_pass"].append(True)
    info["ignored_tracebacks"] = [ignored]
    return steps, updates, info, tracebacks


def find_orphans():
    """Live (non-zombie) Dynamo/vLLM/etcd/NATS processes re-parented to PID 1."""
    pattern = re.compile(
        r"VLLM::|_dynamo_vllm_with_control|dynamo\.frontend|dynamo\.vllm|(^|[ /])etcd( |$)|nats-server"
    )
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,stat=,args="], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    orphans = []
    for row in out.splitlines():
        parts = row.split(None, 3)
        if len(parts) == 4 and parts[1] == "1" and not parts[2].startswith("Z") and pattern.search(parts[3]):
            orphans.append(f"pid {parts[0]}: {parts[3][:100]}")
    return orphans


def kv_hit_rates(path):
    """Hit rate per worker from cumulative counters at the last snapshot.

    Engines are spawned per run, so counters start at zero; deltas from each worker's first
    non-empty snapshot undercount workers the sidecar discovered one scrape later.
    """
    last = {}
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        metrics = row.get("metrics") or {}
        if "vllm:prefix_cache_queries_total" not in metrics:
            continue
        last[row["target"]] = (metrics["vllm:prefix_cache_hits_total"], metrics["vllm:prefix_cache_queries_total"])
    return {target: (hits / queries if queries > 0 else float("nan"), queries) for target, (hits, queries) in last.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=pathlib.Path)
    parser.add_argument("--expect-steps", type=int)
    parser.add_argument("--expect-ranks", type=int)
    parser.add_argument(
        "--orphan-grace", type=float, default=-1, help="seconds to wait, then report orphaned processes (-1: skip)"
    )
    args = parser.parse_args()

    failures, warnings = [], []
    driver = args.run_dir / "driver.log"
    if not driver.exists():
        sys.exit(f"no driver.log in {args.run_dir}")
    steps, updates, info, tracebacks = parse_driver(driver)

    print(f"== {args.run_dir}")
    for address, replicas, workers, nodes in info["pool"]:
        print(f"Dynamo pool: frontend={address} workers={workers} nodes={nodes} logical_replicas={replicas}")
    for registered, expected in info["healthy"]:
        print(f"Frontend health: {registered}/{expected} workers registered")
    if info["selftest"]:
        print(f"Refit self-test (first node-0 shard only): {', '.join(g[0] for g in info['selftest'])}")
        if any(g[0] == "FAILED" for g in info["selftest"]):
            failures.append("refit self-test failed")
    if info["smoke_pass"]:
        print("Smoke: PASS line found")

    print("\n-- Per-step metrics (1: generation correctness, 3: rollout time)")
    print("step  " + "  ".join(label.rjust(14) for label in STEP_KEYS.values()))
    for step in sorted(steps):
        print(f"{step:<5} " + "  ".join(f"{steps[step].get(k, float('nan')):14.4g}" for k in STEP_KEYS))
    if args.expect_steps and max(steps, default=0) < args.expect_steps:
        failures.append(f"completed {max(steps, default=0)}/{args.expect_steps} training steps")
    if args.expect_steps and not any(
        not math.isnan(m.get("training/rollout_actor_probs_pearson_corr", float("nan"))) for m in steps.values()
    ):
        warnings.append("no rollout-vs-actor logprob metrics: generation correctness is unverified (rollout returned no logprobs?)")
    for step, metrics in sorted(steps.items()):
        corr = metrics.get("training/rollout_actor_probs_pearson_corr")
        diff = metrics.get("training/rollout_probs_diff_mean")
        if corr is not None and not math.isnan(corr):
            if corr < 0.8:
                failures.append(f"step {step}: rollout/actor prob correlation {corr:.3f} (<0.8: token or logprob misalignment)")
            elif corr < 0.95:
                warnings.append(f"step {step}: rollout/actor prob correlation {corr:.3f} (<0.95)")
        if diff is not None and diff > 0.02:
            warnings.append(f"step {step}: mean |p_rollout - p_actor| {diff:.4f} (>0.02)")

    print("\n-- Policy update (2): update_weights calls per trainer process")
    if updates:
        enters = {worker: c["enter"] for worker, c in updates.items()}
        errors = sum(c["error"] for c in updates.values())
        done = sum(c["done"] for c in updates.values())
        cleared = sum(c["cleared"] for c in updates.values())
        print(f"trainer processes={len(updates)} update calls each={sorted(set(enters.values()))} "
              f"sends completed={done} kv-cache clears={cleared} errors/timeouts={errors}")
        if errors:
            failures.append(f"{errors} update_weights errors/timeouts")
            print("\n".join(info["update_errors"][:5]))
        if len(set(enters.values())) > 1:
            failures.append(f"uneven update_weights calls across trainer processes: {dict(sorted(enters.items()))}")
        if args.expect_ranks and len(updates) != args.expect_ranks:
            failures.append(f"update_weights seen on {len(updates)}/{args.expect_ranks} trainer processes")
    elif args.expect_steps:
        failures.append("no update_weights log lines found")
    else:
        print("none (expected for the validation-only smoke)")

    kv_file = args.run_dir / "kv-metrics.jsonl"
    print("\n-- Engine prefix-cache hit rate per worker (3: routing)")
    if kv_file.exists() and (rates := kv_hit_rates(kv_file)):
        total_q = sum(q for _, q in rates.values())
        weighted = sum(r * q for r, q in rates.values() if not math.isnan(r)) / total_q if total_q else float("nan")
        for target, (rate, queries) in sorted(rates.items()):
            print(f"  {target}: hit_rate={rate:.3f} queries={queries:.0f}")
        print(f"  aggregate hit_rate={weighted:.3f} over {len(rates)} workers")
        busy = [q for _, q in rates.values()]
        if busy and min(busy) == 0:
            warnings.append("some workers served no prefix-cache queries (check eligibility/routing)")
    else:
        print("  no worker metrics captured (enable_worker_system_metrics / sidecar)")

    print("\n-- Errors")
    dyn_logs = sorted((args.run_dir / "dynamo-logs").rglob("*.log"))
    noisy = []
    for log in dyn_logs:
        text = log.read_text(errors="replace")
        if "Traceback (most recent call last)" in text or "panicked at" in text:
            noisy.append(str(log.relative_to(args.run_dir)))
    print(f"driver tracebacks={tracebacks} (plus {info['ignored_tracebacks'][0]} teardown-noise tracebacks: "
          f"__del__ / DataLoader worker SIGKILL); dynamo logs={len(dyn_logs)} with tracebacks/panics={len(noisy)}")
    for log in noisy[:10]:
        print(f"  {log}")
    if noisy:
        warnings.append(f"{len(noisy)} Dynamo subprocess logs contain tracebacks/panics")
    if tracebacks:
        warnings.append(f"{tracebacks} driver traceback(s); inspect driver.log")

    if args.orphan_grace >= 0:
        time.sleep(args.orphan_grace)
        orphans = find_orphans()
        print(f"\n-- Teardown: {len(orphans)} orphaned Dynamo/vLLM process(es) {args.orphan_grace}s after exit")
        for line in orphans[:12]:
            print(f"  {line}")
        if orphans:
            warnings.append(f"{len(orphans)} orphaned Dynamo/vLLM processes survived teardown; run cleanup-orphans.sh")

    print()
    for message in warnings:
        print(f"WARN: {message}")
    for message in failures:
        print(f"FAIL: {message}")
    print("RESULT:", "FAIL" if failures else "PASS")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
