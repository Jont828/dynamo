"""Data for beats/06_rayjob.sh (Step 5: launch training on KubeRay).

Usage: $PY -I fill/06_rayjob.py   (run.sh and build.sh run it for you)

Kit root = slime-kv-routing/. Sources:
  manifests/tutorial/rayjob.yaml   the RayJob typed in vim: all 101 lines (it has no header comment), split where the
                                   beat changes speed, folds, or pauses (RAYJOB_Y_*); the buffer lines each callout
                                   marks (RAYJOB_E*_FROM/TO); the numbers the callouts quote
  runs/rr/run1/driver.log          the submitter Pod's log (`kubectl logs job/slime-grpo`). Its parsed-argument table
                                   is asserted to agree with the callouts (global batch 64 = 8 x 8, one GPU with TP,
                                   CP, and PP 1, load = ref_load, GRPO, 10 rollouts), and so are its 10 rollout and
                                   10 training-step lines. The beat shows the two lines that
                                   `grep -E 'submitted successfully|Detected external'` matches: the only two in the
                                   whole log (asserted)
  runs/rr/phase.log                the output of the RayJob's apply for run 1
  runs/rr/run1/rayjob.yaml         the RayJob as it ended: its RayCluster and head Pod, creation time, the head's
                                   HeadPodReady time, the Ray job ID
  runs/rr/run1/events.txt          the submitter Pod (job/slime-grpo's SuccessfulCreate); asserted: both Pods were
                                   scheduled and found Slime's image already on their node
  runs/rr/run1/summary.json        asserted: the RayJob start and the job ID agree; engines found 47 s after the start
  ../slime-retool/upstream/image/  optional, read-only: the Slime image's own arguments.py (the same image tag).
                                   Asserted when present: the advantage estimators it offers, --ref-load's help, and
                                   the global-batch-size comment (one update per rollout at 8 x 8)
"""

import json
import pathlib
import re
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves it off sys.path
from _common import KIT, RUNS, Data, check_typed, fmt_int, read_lines, secs  # noqa: E402

RAYJOB_REL = "manifests/tutorial/rayjob.yaml"
RUN = RUNS / "rr" / "run1"
UPSTREAM = KIT.parent / "slime-retool" / "upstream" / "image"
COLS = 120

# Typing segments: (name, first line's exact text). Each runs to the next segment's first line.
SEGMENTS = [
    ("HEAD", "apiVersion: ray.io/v1"),
    ("ENGINES", "    --rollout-external-engine-addrs slime-sglang-engine-0:30000 slime-sglang-engine-1:30000"),
    ("CKPT", "    --hf-checkpoint /data/models/Qwen3-0.6B"),
    ("MODEL", "    --swiglu"),                                   # folded
    ("DATA", "    --prompt-data /data/datasets/gsm8k/train.parquet"),
    ("BATCH", "    --num-rollout 10"),
    ("TRAINER", "    --actor-num-nodes 1"),
    ("GRPO", "    --advantage-estimator grpo"),
    ("OPTIM", "    --optimizer adam"),                           # folded
    ("ENV", "  runtimeEnvYAML: |"),
    ("CLUSTER", "  shutdownAfterJobFinishes: true"),
]

# The buffer lines each callout marks (vim_callout marks one range, FROM..TO): (first text, nth), (last text, nth).
# MODEL is folded by vim_collapse, so E3 (14-30) marks the two checkpoint lines and the fold row.
MARKS = {
    "E1": (("  entrypoint: >-", 1), ("    python3 /root/slime/train.py", 1)),
    "E2": (("    --rollout-external-engine-addrs slime-sglang-engine-0:30000 slime-sglang-engine-1:30000", 1),
           ("    --custom-update-weight-post-write-path weight_permissions.make_weights_readable", 1)),
    "E3": (("    --hf-checkpoint /data/models/Qwen3-0.6B", 1), ("    --qk-layernorm", 1)),
    "E4": (("    --num-rollout 10", 1), ("    --global-batch-size 64", 1)),
    "E5": (("    --actor-num-nodes 1", 1), ("    --actor-num-gpus-per-node 1", 1)),
    "E6": (("    --advantage-estimator grpo", 1), ("    --eps-clip-high 0.28", 1)),
    "E8": (("            - name: ray-head", 1), ("                  mountPath: /dev/shm", 1)),
}


def find(lines, text, nth=1):
    """1-based number of the nth line equal to text."""
    seen = 0
    for i, line in enumerate(lines):
        if line == text:
            seen += 1
            if seen == nth:
                return i + 1
    raise SystemExit(f"06_rayjob.py: no line {text!r} ({nth}) in {RAYJOB_REL}")


def arg(table, name):
    """A value from the driver log's printed argument table."""
    m = re.search(rf"^  {name} \.+ (.*)$", table, re.M)
    assert m, name
    return m.group(1)


def wrap_cols(line, cols=COLS):
    """The rows a terminal COLS wide shows for one long line (it wraps at the last column)."""
    assert "\t" not in line and "\x1b" not in line, repr(line[:80])
    return [line[i:i + cols] for i in range(0, len(line), cols)]


def main():
    lines = read_lines(RAYJOB_REL)
    assert lines[0] == "apiVersion: ray.io/v1" and lines[1] == "kind: RayJob", lines[:2]
    check_typed(lines, RAYJOB_REL, "06_rayjob.py")
    d = Data(__file__)
    d.source(RAYJOB_REL, "runs/rr/run1/driver.log", "runs/rr/phase.log", "runs/rr/run1/rayjob.yaml",
             "runs/rr/run1/events.txt", "runs/rr/run1/summary.json")

    # --- typing segments ------------------------------------------------------------------------
    starts = [find(lines, t) for _, t in SEGMENTS]
    assert starts[0] == 1 and starts == sorted(starts) and len(set(starts)) == len(starts), starts
    joined = []
    for k, (name, _) in enumerate(SEGMENTS):
        a = starts[k]
        b = starts[k + 1] if k + 1 < len(SEGMENTS) else len(lines) + 1
        d.array(f"RAYJOB_Y_{name}", lines[a - 1:b - 1])
        joined += lines[a - 1:b - 1]
    assert joined == lines, "segments don't cover the file exactly"
    d.set("RAYJOB_FILE", RAYJOB_REL)
    for name, ((t1, k1), (t2, k2)) in MARKS.items():
        a, b = find(lines, t1, k1), find(lines, t2, k2)
        assert a <= b, (name, a, b)
        d.set(f"RAYJOB_{name}_FROM", a)
        d.set(f"RAYJOB_{name}_TO", b)

    # --- the numbers the callouts and folds quote, from the manifest -----------------------------------
    text = "\n".join(lines)

    def flag(name):
        m = re.search(rf"^    --{name} (\S+)$", text, re.M)
        assert m, name
        return m.group(1)

    problems, answers, gbs = int(flag("rollout-batch-size")), int(flag("n-samples-per-prompt")), int(flag("global-batch-size"))
    assert problems * answers == gbs == 64, (problems, answers, gbs)   # one optimizer update per step (below)
    d.set("RAYJOB_PROBLEMS", problems)
    d.set("RAYJOB_ANSWERS", answers)
    d.set("RAYJOB_PER_STEP", problems * answers)
    d.set("RAYJOB_STEPS", int(flag("num-rollout")))
    gpus = int(flag("actor-num-gpus-per-node"))
    assert int(flag("actor-num-nodes")) == 1 and gpus == 1
    d.set("RAYJOB_GPUS", gpus)
    assert "--tensor-model-parallel-size" not in text and "--context-parallel-size" not in text
    layers, hidden, heads = int(flag("num-layers")), int(flag("hidden-size")), int(flag("num-attention-heads"))
    assert (layers, hidden, heads) == (28, 1024, 16)
    d.set("RAYJOB_SHAPE", f"{layers} layers, hidden size {fmt_int(hidden)}, {heads} attention heads")
    assert flag("optimizer") == "adam" and flag("lr") == "1e-6" and flag("lr-decay-style") == "constant"
    assert flag("attention-dropout") == flag("hidden-dropout") == "0.0" and flag("attention-backend") == "flash"
    assert "--accumulate-allreduce-grads-in-fp32" in text and "--attention-softmax-in-fp32" in text
    d.set("RAYJOB_LR", "1e-6")
    assert flag("advantage-estimator") == "grpo"
    addrs = re.search(r"^    --rollout-external-engine-addrs (.+)$", text, re.M).group(1).split()
    assert addrs == ["slime-sglang-engine-0:30000", "slime-sglang-engine-1:30000"], addrs
    assert flag("update-weight-transport") == "disk" and flag("update-weight-disk-dir") == "/data/weight-updates"
    assert flag("ref-load") == "/data/models/Qwen3-0.6B_torch_dist"
    assert re.search(r'^                  cpu: "16"$', text, re.M)
    assert len(re.findall(r'^                  nvidia\.com/gpu: "1"$', text, re.M)) == 2
    assert len(re.findall(r"^                  memory: 200Gi$", text, re.M)) == 2
    assert re.search(r"^                sizeLimit: 64Gi$", text, re.M) and "medium: Memory" in text
    assert re.search(r"- name: slime-data\n\s+mountPath: /data\n", text)
    assert re.search(r"- name: slime-functions\n\s+mountPath: /opt/slime-functions\n", text)
    assert "claimName: slime-data" in text and "workerGroupSpecs" not in text   # head only, no workers
    assert "shutdownAfterJobFinishes: true" in text and "ttlSecondsAfterFinished: 60" in text
    d.set("RAYJOB_CPUS", 16)
    d.set("RAYJOB_MEM", "200 Gi")
    d.set("RAYJOB_SHM", "64 Gi")
    d.set("RAYJOB_TTL", 60)

    # --- the run's parsed arguments agree --------------------------------------------------------------
    log_text = (RUN / "driver.log").read_text()
    log = log_text.splitlines()
    a0, a1 = log.index("------------------------ arguments ------------------------"), \
        log.index("-------------------- end of arguments ---------------------")
    table = "\n".join(log[a0:a1])
    assert arg(table, "global_batch_size") == "64" and arg(table, "num_steps_per_rollout") == "None"
    assert arg(table, "rollout_batch_size") == "8" and arg(table, "n_samples_per_prompt") == "8"
    assert arg(table, "num_rollout") == "10" and arg(table, "advantage_estimator") == "grpo"
    for k in ("world_size", "tensor_model_parallel_size", "context_parallel_size", "pipeline_model_parallel_size",
              "data_parallel_size", "actor_num_gpus_per_node"):
        assert arg(table, k) == "1", k
    assert arg(table, "load") == arg(table, "ref_load") == "/data/models/Qwen3-0.6B_torch_dist"
    assert arg(table, "train_backend") == "megatron" and arg(table, "rollout_external") == "True"
    assert arg(table, "use_kl_loss") == "False" and arg(table, "kl_coef") == "0.0"   # no reference model in use
    assert ("using world size: 1, data-parallel size: 1, context-parallel size: 1, hierarchical context-parallel "
            "sizes: None, tensor-model-parallel size: 1, pipeline-model-parallel size: 1") in log_text
    # one optimizer update per rollout: 10 rollouts, 10 training steps (train/step 0..9)
    assert sorted(set(re.findall(r"train_metric_utils\.py:\d+ - rollout (\d+):", log_text)), key=int) == \
        [str(i) for i in range(10)]
    assert re.findall(r"model\.py:\d+ - step (\d+):", log_text) == [str(i) for i in range(10)]
    assert "loading release distributed checkpoint from /data/models/Qwen3-0.6B_torch_dist" in log_text
    assert "MegatronTrainRayActor pid=" in log_text and "RolloutManager pid=" in log_text

    # --- apply output: run 1 of the rr phase --------------------------------------------------------------
    phase = (RUNS / "rr" / "phase.log").read_text().splitlines()
    i = next(k for k, l in enumerate(phase) if l.endswith("rr: run 1: apply RayJob"))
    assert phase[i + 1] == "rayjob.ray.io/slime-grpo created", phase[i + 1]
    d.set("RAYJOB_APPLY_OUT", phase[i + 1])

    # --- the Ray cluster and its Pods: the RayJob as it ended, and the run's events ---------------------
    import yaml   # PyYAML, in the venv
    rj = yaml.safe_load((RUN / "rayjob.yaml").read_text())
    st = rj["status"]
    cluster, head = st["rayClusterName"], st["rayClusterStatus"]["head"]["podName"]
    job_id = st["jobId"]
    created = rj["metadata"]["creationTimestamp"]
    ready = next(c["lastTransitionTime"] for c in st["rayClusterStatus"]["conditions"] if c["type"] == "HeadPodReady")
    assert (cluster, head, job_id) == ("slime-grpo-cvxr4", "slime-grpo-cvxr4-head-hq4nk", "slime-grpo-w9d8v")
    assert created == "2026-11-14T03:51:56Z" and ready == "2026-11-14T03:52:09Z", (created, ready)
    d.set("RAYJOB_CLUSTER", cluster)
    d.set("RAYJOB_HEAD_POD", head)
    d.set("RAYJOB_HEAD_READY_SECS", secs(created, ready))
    summ = json.loads((RUN / "summary.json").read_text())
    assert summ["rayjob_start"].startswith("2026-11-14T03:51:56") and summ["succeeded_job"] == job_id
    found = summ["timeline"]["engines_detected"]
    assert found == 47, found
    d.set("RAYJOB_FOUND_SECS", found)

    ev = (RUN / "events.txt").read_text().splitlines()
    subs = [re.search(r"Created pod: (slime-grpo-\w+)$", l).group(1) for l in ev
            if re.search(r"\s+SuccessfulCreate\s+job/slime-grpo\s+Created pod: ", l)]
    # several runs share the RayJob's name; this run's submitter is the one created 2m52s before the capture,
    # with the head Pod's events at 3m5s (the RayJob's creation, 03:51:56)
    mine = [l for l in ev if l.startswith("2m52s ") and "SuccessfulCreate" in l]
    assert len(mine) == 1, mine
    sub = re.search(r"Created pod: (slime-grpo-\w+)$", mine[0]).group(1)
    assert sub in subs and sub == "slime-grpo-brjh7", sub
    for pod in (head, sub):
        assert any(re.search(rf"Scheduled\s+pod/{pod}\s+Successfully assigned slime-kv/{pod} to ", l) for l in ev), pod
        assert any(re.search(rf"pod/{pod}\s+Container image \"slimerl/slime:nightly-dev-20261027a-cu130\" "
                             r"already present on machine", l) for l in ev), pod
    assert any(re.search(rf"CreatedHeadPod\s+raycluster/{cluster}\s+Created head Pod slime-kv/{head}$", l) for l in ev)
    d.set("RAYJOB_SUB_POD", sub)

    # --- the two log lines the beat shows -------------------------------------------------------------------
    pat = re.compile(r"submitted successfully|Detected external")
    hits = [(k + 1, l) for k, l in enumerate(log) if pat.search(l)]
    assert [n for n, _ in hits] == [3, 28], [n for n, _ in hits]   # grep -E prints exactly these two
    m = re.fullmatch(r"(\S+ (\S+))\t(SUCC cli\.py:\d+ -- )\x1b\[32m(Job '([^']+)' submitted successfully)\x1b\[39m",
                     hits[0][1])
    assert m and m.group(5) == job_id, repr(hits[0][1])
    t_sub = m.group(2)                                   # 03:52:15,160
    d.set("RAYJOB_LOG_SUB_PRE", f"{m.group(1)}\t{m.group(3)}".expandtabs(8))   # the tab, as the terminal shows it
    d.set("RAYJOB_LOG_SUB_MSG", m.group(4))              # Ray's CLI prints it in green
    det = hits[1][1]
    m2 = re.match(r"\[2026-11-14 (\d\d:\d\d:\d\d)\] external\.py:\d+ - Detected external SGLang engines: ", det)
    assert m2 and "'url': 'http://slime-sglang-engine-0:30000'" in det and "'url': 'http://slime-sglang-engine-1:30000'" in det
    rows = wrap_cols(det)
    assert len(rows) == 3 and all(len(r) <= COLS for r in rows), [len(r) for r in rows]
    d.array("RAYJOB_LOG_ENGINES", rows)
    gap = (datetime.strptime(m2.group(1), "%H:%M:%S") - datetime.strptime(t_sub, "%H:%M:%S,%f")).total_seconds()
    d.set("RAYJOB_LOG_GAP", round(gap))
    d.set("RAYJOB_LOG_CMD", "kubectl logs -n $NAMESPACE -f job/slime-grpo | grep -E 'submitted successfully|Detected external'")

    # --- the image's Slime source (optional; the same image tag as the run) ------------------------------------
    if UPSTREAM.is_dir():
        a = (UPSTREAM / "slime" / "utils" / "arguments.py").read_text()
        choices = re.findall(r'"(\w+)"', re.search(r'"--advantage-estimator",\s*type=str,\s*choices=\[(.*?)\]', a, re.S).group(1))
        assert choices == ["grpo", "gspo", "cispo", "reinforce_plus_plus", "reinforce_plus_plus_baseline", "ppo"], choices
        assert "When --load is not set, this will be used as the initial checkpoint for training." in a
        assert "if you hope to train 1 step for each rollout, the global_bach_size should be set as" in a
        print("checked against the image's Slime source in", UPSTREAM)
    else:
        print("note: no", UPSTREAM, "(the image's Slime source); its assertions were skipped")
    d.set("RAYJOB_OTHER_ALGOS", "PPO, GSPO, CISPO, and REINFORCE++")

    d.write()
    print(f"rayjob.yaml: {len(lines)} lines in {len(SEGMENTS)} segments; log gap {gap:.2f} s; head ready "
          f"{secs(created, ready)} s after the RayJob's creation")


if __name__ == "__main__":
    main()
