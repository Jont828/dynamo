"""Data for beats/06_rayjob.sh (Step 5: launch training on KubeRay).

Usage: $PY -I fill/06_rayjob.py   (run.sh and build.sh run it for you)

Kit root = slime-retool/. Sources:
  manifests/rayjob.yaml          the lines typed in vim (its 15-line header comment, which names the kit's
                                 other files, is left out), split where the beat pauses (RAYJOB_Y_*); the
                                 lines each callout marks (RAYJOB_E*_FROM/TO, one range per callout, as
                                 vim_callout takes); the numbers the callouts quote
  upstream/image/slime/utils/arguments.py  the advantage estimators Slime offers (asserted)
  runs/run1.console.log          output of `kubectl apply` for the RayJob
  runs/run1/pods-watch.txt       the head and submitter Pods (first snapshot with both Running and ready)
  runs/run1/pods-at-end.json     their labels (the selector the beat uses picks exactly these two) and the
                                 submitter's command: ray job submit ... -- python3 /root/slime/train.py
  runs/run1/driver.final.log     asserted: the submitter's log says the job was submitted and runs train.py
  runs/run1/driver.stream.log    asserted: Megatron's world size 4 = TP 2 x CP 2, data-parallel 1

check_typed passes on the typed lines, the segments cover the file after its header comment exactly, in
order, and each segment is a contiguous run of the real file.
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves it off sys.path
from _common import KIT, Data, check_typed  # noqa: E402

RAYJOB_REL = "manifests/rayjob.yaml"
RAYJOB = KIT / RAYJOB_REL
RUN = KIT / "runs" / "run1"
UP = KIT / "upstream" / "image"

# Typing segments: (name, first line's exact text). Each runs to the next segment's first line.
SEGMENTS = [
    ("HEAD", "apiVersion: ray.io/v1"),
    ("ENGINES", "    --actor-num-nodes 1"),
    ("MODEL", "    --swiglu"),
    ("DATA", "    --prompt-data /data/datasets/dapo-math-17k/dapo-math-17k.jsonl"),
    ("OPTIM", "    --balance-data"),
    ("GRPO", "    --advantage-estimator grpo"),
    ("PARALLEL", "    --tensor-model-parallel-size 2"),
    ("FUNCS", "    --custom-generate-function-path generate_with_retool.generate"),
    ("CLUSTER", "  shutdownAfterJobFinishes: true"),
    ("VOLUMES", "              volumeMounts:"),
]

# The buffer lines each callout marks (vim_callout marks one range, FROM..TO): (first text, nth), (last text, nth).
# MODEL and OPTIM are folded by vim_collapse, so E3 marks the MODEL fold row (its first line).
MARKS = {
    "E1": (("  entrypoint: >-", 1), ("    python3 /root/slime/train.py", 1)),
    "E2": (("    --rollout-external-engine-addrs slime-sglang-engine-0:30000 slime-sglang-engine-1:30000 "
            "slime-sglang-engine-2:30000 slime-sglang-engine-3:30000", 1),
           ("    --update-weight-disk-dir /data/weight-updates", 1)),
    "E3": (("    --swiglu", 1), ("    --swiglu", 1)),
    "E4": (("    --num-rollout 10", 1), ("    --global-batch-size 256", 1)),
    "E5": (("    --advantage-estimator grpo", 1), ("    --eps-clip-high 0.28", 1)),
    "E6": (("    --tensor-model-parallel-size 2", 1), ("    --context-parallel-size 2", 1)),
    "E7": (("    --custom-generate-function-path generate_with_retool.generate", 1),
           ('      DYNAMO_ROLLOUT_URL: "http://slime-sglang-frontend:8000"', 1)),
    "E8": (("            - name: ray-head", 1), ('                  nvidia.com/gpu: "4"', 2)),
    "E9": (("                - name: slime-data", 1), ("                  mountPath: /opt/slime-functions", 1)),
}


def find(body, text, nth=1):
    """1-based buffer line of the nth body line equal to text."""
    seen = 0
    for i, line in enumerate(body):
        if line == text:
            seen += 1
            if seen == nth:
                return i + 1
    raise SystemExit(f"{RAYJOB.name}: line not found ({nth}): {text!r}")


def kubectl_table(rows):
    """NAME READY STATUS RESTARTS AGE, padded the way kubectl pads columns (widest + 3)."""
    cols = ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
    keys = ["name", "ready", "status", "restarts", "age"]
    width = [max(len(c), *(len(r[k]) for r in rows)) + 3 for c, k in zip(cols, keys)]
    out = ["".join(c.ljust(w) for c, w in zip(cols[:-1], width)) + cols[-1]]
    for r in rows:
        out.append("".join(r[k].ljust(w) for k, w in zip(keys[:-1], width)) + r[keys[-1]])
    return out


def snapshots(path):
    """[(iso time, [row dicts])] from a pods-watch.txt of `kubectl get pods -o wide` frames."""
    frames, cur = [], None
    for line in path.read_text().splitlines():
        if line.startswith("=== "):
            cur = (line[4:].strip(), [])
            frames.append(cur)
        elif cur is not None and line.strip() and not line.startswith("NAME"):
            p = line.split()
            cur[1].append({"name": p[0], "ready": p[1], "status": p[2], "restarts": p[3], "age": p[4]})
    return frames


def main():
    text = RAYJOB.read_text()
    full = text.splitlines()
    n = 0
    while full[n].startswith("#"):
        n += 1
    assert n == 15 and full[n] == "apiVersion: ray.io/v1", (n, full[n])
    body = full[n:]
    check_typed(body, RAYJOB_REL, "06_rayjob.py")
    data, arrays = {}, {}

    # --- typing segments ----------------------------------------------------------------------
    starts = [find(body, t) for _, t in SEGMENTS]
    assert starts[0] == 1 and starts == sorted(starts) and len(set(starts)) == len(starts), starts
    joined = []
    for k, (name, _) in enumerate(SEGMENTS):
        a = starts[k]
        b = starts[k + 1] if k + 1 < len(SEGMENTS) else len(body) + 1
        seg = body[a - 1:b - 1]
        assert seg == full[n + a - 1:n + b - 1]  # a contiguous run of the real file
        arrays[f"RAYJOB_Y_{name}"] = seg
        joined += seg
    assert joined == body, "segments don't cover the file body exactly"
    data["RAYJOB_FILE"] = "manifests/rayjob.yaml"

    for name, ((t1, k1), (t2, k2)) in MARKS.items():
        a, b = find(body, t1, k1), find(body, t2, k2)
        assert a <= b, (name, a, b)
        data[f"RAYJOB_{name}_FROM"] = a
        data[f"RAYJOB_{name}_TO"] = b

    # --- numbers the callouts quote -------------------------------------------------------------
    def flag(name):
        m = re.search(rf"^    --{name} (\S+)$", text, re.M)
        assert m, name
        return m.group(1)

    batch, nsamp, gbs = int(flag("rollout-batch-size")), int(flag("n-samples-per-prompt")), int(flag("global-batch-size"))
    assert batch * nsamp == gbs, (batch, nsamp, gbs)  # one optimizer update per step
    data["RAYJOB_PROBLEMS"] = batch
    data["RAYJOB_SAMPLES"] = nsamp
    data["RAYJOB_PER_STEP"] = batch * nsamp
    data["RAYJOB_GBS"] = gbs
    data["RAYJOB_STEPS"] = int(flag("num-rollout"))
    tp, cp = int(flag("tensor-model-parallel-size")), int(flag("context-parallel-size"))
    mt = int(flag("max-tokens-per-gpu"))
    gpus = int(flag("actor-num-gpus-per-node"))
    assert int(flag("pipeline-model-parallel-size")) == 1 and tp * cp == gpus, (tp, cp, gpus)
    driver = (RUN / "driver.stream.log").read_text()
    assert (f"using world size: {gpus}, data-parallel size: 1, context-parallel size: {cp}, hierarchical "
            f"context-parallel sizes: None, tensor-model-parallel size: {tp}") in driver
    data["RAYJOB_TP"] = tp
    data["RAYJOB_CP"] = cp
    data["RAYJOB_MAXCTX"] = f"{cp * mt:,}"
    data["RAYJOB_GPUS"] = gpus
    addrs = re.search(r"^    --rollout-external-engine-addrs (.+)$", text, re.M).group(1).split()
    assert all(re.fullmatch(r"slime-sglang-engine-\d:30000", a) for a in addrs), addrs
    data["RAYJOB_ENGINES"] = len(addrs)
    assert flag("advantage-estimator") == "grpo"
    args_src = (UP / "slime" / "utils" / "arguments.py").read_text()
    choices = re.search(r'"--advantage-estimator",\s*type=str,\s*choices=\[(.*?)\]', args_src, re.S).group(1)
    choices = re.findall(r'"(\w+)"', choices)
    assert set(choices) >= {"grpo", "ppo", "gspo", "cispo", "reinforce_plus_plus"}, choices
    data["RAYJOB_OTHER_ALGOS"] = "PPO, GSPO, CISPO, REINFORCE++"
    assert re.search(r'^\s+cpu: "48"$', text, re.M) and re.search(r"^\s+memory: 400Gi$", text, re.M)
    assert re.search(r'^\s+nvidia\.com/gpu: "4"$', text, re.M) and re.search(r"^\s+sizeLimit: 64Gi$", text, re.M)
    data["RAYJOB_CPUS"] = 48
    data["RAYJOB_MEM"] = "400 Gi"
    assert "--optimizer-cpu-offload" in text and "shutdownAfterJobFinishes: true" in text

    # --- apply, Pods, submitter log ---------------------------------------------------------------
    applied = [l for l in (KIT / "runs" / "run1.console.log").read_text().splitlines() if l.endswith(" created")]
    assert applied == ["rayjob.ray.io/slime-retool created"], applied
    data["RAYJOB_APPLY_OUT"] = applied[0]

    pods = json.loads((RUN / "pods-at-end.json").read_text())["items"]
    selector = ("app.kubernetes.io/created-by", "kuberay-operator")
    picked = sorted(p["metadata"]["name"] for p in pods if (p["metadata"].get("labels") or {}).get(selector[0]) == selector[1])
    head = [p for p in pods if (p["metadata"].get("labels") or {}).get("ray.io/node-type") == "head"]
    sub = [p for p in pods if (p["metadata"].get("labels") or {}).get("ray.io/originated-from-crd") == "RayJob"]
    assert len(head) == 1 and len(sub) == 1, (len(head), len(sub))
    head_name, sub_name = head[0]["metadata"]["name"], sub[0]["metadata"]["name"]
    assert picked == sorted([head_name, sub_name]), picked  # the selector picks exactly these two
    data["RAYJOB_SELECTOR"] = "=".join(selector)
    assert sub[0]["metadata"]["labels"].get("job-name") == "slime-retool"
    cmd = sub[0]["spec"]["containers"][0]["args"][0]
    m = re.search(r"ray job submit --address (\S+) --no-wait .* --submission-id (\S+) -- (python3 /root/slime/train\.py) ",
                  cmd)
    assert m and "ray job logs --address" in cmd and "--follow" in cmd, cmd[:300]
    job_id = m.group(2)

    for t_iso, rows in snapshots(RUN / "pods-watch.txt"):
        h = [r for r in rows if r["name"] == head_name]
        s = [r for r in rows if r["name"] == sub_name]
        if h and s and h[0]["ready"] == "1/1" and s[0]["ready"] == "1/1" and h[0]["status"] == s[0]["status"] == "Running":
            break
    else:
        raise SystemExit("no snapshot with the head and the submitter both Running and ready")
    arrays["RAYJOB_PODS_OUT"] = kubectl_table(sorted(h + s, key=lambda r: r["name"]))  # kubectl lists by name

    log = (RUN / "driver.final.log").read_text().splitlines()
    pat = re.compile(r"submitted successfully|Running entrypoint")
    hits = [l for l in log if pat.search(l)][:2]  # grep -m2 -E
    assert len(hits) == 2, hits
    sub_line, entry_line = hits
    m = re.fullmatch(r"(\S+ \S+)\t(SUCC cli\.py:\d+ -- )\x1b\[32m(Job '([^']+)' submitted successfully)\x1b\[39m", sub_line)
    assert m and m.group(4) == job_id, repr(sub_line)
    assert entry_line.startswith(f"Running entrypoint for job {job_id}: python3 /root/slime/train.py ")

    d = Data(__file__)
    d.source(RAYJOB_REL, "upstream/image/slime/utils/arguments.py",
             "runs/run1.console.log", "runs/run1/pods-watch.txt", "runs/run1/pods-at-end.json",
             "runs/run1/driver.final.log", "runs/run1/driver.stream.log")
    for k, v in data.items():
        d.set(k, v)
    for k, v in arrays.items():
        d.array(k, v)
    d.write()
    print(f"rayjob.yaml body: {len(body)} lines in {len(SEGMENTS)} segments, widest {max(map(len, body))} columns")
    print(f"Pods at {t_iso}; job {job_id}")


if __name__ == "__main__":
    main()
