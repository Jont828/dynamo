"""Data for beats/05_slime.sh (Step 4: set up the training side with Slime): the slime-functions ConfigMap.

Usage: $PY -I fill/05_slime.py   (run.sh and build.sh run it for you)

Kit root = slime-kv-routing/. Sources:
  manifests/tutorial/slime-functions.yaml  the ConfigMap typed in vim: all 33 lines (it has no header comment),
                                           split where the beat changes speed or pauses (SLIME_Y_*), and the
                                           buffer lines each callout marks (SLIME_C*_FROM/TO)
  manifests/tutorial/rayjob.yaml           asserted: the flags that load both functions, --rm-type math, the
                                           ConfigMap mounted at /opt/slime-functions and on PYTHONPATH, and
                                           DYNAMO_ROLLOUT_URL (the callouts say "set in Step 5")
  runs/rr/run1/driver.log                  asserted: the run's parsed arguments agree (custom generate path, post-write
                                           hook, rm_type math, no custom reward function)
  demo/team/snapshots/05_configmap*.txt    read-only snapshots of the cluster (2026-11-19, 04-05_capture.sh). The kit
                                           applied the ConfigMap once at setup, before its runs, and kept no output,
                                           so the beat shows the apply with lib.sh's unsaved_note, then
                                           `kubectl get configmap` (05_configmap.txt); asserted: kubectl apply created
                                           it on 2026-11-14 (05_configmap-created.txt), and its data is the tutorial
                                           file's, byte for byte (05_configmap-data.txt)
  ../slime-retool/upstream/image/   optional, read-only: the Slime image's own source (the same image tag).
                                           Asserted when present: generate_streaming streams /generate to
                                           sglang_router_ip:port and sets abort_mode "request"; the post-write hook
                                           runs after the files are written, before the engines read them; rm_type
                                           math is 1 or 0
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves it off sys.path
from _common import KIT, RUNS, TEAM, Data, check_typed, read_lines  # noqa: E402

CM_REL = "manifests/tutorial/slime-functions.yaml"
RAYJOB_REL = "manifests/tutorial/rayjob.yaml"
SNAP = TEAM / "snapshots"
UPSTREAM = KIT.parent / "slime-retool" / "upstream" / "image"

# Typing segments: (name, first line's exact text). Each runs to the next segment's first line.
SEGMENTS = [
    ("HEAD", "apiVersion: v1"),                                          # fast
    ("GENKEY", "  dynamo_generate.py: |"),                               # normal
    ("IMPORTS", "    import copy"),                                      # fast
    ("FUNC", "    async def generate_streaming(args, sample, sampling_params):"),   # normal
    ("ABORT", ""),                                                       # normal (the 2 blank lines + abort_mode)
    ("PERM", "  weight_permissions.py: |"),                              # fast
]

# The buffer lines each callout marks (vim_callout marks one range, FROM..TO): first and last line's text.
MARKS = {
    "C1": ("    from slime.rollout.sglang_streaming_rollout import generate_streaming as slime_generate_streaming",
           '    generate_streaming.abort_mode = "request"'),
    "C2": ("  dynamo_generate.py: |", '    generate_streaming.abort_mode = "request"'),
    "C3": ("  weight_permissions.py: |", "        os.chmod(version_dir, 0o755)"),
}


def find(lines, text, start=1):
    """1-based number of the first line equal to text, from line start."""
    for i in range(start - 1, len(lines)):
        if lines[i] == text:
            return i + 1
    raise SystemExit(f"05_slime.py: no line {text!r} in {CM_REL} (from line {start})")


def snapshot(name):
    """The output part of a snapshots/ file (after '# ---'), and its '# screen:' command."""
    text = (SNAP / name).read_text()
    head, sep, out = text.partition("# ---\n")
    assert sep, f"{name}: no '# ---' line"
    screen = re.search(r"^# screen: (.*)$", head, re.M).group(1)
    return screen, out.rstrip("\n")


def main():
    lines = read_lines(CM_REL)
    assert lines[0] == "apiVersion: v1" and lines[1] == "kind: ConfigMap", lines[:2]
    check_typed(lines, CM_REL, "05_slime.py")
    d = Data(__file__)
    d.source(CM_REL, RAYJOB_REL, "runs/rr/run1/driver.log", "demo/team/snapshots/05_configmap.txt",
             "demo/team/snapshots/05_configmap-created.txt", "demo/team/snapshots/05_configmap-data.txt")

    # --- typing segments ------------------------------------------------------------------------
    starts, at = [], 1
    for name, text in SEGMENTS:
        if name == "ABORT":      # the first blank line after the function's return
            at = find(lines, "        return await slime_generate_streaming(frontend_args, sample, sampling_params)", at) + 1
            assert lines[at - 1] == "", lines[at - 1]
        else:
            at = find(lines, text, at)
        starts.append(at)
    assert starts[0] == 1 and starts == sorted(starts) and len(set(starts)) == len(starts), starts
    joined = []
    for k, (name, _) in enumerate(SEGMENTS):
        a = starts[k]
        b = starts[k + 1] if k + 1 < len(SEGMENTS) else len(lines) + 1
        d.array(f"SLIME_Y_{name}", lines[a - 1:b - 1])
        joined += lines[a - 1:b - 1]
    assert joined == lines, "segments don't cover the file exactly"
    d.set("SLIME_FILE", CM_REL)

    for name, (t1, t2) in MARKS.items():
        a, b = find(lines, t1), find(lines, t2)
        assert a <= b, (name, a, b)
        d.set(f"SLIME_{name}_FROM", a)
        d.set(f"SLIME_{name}_TO", b)

    # --- what the callouts say about the code (the file itself) ----------------------------------
    src = "\n".join(lines)
    cm_name = re.search(r"^  name: (\S+)$", src, re.M).group(1)
    assert cm_name == "slime-functions"
    d.set("SLIME_CM", cm_name)
    assert "from slime.rollout.sglang_streaming_rollout import generate_streaming as slime_generate_streaming" in src
    assert 'frontend = urlsplit(os.environ["DYNAMO_ROLLOUT_URL"])' in src
    assert "frontend_args.sglang_router_ip = frontend.hostname" in src
    assert "frontend_args.sglang_router_port = frontend.port" in src
    assert "return await slime_generate_streaming(frontend_args, sample, sampling_params)" in src
    assert 'generate_streaming.abort_mode = "request"' in src
    # the hook: 755 on directories (and the version directory), 644 on files
    assert "os.chmod(os.path.join(root, name), 0o755)" in src and "os.chmod(os.path.join(root, name), 0o644)" in src
    assert src.rstrip().endswith("os.chmod(version_dir, 0o755)")

    # --- Step 5 loads them (the callouts and the apply narration say so) --------------------------
    rj = (KIT / RAYJOB_REL).read_text()
    assert "--custom-generate-function-path dynamo_generate.generate_streaming" in rj
    assert "--custom-update-weight-post-write-path weight_permissions.make_weights_readable" in rj
    assert "--rm-type math" in rj and "--custom-rm-path" not in rj
    assert 'PYTHONPATH: "/root/Megatron-LM:/root/slime:/opt/slime-functions"' in rj
    assert re.search(r"- name: slime-functions\n\s+mountPath: /opt/slime-functions\n", rj)
    assert re.search(r"configMap:\n\s+name: slime-functions\n", rj)
    assert 'DYNAMO_ROLLOUT_URL: "http://slime-sglang-frontend:8000"' in rj
    args = (RUNS / "rr" / "run1" / "driver.log").read_text()
    for k, v in [("custom_generate_function_path", "dynamo_generate.generate_streaming"),
                 ("custom_update_weight_post_write_path", "weight_permissions.make_weights_readable"),
                 ("rm_type", "math"), ("custom_rm_path", "None"), ("update_weight_transport", "disk")]:
        assert re.search(rf"^  {k} \.+ {re.escape(v)}$", args, re.M), (k, v)

    # --- the ConfigMap on the cluster (read-only snapshots) -----------------------------------------
    screen, out = snapshot("05_configmap.txt")
    assert screen == "kubectl get configmap -n $NAMESPACE slime-functions", screen
    rows = out.splitlines()
    assert rows[0].split() == ["NAME", "DATA", "AGE"] and rows[1].split()[:2] == ["slime-functions", "2"], rows
    d.set("SLIME_GET_CMD", screen)
    d.array("SLIME_GET_OUT", rows)
    snap_date = re.search(r"^# date:   (\d{4}-\d\d-\d\d)T", (SNAP / "05_configmap.txt").read_text(), re.M).group(1)
    d.set("SLIME_SNAP_DATE", snap_date)
    _, created = snapshot("05_configmap-created.txt")
    c = created.splitlines()
    assert c[0] == "2026-11-14T03:13:26Z" and c[1].startswith("kubectl-client-side-apply Update 2026-11-14T03:13:26Z"), c
    d.set("SLIME_CREATED", "2026-11-14")
    d.set("SLIME_SETUP_AT", "2026-11-14 03:13")   # lib.sh unsaved_note's time (UTC), as beat 04's Services
    _, data = snapshot("05_configmap-data.txt")
    import yaml   # PyYAML, in the venv
    want = yaml.safe_load((KIT / CM_REL).read_text())["data"]
    assert json.loads(data) == want, "the cluster's ConfigMap data differs from the tutorial file"

    # --- the image's Slime source (optional; the same image tag as the run) --------------------------
    if UPSTREAM.is_dir():
        st = (UPSTREAM / "slime" / "rollout" / "sglang_streaming_rollout.py").read_text()
        assert "async def generate_streaming(args: Namespace, sample: Sample, sampling_params: dict[str, Any]) -> Sample:" in st
        assert 'url = f"http://{args.sglang_router_ip}:{args.sglang_router_port}/generate"' in st
        assert '"stream": True,' in st and 'generate_streaming.abort_mode = "request"' in st
        a = (UPSTREAM / "slime" / "utils" / "arguments.py").read_text()
        assert "called on each trainer rank after a disk weight" in a and "before the engines read them" in a
        assert "Set `abort_mode = 'request'` on the function when it implements request-level abort" in a
        rm = (UPSTREAM / "slime" / "rollout" / "rm_hub" / "__init__.py").read_text()
        assert 'elif rm_type == "math":\n        return 1 if grade_answer_verl(response, label) else 0' in rm
        print("checked against the image's Slime source in", UPSTREAM)
    else:
        print("note: no", UPSTREAM, "(the image's Slime source); its assertions were skipped")

    d.write()


if __name__ == "__main__":
    main()
