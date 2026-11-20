"""Fill slime-retool-demo.template.sh with values from the saved run.

Usage: python3 -I fill_demo.py [--sample N]

Reads ../runs/setup, ../runs/deploy, ../runs/run1 (run tools/analyze_retool.py on
run1 first: it writes summary.json and turns.joined.jsonl) and ../manifests, and
writes slime-retool-demo.sh: the template with its "# @DATA@" line replaced by
bash assignments. Every number and string the replay prints comes from here:

  setup       runs/setup/{job.yaml, setup.log, storage-apply.txt, dataset-rows.txt}
  DGD         runs/deploy/{dgd-ready.txt, pods-at-ready.json, pods-watch.txt,
              sidecar-kv-events.txt}, runs/deploy.console.log (apply output)
  manifests   real `grep` output of manifests/dgd.yaml and manifests/rayjob.yaml
  functions   lines of functions/generate_with_retool.py (checked against the file)
  RayJob      runs/run1/{rayjob.yaml, pods-watch.txt}
  loop        runs/run1/summary.json (driver-log timestamps and per-step metrics)
  trajectory  tools/trajectory.py's rendering of one sample from turns.joined.jsonl
  closing     runs/run1/summary.json (turn logs joined to the frontend's routing log)

--sample picks the trajectory; the default is DEFAULT_SAMPLE below.
"""

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent
RUNS = KIT / "runs"
RUN = RUNS / "run1"
# Picked with `tools/trajectory.py runs/run1 --pick`: step 3, 5 requests all on worker-0, a one-line
# question, 3-line code blocks, a correct answer, and 93-98% of each follow-up's prompt cached.
DEFAULT_SAMPLE = 958

EXCERPT_SOURCE = KIT / "functions" / "generate_with_retool.py"
EXCERPT = '''for turn in range(TOOL_CONFIGS["max_turns"]):
    current_token_ids = prompt_tokens_ids + response_token_ids
    payload = {
        "input_ids": current_token_ids,
        "sampling_params": per_turn_sampling_params,
        "return_logprob": True,
        "rid": f"retool-{sample.index}-t{turn}-{uuid.uuid4().hex[:8]}",
    }
    output, timing = await _stream_generate(args, url, payload)
    ...
    response_token_ids += cur_response_token_ids
    next_obs, done, tool = await execute_predictions(cur_response)
    if done:
        break
    obs_tokens_ids = state.tokenizer(next_obs, add_special_tokens=False)["input_ids"]
    response_token_ids += obs_tokens_ids'''


def q(value) -> str:
    """Single-quote a value for bash."""
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def arr(values) -> str:
    return "(" + " ".join(q(v) for v in values) + ")"


def iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def secs(a, b) -> int:
    return round((iso(b) - iso(a)).total_seconds())


def load_yaml(path):
    import yaml  # PyYAML

    return yaml.safe_load(path.read_text())


def grep(args, path) -> str:
    out = subprocess.run(["grep", *args, str(path)], check=True, capture_output=True, text=True).stdout
    return out.rstrip("\n")


def snapshots(path):
    """[(iso time, [row dicts])] from a pods-watch.txt of `kubectl get pods -o wide` frames."""
    frames, cur, header = [], None, None
    for line in path.read_text().splitlines():
        if line.startswith("=== "):
            cur = (line[4:].strip(), [])
            frames.append(cur)
            header = None
        elif cur is not None and line.startswith("NAME"):
            header = line
        elif cur is not None and header and line.strip():
            parts = line.split()
            cur[1].append({"name": parts[0], "ready": parts[1], "status": parts[2], "restarts": parts[3], "age": parts[4]})
    return frames


def kubectl_table(rows):
    """Format NAME READY STATUS RESTARTS AGE the way kubectl pads columns (widest + 3)."""
    cols = ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
    keys = ["name", "ready", "status", "restarts", "age"]
    width = [max(len(c), *(len(r[k]) for r in rows)) + 3 for c, k in zip(cols, keys)]
    lines = ["".join(c.ljust(w) for c, w in zip(cols[:-1], width)) + cols[-1]]
    for r in rows:
        lines.append("".join(r[k].ljust(w) for k, w in zip(keys[:-1], width)) + r[keys[-1]])
    return "\n".join(lines)


def main():
    sample_arg = sys.argv[sys.argv.index("--sample") + 1] if "--sample" in sys.argv else DEFAULT_SAMPLE
    if sample_arg is None:
        raise SystemExit("pick a trajectory: python3 -I ../tools/trajectory.py ../runs/run1 --pick, then --sample N")
    sample = int(sample_arg)
    data = {}
    arrays = {}

    # --- Step 1: setup Job --------------------------------------------------
    job = load_yaml(RUNS / "setup" / "job.yaml")
    start, done = job["status"]["startTime"], job["status"]["completionTime"]
    log = (RUNS / "setup" / "setup.log").read_text()
    dl = re.search(r"downloads done (\S+)", log).group(1)
    saved = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\] successfully saved checkpoint", log).group(1)
    data["SETUP_SECS"] = secs(start, done)
    data["SETUP_DL_T"] = secs(start, dl)
    data["SETUP_CONVERT_T"] = secs(start, saved.replace(" ", "T") + "Z")
    manifest = (KIT / "manifests" / "setup-job.yaml").read_text()
    data["SETUP_MODEL"] = re.search(r"hf download (\S+) --local-dir", manifest).group(1)
    data["SETUP_DATA"] = re.search(r"hf download --repo-type dataset (\S+)", manifest).group(1)
    data["SETUP_TAIL"] = "\n".join(log.rstrip("\n").splitlines()[-3:])
    data["STORAGE_OUT"] = (RUNS / "setup" / "storage-apply.txt").read_text().rstrip("\n")
    data["DATA_ROWS"] = f"{int((RUNS / 'setup' / 'dataset-rows.txt').read_text().strip()):,}"

    # --- Step 2: DGD ----------------------------------------------------------
    ready = dict(line.split("=", 1) for line in (RUNS / "deploy" / "dgd-ready.txt").read_text().split())
    apply_t = ready["dgd_apply"]
    data["DGD_READY_T"] = int(ready["dgd_ready_secs"])
    data["KV_GREP_OUT"] = grep(["-m", "4", "-A1", "-E", "^ +- (name: DYN_ROUTER|--page-size|--kv-events)"],
                               KIT / "manifests" / "dgd.yaml")
    console = (RUNS / "deploy.console.log").read_text().splitlines()
    data["DGD_APPLY_OUT"] = "\n".join(l for l in console if re.match(r"^(service|dynamographdeployment)\S*/\S+ created$", l))
    pods = json.loads((RUNS / "deploy" / "pods-at-ready.json").read_text())["items"]
    role = {p["metadata"]["labels"]["dynamo.nvidia.com/slime-role"]: p for p in pods}
    order = ["frontend", "worker-0", "worker-1", "worker-2", "worker-3"]
    names = [role[r]["metadata"]["name"] for r in order]
    arrays["POD_NAMES"] = names
    arrays["POD_CREATED_T"] = [secs(apply_t, role[r]["metadata"]["creationTimestamp"]) for r in order]
    tr = []
    last = {}
    for t_iso, rows in snapshots(RUNS / "deploy" / "pods-watch.txt"):
        for r in rows:
            if r["name"] in names:
                i = names.index(r["name"])
                state = (r["status"], r["ready"])
                if last.get(i) != state:
                    last[i] = state
                    tr.append((secs(apply_t, t_iso), i, r["status"], r["ready"]))
    tr.sort()
    arrays["TR_T"] = [x[0] for x in tr]
    arrays["TR_I"] = [x[1] for x in tr]
    arrays["TR_S"] = [x[2] for x in tr]
    arrays["TR_R"] = [x[3] for x in tr]
    data["WATCH_FROM_T"] = min(arrays["POD_CREATED_T"])
    sidecar = (RUNS / "deploy" / "sidecar-kv-events.txt").read_text().splitlines()
    arrays["SIDECAR_LINES"] = [re.search(r"discovered SGLang ZMQ KV-event sources.*block_size=16", l).group(0)
                               for l in sidecar if "discovered SGLang ZMQ KV-event sources" in l]
    assert len(arrays["SIDECAR_LINES"]) == 4, arrays["SIDECAR_LINES"]

    # --- Step 3: functions ------------------------------------------------------
    source = EXCERPT_SOURCE.read_text()
    for line in EXCERPT.splitlines():
        if line.strip() not in ("...", "}") and line.strip() not in source:
            raise SystemExit(f"excerpt line not in {EXCERPT_SOURCE.name}: {line!r}")
    data["GENERATE_EXCERPT"] = EXCERPT

    # --- Step 4: RayJob -------------------------------------------------------
    data["RAYJOB_GREP_OUT"] = "\n".join(line[:92] for line in grep(
        ["-E", "^ +--(rollout-ext|custom-|rollout-stop|update-weight-t)"], KIT / "manifests" / "rayjob.yaml").splitlines())
    rj = load_yaml(RUN / "rayjob.yaml")
    rj_start = rj["status"]["startTime"]
    for t_iso, rows in snapshots(RUN / "pods-watch.txt"):
        head = [r for r in rows if re.match(r"^slime-retool-[a-z0-9]{5}-head-", r["name"])]
        sub = [r for r in rows if re.match(r"^slime-retool-[a-z0-9]{5}$", r["name"])]
        if head and sub and head[0]["status"] == "Running" and sub[0]["status"] == "Running":
            data["RAYJOB_PODS_OUT"] = kubectl_table(sub + head)
            break
    else:
        raise SystemExit("no snapshot with both the head and the submitter Pod running")

    # --- Step 6: loop ----------------------------------------------------------
    s = json.loads((RUN / "summary.json").read_text())
    steps = [st for st in s["steps"] if st["rollout_end"]]
    assert len(steps) == 10, len(steps)
    data["ENGINES_T"] = secs(rj_start, s["engines_detected"])
    data["FIRST_SYNC_T"] = secs(rj_start, s["sync_ends"][0])
    data["FIRST_SYNC_SECS"] = f"{s['sync_secs'][0]:.1f}"
    arrays["ROLLOUT_T"] = [secs(rj_start, st["rollout_end"]) for st in steps]
    arrays["TRAIN_T"] = [secs(rj_start, st["train_end"]) for st in steps]
    arrays["SYNC_T"] = [secs(rj_start, st["sync_end"]) if st["sync_end"] else secs(rj_start, st["train_end"]) for st in steps]
    arrays["ROLL_SECS"] = [f"{st['rollout_time']:.0f}" for st in steps]
    arrays["RPT"] = [f"{st['requests_per_trajectory']:.1f}" for st in steps]
    arrays["HIT"] = [f"{100 * st['hit_rate']:.0f}" for st in steps]
    arrays["STAY"] = [f"{100 * st['same_engine']:.0f}" for st in steps]
    arrays["TRAIN_SECS"] = [f"{st['train_time']:.0f}" for st in steps]
    arrays["SYNC_SECS"] = [f"{st['weight_sync_time']:.1f}" if st["weight_sync_time"] else "–" for st in steps]
    data["DONE_T"] = secs(rj_start, rj["status"]["endTime"])
    data["JOB_ID"] = rj["status"]["jobId"]
    milestones = [data["ENGINES_T"], data["FIRST_SYNC_T"], *arrays["ROLLOUT_T"], *arrays["TRAIN_T"], *arrays["SYNC_T"], data["DONE_T"]]

    # --- Step 5: trajectory ---------------------------------------------------
    spec = importlib.util.spec_from_file_location("trajectory", KIT / "tools" / "trajectory.py")
    traj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(traj)
    by_sample, finals = traj.load(RUN)
    head, qlines, blocks = traj.render(by_sample[sample], finals.get(sample), 2, 1)
    data["TRAJ_CMD"] = f"python3 tools/trajectory.py runs/run1 --sample {sample} 2 1"
    data["TRAJ_HEAD"] = head
    arrays["TRAJ_Q"] = qlines
    flat, starts = [], []
    for b in blocks:
        starts.append(len(flat))
        flat += b
    arrays["TRAJ_LINES"] = flat
    arrays["TRAJ_TURN_START"] = starts
    ts = by_sample[sample]
    follow = ts[1:]
    lo = min(100 * (t["cached_tokens"] or 0) / t["prompt_tokens"] for t in follow)
    hi = max(100 * (t["cached_tokens"] or 0) / t["prompt_tokens"] for t in follow)
    new = [t["prompt_tokens"] - (t["cached_tokens"] or 0) for t in follow]
    obs = [t.get("obs_tokens") for t in ts[:-1] if t.get("obs_tokens") is not None]
    engine = ts[0]["engine"]
    data["TRAJ_NOTE1"] = (f"Turns 2-{len(ts)} found {lo:.0f}-{hi:.0f}% of their prompt in {engine}'s cache and "
                          f"prefilled {min(new)}-{max(new)} new tokens each:")
    data["TRAJ_NOTE2"] = (f"the sandbox output ({min(obs)}-{max(obs)} tokens) plus the tail that didn't fill "
                          f"a 16-token cache block.")

    # --- Step 7: closing --------------------------------------------------------
    ck = s["computed_by_kind"]
    fol = ck["followup_stayed"]["requests"] + ck["followup_moved"]["requests"]
    data["TOTAL_TRAJ"] = f"{s['trajectories']:,}"
    data["TOTAL_REQ"] = f"{s['requests_logged']:,}"
    data["RPT_ALL"] = f"{s['requests_logged'] / s['trajectories']:.1f}"
    data["CACHED_ALL"] = f"{s['cached_tokens'] / 1e6:.1f}"
    data["PROMPT_ALL"] = f"{s['prompt_tokens'] / 1e6:.1f}"
    data["HIT_ALL"] = f"{100 * s['hit_rate']:.0f}"
    data["STAY_ALL"] = f"{100 * s['same_engine']:.0f}"
    data["REUSE_STAYED"] = f"{100 * (1 - ck['followup_stayed']['computed_tokens'] / ck['followup_stayed']['prompt_tokens']):.0f}"
    data["MOVED_ALL"] = f"{100 * ck['followup_moved']['requests'] / fol:.0f}"
    data["MOVED_RECOMPUTED"] = f"{ck['followup_moved']['computed_tokens'] / 1e6:.1f}"
    data["COMPUTED_ALL"] = f"{s['computed_tokens'] / 1e6:.1f}"
    pe = s["per_engine"]
    data["ROUTED_PER_ENGINE"] = " / ".join(f"{pe.get(f'worker-{i}', 0):,}" for i in range(4))

    lines = ["# Filled by fill_demo.py from ../runs (see its docstring for each source)."]
    for k, v in data.items():
        lines.append(f"{k}={v}" if isinstance(v, int) else f"{k}={q(v)}")
    for k, v in arrays.items():
        lines.append(f"{k}={arr(v)}")
    lines.append(f"MILESTONES={arr(milestones)}")
    text = (HERE / "slime-retool-demo.template.sh").read_text()
    marker = "\n# @DATA@\n"
    assert text.count(marker) == 1
    out = HERE / "slime-retool-demo.sh"
    out.write_text(text.replace(marker, "\n" + "\n".join(lines) + "\n"))
    out.chmod(0o755)
    for k in ["SETUP_SECS", "DGD_READY_T", "ENGINES_T", "FIRST_SYNC_T", "DONE_T", "TOTAL_REQ", "HIT_ALL", "STAY_ALL",
              "REUSE_STAYED", "MOVED_ALL", "MOVED_RECOMPUTED", "COMPUTED_ALL", "ROUTED_PER_ENGINE"]:
        print(f"{k:18s} {data[k]}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
