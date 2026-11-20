"""Beat 08 (loop) data: writes build/08_loop.data.sh from kit files only.

Usage: $PY -I fill/08_loop.py

Kit root = slime-retool/. Sources:
  runs/run1/rayjob.yaml          the RayJob's created and finished times (the beat's clock), the Ray job ID
  runs/run1/window.txt           start= : the --since-time the kit passed to `kubectl logs` for the engines
  runs/run1/worker-0.final.log   `kubectl logs -l dynamo.nvidia.com/slime-role=worker-0 --since-time=<start>
                                 --tail=-1` (tools/run_retool.sh); the grep below runs on it, unchanged
  runs/run1/worker-{0..3}.stream.log
                                 the SGLang sidecar's "publishing SGLang engine state ... is_pause=true|false"
                                 lines: when each engine paused for a weight load and resumed (microseconds)
  runs/run1/summary.json         per step: rollout, train, and write times and end times (the driver log's
                                 perf lines and its "Timer update_weights end" lines), requests per
                                 trajectory, correct answers, the trainer-vs-engine logprob gap
  runs/run1/driver.stream.log    the 4 engine URLs Slime detected
  manifests/rayjob.yaml          --lr and --num-rollout
  upstream/image/examples/retool/retool_qwen3_4b_rl.sh   upstream's --num-rollout

What "weight sync" means here: Slime's driver times only the trainer's part ("Timer update_weights", 9.2-12.8 s:
it saves the new weights as a Hugging Face checkpoint on Lustre). The engines then pause, flush, load the
checkpoint, and resume: 9.4-12.7 s more, measured from the sidecar lines, before the next rollout starts.
The beat shows both, as "write" and "load".
"""

import json
import pathlib
import re
import sys
import subprocess
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import Data  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TEAM = HERE.parent
KIT = TEAM.parent.parent
RUN = KIT / "runs" / "run1"
OUT = TEAM / "build" / "08_loop.data.sh"
SOURCES = ["runs/run1/rayjob.yaml", "runs/run1/window.txt", "runs/run1/worker-0.final.log", "runs/run1/worker-{0..3}.stream.log", "runs/run1/summary.json", "runs/run1/driver.stream.log", "manifests/rayjob.yaml", "upstream/image/examples/retool/retool_qwen3_4b_rl.sh"]

GREP = "pause_gen|flush_cache|update_weights|Update weights end|Weight version|continue_gen"
HEAD = 7
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SIDECAR = re.compile(r"^(\S+Z)\s+INFO sglang_grpc_core::server: publishing SGLang engine state .* is_pause=(true|false)")


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_yaml(path):
    import yaml  # PyYAML, in the venv from BRIEF section 2

    return yaml.safe_load(path.read_text())


def engine_pauses(path):
    """[(paused_at, resumed_at)] for one engine, from its sidecar's state lines."""
    out, start = [], None
    for raw in path.read_text(errors="replace").splitlines():
        m = SIDECAR.search(ANSI.sub("", raw))
        if not m:
            continue
        t = iso(m.group(1))
        if m.group(2) == "true":
            start = t
        elif start is not None:
            out.append((start, t))
            start = None
    return out


def main():
    rj = load_yaml(RUN / "rayjob.yaml")["status"]
    t0 = iso(rj["startTime"])
    rel = lambda x: (iso(x) if isinstance(x, str) else x) - t0  # noqa: E731
    secs = lambda x: rel(x).total_seconds()  # noqa: E731
    done_t = round(secs(rj["endTime"]))
    s = json.loads((RUN / "summary.json").read_text())
    steps = s["steps"]
    assert len(steps) == 10 and all(st["rollout_end"] and st["train_end"] for st in steps)

    # --- The first sync in worker-0's log: real output of the command shown --------------------------
    window = dict(l.split("=", 1) for l in (RUN / "window.txt").read_text().split() if "=" in l)
    since = window["start"]
    log_cmd = [
        f"kubectl logs -n $NAMESPACE -l dynamo.nvidia.com/slime-role=worker-0 --since-time={since} --tail=-1 \\",
        f"    | grep -E '{GREP}' | head -{HEAD}",
    ]
    grep = subprocess.run(["grep", "-E", GREP, str(RUN / "worker-0.final.log")], capture_output=True, text=True,
                          check=True).stdout.splitlines()[:HEAD]
    want = ["POST /pause_generation", "GET /flush_cache", "Start update_weights", "Update weights end",
            "Weight version changed. old_version='default' new_version='1'", "POST /update_weights_from_disk",
            "POST /continue_generation"]
    assert len(grep) == HEAD and all(w in l for w, l in zip(want, grep)), grep
    assert all(l.startswith("[2026-11-18 22:42:") for l in grep), grep
    assert max(len(l) for l in grep) <= 118

    # --- Each sync: the trainer's write (driver timer), then the engines' pause (sidecar) ----------------
    pauses = [engine_pauses(RUN / f"worker-{w}.stream.log") for w in range(4)]
    assert all(len(p) == 11 for p in pauses), [len(p) for p in pauses]
    loads = []  # (all paused at, all resumed at) per sync
    for i in range(11):
        a = min(p[i][0] for p in pauses)
        b = max(p[i][1] for p in pauses)
        assert max(p[i][0] for p in pauses) - a < (b - a) / 2  # the 4 engines pause together
        loads.append((a, b))
    writes = s["sync_secs"]
    write_ends = s["sync_ends"]
    assert len(writes) == len(write_ends) == 11
    for (a, _), w_end in zip(loads, write_ends):
        lag = (a - iso(w_end)).total_seconds()
        assert -1 <= lag <= 2, lag  # the engines pause right after the write (driver log has 1 s resolution)
    load_s = [(b - a).total_seconds() for a, b in loads]
    # Each rollout starts when the engines resume: Slime's rollout time ends at its perf line.
    for k, st in enumerate(steps):
        start = iso(st["rollout_end"]).timestamp() - st["rollout_time"]
        assert abs(start - loads[k][1].timestamp()) <= 1.5, (k, start, loads[k][1])

    # --- Timeline in whole seconds since the RayJob was created -----------------------------------------
    engines_t = round(secs(s["engines_detected"]))
    write0_start = round(secs(write_ends[0]) - writes[0])
    scalars = {
        "LOOP_LOG_SINCE": since,
        "LOOP_ENGINES_T": engines_t,
        "LOOP_V1_WRITE_FROM_T": write0_start,
        "LOOP_V1_WRITE_T": round(secs(write_ends[0])),
        "LOOP_V1_LOAD_T": round(secs(loads[0][1])),
        "LOOP_V1_WRITE_S": f"{writes[0]:.1f}",
        "LOOP_V1_LOAD_S": f"{load_s[0]:.1f}",
        "LOOP_DONE_T": done_t,
        "LOOP_JOB_ID": rj["jobId"],
    }
    assert rj["jobStatus"] == "SUCCEEDED" and s["succeeded"] == rj["jobId"]
    arrays = {
        "LOOP_LOG_CMD": log_cmd,
        "LOOP_LOG_OUT": grep,
        "LOOP_START_T": [round(secs(loads[k][1])) for k in range(10)],         # rollout k starts (engines resumed)
        "LOOP_ROLLOUT_T": [round(secs(st["rollout_end"])) for st in steps],
        "LOOP_TRAIN_T": [round(secs(st["train_end"])) for st in steps],
        "LOOP_WRITE_T": [round(secs(write_ends[k + 1])) for k in range(10)],  # v(k+2) written
        "LOOP_LOAD_T": [round(secs(loads[k + 1][1])) for k in range(10)],     # v(k+2) loaded
        "LOOP_ROLL_S": [f"{st['rollout_time']:.0f}" for st in steps],
        "LOOP_RPT": [f"{st['requests_per_trajectory']:.1f}" for st in steps],
        "LOOP_TRAIN_S": [f"{st['train_time']:.0f}" for st in steps],
        "LOOP_WRITE_S": [f"{writes[k + 1]:.1f}" for k in range(10)],
        "LOOP_LOAD_S": [f"{load_s[k + 1]:.1f}" for k in range(10)],
        "LOOP_CORRECT": [f"{100 * st['acc']:.0f}" for st in steps],
    }
    for name in ("LOOP_START_T", "LOOP_ROLLOUT_T", "LOOP_TRAIN_T", "LOOP_WRITE_T", "LOOP_LOAD_T"):
        assert arrays[name] == sorted(arrays[name]), name
    for k in range(10):
        assert arrays["LOOP_START_T"][k] < arrays["LOOP_ROLLOUT_T"][k] <= arrays["LOOP_TRAIN_T"][k] \
            <= arrays["LOOP_WRITE_T"][k] < arrays["LOOP_LOAD_T"][k] <= done_t, k
        if k + 1 < 10:
            assert arrays["LOOP_LOAD_T"][k] == arrays["LOOP_START_T"][k + 1]
    assert all(st["trajectories"] == 256 for st in steps)
    # Every moment the table changes, sorted: the beat redraws the whole table only when it passes one.
    events = {engines_t, write0_start, scalars["LOOP_V1_WRITE_T"], scalars["LOOP_V1_LOAD_T"], done_t}
    for name in ("LOOP_START_T", "LOOP_ROLLOUT_T", "LOOP_TRAIN_T", "LOOP_WRITE_T", "LOOP_LOAD_T"):
        events |= set(arrays[name])
    arrays["LOOP_EVENTS"] = sorted(events)
    scalars["LOOP_V1_WRITE_FROM_T"] = write0_start

    # The 4 engines Slime attached to (driver log).
    drv = ANSI.sub("", (RUN / "driver.stream.log").read_text(errors="replace"))
    urls = re.findall(r"http://(slime-sglang-engine-\d):(\d+)", re.search(r"Detected external SGLang engines: (.*)", drv)
                      .group(1))
    assert [u for u, _ in urls] == [f"slime-sglang-engine-{i}" for i in range(4)] and {p for _, p in urls} == {"30000"}
    scalars["LOOP_ENGINE_SVCS"] = "slime-sglang-engine-0..3:30000"

    # The caveat: no trend in ten steps.
    acc = [round(100 * st["acc"]) for st in steps]
    scalars["LOOP_CORRECT_MIN"] = min(acc)
    scalars["LOOP_CORRECT_MAX"] = max(acc)
    rayjob = (KIT / "manifests" / "rayjob.yaml").read_text()
    upstream = (KIT / "upstream" / "image" / "examples" / "retool" / "retool_qwen3_4b_rl.sh").read_text()
    arg = lambda text, flag: re.search(rf"^\s+--{flag} (\S+)\s*\\?$", text, re.M).group(1)  # noqa: E731
    scalars["LOOP_LR"] = arg(rayjob, "lr")
    assert arg(upstream, "lr") == scalars["LOOP_LR"]
    assert arg(rayjob, "num-rollout") == "10"
    scalars["LOOP_UPSTREAM_STEPS"] = f"{int(arg(upstream, 'num-rollout')):,}"
    diffs = [st["logprob_diff"] for st in steps]
    assert all(0.009 <= d <= 0.0115 for d in diffs), diffs  # "about 0.01"
    scalars["LOOP_LOGPROB_GAP"] = "0.01"

    d = Data(__file__)
    d.source(*SOURCES)
    for k, v in scalars.items():
        d.set(k, v)
    for k, v in arrays.items():
        d.array(k, v)
    d.write()
    print(f"wrote {OUT.relative_to(TEAM)}: engines at {engines_t}s, v1 written {scalars['LOOP_V1_WRITE_T']}s "
          f"({scalars['LOOP_V1_WRITE_S']} s) and loaded {scalars['LOOP_V1_LOAD_T']}s ({scalars['LOOP_V1_LOAD_S']} s), "
          f"done {done_t}s; writes {min(writes):.1f}-{max(writes):.1f} s, loads {min(load_s):.1f}-{max(load_s):.1f} s; "
          f"correct {min(acc)}-{max(acc)}%")


if __name__ == "__main__":
    main()
