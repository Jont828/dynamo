#!/usr/bin/env python3
"""Show free GPUs per node and pick a node for the demo pod.

A node qualifies when all of its GPUs are unclaimed by running or pending pods and it has no
NoSchedule/NoExecute taint. Among those, prefer a node that already has the image cached.

Usage: python3 gpu_free.py [--image IMAGE] [--need N] [--pick]
  --pick  print only the chosen node name (exit 1 if none qualifies)
"""

import argparse
import json
import subprocess
import sys


def kubectl_json(*args):
    return json.loads(subprocess.run(["kubectl", *args, "-o", "json"], check=True, capture_output=True, text=True).stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1")
    parser.add_argument("--need", type=int, default=8)
    parser.add_argument("--pick", action="store_true")
    args = parser.parse_args()

    nodes = [n for n in kubectl_json("get", "nodes")["items"]
             if int(n["status"].get("allocatable", {}).get("nvidia.com/gpu", 0) or 0) > 0]
    used = {}
    for pod in kubectl_json("get", "pods", "-A")["items"]:
        if pod["status"].get("phase") not in ("Running", "Pending") or not pod["spec"].get("nodeName"):
            continue
        gpus = sum(int((c.get("resources", {}).get("limits") or {}).get("nvidia.com/gpu", 0) or 0)
                   for c in pod["spec"]["containers"])
        used[pod["spec"]["nodeName"]] = used.get(pod["spec"]["nodeName"], 0) + gpus

    rows = []
    for node in nodes:
        name = node["metadata"]["name"]
        total = int(node["status"]["allocatable"]["nvidia.com/gpu"])
        taints = [t["key"].split("/")[-1] for t in node["spec"].get("taints", []) if t.get("effect") in ("NoSchedule", "NoExecute")]
        if node["spec"].get("unschedulable"):
            taints.append("cordoned")
        cached = any(args.image in names for img in node["status"].get("images", []) for names in img.get("names", []))
        free = total - used.get(name, 0)
        ok = free >= args.need and not taints
        rows.append((name, total, used.get(name, 0), free, ",".join(taints) or "-", cached, ok))

    candidates = sorted((r for r in rows if r[6]), key=lambda r: (not r[5], r[0]))
    pick = candidates[0][0] if candidates else None
    if args.pick:
        if not pick:
            sys.exit(1)
        print(pick)
        return

    print(f"{'NODE':<38} {'GPUS':>4} {'IN USE':>6} {'FREE':>4}  {'TAINTS':<16} {'IMAGE CACHED':<12}")
    for name, total, in_use, free, taints, cached, ok in rows:
        mark = "  ← demo" if name == pick else ""
        print(f"{name:<38} {total:>4} {in_use:>6} {free:>4}  {taints:<16} {'yes' if cached else 'no':<12}{mark}")


if __name__ == "__main__":
    main()
