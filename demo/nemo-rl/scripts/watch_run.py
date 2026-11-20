#!/usr/bin/env python3
"""Follow a demo run's run.log and print the RL loop as it happens, one line per event.

Shows NeMo RL joining KubeRay's Ray cluster, the driver starting Dynamo (etcd, NATS, the frontend),
the vLLM engines registering with it, then one row per GRPO step: reward, how closely the engines'
token probabilities match the trainer's, step and refit time, and how Dynamo's KV router spread the
requests it has routed so far. Exits when rayjob-entrypoint.sh marks the run done.

Usage: python3 watch_run.py RUN_DIR
"""

import argparse
import json
import math
import pathlib
import re
import sys
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Ray's per-process prefixes, possibly nested: "(VllmAsyncGenerationWorker pid=1) (EngineCore pid=2) ".
PREFIX_RE = re.compile(r"^(?:\((?:[\w.]+(?:\[[^\]]*\])? )?pid=\d+(?:, ip=[^)]*)?\)\s?)+")

# Dynamo's frontend log (ai-dynamo 1.5.1): one "Selected worker" per routing decision, and one
# "route request completed" per finished request, with the prefix blocks the router found cached on
# the chosen engine (overlap_blocks).
SELECTED_RE = re.compile(r'Selected worker router_mode="(\w+)"')
WORKER_ID_RE = re.compile(r"\bworker_id=(\d+)")
ROUTED_RE = re.compile(r'route request completed request\.outcome="success"')
OVERLAP_RE = re.compile(r"\boverlap_blocks=(\d+)")
# Each engine's Dynamo worker logs its id when it registers.
REGISTERED_RE = re.compile(r"WorkerKvQuery endpoint starting for worker (\d+)")
KV_APPLIED_RE = re.compile(r"\[Dynamo\] router applied KV events: (\{.*\})")

TIMING_START_RE = re.compile(r"^\s*\S*\s*Timing:\s*$")
TIMING_ITEM_RE = re.compile(r"^\s*•\s*([\w/ .-]+?):\s*(-?[\d.]+)s\b")
STEP_METRICS_RE = re.compile(r"step_metrics=\{(.*)\}\s*$")
METRIC_RE = re.compile(r"'([^']+)':\s*(?:np\.\w+\()?\s*(-?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|nan|inf))")
TRAIN_STEP_RE = re.compile(r"\btrain step (\d+)/(\d+)\b")

B, D, G, Y, R, C, M, X = "\x1b[1m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m", "\x1b[0m"


def clean(raw):
    return PREFIX_RE.sub("", ANSI_RE.sub("", raw)).replace("\r", " ").rstrip()


def parse_metrics(body):
    return {key: float(value) for key, value in METRIC_RE.findall(body)}


def bar(value, width=10):
    if value is None or math.isnan(value):
        return " " * width
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


class Routing:
    """Per-engine counts of the KV router's decisions, numbered in engine registration order."""

    def __init__(self):
        self.order = []
        self.selected = {}
        self.completed = 0
        self.cached = 0
        self.overlap_blocks = 0
        self.modes = set()

    def engine(self, worker_id):
        if worker_id not in self.order:
            self.order.append(worker_id)
        return self.order.index(worker_id) + 1

    def feed(self, line):
        if m := REGISTERED_RE.search(line):
            self.engine(m[1])
        elif m := SELECTED_RE.search(line):
            self.modes.add(m[1])
            if w := WORKER_ID_RE.search(line):
                self.engine(w[1])
                self.selected[w[1]] = self.selected.get(w[1], 0) + 1
            return "selected"
        elif ROUTED_RE.search(line):
            self.completed += 1
            if o := OVERLAP_RE.search(line):
                self.overlap_blocks += int(o[1])
                self.cached += int(o[1]) > 0
        return None

    def snapshot(self):
        return dict(self.selected), self.completed, self.cached


class Steps:
    """Collects the SingleController's per-step "Timing:" block and step_metrics line."""

    def __init__(self):
        self.timing, self.in_timing, self.metrics = {}, False, None
        self.rows = []

    def feed(self, line):
        if TIMING_START_RE.match(line) and "Validation" not in line:
            self.timing, self.in_timing = {}, True
            return None
        if self.in_timing and (m := TIMING_ITEM_RE.match(line)):
            self.timing[m[1].strip()] = float(m[2])
            return None
        if m := STEP_METRICS_RE.search(line):
            self.in_timing = False
            self.metrics = parse_metrics(m[1])
            return None
        if (m := TRAIN_STEP_RE.search(line)) and self.metrics is not None:
            row = {"step": int(m[1]), "of": int(m[2]), "metrics": self.metrics, "timing": self.timing}
            self.rows.append(row)
            self.metrics, self.timing = None, {}
            return row
        return None


ONCE_EVENTS = [
    (re.compile(r"Connected to existing Ray cluster"),
     lambda m: f"{C}ray{X}      NeMo RL joined the RayJob's Ray cluster  {D}(ray job submit set RAY_ADDRESS){X}"),
    (re.compile(r"Started a local Ray instance|Started local cluster with tag"),
     lambda m: f"{Y}ray{X}      NeMo RL started its own local Ray cluster  {D}(no RAY_ADDRESS){X}"),
    (re.compile(r"Training dataset loaded with (\d+) samples"),
     lambda m: f"{C}data{X}     OpenMathInstruct-2: {int(m[1]):,} training problems"),
    (re.compile(r"\[Dynamo\] managed environment=.*'DYN_NAMESPACE': '([^']+)'"),
     lambda m: f"{M}dynamo{X}   the driver starts Dynamo itself: etcd, NATS and the frontend  {D}(namespace {m[1]}){X}"),
    (re.compile(r"\[Dynamo\] etcd ready on \S+:(\d+)"),
     lambda m: f"{M}dynamo{X}   etcd ready on :{m[1]}  {D}(engine discovery){X}"),
    (re.compile(r"\[Dynamo\] NATS ready on \S+:(\d+)"),
     lambda m: f"{M}dynamo{X}   NATS ready on :{m[1]}  {D}(engines publish KV-cache events to the router){X}"),
    (re.compile(r"\[Dynamo\] launching frontend argv=.*'--http-port', '(\d+)'.*'--router-mode', '(\w+)'"),
     lambda m: f"{M}dynamo{X}   frontend starting on :{m[1]}  {D}(OpenAI API + router, router_mode={m[2]}){X}"),
    (re.compile(r"Loading from (\S+)\s.*100%"),
     lambda m: f"{C}trainer{X}  Megatron loads {m[1]} on 1 GPU"),
    (re.compile(r"Model, optimizer, and learning rate scheduler built"),
     lambda m: f"{C}trainer{X}  Megatron model and optimizer ready"),
    (re.compile(r"\[Dynamo\] frontend \S+ routes to (\d+) engine"),
     lambda m: f"{M}dynamo{X}   {G}frontend routes to {m[1]} engine(s) ✓{X}"),
    (re.compile(r"Launching SingleControllerActor"),
     lambda m: f"{C}nemo-rl{X}  training loop starts  {D}(SingleController actor){X}"),
    (SELECTED_RE,
     lambda m: f"{M}dynamo{X}   first rollout request routed by the KV router"),
    (KV_APPLIED_RE,
     lambda m: f"{M}dynamo{X}   {G}router applied {json.loads(m[1]).get('ok', 0):,.0f} KV-cache events from the engines ✓{X}"),
]
ENGINE_START_RE = re.compile(r"Initializing a V1 LLM engine \(v([\w.+]+)\) with config: model='([^']+)'")
ENGINE_KV_RE = re.compile(r"\[Dynamo\] engine publishes KV events: (\w+)")
STOPPED_RE = re.compile(r"\[Dynamo\] (frontend|NATS|etcd) stopped")
ALERT_RE = re.compile(
    r"ncclP2pImportShareableBuffer|NCCL error|NCCLError|OutOfMemoryError|CUDA out of memory|"
    r"RayTaskError|ActorDiedError|Error executing job|Traceback \(most recent call last\)|"
    r"did not see every vLLM engine|is not alive \(code="
)
# vLLM logs some optional-import failures as WARNING tracebacks; those are not alerts.
WARNING_RE = re.compile(r"^(?:WARNING|WARN)\b|\bWARNING \d\d-\d\d ")
MAX_ALERTS = 8


class Printer:
    def __init__(self, started):
        self.started = started

    def __call__(self, text=""):
        print(text, flush=True)

    def event(self, text):
        elapsed = max(0, int(time.time() - self.started))
        print(f"{D}[+{elapsed // 60:02d}:{elapsed % 60:02d}]{X} {text}", flush=True)


def fmt(value, spec, width):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-".rjust(width)
    return format(value, spec).rjust(width)


# One row per training step. Each column: (title, subtitle, width); every cell is right-aligned to its width.
COLUMNS = [
    ("step", "", 4),
    ("reward", "", 16),
    ("gen_kl", "(<0.002)", 8),
    ("prob_err", "(1=same)", 8),
    ("resp_len", "tokens", 8),
    ("step", "s", 6),
    ("gen_wait", "s", 8),
    ("train", "s", 6),
    ("refit", "s", 6),
    ("routed e1/e2", "so far", 14),
    ("cached", "so far", 6),
]


def table_line(cells):
    return "  " + "  ".join(cell.rjust(width) for cell, (_, _, width) in zip(cells, COLUMNS))


def header(metrics):
    """The table header; the reward column says how many answers each step averages."""
    groups = metrics.get("generated_prompt_groups")
    samples = metrics.get("num_valid_samples")
    reward_sub = f"mean of {groups:.0f}×{samples / groups:.0f}" if groups and samples else "mean"
    titles = [title.ljust(width) if idx == 1 else title for idx, (title, _, width) in enumerate(COLUMNS)]
    subs = [reward_sub.ljust(16) if idx == 1 else sub for idx, (_, sub, _) in enumerate(COLUMNS)]
    return f"{B}{table_line(titles)}{X}\n{D}{table_line(subs)}{X}"


def step_row(row, routed, cached_share):
    met, tim = row["metrics"], row["timing"]
    reward = met.get("reward")
    resp = met.get("gen_tokens_per_sample/mean", met.get("mean_gen_tokens_per_sample"))
    split = "/".join(str(n) for n in routed) if routed else "-"
    cells = [
        str(row["step"]),
        f"{fmt(reward, '.3f', 5)} {bar(reward)}",
        fmt(met.get("gen_kl_error"), ".5f", 8),
        fmt(met.get("token_mult_prob_error"), ".4f", 8),
        fmt(resp, ".0f", 8),
        fmt(tim.get("Total step time"), ".1f", 6),
        fmt(tim.get("exposed_generation"), ".1f", 8),
        fmt(tim.get("policy_training"), ".1f", 6),
        fmt(tim.get("weight_sync"), ".1f", 6),
        split,
        fmt(cached_share, ".0%", 6),
    ]
    line = table_line(cells)
    # Color the reward bar after alignment, so escape codes don't count toward the widths.
    if reward is not None:
        line = line.replace(bar(reward), f"{G}{bar(reward)}{X}", 1)
    return line


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=pathlib.Path)
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()

    log, status = args.run_dir / "run.log", args.run_dir / "STATUS"
    while not (args.run_dir / "started_at").exists():
        time.sleep(args.poll)
    out = Printer(int((args.run_dir / "started_at").read_text().strip()))

    routing, steps = Routing(), Steps()
    seen, engines_started, engines_registered, alerts = set(), 0, 0, 0
    header_done = False
    offset, partial = 0, ""
    while True:
        done = status.exists() and status.read_text().startswith("done")
        chunk = ""
        if log.exists():
            with log.open("r", errors="replace") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        lines = (partial + chunk).split("\n")
        partial = lines.pop()
        if done:
            lines.append(partial)
            partial = ""
        for raw in lines:
            line = clean(raw)
            if not line:
                continue
            routing.feed(line)
            for idx, (regex, render) in enumerate(ONCE_EVENTS):
                if idx not in seen and (m := regex.search(line)):
                    seen.add(idx)
                    out.event(render(m))
            if m := ENGINE_START_RE.search(line):
                engines_started += 1
                out.event(f"{C}vllm{X}     engine {engines_started} starting: vLLM {m[1]}, {m[2]}")
            if m := ENGINE_KV_RE.search(line):
                engines_registered += 1
                out.event(f"{M}dynamo{X}   engine {engines_registered} registered with the frontend; "
                          f"publishes KV-cache events: {m[1]}")
            if m := STOPPED_RE.search(line):
                out.event(f"{M}dynamo{X}   {m[1]} stopped")
            if alerts < MAX_ALERTS and ALERT_RE.search(line) and not WARNING_RE.search(line):
                alerts += 1
                out.event(f"{R}alert{X}    {line.strip()[:140]}")
            if row := steps.feed(line):
                if not header_done:
                    header_done = True
                    out()
                    out(header(row["metrics"]))
                # Running totals: Ray forwards the trainer's step lines asynchronously, while the
                # frontend writes straight to the log, so per-row deltas would be noisy.
                selected, completed, cached = routing.snapshot()
                routed = [selected.get(w, 0) for w in routing.order]
                out(step_row(row, routed, cached / completed if completed else None))
        if done:
            out()
            out.event(f"{C}run{X}      training process exited: {status.read_text().strip()}")
            return
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
