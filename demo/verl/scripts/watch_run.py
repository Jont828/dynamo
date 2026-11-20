#!/usr/bin/env python3
"""Follow a demo run's driver.log and print the RL loop as it happens, one line per event.

Shows the Dynamo stack coming up, the baseline validation, one row per GRPO step, and each
later validation. Exits when run-demo-train.sh marks the run done.

Usage: python3 watch_run.py RUN_DIR [--skip N]
  --skip N  suppress the first N output lines (to resume after a dropped kubectl exec)
"""

import argparse
import json
import pathlib
import re
import sys
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PREFIX_RE = re.compile(r"^\((?:[A-Za-z0-9_]+) pid=\d+(?:, ip=[^)]+)?\)\s*")
STEP_RE = re.compile(r"\bstep:(\d+) - (.*)$")
NUMBER_RE = re.compile(r"^(?:np\.float\d*\()?([-+0-9.eE]+|nan|inf|-inf)\)?$")
VAL_KEY_RE = re.compile(r"^val-core/(.+)/(acc|reward)/mean@\d+$")
# verl's strict GSM8K grader (verl/utils/reward_score/gsm8k.py): "#### $71" or "####71" scores 0.
FORMAT_RE = re.compile(r"#### (\-?[0-9\.\,]+)")

B, D, G, Y, R, C, M, X = "\x1b[1m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m", "\x1b[0m"

ONCE_EVENTS = [
    (re.compile(r"Started a local Ray instance"), lambda m: f"{C}ray{X}     local Ray cluster started inside the pod"),
    (re.compile(r"Connecting to existing Ray cluster at address"),
     lambda m: f"{C}ray{X}     verl joined the existing Ray cluster (RAY_ADDRESS set by the Ray job)"),
    (re.compile(r"Loading weights:"), lambda m: f"{C}trainer{X} loading the model into 8 FSDP workers (actor + reference policy)"),
    (re.compile(r"\[DynamoHttpServer\] init replica=(\d+) node=(\d+) nnodes=(\d+) gpus=(\d+)"),
     lambda m: f"{M}dynamo{X}  rollout server actor started on node {m[2]} ({m[4]} GPUs)"),
    (re.compile(r"\[DynamoHttpServer\] etcd healthy on :(\d+)"),
     lambda m: f"{M}dynamo{X}  etcd ready on :{m[1]}  {D}(worker discovery){X}"),
    (re.compile(r"\[DynamoHttpServer\] NATS port :(\d+) open"),
     lambda m: f"{M}dynamo{X}  NATS ready on :{m[1]}  {D}(engines publish KV-cache events to the router){X}"),
    (re.compile(r"\[DynamoHttpServer\] starting dynamo\.frontend on :(\d+)"),
     lambda m: f"{M}dynamo{X}  frontend starting on :{m[1]}  {D}(OpenAI-compatible API + KV-aware router){X}"),
    (re.compile(r"\[DynamoHttpServer\] frontend healthy: (\d+)/(\d+) workers registered"),
     lambda m: f"{M}dynamo{X}  {G}all {m[1]}/{m[2]} engines registered with the router ✓{X}"),
    (re.compile(r"\[DynamoHttpServer\] logprob channel probe OK"),
     lambda m: f"{M}dynamo{X}  {G}engines return token IDs + logprobs to the trainer ✓{X}"),
    (re.compile(r"\[DynamoHttpServer\] refit self-test PASSED"),
     lambda m: f"{M}dynamo{X}  {G}weight-update channel self-test passed ✓{X}"),
    (re.compile(r"\[DynamoReplica pool\] ready: server_address=(\S+) logical_replicas=(\d+) workers=(\d+)"),
     lambda m: f"{C}verl{X}    rollout pool ready: {m[2]} replicas behind one frontend at {m[1]}"),
]
REPEAT_EVENTS = [
    (re.compile(r"\[DynamoHttpServer\] starting dynamo\.vllm shard (\d+)/(\d+) \(replica=\d+, rank_offset=\d+, GPUs=([\d,]+)"),
     lambda m: f"{M}dynamo{X}  vLLM engine {int(m[1]) + 1}/{m[2]} starting on GPUs {m[3].strip(',')}"),
]
ALERTS = re.compile(
    r"not healthy within|ActorDiedError|RayTaskError|OutOfMemoryError|CUDA out of memory|"
    r"update_weights.*(?:ERROR|TIMEOUT)|Error executing job"
)


class Printer:
    def __init__(self, skip, started):
        self.skip, self.count, self.started = skip, 0, started

    def __call__(self, text=""):
        self.count += 1
        if self.count > self.skip:
            print(text, flush=True)

    def event(self, text):
        elapsed = max(0, int(time.time() - self.started))
        self(f"{D}[+{elapsed // 60:02d}:{elapsed % 60:02d}]{X} {text}")


def parse_metrics(body):
    metrics = {}
    for item in body.split(" - "):
        key, _, raw = item.partition(":")
        if match := NUMBER_RE.match(raw.strip()):
            metrics[key.strip()] = float(match.group(1))
    return metrics


def format_rate(path):
    try:
        outputs = [json.loads(line).get("output", "") for line in path.read_text(errors="replace").splitlines() if line]
    except (OSError, json.JSONDecodeError):
        return None
    return sum(bool(FORMAT_RE.search(o)) for o in outputs) / len(outputs) if outputs else None


def bar(value, width=10):
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


def val_score(metrics):
    for key, value in metrics.items():
        if VAL_KEY_RE.match(key):
            return value
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=pathlib.Path)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()

    run_dir = args.run_dir
    driver, status = run_dir / "driver.log", run_dir / "STATUS"
    while not (run_dir / "started_at").exists():
        time.sleep(args.poll)
    out = Printer(args.skip, int((run_dir / "started_at").read_text().strip()))

    seen, header_done = set(), False
    baseline, offset, partial = None, 0, ""
    while True:
        done = status.exists() and status.read_text().startswith("done")
        if driver.exists():
            with driver.open("r", errors="replace") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        else:
            chunk = ""
        text = partial + chunk
        lines = text.split("\n")
        partial = lines.pop()
        if done:
            lines.append(partial)
            partial = ""
        for raw in lines:
            line = PREFIX_RE.sub("", ANSI_RE.sub("", raw)).replace("\r", " ")
            for idx, (regex, render) in enumerate(ONCE_EVENTS):
                if idx not in seen and (m := regex.search(line)):
                    seen.add(idx)
                    out.event(render(m))
            for regex, render in REPEAT_EVENTS:
                if m := regex.search(line):
                    out.event(render(m))
            if ALERTS.search(line):
                out.event(f"{R}alert{X}   {line.strip()[:150]}")
            if not (m := STEP_RE.search(line)):
                continue
            step, metrics = int(m[1]), parse_metrics(m[2])
            score = val_score(metrics)
            if step == 0 and score is not None:
                baseline = score
                out.event(f"{Y}{B}validate{X} before training: GSM8K test accuracy {B}{score:.3f}{X}  "
                          f"{D}(1,319 held-out questions, greedy){X}")
                continue
            if "critic/score/mean" not in metrics:
                continue
            if not header_done:
                header_done = True
                out()
                out(f"{B}  step  reward (train batch)   format  resp_len   rollout   train  weight-sync   step   engine≈trainer{X}")
                out(f"{D}        mean of 64×8 answers   ####    tokens     s         s      s            s      prob. corr{X}")
            reward = metrics["critic/score/mean"]
            fmt = format_rate(run_dir / "rollouts" / f"{step}.jsonl")
            corr = metrics.get("training/rollout_actor_probs_pearson_corr", float("nan"))
            out(f"  {step:>4}  {reward:5.3f} {G}{bar(reward)}{X}      "
                f"{(f'{fmt:4.0%}' if fmt is not None else '  - ')}   "
                f"{metrics.get('response_length/mean', float('nan')):7.0f}   "
                f"{metrics.get('timing_s/gen', float('nan')):7.1f}  "
                f"{metrics.get('timing_s/update_actor', float('nan')):6.1f}  "
                f"{metrics.get('timing_s/update_weights', float('nan')):9.1f}   "
                f"{metrics.get('timing_s/step', float('nan')):6.1f}   {corr:8.4f}")
            if score is not None:
                delta = f"  ({'+' if score >= baseline else ''}{score - baseline:.3f} vs. before training)" if baseline is not None else ""
                out(f"        {Y}{B}↳ validate{X} after step {step}: GSM8K test accuracy {B}{score:.3f}{X}{delta}")
        if done:
            out()
            out.event(f"{C}run{X}     training process exited: {status.read_text().strip()}")
            return
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
