"""Beat 10 (closing) data: writes build/10_closing.data.sh from kit files only.

Usage: $PY -I fill/10_closing.py

Kit root = slime-retool/. Sources:
  runs/run1/pods-at-end.json        the node every GPU Pod ran on, and each Pod's GPUs (engines, trainer)
  demo/team/snapshots/nodes.txt     that node's instance type and GPU count (read-only kubectl query)
  manifests/storage.yaml            the CSI driver (Azure Managed Lustre)
  runs/run1/summary.json            trajectories, requests, correct answers, weight writes (driver timer)
  runs/run1/worker-{0..3}.stream.log   the engines' pauses for each weight load (sidecar is_pause lines)
  runs/compare-kv-rr.json           RayJob duration, rollout and training sums, output and prefilled tokens
"""

import json
import pathlib
import re
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import Data  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TEAM = HERE.parent
KIT = TEAM.parent.parent
RUN = KIT / "runs" / "run1"
OUT = TEAM / "build" / "10_closing.data.sh"
SOURCES = ["runs/run1/pods-at-end.json", "demo/team/snapshots/nodes.txt", "manifests/storage.yaml", "runs/run1/summary.json", "runs/run1/worker-{0..3}.stream.log", "runs/compare-kv-rr.json"]

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SIDECAR = re.compile(r"^(\S+Z)\s+INFO sglang_grpc_core::server: publishing SGLang engine state .* is_pause=(true|false)")


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def mmss(x: float) -> str:
    return f"{int(x // 60)} min {int(round(x % 60))} s"


def main():
    # --- Where it ran -------------------------------------------------------------------------------------
    pods = json.loads((RUN / "pods-at-end.json").read_text())["items"]
    gpu_pods = {}
    for p in pods:
        g = sum(int(c.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", 0)) for c in p["spec"]["containers"])
        if g and p["status"].get("phase") == "Running":
            gpu_pods[p["metadata"]["name"]] = (p["spec"]["nodeName"], g)
    nodes = {n for n, _ in gpu_pods.values()}
    assert len(nodes) == 1, nodes
    node = nodes.pop()
    engine_gpus = sum(g for name, (_, g) in gpu_pods.items() if "-worker-" in name)
    trainer_gpus = sum(g for name, (_, g) in gpu_pods.items() if "-head-" in name)
    assert (engine_gpus, trainer_gpus) == (4, 4), gpu_pods
    row = next(l.split() for l in (TEAM / "snapshots" / "nodes.txt").read_text().splitlines() if l.startswith(node))
    instance, gpu_count = row[5], int(row[6])
    assert instance == "Standard_ND96isr_H100_v5" and gpu_count == engine_gpus + trainer_gpus == 8, row
    storage = (KIT / "manifests" / "storage.yaml").read_text()
    assert "driver: azurelustre.csi.azure.com" in storage

    # --- What it did --------------------------------------------------------------------------------------
    s = json.loads((RUN / "summary.json").read_text())
    c = json.loads((KIT / "runs" / "compare-kv-rr.json").read_text())
    K, R = c["KV"], c["RR"]
    finals = sum(round(st["acc"] * st["trajectories"]) for st in s["steps"])
    assert K["trajectories"] == s["trajectories"] == 2560 and K["requests"] == s["requests_logged"]

    # Weight syncs: the trainer's write (driver timer) + the engines' load (sidecar pause -> resume).
    loads = []
    per_engine = []
    for w in range(4):
        spans, start = [], None
        for raw in (RUN / f"worker-{w}.stream.log").read_text(errors="replace").splitlines():
            m = SIDECAR.search(ANSI.sub("", raw))
            if not m:
                continue
            if m.group(2) == "true":
                start = iso(m.group(1))
            elif start is not None:
                spans.append((start, iso(m.group(1))))
                start = None
        per_engine.append(spans)
    assert all(len(p) == len(s["sync_secs"]) == 11 for p in per_engine)
    for i in range(11):
        loads.append((max(p[i][1] for p in per_engine) - min(p[i][0] for p in per_engine)).total_seconds())
    sync_total = sum(s["sync_secs"]) + sum(loads)

    scalars = {
        "CLOSING_INSTANCE": instance.removeprefix("Standard_").replace("_", " "),  # ND96isr H100 v5
        "CLOSING_ENGINE_GPUS": engine_gpus,
        "CLOSING_TRAINER_GPUS": trainer_gpus,
        "CLOSING_STEPS": len(s["steps"]),
        "CLOSING_SYNCS": len(s["sync_secs"]),
        "CLOSING_TRAJ": f"{s['trajectories']:,}",
        "CLOSING_REQ": f"{s['requests_logged']:,}",
        "CLOSING_RPT": f"{s['requests_logged'] / s['trajectories']:.1f}",
        "CLOSING_OUT_TOKENS": f"{K['output_tokens'] / 1e6:.2f}M",
        "CLOSING_RAYJOB": mmss(K["rayjob_s"]),
        "CLOSING_ROLLOUT_S": f"{K['rollout_sum_s']:,.0f}",
        "CLOSING_TRAIN_S": f"{K['train_sum_s']:,.0f}",
        "CLOSING_SYNC_S": f"{sync_total:,.0f}",
        "CLOSING_WRITE_S": f"{sum(s['sync_secs']):,.0f}",   # compare_runs.py's "Weight syncs, sum of 11" row
        "CLOSING_LOAD_S": f"{sum(loads):,.0f}",
        "CLOSING_RIGHT": f"{finals:,}",
        "CLOSING_RIGHT_PCT": f"{100 * K['acc']:.0f}",
        "CLOSING_PREFILL_KV": f"{K['computed_tokens'] / 1e6:.2f}M",
        "CLOSING_PREFILL_RR": f"{R['computed_tokens'] / 1e6:.2f}M",
    }
    expect = {"CLOSING_TRAJ": "2,560", "CLOSING_REQ": "12,402", "CLOSING_RPT": "4.8", "CLOSING_OUT_TOKENS": "6.56M",
              "CLOSING_RAYJOB": "29 min 14 s", "CLOSING_ROLLOUT_S": "985", "CLOSING_TRAIN_S": "383",
              "CLOSING_SYNC_S": "229", "CLOSING_WRITE_S": "108", "CLOSING_LOAD_S": "121", "CLOSING_RIGHT": "1,286",
              "CLOSING_RIGHT_PCT": "50",
              "CLOSING_PREFILL_KV": "5.66M", "CLOSING_PREFILL_RR": "10.45M", "CLOSING_SYNCS": 11}
    for k, v in expect.items():
        assert scalars[k] == v, (k, scalars[k], v)

    d = Data(__file__)
    d.source(*SOURCES)
    for k, v in scalars.items():
        d.set(k, v)
    d.write()
    print(f"wrote {OUT.relative_to(TEAM)}: {instance} ({node}), {scalars['CLOSING_TRAJ']} trajectories, "
          f"{scalars['CLOSING_REQ']} requests, syncs {scalars['CLOSING_SYNC_S']} s in all")


if __name__ == "__main__":
    main()
