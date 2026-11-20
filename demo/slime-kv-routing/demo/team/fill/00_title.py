"""Beat 00 (title card) data: writes build/00_title.data.sh from kit files only.

Usage: $PY -I fill/00_title.py

Sources (kit root = slime-kv-routing/):
  runs/rr/run1/rayjob.yaml            the RayJob as it ended: created 03:51:56Z, status.endTime 03:54:41Z,
                                      SUCCEEDED; its head Pod's nodeSelector (the run's one GPU node)
  runs/rr/run1/summary.json           rayjob_start, rayjob_end, job_secs (must agree) and the 10 steps found
  manifests/tutorial/rayjob.yaml      --num-rollout (the number of RL steps) and the head Pod's GPU
  manifests/tutorial/dgd.yaml         the engines' GPUs
  runs/rr/pods-at-ready.json          the engines' node (the same node as the head)
  demo/team/snapshots/nodes-gpu.txt   GPUs per node, as the GPU Operator labels them

The duration is the RayJob's own clock, creationTimestamp to status.endTime (165 s, summary.json's
job_secs). plans/A_intro.md proposes it for every beat; window.txt (03:51:52Z to 03:54:44Z) is the kit
script's wall clock around it.
"""

import json
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, RUNS, TEAM, Data, iso, secs  # noqa: E402

RUN = RUNS / "rr" / "run1"


def snapshot_body(name):
    """The output part of a snapshot file (after the '# ---' line)."""
    text = (TEAM / "snapshots" / name).read_text()
    _, sep, body = text.partition("# ---\n")
    assert sep, f"{name}: no '# ---' line"
    return body.rstrip("\n").splitlines()


def main():
    rayjob = yaml.safe_load((RUN / "rayjob.yaml").read_text())
    created, ended = rayjob["metadata"]["creationTimestamp"], rayjob["status"]["endTime"]
    assert rayjob["status"]["jobStatus"] == "SUCCEEDED", rayjob["status"]["jobStatus"]
    job_secs = secs(created, ended)
    summary = json.loads((RUN / "summary.json").read_text())
    assert iso(summary["rayjob_start"]) == iso(created) and iso(summary["rayjob_end"]) == iso(ended), summary
    assert job_secs == int(summary["job_secs"]) == 165, (job_secs, summary["job_secs"])
    duration = f"{job_secs // 60} min {job_secs % 60} s"

    tut_rayjob = yaml.safe_load((KIT / "manifests" / "tutorial" / "rayjob.yaml").read_text())
    entry = tut_rayjob["spec"]["entrypoint"]
    steps = int(re.search(r"--num-rollout (\d+)", entry).group(1))
    assert len(summary["steps"]) == steps == 10, (len(summary["steps"]), steps)
    assert rayjob["spec"]["entrypoint"].split() == entry.split(), "the run's entrypoint isn't the tutorial's"

    head = tut_rayjob["spec"]["rayClusterSpec"]["headGroupSpec"]["template"]["spec"]["containers"][0]
    train_gpus = int(head["resources"]["limits"]["nvidia.com/gpu"])
    dgd = yaml.safe_load((KIT / "manifests" / "tutorial" / "dgd.yaml").read_text())
    workers = [c for c in dgd["spec"]["components"] if c["type"] == "worker"]
    engine_gpus = sum(int(c["podTemplate"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
                      for c in workers)
    assert (len(workers), engine_gpus, train_gpus) == (2, 2, 1), (len(workers), engine_gpus, train_gpus)

    # One node: the engines (as placed) and the head (its nodeSelector, from tools/pin.py) share it.
    pods = json.loads((RUNS / "rr" / "pods-at-ready.json").read_text())["items"]
    engine_nodes = {p["spec"]["nodeName"] for p in pods if "-worker-" in p["metadata"]["name"]}
    head_node = rayjob["spec"]["rayClusterSpec"]["headGroupSpec"]["template"]["spec"]["nodeSelector"][
        "kubernetes.io/hostname"]
    assert len(engine_nodes) == 1 and engine_nodes == {head_node}, (engine_nodes, head_node)

    rows = snapshot_body("nodes-gpu.txt")
    assert rows[0].split()[-2:] == ["INSTANCE-TYPE", "GPU.COUNT"], rows[0]
    per_node = {r.split()[-1] for r in rows[1:]}
    types = {r.split()[-2] for r in rows[1:]}
    assert per_node == {"8"} and types == {"Standard_ND96isr_H100_v5"}, (per_node, types)
    assert any(r.split()[0] == head_node for r in rows[1:]), head_node

    d = Data(__file__)
    d.source("runs/rr/run1/rayjob.yaml", "runs/rr/run1/summary.json", "manifests/tutorial/rayjob.yaml",
             "manifests/tutorial/dgd.yaml", "runs/rr/pods-at-ready.json", "demo/team/snapshots/nodes-gpu.txt")
    d.set("TITLE_STEPS", steps)
    d.set("TITLE_DURATION", duration)
    d.set("TITLE_GPUS", engine_gpus + train_gpus)
    d.set("TITLE_NODE_GPUS", int(per_node.pop()))
    d.set("TITLE_DATE", iso(created).date().isoformat())
    d.write()


if __name__ == "__main__":
    main()
