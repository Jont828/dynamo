"""Pin every Pod template in a tutorial manifest to one node.

Usage: python -I pin.py <manifest.yaml> <node-name> > pinned.yaml

Adds nodeSelector kubernetes.io/hostname=<node> to the setup Job, each DGD
component, and the RayJob's Ray head. This is a measurement control: both
routing modes then run their engines, frontend, and trainer on the same node
with the images already cached. Nothing else in the manifest changes.
"""

import sys

import yaml


def pin(spec: dict, node: str) -> None:
    spec.setdefault("nodeSelector", {})["kubernetes.io/hostname"] = node


def main() -> None:
    path, node = sys.argv[1], sys.argv[2]
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    for doc in docs:
        kind = doc["kind"]
        if kind == "Job":
            pin(doc["spec"]["template"]["spec"], node)
        elif kind == "DynamoGraphDeployment":
            for component in doc["spec"]["components"]:
                pin(component["podTemplate"].setdefault("spec", {}), node)
        elif kind == "RayJob":
            pin(doc["spec"]["rayClusterSpec"]["headGroupSpec"]["template"]["spec"], node)
    yaml.safe_dump_all(docs, sys.stdout, sort_keys=False, width=1000)


if __name__ == "__main__":
    main()
