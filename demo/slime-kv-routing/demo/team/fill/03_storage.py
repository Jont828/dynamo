"""Data for beats/03_storage.sh (Step 2: stage the model and data on Azure Managed Lustre).

Reads (kit root = slime-kv-routing/):
  manifests/storage.yaml               the lines typed in vim (its kit-internal header comment left out)
  manifests/tutorial/setup-job.yaml    the Job's name, GPU, and what its script downloads and creates
  runs/setup/start.txt, end.txt        the apply (03:13:21) and the Job's completion (03:14:30): 69 s
  runs/setup/setup-job.log             the hf download times (progress bars), the converter's first
                                       timestamp and its "successfully saved checkpoint" line, and the
                                       log's last 6 lines (the script's `ls /data/datasets /data/models`)
  demo/team/snapshots/03_setup-job.txt     read-only `kubectl get` of the Job and its Pod: created 03:13:23,
                                           container 03:13:25-03:14:28 (exit 0), completed 03:14:30
  demo/team/snapshots/03_storage-objects.txt  the namespace, PV, and PVC: created 03:13:12-13, Bound
                                           (so the apply printed "created" for all three)
  demo/team/snapshots/03_lustre-class.txt  the cluster's only Lustre StorageClass (another MGS address) and
                                           h100-lustre's MGS address: why the PV is static

The beat types STORAGE_YAML fast, in three parts split after the lines its callouts explain
(STORAGE_LN_ACCESS + 1, STORAGE_LN_SUBDIR, and the end); this script checks that the
typed lines are the whole file after its header, unchanged and in order.
"""

import pathlib
import re
import sys

import yaml  # PyYAML, in the venv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import (TEAM, Data, check_in_file, check_typed, find_line, kit_path, load_yaml,  # noqa: E402
                     manifest_lines, max_width, read_lines, secs)

STORAGE = "manifests/storage.yaml"
SETUP = "manifests/tutorial/setup-job.yaml"
SNAP_JOB = TEAM / "snapshots" / "03_setup-job.txt"
SNAP_OBJ = TEAM / "snapshots" / "03_storage-objects.txt"
SNAP_CLASS = TEAM / "snapshots" / "03_lustre-class.txt"

d = Data(__file__)
d.source(STORAGE, SETUP, "runs/setup/start.txt", "runs/setup/end.txt", "runs/setup/setup-job.log",
         SNAP_JOB, SNAP_OBJ, SNAP_CLASS)


def snapshot(path):
    """The output part of a snapshots/ file (after its '# ---' header line)."""
    lines = path.read_text().splitlines()
    return lines[lines.index("# ---") + 1:]


# --- storage.yaml, typed in vim ----------------------------------------------------------------
lines = manifest_lines(STORAGE)
check_typed(lines, STORAGE)
check_in_file("\n".join(lines), STORAGE)          # contiguous: nothing skipped after the header
assert lines[-1] == read_lines(STORAGE)[-1]       # ... through the file's last line
assert len(read_lines(STORAGE)) - len(lines) == 7  # the 7-line kit header (it names other kits)
d.array("STORAGE_YAML", lines)
d.set("STORAGE_FILE", STORAGE)

ln = {
    "STORAGE_LN_NS": find_line(lines, "  name: slime-kv"),                         # the Namespace's name
    "STORAGE_LN_ACCESS": find_line(lines, "  accessModes:"),                       # the PV's
    "STORAGE_LN_RETAIN": find_line(lines, "  persistentVolumeReclaimPolicy: Retain"),
    "STORAGE_LN_CSI": find_line(lines, "  csi:"),
    "STORAGE_LN_SUBDIR": find_line(lines, "      sub-dir: slime-kv"),
    "STORAGE_LN_CLAIMREF": find_line(lines, "  claimRef:"),
    "STORAGE_LN_PVC": find_line(lines, "kind: PersistentVolumeClaim"),
    "STORAGE_LN_VOLNAME": find_line(lines, "  volumeName: pv-lustre-slime-kv"),
}
# find_line is 1-based, so lines[n] is line n + 1.
assert lines[:ln["STORAGE_LN_NS"]] == ["apiVersion: v1", "kind: Namespace", "metadata:", "  name: slime-kv"]
assert lines[ln["STORAGE_LN_ACCESS"]] == "    - ReadWriteMany"
assert ln["STORAGE_LN_RETAIN"] == ln["STORAGE_LN_ACCESS"] + 2
assert lines[ln["STORAGE_LN_CSI"]] == "    driver: azurelustre.csi.azure.com"
assert lines[ln["STORAGE_LN_CSI"] + 1].startswith("    volumeHandle: ")
assert lines[ln["STORAGE_LN_SUBDIR"] - 2].startswith("      mgs-ip-address: ")
assert lines[ln["STORAGE_LN_CLAIMREF"]:ln["STORAGE_LN_CLAIMREF"] + 2] == ["    namespace: slime-kv",
                                                                       "    name: slime-data"]
assert (ln["STORAGE_LN_NS"] < ln["STORAGE_LN_ACCESS"] < ln["STORAGE_LN_RETAIN"] < ln["STORAGE_LN_CSI"]
        < ln["STORAGE_LN_SUBDIR"] < ln["STORAGE_LN_CLAIMREF"] < ln["STORAGE_LN_PVC"]
        < ln["STORAGE_LN_VOLNAME"] < len(lines))
for name in ("STORAGE_LN_NS", "STORAGE_LN_ACCESS", "STORAGE_LN_CSI", "STORAGE_LN_SUBDIR", "STORAGE_LN_CLAIMREF"):
    d.set(name, ln[name])          # the lines the beat's callouts mark (the others are only checked here)
d.set("STORAGE_LN_END", len(lines))

docs = list(yaml.safe_load_all(kit_path(STORAGE).read_text()))
ns = next(x for x in docs if x["kind"] == "Namespace")["metadata"]["name"]
pv = next(x for x in docs if x["kind"] == "PersistentVolume")
pvc = next(x for x in docs if x["kind"] == "PersistentVolumeClaim")
assert pv["spec"]["accessModes"] == ["ReadWriteMany"] == pvc["spec"]["accessModes"]
assert pv["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
assert pv["spec"]["csi"]["driver"] == "azurelustre.csi.azure.com"
assert pv["spec"]["csi"]["volumeAttributes"]["sub-dir"] == ns
assert pv["spec"]["claimRef"] == {"namespace": ns, "name": pvc["metadata"]["name"]}
assert pvc["spec"]["volumeName"] == pv["metadata"]["name"]
d.set("STORAGE_NAMESPACE", ns)
d.set("STORAGE_PVC", pvc["metadata"]["name"])
d.set("STORAGE_SUBDIR", pv["spec"]["csi"]["volumeAttributes"]["sub-dir"])

# The static-PV callout says no storage class here provisions volumes on this file system: the PV's class,
# azurelustre-static, is the cluster's only Lustre class and points at another MGS address, and the PV's
# address is h100-lustre's, the only AMLFS in the resource group (snapshots/03_lustre-class.txt, read-only).
mgs = pv["spec"]["csi"]["volumeAttributes"]["mgs-ip-address"]
lustre = snapshot(SNAP_CLASS)
m = re.fullmatch(r'storageclass/(\S+) created=\S+ provisioner=azurelustre\.csi\.azure\.com '
                 r'parameters=\{"mgs-ip-address":"([\d.]+)"\}', lustre[0])
assert m and m.group(1) == pv["spec"]["storageClassName"] == pvc["spec"]["storageClassName"], lustre[0]
assert m.group(2) != mgs, (m.group(2), mgs)
assert re.fullmatch(rf"amlfs/h100-lustre created=\S+ mgsAddress={re.escape(mgs)}", lustre[1]), lustre[1]
assert lustre[2].split(":", 1)[1].split() == ["h100-lustre"], lustre[2]

# --- the apply: kubectl's output for creating these three objects. The run didn't save it, but the
# objects on the cluster were created by it (03:13:12-13) and are still Bound (snapshots/).
apply_out = [f"namespace/{ns} created", f"persistentvolume/{pv['metadata']['name']} created",
             f"persistentvolumeclaim/{pvc['metadata']['name']} created"]
obj = snapshot(SNAP_OBJ)
assert obj[0] == f"namespace/{ns} created=2026-11-14T03:13:12Z", obj
assert obj[1].startswith(f"persistentvolume/{pv['metadata']['name']} created=2026-11-14T03:13:12Z phase=Bound "
                         f"claim={ns}/{pvc['metadata']['name']} reclaim=Retain"), obj
assert obj[2].startswith(f"persistentvolumeclaim/{pvc['metadata']['name']} created=2026-11-14T03:13:13Z "
                         "phase=Bound"), obj
d.array("STORAGE_APPLY_OUT", apply_out)

# --- the setup Job ---------------------------------------------------------------------------
job_manifest = load_yaml(SETUP)
job = job_manifest["metadata"]["name"]
container = job_manifest["spec"]["template"]["spec"]["containers"][0]
script = container["args"][0]
assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
assert container["image"] == "slimerl/slime:nightly-dev-20261027a-cu130"
assert "convert_hf_to_torch_dist.py" in script and "--save /data/models/Qwen3-0.6B_torch_dist" in script
assert "mkdir -p /data/weight-updates" in script and "ls /data/datasets /data/models" in script
model = re.search(r"hf download (\S+) --local-dir", script).group(1)
dataset = re.search(r"hf download --repo-type dataset (\S+)", script).group(1)
assert (model, dataset) == ("Qwen/Qwen3-0.6B", "zhuzilin/gsm8k")
d.set("STORAGE_JOB", job)
d.set("STORAGE_JOB_FILE", SETUP)
d.set("STORAGE_MODEL", model)
d.set("STORAGE_DATASET", dataset)
# kubectl's only output for creating this Job and for waiting until it completes. The Job on the
# cluster (snapshots/03_setup-job.txt) was created at 03:13:23 and completed at 03:14:30.
d.set("STORAGE_JOB_CREATED_OUT", f"job.batch/{job} created")
d.set("STORAGE_JOB_MET_OUT", f"job.batch/{job} condition met")

# Timeline, in seconds since the apply (runs/setup/start.txt, 03:13:21; the Job object appears 2 s later).
t0 = read_lines("runs/setup/start.txt")[0]
end = read_lines("runs/setup/end.txt")[0]
snap = snapshot(SNAP_JOB)
m = re.search(r"created=(\S+) start=(\S+) completion=(\S+) succeeded=1$", snap[0])
assert snap[0].startswith(f"job.batch/{job} ") and m, snap
job_created, completion = m.group(1), m.group(3)
assert completion == end, (completion, end)   # end.txt is the Job's completionTime
m = re.search(r"started=(\S+) finished=(\S+) exit=0 reason=Completed$", snap[-1])
assert snap[-1].startswith("  container setup image=docker.io/slimerl/slime:nightly-dev-20261027a-cu130") and m
c_started, c_finished = m.group(1), m.group(2)

raw = kit_path("runs/setup/setup-job.log").read_text()
log = raw.split("\n")                    # kubectl counts lines by \n (progress bars use \r inside one)
if log[-1] == "":
    log = log[:-1]
first_ts = re.search(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] ", raw, re.M)   # the converter's first line
conv_start = f"{first_ts.group(1)}T{first_ts.group(2)}Z"
saved = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.\d+\] successfully saved checkpoint", raw)
saved_iso = f"{saved.group(1)}T{saved.group(2)}Z"


def fetch_secs(n_files):
    """The hf progress bar's elapsed time when all n_files were fetched (its last 100% frame)."""
    frames = re.findall(rf"Fetching {n_files} files: 100%\|[^|]*\| {n_files}/{n_files} \[(\d\d):(\d\d)<", raw)
    mm, ss = frames[-1]
    return int(mm) * 60 + int(ss)


# The container started at 4 s (the image was cached) and exited 0 at 67 s; the Job completed at 69 s.
d.set("STORAGE_JOB_SECS", secs(t0, end))               # 69: the fast-forward's length
d.set("STORAGE_CONV_T", secs(t0, conv_start))          # 50: the converter's first log line
d.set("STORAGE_SAVED_T", secs(t0, saved_iso) + 1)      # 57: the torch_dist copy saved (03:14:17.9)
d.set("STORAGE_MODEL_DL_SECS", fetch_secs(10))         # 12
d.set("STORAGE_DATA_DL_SECS", fetch_secs(3))           # 3
assert secs(t0, end) == 69 and secs(t0, job_created) == 2
assert (secs(t0, c_started), secs(t0, conv_start), secs(t0, saved_iso) + 1, secs(t0, c_finished)) == (4, 50, 57, 67)
assert (fetch_secs(10), fetch_secs(3)) == (12, 3)
assert "Downloaded\x1b[0m\n  path: /data/models/Qwen3-0.6B" in raw and "path: /data/datasets/gsm8k" in raw

# The log's last lines: the setup script's `ls /data/datasets /data/models`.
tail = log[-6:]
assert tail == ["/data/datasets:", "gsm8k", "", "/data/models:", "Qwen3-0.6B", "Qwen3-0.6B_torch_dist"], tail
assert "successfully saved checkpoint" in log[-7]
max_width(tail)
d.set("STORAGE_TAIL_N", len(tail))
d.array("STORAGE_TAIL_OUT", tail)
d.write()
