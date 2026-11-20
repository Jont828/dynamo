"""Data for beats/03_storage.sh (Step 2: stage the model and data on Azure Managed Lustre).

Reads (kit root = slime-retool/):
  manifests/storage.yaml          the lines typed in vim (its kit-internal header comment left out)
  manifests/setup-job.yaml        the Job's name; the model and the dataset it downloads
  runs/setup/storage-apply.txt    output of `kubectl apply -f manifests/storage.yaml`
  runs/setup/job.yaml, times.txt  the Job's start and completion
  runs/setup/setup.log            phase times (setup start, downloads done, checkpoint saved, setup end)
                                  and the log's last lines (the setup script's `ls` and `du -sh`)
  runs/setup/dataset-rows.txt     the number of problems

The beat types STORAGE_YAML in four parts, split after the lines its callouts explain
(STORAGE_LN_RETAIN, STORAGE_LN_SUBDIR, STORAGE_LN_VOLNAME); this script checks that the
typed lines are the file after its header, unchanged and in order.
"""

import pathlib
import re
import sys

import yaml  # PyYAML, in the venv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import (Data, check_in_file, check_typed, find_line, fmt_int, kit_path, load_yaml,  # noqa: E402
                     manifest_lines, max_width, read_lines, secs)

STORAGE = "manifests/storage.yaml"

d = Data(__file__)
d.source(STORAGE, "manifests/setup-job.yaml", "runs/setup/storage-apply.txt", "runs/setup/job.yaml",
         "runs/setup/times.txt", "runs/setup/setup.log", "runs/setup/dataset-rows.txt")

# --- storage.yaml, typed in vim ----------------------------------------------------------------
lines = manifest_lines(STORAGE)
check_typed(lines, STORAGE)
check_in_file("\n".join(lines), STORAGE)          # contiguous: nothing skipped after the header
assert lines[-1] == read_lines(STORAGE)[-1]       # ... through the file's last line
d.array("STORAGE_YAML", lines)
d.set("STORAGE_FILE", STORAGE)

ln = {
    "STORAGE_LN_ACCESS": find_line(lines, "  accessModes:"),                       # the PV's
    "STORAGE_LN_RETAIN": find_line(lines, "  persistentVolumeReclaimPolicy: Retain"),
    "STORAGE_LN_CSI": find_line(lines, "  csi:"),
    "STORAGE_LN_DRIVER": find_line(lines, "    driver: azurelustre.csi.azure.com"),
    "STORAGE_LN_SUBDIR": find_line(lines, "      sub-dir: slime-retool"),
    "STORAGE_LN_CLAIMREF": find_line(lines, "  claimRef:"),
    "STORAGE_LN_VOLNAME": find_line(lines, "  volumeName: pv-lustre-slime-retool"),
}
assert lines[ln["STORAGE_LN_ACCESS"]] == "    - ReadWriteMany"
assert ln["STORAGE_LN_RETAIN"] == ln["STORAGE_LN_ACCESS"] + 2
assert lines[ln["STORAGE_LN_CSI"]] == "    driver: azurelustre.csi.azure.com"   # find_line is 1-based:
assert lines[ln["STORAGE_LN_CSI"] + 1].startswith("    volumeHandle: ")        # lines[n] is line n + 1
assert lines[ln["STORAGE_LN_SUBDIR"] - 2].startswith("      mgs-ip-address: ")
assert lines[ln["STORAGE_LN_CLAIMREF"]:ln["STORAGE_LN_CLAIMREF"] + 2] == ["    namespace: slime-retool",
                                                                       "    name: slime-data"]
assert (ln["STORAGE_LN_ACCESS"] < ln["STORAGE_LN_RETAIN"] < ln["STORAGE_LN_CSI"] < ln["STORAGE_LN_SUBDIR"]
        < ln["STORAGE_LN_CLAIMREF"] < ln["STORAGE_LN_VOLNAME"] < len(lines))
for name, value in ln.items():
    d.set(name, value)

docs = list(yaml.safe_load_all(kit_path(STORAGE).read_text()))
ns = next(x for x in docs if x["kind"] == "Namespace")["metadata"]["name"]
pvc = next(x for x in docs if x["kind"] == "PersistentVolumeClaim")["metadata"]["name"]
d.set("STORAGE_NAMESPACE", ns)
d.set("STORAGE_PVC", pvc)

# --- the apply -------------------------------------------------------------------------------
apply_out = read_lines("runs/setup/storage-apply.txt")
assert apply_out == [f"namespace/{ns} created", "persistentvolume/pv-lustre-slime-retool created",
                     f"persistentvolumeclaim/{pvc} created"], apply_out
d.array("STORAGE_APPLY_OUT", apply_out)

# --- the setup Job ---------------------------------------------------------------------------
job_manifest = load_yaml("manifests/setup-job.yaml")
job = job_manifest["metadata"]["name"]
script = job_manifest["spec"]["template"]["spec"]["containers"][0]["args"][0]
gpus = job_manifest["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"]
assert gpus == "1", gpus
d.set("STORAGE_JOB", job)
d.set("STORAGE_MODEL", re.search(r"hf download (\S+) --local-dir", script).group(1))
d.set("STORAGE_DATASET", re.search(r"hf download --repo-type dataset (\S+)", script).group(1))
assert "convert_hf_to_torch_dist.py" in script and "mkdir -p /data/weight-updates /data/turn-logs" in script
# kubectl's only output for creating this Job, and for waiting until it completes (runs/setup/job.yaml
# records that it was created and completed).
d.set("STORAGE_JOB_CREATED_OUT", f"job.batch/{job} created")
d.set("STORAGE_JOB_MET_OUT", f"job.batch/{job} condition met")

status = load_yaml("runs/setup/job.yaml")
assert status["metadata"]["name"] == job
start, done = status["status"]["startTime"], status["status"]["completionTime"]
assert read_lines("runs/setup/times.txt") == [f"start={start} completion={done}"]
log_lines = read_lines("runs/setup/setup.log")
log = "\n".join(log_lines)
setup_start = re.search(r"^setup start (\S+)$", log, re.M).group(1)
dl_done = re.search(r"^downloads done (\S+)$", log, re.M).group(1)
m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\.\d+\] successfully saved checkpoint", log)
saved = f"{m.group(1)}T{m.group(2)}Z"
setup_end = re.search(r"^setup end (\S+)$", log, re.M).group(1)
d.set("STORAGE_JOB_SECS", secs(start, done))         # 109: the fast-forward's length
d.set("STORAGE_DL_T", secs(start, dl_done))          # Job-seconds when the downloads were done
d.set("STORAGE_DL_SECS", secs(setup_start, dl_done))
d.set("STORAGE_CONV_T", secs(start, saved))          # ... when the torch_dist copy was saved
d.set("STORAGE_CONV_SECS", secs(dl_done, saved))

# The log's last lines: the setup script's `ls` and `du -sh`, then `setup end`.
first = max(i for i, line in enumerate(log_lines) if line == "/data/datasets/dapo-math-17k:")
tail = [line.expandtabs(8) for line in log_lines[first:]]
assert len(tail) == 9 and tail[-1] == f"setup end {setup_end}", tail
max_width(tail)
d.set("STORAGE_TAIL_N", len(tail))
d.array("STORAGE_TAIL_OUT", tail)
rows = int(read_lines("runs/setup/dataset-rows.txt")[0])
d.set("STORAGE_PROBLEMS", fmt_int(rows))
d.write()
