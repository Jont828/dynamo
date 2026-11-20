"""Extract the kubectl heredoc manifests from the Slime tutorial pages.

Usage: python3 -I extract_heredocs.py <slime-pages-dir> <out-dir>
Writes one YAML file per heredoc, named after its first object.
"""
import pathlib
import re
import sys

import yaml

pages = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
names = {
    ("PersistentVolumeClaim", "slime-data"): "pvc.yaml",
    ("Job", "slime-setup"): "setup-job.yaml",
    ("Service", "slime-sglang-engine-0"): "engine-services.yaml",
    ("DynamoGraphDeployment", "slime-sglang"): "dgd.yaml",
    ("ConfigMap", "slime-functions"): "slime-functions.yaml",
    ("RayJob", "slime-grpo"): "rayjob.yaml",
}
pat = re.compile(r"-f - <<'?EOF'?\n(.*?)\nEOF\n", re.S)
for page in ["installation.mdx", "serve-inference.mdx", "train.mdx"]:
    text = (pages / page).read_text()
    for body in pat.findall(text):
        docs = [d for d in yaml.safe_load_all(body) if d]
        key = (docs[0]["kind"], docs[0]["metadata"]["name"])
        name = names[key]
        (out / name).write_text(body + "\n")
        print(f"{page}: {key} -> {name} ({len(docs)} objects)")
