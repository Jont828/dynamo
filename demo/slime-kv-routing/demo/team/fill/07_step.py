"""Beat 07 (inside one step) data: writes build/07_step.data.sh from kit files only.

Usage: $PY -I fill/07_step.py

Kit root = slime-kv-routing/. Sources (runs/rr/run1/, the tutorial run on fresh engines, 2026-11-14):
  driver.log      `kubectl logs job/slime-grpo --tail=-1` (tools/run_phase.sh): the RayJob submitter's log, which
                  carries the Ray driver's output. Two pipelines below run on it unchanged and their stdout is
                  what the beat prints: the trainer's write of v1 (Slime's "Timer update_weights"), and step 0's
                  graded answers, totals, and training timer.
  worker-0.log    `kubectl logs -l dynamo.nvidia.com/slime-role=worker-0 --since-time=<start> --tail=-1`: the
  worker-1.log    engine side of each weight sync (SGLang's pause/flush/load/continue lines, run through the
                  grep shown on screen) and the Dynamo sidecar's "is_pause=true|false" lines (microseconds)
  frontend.log    the frontend's "Selected worker" lines: step 0's 64 routing decisions
  window.txt      start= : the --since-time the kit passed to `kubectl logs` for the engines
  summary.json    per-step train timers and rewards (tools/analyze.py, from driver.log): cross-checks
  rayjob.yaml     the Ray head Pod's IP: the caller in the engines' weight-sync lines

What the driver's sync timer covers (checked here, for every one of the 11 syncs): Slime's "Timer update_weights"
wraps the trainer actor's update_weights(), which saves the new weights as a Hugging Face checkpoint on Lustre.
The engines pause only after it ends (their pause is logged in the same second as the timer's end, which the
driver logs at 1 s resolution), and they finish loading 1.5-2.6 s later. So a sync is write + load.
"""

import json
import pathlib
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import RUNS, Data, load_yaml  # noqa: E402

RUN = RUNS / "rr" / "run1"
REL = "runs/rr/run1"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SIDECAR = re.compile(r"^(\S+Z)\s+INFO sglang_grpc_core::server: publishing SGLang engine state .* is_pause=(true|false)")

# The pipelines shown on screen. The beat types the commands; the fill runs the same pipeline on the saved log.
DRV_GREP = "Timer update_weights|weight_v000001"
DRV_CMD = f"kubectl logs -n $NAMESPACE job/slime-grpo | grep -E '{DRV_GREP}' | head -4 | cut -d' ' -f4-"
W0_GREP = "pause_gen|flush_cache|update_weights|Update weights end|Weight version|continue_gen"
LOG_GREP = "label: [0-9]+, reward: [0-9]+|'rollout/(zero_std/count_1|raw_reward)': [0-9.]+|Timer train end.*"


def run(cmd: str, path: pathlib.Path) -> list:
    """Run a pipeline with the saved log as its stdin (standing in for kubectl logs); its stdout lines."""
    with path.open("rb") as fh:
        out = subprocess.run(["/bin/bash", "-c", cmd], stdin=fh, capture_output=True, check=True).stdout
    return out.decode().rstrip("\n").split("\n")


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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


def hms_ms(t: datetime) -> str:
    """HH:MM:SS.mmm, rounded (not truncated) to the millisecond."""
    us = t.hour * 3_600_000_000 + t.minute * 60_000_000 + t.second * 1_000_000 + t.microsecond
    ms = (us + 500) // 1000
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def write_timers(drv: str) -> list:
    """[(end second, elapsed s)] for each "Timer update_weights end" line, in order."""
    out = []
    for m in re.finditer(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] timer\.py:\d+ - Timer update_weights end "
                         r"\(elapsed: ([\d.]+)s\)", drv):
        out.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), m.group(2)))
    return out


def main():
    drv = ANSI.sub("", (RUN / "driver.log").read_text(errors="replace"))
    window = dict(l.split("=", 1) for l in (RUN / "window.txt").read_text().split() if "=" in l)
    since = window["start"]
    assert since == "2026-11-14T03:51:52Z", since

    # --- 1. The trainer's side of the first sync: real output of the command shown --------------------------
    drv_out = run(f"grep -E '{DRV_GREP}' | head -4 | cut -d' ' -f4-", RUN / "driver.log")
    want = [r"^03:53:17\] timer\.py:24 - Timer update_weights start$",
            r"^03:53:18\] hf_checkpoint_saver\.py:56 - Saving model in HuggingFace format to "
            r"/data/weight-updates/weight_v000001$",
            r"^03:53:20\] hf_checkpoint_saver\.py:132 - Successfully saved HuggingFace model to "
            r"/data/weight-updates/weight_v000001$",
            r"^03:53:20\] timer\.py:32 - Timer update_weights end \(elapsed: 3\.2s\)$"]
    assert len(drv_out) == 4 and all(re.match(w, l) for w, l in zip(want, drv_out)), drv_out
    assert max(len(l) for l in drv_out) <= 118

    # --- 2. The engines' side: real output of the worker-0 command shown ----------------------------------
    w0_cmd = [f"kubectl logs -n $NAMESPACE -l dynamo.nvidia.com/slime-role=worker-0 --since-time={since} --tail=-1 \\",
              f"    | grep -E '{W0_GREP}' | head -7"]
    w0_out = run(f"grep -E '{W0_GREP}' | head -7", RUN / "worker-0.log")
    want = ['"POST /pause_generation HTTP/1.1" 200 OK', '"GET /flush_cache HTTP/1.1" 200 OK',
            "Start update_weights. Load format=auto", "Update weights end.",
            "Weight version changed. old_version='default' new_version='1'",
            '"POST /update_weights_from_disk HTTP/1.1" 200 OK', '"POST /continue_generation HTTP/1.1" 200 OK']
    assert len(w0_out) == 7 and all(w in l for w, l in zip(want, w0_out)), w0_out
    assert all(l.startswith(("[2026-11-14 03:53:20]", "[2026-11-14 03:53:21]")) for l in w0_out), w0_out
    assert not any("\x1b" in l for l in w0_out + drv_out)
    assert max(len(l) for l in w0_out) <= 118
    # The caller of every weight-sync call is the Ray head Pod (Slime), not Dynamo's frontend.
    head_ip = load_yaml(f"{REL}/rayjob.yaml")["status"]["rayClusterStatus"]["head"]["podIP"]
    w0_log = ANSI.sub("", (RUN / "worker-0.log").read_text(errors="replace"))
    callers = set(re.findall(r"INFO:\s+([\d.]+):\d+ - \"(?:POST|GET) /(?:pause_generation|flush_cache|"
                             r"update_weights_from_disk|continue_generation) HTTP", w0_log))
    assert callers == {head_ip}, (callers, head_ip)
    # Slime's engine addresses are the engine Services (BRIEF: manifests/tutorial/engine-services.yaml).
    m = re.search(r"Detected external SGLang engines: (.*)", drv)
    assert re.findall(r"'url': 'http://([\w-]+):(\d+)'", m.group(1)) == [
        ("slime-sglang-engine-0", "30000"), ("slime-sglang-engine-1", "30000")]

    # --- 3. Both halves of every sync: the write (driver timer), then the load (sidecar pause -> resume) -----
    p = [pauses(RUN / f"worker-{w}.log") for w in (0, 1)]
    timers = write_timers(drv)
    assert len(timers) == len(p[0]) == len(p[1]) == 11, (len(timers), len(p[0]), len(p[1]))
    loads = []
    for i in range(11):
        a = min(p[0][i][0], p[1][i][0])
        b = max(p[0][i][1], p[1][i][1])
        assert abs((p[0][i][0] - p[1][i][0]).total_seconds()) < 0.05  # both engines pause together...
        assert abs((p[0][i][1] - p[1][i][1]).total_seconds()) < 0.05  # ...and resume together
        lag = (a - timers[i][0]).total_seconds()
        assert 0 <= lag < 1, (i, lag)  # the pause comes in the same second the write timer ends
        loads.append((a, b))
    # Every engine log has 11 loads, versions 1..11: one before step 0, one after each of the 10 training steps.
    for w in (0, 1):
        log = ANSI.sub("", (RUN / f"worker-{w}.log").read_text(errors="replace"))
        assert log.count('"POST /update_weights_from_disk HTTP/1.1" 200 OK') == 11
        versions = re.findall(r"Weight version changed\. old_version='(\w+)' new_version='(\d+)'", log)
        assert versions == [("default", "1")] + [(str(v), str(v + 1)) for v in range(1, 11)], versions
    assert drv.count("Timer train end") == 10

    v1_write = timers[0][1]
    v1_load = (loads[0][1] - loads[0][0]).total_seconds()
    v2_write = timers[1][1]
    v2_load = (loads[1][1] - loads[1][0]).total_seconds()
    assert (v1_write, f"{v1_load:.1f}", v2_write, f"{v2_load:.1f}") == ("3.2", "1.5", "1.8", "1.9")

    # --- 4. Step 0's rollout: 64 requests, split by the frontend --------------------------------------------
    sel = []
    for raw in (RUN / "frontend.log").read_text(errors="replace").splitlines():
        if "Selected worker" in raw:
            line = ANSI.sub("", raw)
            sel.append((iso(line.split()[0]), re.search(r'router_mode="([a-z-]+)"', line).group(1),
                        re.search(r"worker_id=(\d+)", line).group(1)))
    assert len(sel) == 640 and {mode for _, mode, _ in sel} == {"round-robin"}
    step0 = [s for s in sel if loads[0][1] < s[0] < loads[1][0]]  # between the v1 and v2 loads
    assert len(step0) == 64 and step0 == sel[:64]
    counts = sorted(sum(1 for s in step0 if s[2] == w) for w in {s[2] for s in step0})
    assert counts == [32, 32], counts

    # --- 5. Grading and training: real output of the command shown --------------------------------------
    log_cmd = ["kubectl logs -n $NAMESPACE job/slime-grpo | grep -oE \\",
               f"    \"{LOG_GREP}\" | head -5"]
    log_out = run(f"grep -oE \"{LOG_GREP}\" | head -5", RUN / "driver.log")
    assert log_out == ["label: 1350, reward: 0", "label: 114, reward: 1", "'rollout/zero_std/count_1': 2",
                       "'rollout/raw_reward': 0.703125", "Timer train end (elapsed: 6.8s)"], log_out
    # The two logged answers are step 0's (driver.log lines 1905 and 1914): Darla's bill and John's zoo (beat 01).
    lines = (RUN / "driver.log").read_text(errors="replace").splitlines()
    s0 = [i for i, l in enumerate(lines, 1) if re.search(r"sglang_rollout\.py:(545|583) - ", l)][:2]
    assert s0 == [1905, 1914], s0
    assert "Darla needs to pay" in lines[1904] and lines[1904].endswith("label: 1350, reward: 0")
    assert "John wants to start a zoo" in lines[1913] and lines[1913].endswith("label: 114, reward: 1")
    assert "\\\\boxed{550}" in lines[1904] and "\\\\boxed{114}" in lines[1913]
    # --rm-type math: 1 if the last \boxed{} answer matches the label, else 0 (slime/rollout/rm_hub/__init__.py).
    rewards = re.findall(r"sglang_rollout\.py:(?:545|583) - .*, label: \S+, reward: (\S+)$", ANSI.sub("", "\n".join(lines)),
                         re.M)
    assert len(rewards) == 20 and set(rewards) <= {"0", "1"}, rewards

    s = json.loads((RUN / "summary.json").read_text())
    st = s["steps"]
    assert st[0]["raw_reward"] == 0.703125 == 45 / 64
    right = round(st[0]["raw_reward"] * 64)
    # zero_std/count_1 = groups whose 8 rewards are all 1 (slime/observability/rollout_metrics.py).
    assert "'rollout/zero_std/count_1': 2" in drv and "'rollout/zero_std/count_0'" not in drv.split("perf 1:")[0]

    # GRPO's group score, as Slime computes it (slime/data/batch_builder.py, grpo_std_normalization True):
    # (reward - group mean) / (std + 1e-6), std unbiased as torch's. A worked example, labeled "for example".
    ex = [1] * 6 + [0] * 2
    mean = sum(ex) / len(ex)
    sd = statistics.stdev([r - mean for r in ex])
    ex_right, ex_wrong = (1 - mean) / (sd + 1e-6), (0 - mean) / (sd + 1e-6)
    assert (f"{ex_right:+.2f}", f"{ex_wrong:+.2f}") == ("+0.54", "-1.62")
    assert "grpo_std_normalization .......................... True" in drv

    # --- 6. Training: step 0's timer against steps 1-9 ----------------------------------------------------
    train = re.findall(r"Timer train end \(elapsed: ([\d.]+)s\)", drv)
    assert len(train) == 10 and train[0] == "6.8"
    rest = sorted(float(x) for x in train[1:])
    assert (rest[0], rest[-1]) == (0.4, 0.6), rest
    assert abs(st[0]["actor_train_time"] - 6.77) < 0.01 and max(x["actor_train_time"] for x in st[1:]) < 0.6
    # The same work: 2 microbatches in step 0 and in step 1.
    mb = re.findall(r"actor train: 100%\|[^|]*\| (\d+)/\1 ", drv)
    assert mb[:2] == ["2", "2"], mb[:3]
    # One optimizer update per step: 8 x 8 = 64 answers = --global-batch-size 64; 10 "step N" lines.
    assert len(re.findall(r"model\.py:\d+ - step \d+: \{.*'train/global_batch_size': 64", drv)) == 10

    d = Data(__file__)
    d.source(f"{REL}/driver.log", f"{REL}/worker-0.log", f"{REL}/worker-1.log", f"{REL}/frontend.log",
             f"{REL}/window.txt", f"{REL}/summary.json", f"{REL}/rayjob.yaml")
    d.set("STEP_DRV_CMD", DRV_CMD)
    d.array("STEP_DRV_OUT", drv_out)
    d.array("STEP_W0_CMD", w0_cmd)
    d.array("STEP_W0_OUT", w0_out)
    d.array("STEP_LOG_CMD", log_cmd)
    d.array("STEP_LOG_OUT", log_out)
    # The run's shape, from the entrypoint (driver.log line 14): --rollout-batch-size 8 --n-samples-per-prompt 8.
    entry = next(l for l in lines if l.startswith("Running entrypoint for job slime-grpo-w9d8v: python3 "))
    n_prompts = int(re.search(r"--rollout-batch-size (\d+)", entry).group(1))
    n_samples = int(re.search(r"--n-samples-per-prompt (\d+)", entry).group(1))
    assert n_prompts * n_samples == len(step0) == 64 and n_samples == len(ex)
    n_engines = len(re.findall(r"slime-sglang-engine-\d:30000", entry.split("--custom-generate")[0]))
    assert n_engines == len(counts) == 2
    # The frontend kept both engines the whole time: every discovery line in its log lists 2 instances.
    fe = ANSI.sub("", (RUN / "frontend.log").read_text(errors="replace"))
    disc = re.findall(r"KubeDiscoveryClient::list returning (\d+) instances for query=AllEndpoints", fe)
    assert disc and set(disc) == {str(n_engines)}, disc

    pause_ms, resume_ms = hms_ms(loads[0][0]), hms_ms(loads[0][1])
    assert (pause_ms, resume_ms) == ("03:53:20.220", "03:53:21.766"), (pause_ms, resume_ms)
    d.set("STEP_V1_WRITE", v1_write)
    d.set("STEP_V1_PAUSE", pause_ms)
    d.set("STEP_V1_RESUME", resume_ms)
    d.set("STEP_V1_LOAD", f"{v1_load:.1f}")
    d.set("STEP_V1_TOTAL", f"{float(v1_write) + v1_load:.1f}")
    d.set("STEP_V2_WRITE", v2_write)
    d.set("STEP_V2_LOAD", f"{v2_load:.1f}")
    d.set("STEP_N_ENGINES", n_engines)
    d.set("STEP_N_PROMPTS", n_prompts)
    d.set("STEP_N_SAMPLES", n_samples)
    d.set("STEP_N_REQ", len(step0))
    d.set("STEP_PER_ENGINE", counts[0])
    d.set("STEP_LABEL_A", 1350)
    d.set("STEP_LABEL_B", 114)
    d.set("STEP_RIGHT", right)
    d.set("STEP_REWARD", f"{st[0]['raw_reward']:.3f}")
    d.set("STEP_ALL_RIGHT", 2)
    d.set("STEP_ALL_RIGHT_N", 2 * n_samples)
    d.set("STEP_EX_K", sum(ex))
    d.set("STEP_EX_RIGHT", f"{ex_right:+.2f}")
    d.set("STEP_EX_WRONG", f"{ex_wrong:+.2f}".replace("-", "−"))
    d.set("STEP_TRAIN", train[0])
    d.set("STEP_TRAIN_LO", f"{rest[0]:.1f}")
    d.set("STEP_TRAIN_HI", f"{rest[-1]:.1f}")
    d.write()
    print(f"  v1: write {v1_write} s + load {v1_load:.2f} s; v2: write {v2_write} s + load {v2_load:.2f} s; "
          f"step 0: {right}/64 right, split {counts}, train {train[0]} s (later {rest[0]}-{rest[-1]} s)")


if __name__ == "__main__":
    main()
