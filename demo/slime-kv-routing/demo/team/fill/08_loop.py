"""Beat 08 (loop) data: writes build/08_loop.data.sh from kit files only.

Usage: $PY -I fill/08_loop.py

Kit root = slime-kv-routing/. Sources (runs/rr/run1/, the tutorial run on fresh engines, 2026-11-14):
  rayjob.yaml     the RayJob's start and end (the beat's clock: seconds since the RayJob was created), the Ray job ID
  driver.log      when Slime attached to the engines ("Detected external SGLang engines"), each weight write
                  (Slime's "Timer update_weights" start/end lines, 1 s resolution, elapsed to 0.1 s), each training
                  step ("Timer train end", elapsed to 0.1 s), and the precise rollout and train times (perf lines)
  worker-0.log    each engine's pause and resume around every weight load (the Dynamo sidecar's
  worker-1.log    "publishing SGLang engine state ... is_pause=true|false" lines, to the microsecond)
  frontend.log    the frontend's "Selected worker" lines: requests per engine in each step
  summary.json    per step: reward (mean of 64, each 0 or 1), the trainer-vs-engine logprob gap (tools/analyze.py,
                  from driver.log; cross-checked against the driver's own lines here)

A step on screen is the run's order: rollout k on weights v(k+1), then train k, then weight sync v(k+2), which is a
write (trainer -> Lustre, Slime's timer) followed by a load (both engines pause, load, resume: sidecar lines).
The initial sync, v1, comes before step 0. Rollout k starts when the engines resume after loading v(k+1): the
frontend's first request of every step comes 0.06-1.50 s after that resume, and the rollout time Slime reports
ends at that step's last request (both asserted below).
"""

import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import RUNS, Data, load_yaml  # noqa: E402

RUN = RUNS / "rr" / "run1"
REL = "runs/rr/run1"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SIDECAR = re.compile(r"^(\S+Z)\s+INFO sglang_grpc_core::server: publishing SGLang engine state .* is_pause=(true|false)")
LOG_TS = r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]"


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def log_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def pauses(path: pathlib.Path) -> list:
    """[(paused_at, resumed_at)] for one engine, from its Dynamo sidecar's engine-state lines."""
    out, start = [], None
    for raw in path.read_text(errors="replace").splitlines():
        m = SIDECAR.search(ANSI.sub("", raw))
        if not m:
            continue
        if m.group(2) == "true":
            start = iso(m.group(1))
        elif start is not None:
            out.append((start, iso(m.group(1))))
            start = None
    return out


def main():
    rj = load_yaml(f"{REL}/rayjob.yaml")
    st_ = rj["status"]
    t0 = iso(st_["startTime"])
    assert rj["metadata"]["creationTimestamp"] == st_["startTime"] == "2026-11-14T03:51:56Z"
    assert st_["jobStatus"] == "SUCCEEDED" and st_["jobDeploymentStatus"] == "Complete"
    secs = lambda x: (x - t0).total_seconds()  # noqa: E731
    done_t = round(secs(iso(st_["endTime"])))
    s = json.loads((RUN / "summary.json").read_text())
    assert s["rayjob_start"] == "2026-11-14T03:51:56+00:00" and s["job_secs"] == done_t == 165
    steps = s["steps"]
    assert len(steps) == 10

    drv = ANSI.sub("", (RUN / "driver.log").read_text(errors="replace"))
    engines_at = log_ts(re.search(LOG_TS + r" external\.py:\d+ - Detected external SGLang engines", drv).group(1))
    urls = re.findall(r"'url': 'http://([\w-]+):(\d+)'", re.search(r"Detected external SGLang engines: (.*)", drv).group(1))
    assert urls == [("slime-sglang-engine-0", "30000"), ("slime-sglang-engine-1", "30000")], urls

    # Writes: Slime's "Timer update_weights" (start and end second, elapsed to 0.1 s), 11 of them.
    w_start = [log_ts(x) for x in re.findall(LOG_TS + r" timer\.py:\d+ - Timer update_weights start", drv)]
    w_end = [(log_ts(a), b) for a, b in re.findall(LOG_TS + r" timer\.py:\d+ - Timer update_weights end "
                                                     r"\(elapsed: ([\d.]+)s\)", drv)]
    assert len(w_start) == len(w_end) == 11
    assert [float(x) for _, x in w_end] == s["sync_secs"]
    # Training: "Timer train end", 10 of them; precise times from the perf lines.
    t_end = [(log_ts(a), b) for a, b in re.findall(LOG_TS + r" timer\.py:\d+ - Timer train end \(elapsed: ([\d.]+)s\)", drv)]
    train_time = [float(x) for x in re.findall(r"'perf/train_time': ([\d.]+)", drv)]
    roll_time = [float(x) for x in re.findall(r"'perf/rollout_time': ([\d.]+)", drv)]
    assert len(t_end) == len(train_time) == len(roll_time) == 10
    assert all(abs(r - st["rollout_time"]) < 1e-9 for r, st in zip(roll_time, steps))

    # Loads: both engines pause together after each write and resume together.
    p = [pauses(RUN / f"worker-{w}.log") for w in (0, 1)]
    assert len(p[0]) == len(p[1]) == 11
    loads = []
    for i in range(11):
        a, b = min(p[0][i][0], p[1][i][0]), max(p[0][i][1], p[1][i][1])
        assert 0 <= (a - w_end[i][0]).total_seconds() < 1, i  # the pause follows the write timer's end
        loads.append((a, b))
    load_s = [(b - a).total_seconds() for a, b in loads]

    # Requests per engine and step, from the frontend's routing lines (all round-robin).
    sel = []
    for raw in (RUN / "frontend.log").read_text(errors="replace").splitlines():
        if "Selected worker" in raw:
            line = ANSI.sub("", raw)
            assert 'router_mode="round-robin"' in line
            sel.append((iso(line.split()[0]), re.search(r"worker_id=(\d+)", line).group(1)))
    assert len(sel) == 640
    workers = sorted({w for _, w in sel})
    # Which ID is which engine: each engine's own log names its ID (worker-0.log holds only its own).
    w0_log = (RUN / "worker-0.log").read_text(errors="replace")
    order = [w for w in workers if w in w0_log] + [w for w in workers if w not in w0_log]
    assert len(order) == 2 and order[0] in w0_log and order[1] not in w0_log
    split, starts, rollout_end, train_end = [], [], [], []
    for k in range(10):
        start = loads[k][1]                                   # rollout k starts when v(k+1) is loaded
        in_step = [(t, w) for t, w in sel if start < t < loads[k + 1][0]]
        c = [sum(1 for _, w in in_step if w == x) for x in order]
        assert c == [32, 32], (k, c)
        first = min(t for t, _ in in_step)
        assert 0 < (first - start).total_seconds() < 1.6, k   # the step's first request follows the resume
        split.append(f"{c[0]} + {c[1]}")
        r_end = start.timestamp() + roll_time[k]
        tr_end = r_end + train_time[k]
        # The driver logs the train timer's end at 1 s resolution: our estimate must fall in that second.
        assert 0 <= tr_end - t_end[k][0].timestamp() < 1.0, (k, tr_end, t_end[k][0])
        assert tr_end < loads[k + 1][0].timestamp()
        starts.append(start.timestamp())
        rollout_end.append(r_end)
        train_end.append(tr_end)
    # The frontend saw each step's last request finish when Slime's rollout timer stopped (within 0.1 s).
    done = []
    for raw in (RUN / "frontend.log").read_text(errors="replace").splitlines():
        if "route request completed" in raw:
            done.append(iso(ANSI.sub("", raw).split()[0]).timestamp())
    for k in range(10):
        last = max(x for x in done if starts[k] < x < loads[k + 1][0].timestamp())
        assert abs(last - rollout_end[k]) < 0.1, (k, last, rollout_end[k])

    rel = lambda ts: round(ts - t0.timestamp())  # noqa: E731
    arrays = {
        "LOOP_START_T": [rel(x) for x in starts],
        "LOOP_ROLLOUT_T": [rel(x) for x in rollout_end],
        "LOOP_TRAIN_T": [rel(x) for x in train_end],
        "LOOP_WRITE_T": [rel(loads[k + 1][0].timestamp()) for k in range(10)],   # v(k+2) written: engines pause
        "LOOP_LOAD_T": [rel(loads[k + 1][1].timestamp()) for k in range(10)],    # v(k+2) loaded: engines resume
    }
    for k in range(10):
        a = [arrays[n][k] for n in ("LOOP_START_T", "LOOP_ROLLOUT_T", "LOOP_TRAIN_T", "LOOP_WRITE_T", "LOOP_LOAD_T")]
        assert a[0] < a[1] <= a[2] <= a[3] < a[4] <= done_t, (k, a)
        if k + 1 < 10:
            assert arrays["LOOP_LOAD_T"][k] == arrays["LOOP_START_T"][k + 1]
    scalars = {
        "LOOP_ENGINES_T": rel(engines_at.timestamp()),
        "LOOP_V1_WRITE_FROM_T": rel(w_start[0].timestamp()),
        "LOOP_V1_WRITE_T": rel(loads[0][0].timestamp()),
        "LOOP_V1_LOAD_T": rel(loads[0][1].timestamp()),
        "LOOP_V1_WRITE_S": w_end[0][1],
        "LOOP_V1_LOAD_S": f"{load_s[0]:.1f}",
        "LOOP_DONE_T": done_t,
        "LOOP_JOB_ID": st_["jobId"],
        "LOOP_ENGINE_SVCS": ", ".join(f"{h}:{p_}" for h, p_ in urls),
        "LOOP_N_ENGINES": len(urls),
        "LOOP_LOG_LINES": f"{len((RUN / 'driver.log').read_text(errors='replace').splitlines()):,}",
    }
    assert scalars["LOOP_ENGINES_T"] < scalars["LOOP_V1_WRITE_FROM_T"] < scalars["LOOP_V1_WRITE_T"] \
        < scalars["LOOP_V1_LOAD_T"] == arrays["LOOP_START_T"][0]
    assert st_["jobId"] == s["succeeded_job"] == "slime-grpo-w9d8v"

    # Per-step cells. Train shows the driver's own timer (what `kubectl logs` prints), not the actor-only time.
    right = [round(st["raw_reward"] * 64) for st in steps]
    assert all(abs(r / 64 - st["raw_reward"]) < 1e-12 for r, st in zip(right, steps)) and sum(right) == 380
    raw = re.findall(r"train_metric_utils\.py:\d+ - rollout (\d+): \{.*'rollout/raw_reward': ([\d.]+)", drv)
    assert [(int(a), float(b)) for a, b in raw] == [(k, st["raw_reward"]) for k, st in enumerate(steps)]
    arrays |= {
        "LOOP_ROLL_S": [f"{x:.1f}" for x in roll_time],
        "LOOP_SPLIT": split,
        "LOOP_REWARD": [f"{st['raw_reward']:.3f}" for st in steps],
        "LOOP_RIGHT": right,
        "LOOP_TRAIN_S": [x for _, x in t_end],
        "LOOP_WRITE_S": [w_end[k + 1][1] for k in range(10)],
        "LOOP_LOAD_S": [f"{load_s[k + 1]:.1f}" for k in range(10)],
    }
    assert [f"{x:.1f}" for x in train_time] == arrays["LOOP_TRAIN_S"], (train_time, arrays["LOOP_TRAIN_S"])

    # The caveats: no trend in the reward, and the trainer-vs-engine logprob gap.
    rewards = [st["raw_reward"] for st in steps]
    assert rewards[0] == rewards[-1] == max(rewards) and min(rewards[1:-1]) < rewards[0]
    scalars["LOOP_REWARD_MIN"] = f"{min(rewards):.3f}"
    scalars["LOOP_REWARD_MAX"] = f"{max(rewards):.3f}"
    scalars["LOOP_REWARD_MEAN"] = f"{s['agg']['reward_mean']:.3f}"
    assert abs(sum(rewards) / 10 - s["agg"]["reward_mean"]) < 1e-12
    gaps = [float(x) for x in re.findall(r"'train/train_rollout_logprob_abs_diff': ([\d.]+)", drv)]
    assert len(gaps) == 10 and all(abs(g - st["logprob_diff"]) < 1e-12 for g, st in zip(gaps, steps))
    scalars["LOOP_GAP_MIN"] = f"{min(gaps):.4f}"
    scalars["LOOP_GAP_MAX"] = f"{max(gaps):.4f}"
    assert (scalars["LOOP_GAP_MIN"], scalars["LOOP_GAP_MAX"]) == ("0.0096", "0.0119")
    # How many steps real runs take: Slime's own example scripts, from a local clone of
    # THUDM/slime (read-only) when SLIME_REPO points at one; otherwise the check is skipped.
    slime = pathlib.Path(os.environ.get("SLIME_REPO", "/nonexistent"), "scripts")
    if slime.is_dir():
        counts = {f.name: int(m.group(1)) for f in slime.glob("run-*.sh")
                  if (m := re.search(r"--num-rollout (\d+)", f.read_text()))}
        assert counts.get("run-qwen3-4B.sh") == 3000 and counts.get("run-qwen3-4B-reproducibility.sh") == 100, counts
    scalars["LOOP_REAL_LO"] = 100
    scalars["LOOP_REAL_HI"] = "3,000"
    # The step's shape, from the entrypoint: 8 problems x 8 answers = 64 requests.
    entry = re.search(r"Running entrypoint for job slime-grpo-w9d8v: (python3 .*)", drv).group(1)
    n_prompts = int(re.search(r"--rollout-batch-size (\d+)", entry).group(1))
    n_samples = int(re.search(r"--n-samples-per-prompt (\d+)", entry).group(1))
    assert n_prompts * n_samples == 64 == sum(int(x) for x in split[0].split(" + "))
    scalars["LOOP_N_PROMPTS"] = n_prompts
    scalars["LOOP_N_ANSWERS"] = n_prompts * n_samples

    # Every moment the table changes: the beat redraws the rows only when it passes one.
    events = {scalars["LOOP_ENGINES_T"], scalars["LOOP_V1_WRITE_FROM_T"], scalars["LOOP_V1_WRITE_T"],
              scalars["LOOP_V1_LOAD_T"], done_t}
    for name in ("LOOP_START_T", "LOOP_ROLLOUT_T", "LOOP_TRAIN_T", "LOOP_WRITE_T", "LOOP_LOAD_T"):
        events |= set(arrays[name])
    arrays["LOOP_EVENTS"] = sorted(events)

    d = Data(__file__)
    d.source(f"{REL}/rayjob.yaml", f"{REL}/driver.log", f"{REL}/worker-0.log", f"{REL}/worker-1.log",
             f"{REL}/frontend.log", f"{REL}/summary.json")
    for k, v in scalars.items():
        d.set(k, v)
    for k, v in arrays.items():
        d.array(k, v)
    d.write()
    print(f"  clock: engines {scalars['LOOP_ENGINES_T']}s, v1 {scalars['LOOP_V1_WRITE_FROM_T']}-"
          f"{scalars['LOOP_V1_WRITE_T']}-{scalars['LOOP_V1_LOAD_T']}s, done {done_t}s; "
          f"writes {min(x for _, x in w_end)}-{max(x for _, x in w_end)} s, loads {min(load_s):.2f}-{max(load_s):.2f} s; "
          f"reward {scalars['LOOP_REWARD_MIN']}-{scalars['LOOP_REWARD_MAX']}")


if __name__ == "__main__":
    main()
