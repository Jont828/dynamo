"""Data for beats/04_serve.sh (Step 3: serve an inference endpoint with Dynamo).

Reads (kit root = slime-kv-routing/):
  manifests/tutorial/dgd.yaml            typed in vim (no header comment); checks that worker-1 is
                                         worker-0 with another name and label
  manifests/tutorial/engine-services.yaml  its first Service shown with head; checks that engine-1 is
                                         engine-0 with another name and selector
  manifests/tutorial/rayjob.yaml         the engine addresses Slime gets and the frontend URL it sends
                                         rollouts to (DYNAMO_ROLLOUT_URL)
  runs/rr/applied/dgd.source.yaml        the DGD the rr phase applied: the tutorial's, byte for byte
  runs/rr/applied/dgd.yaml               ... as applied: plus one nodeSelector per component (tools/pin.py)
  runs/rr/dgd-ready.txt                  when the DGD was applied (03:50:30) and seconds until Ready (73)
  runs/rr/phase.log                      the apply's and the wait's real output, and each engine's
                                         "sglang sidecar started" line (ANSI codes already stripped)
  runs/rr/pods-at-ready.json             each Pod's role, name, node, image, and condition times
  runs/rr/run1/events.txt                the operator's CreateService events (it creates slime-sglang-
                                         frontend, -worker-0, -worker-1) and each Pod's image "already
                                         present on machine"
  runs/rr/run1/driver.log                Slime found both engines through their Services (/server_info)
  runs/rr/run1/worker-0.log              Slime calls the engine directly at each weight sync
  demo/team/snapshots/04_engine-services.txt  the engine Services on the cluster: created 03:13:26-27 (at
                                         setup, before the first DGD), port 30000, one selector each
  demo/team/snapshots/04_engine-services-get.txt  `kubectl get svc` of the two engine Services (read-only,
                                         2026-11-19): shown after their apply, whose output was never saved

The beat types SERVE_DGD in parts that start at the SERVE_LN_* lines (1-based buffer lines).
"""

import json
import pathlib
import re
import sys

import yaml  # PyYAML, in the venv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import (TEAM, Data, check_in_file, check_typed, find_line, kit_path, load_json, load_yaml,  # noqa: E402
                     manifest_lines, max_width, read_lines, secs)

DGD = "manifests/tutorial/dgd.yaml"
SVC = "manifests/tutorial/engine-services.yaml"
RAYJOB = "manifests/tutorial/rayjob.yaml"
ROLES = ["frontend", "worker-0", "worker-1"]
SNAP_SVC = TEAM / "snapshots" / "04_engine-services.txt"
SNAP_GET = TEAM / "snapshots" / "04_engine-services-get.txt"

d = Data(__file__)
d.source(DGD, SVC, RAYJOB, "runs/rr/applied/dgd.source.yaml", "runs/rr/applied/dgd.yaml", "runs/rr/dgd-ready.txt",
         "runs/rr/phase.log", "runs/rr/pods-at-ready.json", "runs/rr/run1/events.txt", "runs/rr/run1/driver.log",
         "runs/rr/run1/worker-0.log", SNAP_SVC, SNAP_GET)


def same_but(block, ref, k, words, allowed):
    """block == ref with each WORD-0 replaced by WORD-k, and only the allowed lines differ."""
    subst = list(ref)
    for w in words:
        subst = [line.replace(f"{w}-0", f"{w}-{k}") for line in subst]
    assert block == subst, f"block {k} differs from block 0 beyond the names"
    differ = [b.strip() for a, b in zip(ref, block) if a != b]
    assert differ == allowed, differ


# --- dgd.yaml, typed in vim -------------------------------------------------------------------
lines = manifest_lines(DGD)
assert lines == read_lines(DGD)                       # the tutorial's file has no header to leave out
check_typed(lines, DGD)
d.array("SERVE_DGD", lines)
d.set("SERVE_DGD_FILE", DGD)

# The rr phase applied this file unchanged, plus tools/pin.py's nodeSelector (a measurement control).
assert kit_path("runs/rr/applied/dgd.source.yaml").read_text() == kit_path(DGD).read_text()
applied = load_yaml("runs/rr/applied/dgd.yaml")
pins = {c["podTemplate"]["spec"].pop("nodeSelector")["kubernetes.io/hostname"] for c in applied["spec"]["components"]}
assert applied == load_yaml(DGD) and pins == {"aks-ndh100pool-82423760-vmss000003"}, pins

engine_image = "nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.1"
ln = {
    "SERVE_LN_KIND": find_line(lines, "kind: DynamoGraphDeployment"),
    "SERVE_LN_FRONTEND": find_line(lines, "    - name: frontend"),
    "SERVE_LN_FE_MAIN": find_line(lines, "            - name: main"),
    "SERVE_LN_ENV": find_line(lines, "- name: DYN_SGLANG_ENABLE_GENERATE"),
    "SERVE_LN_W0": find_line(lines, "    - name: worker-0"),
    "SERVE_LN_W0_IMAGE": find_line(lines, f"image: {engine_image}"),
    "SERVE_LN_W0_LAUNCH": find_line(lines, "- sglang.launch_server"),
    "SERVE_LN_W0_SIDECAR": find_line(lines, "- --sidecar"),
    "SERVE_LN_W0_SIDECAR_ARGS": find_line(lines, '"--grpc-endpoint","http://127.0.0.1:30001"'),
    "SERVE_LN_W0_PROBE": find_line(lines, "startupProbe:"),
    "SERVE_LN_W1": find_line(lines, "    - name: worker-1"),
}
for name in ("SERVE_LN_KIND", "SERVE_LN_FRONTEND", "SERVE_LN_W0", "SERVE_LN_W0_IMAGE", "SERVE_LN_W0_LAUNCH",
             "SERVE_LN_W0_SIDECAR_ARGS", "SERVE_LN_W1"):
    d.set(name, ln[name])          # where the beat changes speed or calls out (the others are only checked)


def part(a, b):
    """Buffer lines a..b (1-based, inclusive)."""
    return lines[a - 1:b]


assert ln["SERVE_LN_KIND"] == 2                       # typed at normal speed: apiVersion, kind
head = part(1, ln["SERVE_LN_FRONTEND"] - 1)
assert head == ["apiVersion: nvidia.com/v1beta1", "kind: DynamoGraphDeployment", "metadata:", "  name: slime-sglang",
                "spec:", "  backendFramework: sglang", "  components:"], head
fe = part(ln["SERVE_LN_FE_MAIN"], ln["SERVE_LN_W0"] - 1)
assert [x.strip() for x in fe] == ["- name: main", "image: nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.6.1", "env:",
                                   "- name: DYN_SGLANG_ENABLE_GENERATE", 'value: "1"'], fe
assert ln["SERVE_LN_ENV"] == ln["SERVE_LN_W0"] - 2
assert not any("DYN_ROUTER" in x for x in lines)      # no router setting: the default, round-robin
cmd = part(ln["SERVE_LN_W0_IMAGE"], ln["SERVE_LN_W0_LAUNCH"])
assert [x.strip() for x in cmd] == [f"image: {engine_image}", "command:", "- python3", "- -m",
                                    "- sglang.launch_server"], cmd
side = part(ln["SERVE_LN_W0_SIDECAR"], ln["SERVE_LN_W0_SIDECAR_ARGS"])
assert [x.strip() for x in side] == ["- --sidecar", "- dynamo.sglang.sidecar", "- --sidecar-args",
                                     "- '[\"--grpc-endpoint\",\"http://127.0.0.1:30001\"]'"], side
args = [x.strip() for x in part(ln["SERVE_LN_W0_LAUNCH"] + 1, ln["SERVE_LN_W0_SIDECAR"] - 1)]
assert args == ["args:", "- --model-path", "- /data/models/Qwen3-0.6B", "- --served-model-name", "- Qwen/Qwen3-0.6B",
                "- --host", "- 0.0.0.0", "- --port", '- "30000"', "- --grpc-port", '- "30001"',
                "- --incremental-streaming-output"], args
probe = [x.strip() for x in part(ln["SERVE_LN_W0_PROBE"], ln["SERVE_LN_W1"] - 1)]
assert probe[:6] == ["startupProbe:", "httpGet:", "path: /health_generate", "port: 30000", "periodSeconds: 5",
                     "timeoutSeconds: 5"] and probe[6] == "failureThreshold: 60" and "readinessProbe:" in probe, probe

# worker-1 is worker-0 with another name and label.
w0 = part(ln["SERVE_LN_W0"], ln["SERVE_LN_W1"] - 1)
rest = lines[ln["SERVE_LN_W1"] - 1:]
assert len(rest) == len(w0), (len(rest), len(w0))
same_but(rest, w0, 1, ["worker"], ["- name: worker-1", "dynamo.nvidia.com/slime-role: worker-1"])
dgd = load_yaml(DGD)
assert [c["name"] for c in dgd["spec"]["components"]] == ROLES
assert all(c["replicas"] == 1 for c in dgd["spec"]["components"])
d.set("SERVE_DGD_NAME", dgd["metadata"]["name"])

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
assert hide[ln["SERVE_LN_W0_SIDECAR_ARGS"] - 1] and not hide[ln["SERVE_LN_W0_SIDECAR"] - 1]
assert not hide[ln["SERVE_LN_W0_IMAGE"] - 1] and not hide[ln["SERVE_LN_ENV"] - 1]
d.array("SERVE_HIDE", [str(h) for h in hide])
d.array("SERVE_HIDE_NOTE", notes)

# --- engine-services.yaml: its first Service, shown with head ----------------------------------
svc = read_lines(SVC)
check_in_file("\n".join(svc), SVC)
n = svc.index("---")
assert svc[n + 1:] == [x.replace("engine-0", "engine-1").replace("worker-0", "worker-1") for x in svc[:n]]
services = list(yaml.safe_load_all(kit_path(SVC).read_text()))
assert [s["metadata"]["name"] for s in services] == ["slime-sglang-engine-0", "slime-sglang-engine-1"]
assert [s["spec"]["selector"] for s in services] == [{"dynamo.nvidia.com/slime-role": f"worker-{k}"} for k in (0, 1)]
assert [s["spec"]["ports"] for s in services] == [[{"name": "sglang-http", "port": 30000}]] * 2
max_width(svc)
d.set("SERVE_SVC_FILE", SVC)
d.set("SERVE_SVC_HEAD_N", n)                          # `head -n 10`: the first Service, engine-0
d.array("SERVE_SVC_HEAD_OUT", svc[:n])
# On the cluster they're still there, as the file says. The kit created them once, at setup
# (03:13:26-27), with slime-functions.yaml; every phase's DGD reused them.
snap = read_lines(SNAP_SVC)
snap = snap[snap.index("# ---") + 1:]
assert [s.split()[0] for s in snap] == ["service/slime-sglang-engine-0", "service/slime-sglang-engine-1"], snap
assert all("type=ClusterIP" in s and '"port":30000' in s for s in snap) and "created=2026-11-14T03:13:2" in snap[0]

# The operator's own Services for the DGD (events.txt, the rr phase's apply, LAST SEEN 4m28s):
# slime-sglang-frontend (port 8000) and slime-sglang-worker-0/-1 (Dynamo's system port, 9090; Dynamo
# v1.6.1 deploy/operator/internal/dynamo/graph.go GenerateComponentService).
events = read_lines("runs/rr/run1/events.txt")
created_svcs = sorted({re.search(r"service/(\S+)", e).group(1) for e in events
                       if e.startswith("4m28s ") and "CreateService " in e})
assert created_svcs == ["slime-sglang-frontend", "slime-sglang-worker-0", "slime-sglang-worker-1"], created_svcs

# --- what Slime does with the engine addresses -------------------------------------------------
rayjob = kit_path(RAYJOB).read_text()
assert "--rollout-external-engine-addrs slime-sglang-engine-0:30000 slime-sglang-engine-1:30000" in rayjob
url = re.search(r'DYNAMO_ROLLOUT_URL: "([^"]+)"', rayjob).group(1)
assert url == "http://slime-sglang-frontend:8000", url
d.set("SERVE_FRONTEND_URL", url)
driver = kit_path("runs/rr/run1/driver.log").read_text()
assert "Detected external SGLang engines: [{'url': 'http://slime-sglang-engine-0:30000'" in driver
w0log = kit_path("runs/rr/run1/worker-0.log").read_text()
for route in ('"POST /pause_generation', '"POST /update_weights_from_disk', '"POST /continue_generation'):
    assert route in w0log, route

# --- the apply and the wait: phase.log has the DGD's lines. The kit created the engine Services once, at
# setup (2026-11-14 03:13:26-27, with slime-functions.yaml, README "Rerun"), and saved no output. So the
# beat shows their apply with lib.sh's unsaved_note, then `kubectl get svc` from a read-only snapshot
# (snapshots/04_engine-services-get.txt, 04-05_capture.sh); the DGD's apply is the real one.
phase = read_lines("runs/rr/phase.log")
assert "dynamographdeployment.nvidia.com/slime-sglang created" in phase
assert "dynamographdeployment.nvidia.com/slime-sglang condition met" in phase
assert not any(line.startswith("service/") for line in phase)   # the rr apply didn't touch the Services
d.set("SERVE_DGD_APPLY_OUT", "dynamographdeployment.nvidia.com/slime-sglang created")
d.set("SERVE_WAIT_OUT", "dynamographdeployment.nvidia.com/slime-sglang condition met")
created = sorted(re.search(r"created=(\S+)", s).group(1) for s in snap)
assert all(c.startswith("2026-11-14T03:13:2") for c in created), created
d.set("SERVE_SETUP_AT", "2026-11-14 03:13")                    # unsaved_note's time (UTC)
get_text = SNAP_GET.read_text()
get_head, _, get_body = get_text.partition("# ---\n")
get_cmd = re.search(r"^# screen: (.*)$", get_head, re.M).group(1)
get_date = re.search(r"^# date:   (\d{4}-\d\d-\d\d)T", get_head, re.M).group(1)
assert get_cmd == "kubectl get svc -n $NAMESPACE slime-sglang-engine-0 slime-sglang-engine-1", get_cmd
get_out = get_body.rstrip("\n").split("\n")
assert get_out[0].split() == ["NAME", "TYPE", "CLUSTER-IP", "EXTERNAL-IP", "PORT(S)", "AGE"], get_out[0]
assert [r.split()[0] for r in get_out[1:]] == ["slime-sglang-engine-0", "slime-sglang-engine-1"], get_out
assert all(r.split()[1] == "ClusterIP" and r.split()[4] == "30000/TCP" for r in get_out[1:]), get_out
max_width(get_out)
d.set("SERVE_SVC_GET_CMD", get_cmd)
d.array("SERVE_SVC_GET_OUT", get_out)
d.set("SERVE_SVC_SNAP_DATE", get_date)

# --- the Pods starting, from the apply to Ready ------------------------------------------------
ready = dict(x.split("=", 1) for x in read_lines("runs/rr/dgd-ready.txt"))
t0 = ready["dgd_apply"]
ready_t = int(ready["dgd_ready_secs"])
assert (t0, ready_t) == ("2026-11-14T03:50:30Z", 73)
assert "[03:51:43] rr: DGD Ready after 73 s" in phase
pods = load_json("runs/rr/pods-at-ready.json")["items"]
role = {p["metadata"]["labels"]["dynamo.nvidia.com/slime-role"]: p for p in pods}
assert sorted(role) == sorted(ROLES)


def cond(p, kind):
    return next(c["lastTransitionTime"] for c in p["status"]["conditions"] if c["type"] == kind)


images = {"frontend": "nvcr.io/nvidia/ai-dynamo/dynamo-frontend:1.6.1", "worker-0": engine_image,
          "worker-1": engine_image}
for r in ROLES:
    p = role[r]
    assert p["spec"]["nodeName"] == "aks-ndh100pool-82423760-vmss000003"
    assert p["spec"]["schedulerName"] == "kai-scheduler"
    (cs,) = p["status"]["containerStatuses"]
    assert cs["image"] == images[r] and cs["ready"] and cs["restartCount"] == 0
    name = p["metadata"]["name"]
    assert any(e.startswith("4m2") and f"pod/{name}" in e and "already present on machine" in e for e in events), name
d.set("SERVE_READY_T", ready_t)                                                        # 73
d.set("SERVE_CREATED_T", secs(t0, role["frontend"]["metadata"]["creationTimestamp"]))  # 3
d.set("SERVE_FE_READY_T", secs(t0, cond(role["frontend"], "Ready")))                    # 17
d.set("SERVE_W0_READY_T", secs(t0, cond(role["worker-0"], "Ready")))                    # 73
d.set("SERVE_W1_READY_T", secs(t0, cond(role["worker-1"], "Ready")))                    # 72
# Every Pod was created at 3 s and scheduled at 4 s; the engines' containers started at 6 s.
assert all(secs(t0, p["metadata"]["creationTimestamp"]) == 3 for p in pods)
assert all(secs(t0, cond(p, "PodScheduled")) == 4 for p in pods)
assert [secs(t0, role[w]["status"]["containerStatuses"][0]["state"]["running"]["startedAt"]) for w in ROLES[1:]] == [6, 6]
assert max(secs(t0, cond(p, "Ready")) for p in pods) == ready_t

# --- each engine's sidecar started --------------------------------------------------------------
# phase.log has the lines run_phase.sh grepped from `kubectl logs -l ...=worker-N --tail=-1` with the
# ANSI codes stripped. (runs/rr/run1/worker-N.log starts at the RayJob, 03:51:52, after them.) In the
# raw log the message is plain text and the codes sit around each field name, so `grep -o 'sglang
# sidecar started.*'` matches, and a terminal shows this text.
side, side_t = [], []
for line in phase:
    m = re.match(r"^\s*(worker-\d): (\S+)\s+INFO dynamo_sglang_sidecar::engine: (sglang sidecar started .*)$", line)
    if m:
        side.append(f"{m.group(1)}: {m.group(3)}")
        side_t.append(m.group(2))
assert [s.split(":")[0] for s in side] == ROLES[1:], side
assert all(s.endswith(" model=/data/models/Qwen3-0.6B mode=Aggregated connections=8 kv_event_sources=0")
           for s in side), side
assert "sglang sidecar started" not in kit_path("runs/rr/run1/worker-0.log").read_text()
max_width(side)
d.array("SERVE_SIDECAR_OUT", side)
assert [secs(t0, t[:19] + "Z") for t in side_t] == [70, 69]   # worker-0, worker-1: 2-3 s before Ready
d.write()
