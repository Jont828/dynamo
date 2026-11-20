"""Data for beats/99_frameworktest.sh, the framework's own test beat (built only when named).

Writes build/99_frameworktest.data.sh from:
  runs/setup/{storage-apply.txt, job.yaml, setup.log}   the narrated command and the wait
  manifests/storage.yaml                                 typed in vim (without its header)
  manifests/rayjob.yaml                                  a long line that soft-wraps, a collapse
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import RUNS, Data, check_typed, find_line, load_yaml, manifest_lines, secs  # noqa: E402

d = Data(__file__)
d.source("runs/setup/storage-apply.txt", "runs/setup/job.yaml", "runs/setup/setup.log",
         "manifests/storage.yaml", "manifests/rayjob.yaml")

# Shell part: a command's real output and the setup Job's timeline.
d.set("FRAMEWORKTEST_STORAGE_OUT", (RUNS / "setup" / "storage-apply.txt").read_text().rstrip("\n"))
job = load_yaml("runs/setup/job.yaml")
start, done = job["status"]["startTime"], job["status"]["completionTime"]
log = (RUNS / "setup" / "setup.log").read_text()
downloaded = re.search(r"downloads done (\S+)", log).group(1)
saved = re.search(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\] successfully saved checkpoint", log).group(1)
d.set("FRAMEWORKTEST_SETUP_SECS", secs(start, done))
d.set("FRAMEWORKTEST_DL_T", secs(start, downloaded))
d.set("FRAMEWORKTEST_CONVERT_T", secs(start, saved.replace(" ", "T") + "Z"))
d.set("FRAMEWORKTEST_SETUP_TAIL", "\n".join(log.rstrip("\n").splitlines()[-3:]))

# storage.yaml without its kit-internal header comment: 42 lines, so typing it scrolls.
storage = manifest_lines("manifests/storage.yaml")
check_typed(storage, "manifests/storage.yaml")
d.array("FRAMEWORKTEST_STORAGE_LINES", storage)
pvc = find_line(storage, "kind: PersistentVolumeClaim") - 1   # the "apiVersion: v1" before it
d.set("FRAMEWORKTEST_PV_END", pvc - 2)                         # last PV line before "---"
d.set("FRAMEWORKTEST_PVC_START", pvc)
d.set("FRAMEWORKTEST_CSI_FROM", find_line(storage, "  csi:"))
d.set("FRAMEWORKTEST_CSI_TO", find_line(storage, "      sub-dir: slime-retool"))

# rayjob.yaml up to the entrypoint's first flags, the 147-column engine-address line,
# and the rest of the flags (collapsed).
rayjob = manifest_lines("manifests/rayjob.yaml")
check_typed(rayjob, "manifests/rayjob.yaml")
long_line = find_line(rayjob, "--rollout-external-engine-addrs")
last_flag = find_line(rayjob, "--custom-rm-path")
assert len(rayjob[long_line - 1]) > 114, "the engine-address line no longer needs to wrap"
d.array("FRAMEWORKTEST_RAYJOB_LINES", rayjob)
d.set("FRAMEWORKTEST_RAYJOB_LONG", long_line)
d.set("FRAMEWORKTEST_RAYJOB_LAST_FLAG", last_flag)
d.set("FRAMEWORKTEST_RAYJOB_KIND", find_line(rayjob, "kind: RayJob"))
d.set("FRAMEWORKTEST_RAYJOB_LONG_W", len(rayjob[long_line - 1]))
d.set("FRAMEWORKTEST_RAYJOB_FOLDED", last_flag - long_line)
d.write()
