"""Beat 00 (title card) data: writes build/00_title.data.sh from kit files only.

Usage: $PY -I fill/00_title.py

Sources (kit root = slime-retool/):
  runs/run1/rayjob.yaml               the RayJob as it ended: created 22:40:24Z, status.endTime 23:09:38Z
  manifests/rayjob.yaml               --num-rollout (the number of RL steps)
  runs/run1/summary.json              the steps the analysis found (must equal --num-rollout)
  manifests/dgd.yaml                  GPUs held by the engines
  demo/team/snapshots/nodes-gpu.txt   GPUs per node, as the GPU Operator labels them
"""

import datetime
import json
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, TEAM, Data  # noqa: E402


def snapshot_body(name):
    """The output part of a snapshot file (after the '# ---' line)."""
    text = (TEAM / "snapshots" / name).read_text()
    head, sep, body = text.partition("# ---\n")
    assert sep, f"{name}: no '# ---' line"
    return body.rstrip("\n").splitlines()


def main():
    rayjob = yaml.safe_load((KIT / "runs" / "run1" / "rayjob.yaml").read_text())
    created = rayjob["metadata"]["creationTimestamp"]
    ended = rayjob["status"]["endTime"]
    assert rayjob["status"]["jobStatus"] == "SUCCEEDED", rayjob["status"]["jobStatus"]
    t0 = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
    t1 = datetime.datetime.fromisoformat(ended.replace("Z", "+00:00"))
    secs = int((t1 - t0).total_seconds())
    duration = f"{secs // 60} min {secs % 60} s"
    assert duration == "29 min 14 s", duration  # REPORT §5 gives the same

    entry = yaml.safe_load((KIT / "manifests" / "rayjob.yaml").read_text())["spec"]["entrypoint"]
    steps = int(re.search(r"--num-rollout (\d+)", entry).group(1))
    summary = json.loads((KIT / "runs" / "run1" / "summary.json").read_text())
    assert len(summary["steps"]) == steps == 10, (len(summary["steps"]), steps)

    head = yaml.safe_load((KIT / "manifests" / "rayjob.yaml").read_text())["spec"]["rayClusterSpec"]["headGroupSpec"]
    train_gpus = int(head["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
    dgd = yaml.safe_load((KIT / "manifests" / "dgd.yaml").read_text())
    engine_gpus = sum(
        int(c["podTemplate"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
        for c in dgd["spec"]["components"] if c["type"] == "worker"
    )
    rows = snapshot_body("nodes-gpu.txt")
    assert rows[0].split()[-1] == "GPU.COUNT", rows[0]
    per_node = {r.split()[-1] for r in rows[1:]}
    assert per_node == {"8"}, per_node
    assert engine_gpus + train_gpus == 8, (engine_gpus, train_gpus)

    d = Data(__file__)
    d.source("runs/run1/rayjob.yaml", "manifests/rayjob.yaml", "runs/run1/summary.json", "manifests/dgd.yaml",
             "demo/team/snapshots/nodes-gpu.txt")
    d.set("TITLE_STEPS", steps)
    d.set("TITLE_DURATION", duration)
    d.set("TITLE_GPUS", engine_gpus + train_gpus)
    d.set("TITLE_DATE", t0.date().isoformat())
    d.write()


if __name__ == "__main__":
    main()
