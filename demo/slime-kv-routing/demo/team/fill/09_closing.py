"""Beat 09 (closing) data: writes build/09_closing.data.sh from kit files only.

Usage: $PY -I fill/09_closing.py

Kit root = slime-kv-routing/. Sources:
  runs/rr/pods-at-end.json          the DGD's Pods at the end of the phase: node, GPUs, restarts (engines, frontend)
  runs/rr/run1/rayjob.yaml          the trainer's GPU (the Ray head Pod's limits) and the RayJob's start and end
  runs/rr/run1/events.txt           the node the Ray head Pod ran on
  demo/team/snapshots/nodes.txt     that node's instance type and GPU count (read-only kubectl query, 2026-11-19)
  manifests/storage.yaml            the CSI driver (Azure Managed Lustre)
  runs/rr/run1/summary.json         per step: rollout time, reward, response lengths (tools/analyze.py, from driver.log)
  runs/rr/run1/driver.log           the first weight write's start, every write's timer, every training timer
  runs/rr/run1/worker-{0,1}.log     each engine's pause and resume around every weight load (Dynamo sidecar lines)
  runs/rr/run1/frontend.log         one routing line per request: 640, 320 per engine
  runs/rr/run1/frontend-metrics-after.txt   the frontend's own count: 640 successful requests

Weight syncs here are write + load: Slime's timer covers only the trainer's write to Lustre (20.7 s over 11 syncs);
both engines then pause, load, and resume (18.8 s in all). Beat 07 shows both halves in the logs.
"""

import json
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, RUNS, TEAM, Data, load_yaml  # noqa: E402

RUN = RUNS / "rr" / "run1"
REL = "runs/rr/run1"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SIDECAR = re.compile(r"^(\S+Z)\s+INFO sglang_grpc_core::server: publishing SGLang engine state .* is_pause=(true|false)")


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pauses(path: pathlib.Path) -> list:
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
    # --- Where it ran ------------------------------------------------------------------------------------
    pods = json.loads((RUNS / "rr" / "pods-at-end.json").read_text())["items"]
    engines = {}
    for p in pods:
        role = p["metadata"]["labels"].get("dynamo.nvidia.com/slime-role", "")
        gpus = sum(int(c.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", 0)) for c in p["spec"]["containers"])
        restarts = sum(c["restartCount"] for c in p["status"]["containerStatuses"])
        assert restarts == 0, p["metadata"]["name"]   # no Pod restarted, weight syncs included
        if role.startswith("worker-"):
            engines[role] = (p["spec"]["nodeName"], gpus)
    assert sorted(engines) == ["worker-0", "worker-1"] and {g for _, g in engines.values()} == {1}
    node = {n for n, _ in engines.values()}
    assert len(node) == 1
    node = node.pop()
    rj = load_yaml(f"{REL}/rayjob.yaml")
    head = rj["spec"]["rayClusterSpec"]["headGroupSpec"]["template"]["spec"]
    trainer_gpus = int(head["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
    assert trainer_gpus == 1
    head_pod = rj["status"]["rayClusterStatus"]["head"]["podName"]
    events = (RUN / "events.txt").read_text()
    assert re.search(rf"pod/{head_pod}\s+Successfully assigned slime-kv/{head_pod} to {node}\b", events), head_pod
    row = next(l.split() for l in (TEAM / "snapshots" / "nodes.txt").read_text().splitlines() if l.startswith(node))
    instance, node_gpus = row[5], int(row[6])
    assert instance == "Standard_ND96isr_H100_v5" and node_gpus == 8, row
    assert "driver: azurelustre.csi.azure.com" in (KIT / "manifests" / "storage.yaml").read_text()

    # --- What it did ----------------------------------------------------------------------------------------
    s = json.loads((RUN / "summary.json").read_text())
    steps = s["steps"]
    drv = ANSI.sub("", (RUN / "driver.log").read_text(errors="replace"))
    entry = re.search(r"Running entrypoint for job slime-grpo-w9d8v: (python3 .*)", drv).group(1)
    n_prompts = int(re.search(r"--rollout-batch-size (\d+)", entry).group(1))
    n_samples = int(re.search(r"--n-samples-per-prompt (\d+)", entry).group(1))
    n_steps = int(re.search(r"--num-rollout (\d+)", entry).group(1))
    assert (n_prompts, n_samples, n_steps) == (8, 8, 10) == (8, 8, len(steps))
    answers = n_prompts * n_samples * n_steps
    sel = [ANSI.sub("", l) for l in (RUN / "frontend.log").read_text(errors="replace").splitlines() if "Selected worker" in l]
    per = {}
    for l in sel:
        w = re.search(r"worker_id=(\d+)", l).group(1)
        per[w] = per.get(w, 0) + 1
    assert len(sel) == answers == 640 and sorted(per.values()) == [320, 320], per
    metrics = (RUN / "frontend-metrics-after.txt").read_text()
    assert re.search(r'^dynamo_frontend_requests_total\{endpoint="generate",[^}]*status="success"\} 640$', metrics, re.M)
    tokens = sum(st["response_len_mean"] * n_samples * n_prompts for st in steps)
    assert tokens == int(tokens) and round(s["agg"]["response_len_mean"] * answers) == tokens
    right = sum(round(st["raw_reward"] * n_prompts * n_samples) for st in steps)
    assert right == 380 and abs(right / answers - s["agg"]["reward_mean"]) < 1e-12

    # --- How long -------------------------------------------------------------------------------------------
    t0, t1 = iso(rj["status"]["startTime"]), iso(rj["status"]["endTime"])
    rayjob_s = (t1 - t0).total_seconds()
    assert rayjob_s == s["job_secs"] == 165
    first_write = datetime.strptime(re.search(r"\[(\S+ \S+)\] timer\.py:\d+ - Timer update_weights start", drv).group(1),
                                    "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    startup = (first_write - t0).total_seconds()
    writes = [float(x) for x in re.findall(r"Timer update_weights end \(elapsed: ([\d.]+)s\)", drv)]
    trains = [float(x) for x in re.findall(r"'perf/train_time': ([\d.]+)", drv)]
    assert writes == s["sync_secs"] and len(writes) == 11 and len(trains) == 10
    p = [pauses(RUN / f"worker-{w}.log") for w in (0, 1)]
    assert len(p[0]) == len(p[1]) == 11
    loads = [(max(p[0][i][1], p[1][i][1]) - min(p[0][i][0], p[1][i][0])).total_seconds() for i in range(11)]
    rollouts = sum(st["rollout_time"] for st in steps)

    scalars = {
        "CLOSING_INSTANCE": instance.removeprefix("Standard_").replace("_", " "),   # ND96isr H100 v5
        "CLOSING_ENGINE_GPUS": len(engines),
        "CLOSING_TRAINER_GPUS": trainer_gpus,
        "CLOSING_USED_GPUS": sum(g for _, g in engines.values()) + trainer_gpus,
        "CLOSING_NODE_GPUS": node_gpus,
        "CLOSING_SYNCS": len(writes),
        "CLOSING_ANSWERS": answers,
        "CLOSING_PROBLEMS": n_prompts * n_steps,
        "CLOSING_PER_ENGINE": answers // len(engines),
        "CLOSING_TOKENS": f"{int(tokens):,}",
        "CLOSING_RAYJOB": f"{int(rayjob_s // 60)} min {int(rayjob_s % 60)} s",
        "CLOSING_STARTUP_S": round(startup),
        "CLOSING_ROLLOUT_S": f"{rollouts:.0f}",
        "CLOSING_TRAIN_S": f"{sum(trains):.0f}",
        "CLOSING_SYNC_S": f"{sum(writes) + sum(loads):.0f}",
        "CLOSING_WRITE_S": f"{sum(writes):.0f}",
        "CLOSING_LOAD_S": f"{sum(loads):.0f}",
        "CLOSING_RIGHT": right,
        "CLOSING_RIGHT_PCT": f"{100 * right / answers:.0f}",
        "CLOSING_STEPS": n_steps,
    }
    expect = {"CLOSING_INSTANCE": "ND96isr H100 v5", "CLOSING_TOKENS": "122,535", "CLOSING_RAYJOB": "2 min 45 s",
              "CLOSING_STARTUP_S": 81, "CLOSING_ROLLOUT_S": "17", "CLOSING_TRAIN_S": "11", "CLOSING_SYNC_S": "40",
              "CLOSING_WRITE_S": "21", "CLOSING_LOAD_S": "19", "CLOSING_RIGHT_PCT": "59", "CLOSING_PROBLEMS": 80}
    for k, v in expect.items():
        assert scalars[k] == v, (k, scalars[k], v)
    # The rounded parts must add up to the rounded whole on screen.
    assert int(scalars["CLOSING_WRITE_S"]) + int(scalars["CLOSING_LOAD_S"]) == int(scalars["CLOSING_SYNC_S"])

    d = Data(__file__)
    d.source("runs/rr/pods-at-end.json", f"{REL}/rayjob.yaml", f"{REL}/events.txt", "demo/team/snapshots/nodes.txt",
             "manifests/storage.yaml", f"{REL}/summary.json", f"{REL}/driver.log", f"{REL}/worker-0.log",
             f"{REL}/worker-1.log", f"{REL}/frontend.log", f"{REL}/frontend-metrics-after.txt")
    for k, v in scalars.items():
        d.set(k, v)
    d.write()
    print(f"  {instance} ({node}): {answers} answers, {int(tokens):,} tokens, RayJob {scalars['CLOSING_RAYJOB']} "
          f"(startup {startup:.0f} s), syncs {sum(writes):.1f} + {sum(loads):.1f} s")


if __name__ == "__main__":
    main()
