"""Data for beats/04_serve.sh (Step 3: serve an inference endpoint with Dynamo).

Reads (kit root = slime-retool/):
  manifests/dgd.yaml                 typed in vim (its kit-internal header comment left out); checks that
                                     worker-1..3 are worker-0 with another name and label
  manifests/engine-services.yaml     the first Service, shown with `head`; checks that the other three are
                                     the same with another name and selector
  manifests/rayjob.yaml              the frontend URL Slime sends rollouts to (DYNAMO_ROLLOUT_URL)
  runs/deploy.console.log            output of the apply (the Services and the DGD)
  runs/deploy/dgd-ready.txt          when the DGD was applied; seconds until Ready
  runs/deploy/dgd-status.yaml        the DGD's creation time, Ready condition, and backend
  runs/deploy/pods-at-ready.json     each Pod's name, role label, and creation time
  runs/deploy/pods-watch.txt         `kubectl get pods` every few seconds: each Pod's STATUS and READY
  runs/deploy/sidecar-kv-events.txt  each engine's sidecar line for its KV-event source

The beat types SERVE_DGD in parts that start at the SERVE_LN_* lines (1-based buffer lines).
"""

import pathlib
import re
import sys

import yaml  # PyYAML, in the venv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import (Data, check_in_file, check_typed, find_line, kit_path, load_json, load_yaml,  # noqa: E402
                     manifest_lines, max_width, read_lines, secs)

DGD = "manifests/dgd.yaml"
SVC = "manifests/engine-services.yaml"
ROLES = ["frontend", "worker-0", "worker-1", "worker-2", "worker-3"]

d = Data(__file__)
d.source(DGD, SVC, "manifests/rayjob.yaml", "runs/deploy.console.log", "runs/deploy/dgd-ready.txt",
         "runs/deploy/dgd-status.yaml", "runs/deploy/pods-at-ready.json", "runs/deploy/pods-watch.txt",
         "runs/deploy/sidecar-kv-events.txt")


def same_but(block, ref, k, words, allowed):
    """block == ref with each WORD-0 replaced by WORD-k, and only the allowed lines differ."""
    subst = list(ref)
    for w in words:
        subst = [line.replace(f"{w}-0", f"{w}-{k}") for line in subst]
    assert block == subst, f"block {k} differs from block 0 beyond the names"
    differ = [b.strip() for a, b in zip(ref, block) if a != b]
    assert differ == allowed, differ


def fmt_age(s: int) -> str:
    """kubectl's AGE (HumanDuration), up to an hour."""
    if s < 120:
        return f"{s}s"
    return f"{s // 60}m" if s % 60 == 0 else f"{s // 60}m{s % 60}s"


# --- dgd.yaml, typed in vim -------------------------------------------------------------------
lines = manifest_lines(DGD)
check_typed(lines, DGD)
check_in_file("\n".join(lines), DGD)
assert lines[-1] == read_lines(DGD)[-1]
d.array("SERVE_DGD", lines)
d.set("SERVE_DGD_FILE", DGD)

engine_image = "nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.1"
ln = {
    "SERVE_LN_FRONTEND": find_line(lines, "  - name: frontend"),
    "SERVE_LN_ENV": find_line(lines, "- name: DYN_SGLANG_ENABLE_GENERATE"),
    "SERVE_LN_W0": find_line(lines, "  - name: worker-0"),
    "SERVE_LN_W0_IMAGE": find_line(lines, f"image: {engine_image}"),
    "SERVE_LN_W0_LAUNCH": find_line(lines, "- sglang.launch_server"),
    "SERVE_LN_W0_PAGE": find_line(lines, "- --page-size"),
    "SERVE_LN_W0_SIDECAR_ARGS": find_line(lines, '"--grpc-endpoint","http://127.0.0.1:30001"'),
    "SERVE_LN_W1": find_line(lines, "  - name: worker-1"),
}
for name, value in ln.items():
    d.set(name, value)


def part(a, b):
    """Buffer lines a..b (1-based, inclusive)."""
    return lines[a - 1:b]


head = part(1, ln["SERVE_LN_FRONTEND"] - 1)
assert head == ["apiVersion: nvidia.com/v1beta1", "kind: DynamoGraphDeployment", "metadata:", "  name: slime-sglang",
                "spec:", "  backendFramework: sglang", "  components:"], head
assert part(ln["SERVE_LN_ENV"] - 1, ln["SERVE_LN_ENV"] - 1) == ["          env:"]
env = part(ln["SERVE_LN_ENV"], ln["SERVE_LN_W0"] - 1)
assert [x.strip() for x in env] == ["- name: DYN_SGLANG_ENABLE_GENERATE", "value: '1'", "- name: DYN_ROUTER_MODE",
                                    "value: kv", "- name: DYN_ROUTER_PREDICTED_TTL_SECS", "value: '5'"], env
cmd = part(ln["SERVE_LN_W0_IMAGE"], ln["SERVE_LN_W0_LAUNCH"])
assert [x.strip() for x in cmd] == [f"image: {engine_image}", "command:", "- python3", "- -m",
                                    "- sglang.launch_server"], cmd
kv = part(ln["SERVE_LN_W0_PAGE"], ln["SERVE_LN_W0_SIDECAR_ARGS"])
assert [x.strip() for x in kv[::2]] == ["- --page-size", "- --kv-events-config", "- --sidecar", "- --sidecar-args"], kv
assert kv[1].strip() == "- '16'" and "zmq" in kv[3] and "tcp://*:5557" in kv[3] and kv[5].strip() == "- dynamo.sglang.sidecar"
assert ln["SERVE_LN_W0_SIDECAR_ARGS"] < ln["SERVE_LN_W1"]

# worker-1..3 are worker-0 with another name and label.
w0 = part(ln["SERVE_LN_W0"], ln["SERVE_LN_W1"] - 1)
rest = lines[ln["SERVE_LN_W1"] - 1:]
assert len(rest) == 3 * len(w0), (len(rest), len(w0))
for k in (1, 2, 3):
    same_but(rest[(k - 1) * len(w0):k * len(w0)], w0, k, ["worker"],
             [f"- name: worker-{k}", f"dynamo.nvidia.com/slime-role: worker-{k}"])
d.set("SERVE_DGD_WORKER_LINES", len(w0))
docs = yaml.safe_load(kit_path(DGD).read_text())
assert [c["name"] for c in docs["spec"]["components"]] == ROLES
assert all(c["replicas"] == 1 for c in docs["spec"]["components"])

# Lines vim shows as closed folds instead of typing them: each engine's "--host 0.0.0.0" and its
# sidecar's "127.0.0.1" endpoint. They aren't secrets, but tools/redact_cast.py flags every IPv4
# address it can't mask, and the public cast must pass it clean. SERVE_HIDE has one entry per buffer
# line: 0 = typed, k = the k-th fold (a flag line and its value); SERVE_HIDE_NOTE[k-1] is its text.
IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.]*\d)")   # redact_cast.py's IPv4 check
FOLD_NOTES = {"- --host": "--host: listen on every interface, so the engine's Service can reach it",
              "- --sidecar-args": "--sidecar-args: the sidecar reaches this engine's gRPC port, 30001, in the Pod"}
hide, notes = [0] * len(lines), []
for i, line in enumerate(lines):
    if IPV4.search(line):
        flag = lines[i - 1].strip()
        assert flag in FOLD_NOTES and hide[i - 1] == 0, (i + 1, flag, line)
        notes.append(FOLD_NOTES[flag])
        hide[i - 1] = hide[i] = len(notes)
assert [lines[i].strip() for i in range(len(lines)) if hide[i]][:4] == [
    "- --host", "- 0.0.0.0", "- --sidecar-args", "- '[\"--grpc-endpoint\",\"http://127.0.0.1:30001\"]'"]
assert len(notes) == 2 * len(ROLES[1:]), notes   # two per engine
assert hide[ln["SERVE_LN_W0_SIDECAR_ARGS"] - 1] and not hide[ln["SERVE_LN_W0_PAGE"] - 1]
d.array("SERVE_HIDE", [str(h) for h in hide])
d.array("SERVE_HIDE_NOTE", notes)

# --- engine-services.yaml: the first Service, with `head` --------------------------------------
svc = read_lines(SVC)
n = svc.index("---")                                  # the first Service ends before the first ---
svc_head = svc[:n]
assert svc_head[0].startswith("# ") and svc_head[1].startswith("# ") and svc_head[2] == "apiVersion: v1"
assert svc_head[-1] == "    port: 30000", svc_head
max_width(svc_head)
d.set("SERVE_SVC_FILE", SVC)
d.set("SERVE_SVC_HEAD_N", len(svc_head))
d.array("SERVE_SVC_HEAD_OUT", svc_head)
body = svc[2:] + ["---"]                              # four Services, each ending in ---
size = len(svc_head) - 2 + 1
assert len(body) == 4 * size, (len(body), size)
for k in (1, 2, 3):
    same_but(body[k * size:(k + 1) * size], body[:size], k, ["engine", "worker"],
             [f"name: slime-sglang-engine-{k}", f"dynamo.nvidia.com/slime-role: worker-{k}"])
services = list(yaml.safe_load_all(kit_path(SVC).read_text()))
assert [s["metadata"]["name"] for s in services] == [f"slime-sglang-engine-{k}" for k in range(4)]
d.set("SERVE_ENGINE_PORT", services[0]["spec"]["ports"][0]["port"])

# --- the frontend URL Slime sends rollouts to --------------------------------------------------
url = re.search(r'DYNAMO_ROLLOUT_URL: "([^"]+)"', kit_path("manifests/rayjob.yaml").read_text()).group(1)
assert url == "http://slime-sglang-frontend:8000", url
d.set("SERVE_FRONTEND_URL", url)
d.set("SERVE_FRONTEND_SVC", url.split("//", 1)[1])

# --- the apply ---------------------------------------------------------------------------------
console = read_lines("runs/deploy.console.log")
apply_out = [x for x in console if re.match(r"^(service|dynamographdeployment)\S*/\S+ created$", x)]
assert apply_out == [f"service/slime-sglang-engine-{k} created" for k in range(4)] + [
    "dynamographdeployment.nvidia.com/slime-sglang created"], apply_out
d.array("SERVE_APPLY_OUT", apply_out)

# --- the Pods starting, and the DGD ------------------------------------------------------------
ready = dict(x.split("=", 1) for x in read_lines("runs/deploy/dgd-ready.txt"))
t0 = ready["dgd_apply"]
status = load_yaml("runs/deploy/dgd-status.yaml")
cond = next(c for c in status["status"]["conditions"] if c["type"] == "Ready")
assert cond["status"] == "True"
created_t = secs(t0, status["metadata"]["creationTimestamp"])
ready_t = secs(t0, cond["lastTransitionTime"])
assert ready_t == int(ready["dgd_ready_secs"]), (ready_t, ready)
d.set("SERVE_DGD_NAME", status["metadata"]["name"])
d.set("SERVE_DGD_READY", cond["status"])
d.set("SERVE_DGD_BACKEND", status["spec"]["backendFramework"])
d.set("SERVE_DGD_READY_T", ready_t)
d.set("SERVE_DGD_AGE", fmt_age(ready_t - created_t))   # `kubectl get dgd` when the watch ends, at Ready

pods = load_json("runs/deploy/pods-at-ready.json")["items"]
role = {p["metadata"]["labels"]["dynamo.nvidia.com/slime-role"]: p for p in pods}
names = [role[r]["metadata"]["name"] for r in ROLES]
d.array("SERVE_POD_NAMES", names)
d.array("SERVE_POD_CREATED_T", [secs(t0, role[r]["metadata"]["creationTimestamp"]) for r in ROLES])


def snapshots(rel):
    """[(iso time, [row dicts])] from a pods-watch.txt of `kubectl get pods -o wide` frames."""
    frames, cur, header = [], None, None
    for line in read_lines(rel):
        if line.startswith("=== "):
            cur = (line[4:].strip(), [])
            frames.append(cur)
            header = None
        elif cur is not None and line.startswith("NAME"):
            header = line
        elif cur is not None and header and line.strip():
            p = line.split()
            cur[1].append({"name": p[0], "ready": p[1], "status": p[2], "restarts": p[3]})
    return frames


trans, last, first_t = [], {}, None
for t_iso, rows in snapshots("runs/deploy/pods-watch.txt"):
    t = secs(t0, t_iso)
    mine = [r for r in rows if r["name"] in names]
    if len(mine) == len(names) and first_t is None:
        first_t = t
    for r in mine:
        assert r["restarts"] == "0", r
        i = names.index(r["name"])
        state = (r["status"], r["ready"])
        if last.get(i) != state:
            last[i] = state
            trans.append((t, i, r["status"], r["ready"]))
trans.sort()
assert first_t is not None and all(t >= first_t for t, *_ in trans)
assert {i for t, i, *_ in trans if t == first_t} == set(range(len(names)))   # every Pod has a state from the start
assert all(last[i] == ("Running", "1/1") for i in range(len(names))), last
assert max(t for t, *_ in trans) <= ready_t
d.set("SERVE_WATCH_FROM_T", first_t)   # the first snapshot with all five Pods (no invented Pending)
d.set("SERVE_WATCH_TO_T", ready_t)
d.array("SERVE_TR_T", [x[0] for x in trans])
d.array("SERVE_TR_I", [x[1] for x in trans])
d.array("SERVE_TR_S", [x[2] for x in trans])
d.array("SERVE_TR_R", [x[3] for x in trans])

# --- each engine's sidecar found its KV-event source ------------------------------------------
# The kit saved these lines with the ANSI codes stripped; the visible text of
# `grep -o 'discovered SGLang ZMQ KV-event sources.*'` on the raw log is the same.
side = []
for line in read_lines("runs/deploy/sidecar-kv-events.txt"):
    m = re.match(r"^\s*(worker-\d): .*?(discovered SGLang ZMQ KV-event sources.*)$", line)
    if m:
        side.append(f"{m.group(1)}: {m.group(2)}")
assert [s.split(":")[0] for s in side] == ROLES[1:], side
assert all("block_size=16" in s and "base_port=5557" in s for s in side), side
max_width(side)
d.array("SERVE_SIDECAR_OUT", side)
d.write()
