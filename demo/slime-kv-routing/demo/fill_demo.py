"""Fill slime-demo-kv.template.sh's comparison numbers from the run summaries.

Usage: python3 -I fill_demo.py <runs-dir>

Writes slime-demo-kv.sh next to the template. The closing card compares
round-robin (runs/rr) with KV-aware routing (runs/kv): the prefix-cache hit
rate pooled over all 30 rollouts, rollout and step time pooled over steps
1-9, and the Ray job's submit-to-finish time, each the mean of three
RayJobs. The narration quotes KV run 1's own hit rate, the run the demo
replays.
"""

import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent


def runs(runs_dir: pathlib.Path, phase: str):
    out = [json.loads(p.read_text()) for p in sorted((runs_dir / phase).glob("run*/summary.json"))]
    if len(out) != 3:
        raise SystemExit(f"{phase}: expected 3 runs, found {len(out)}")
    return out


def pooled(rs, field, first_step):
    return statistics.fmean(st[field] for r in rs for st in r["steps"] if st["step"] >= first_step)


def main() -> None:
    runs_dir = pathlib.Path(sys.argv[1])
    rr, kv = runs(runs_dir, "rr"), runs(runs_dir, "kv")
    kv1 = json.loads((runs_dir / "kv" / "run1" / "summary.json").read_text())
    values = {
        "RR_HIT": f"{100 * pooled(rr, 'prefix_cache_hit_rate', 0):.0f}%",
        "KV_HIT": f"{100 * pooled(kv, 'prefix_cache_hit_rate', 0):.0f}%",
        "RR_ROLLOUT": f"{pooled(rr, 'rollout_time', 1):.2f} s",
        "KV_ROLLOUT": f"{pooled(kv, 'rollout_time', 1):.2f} s",
        "RR_STEP": f"{pooled(rr, 'step_time', 1):.2f} s",
        "KV_STEP": f"{pooled(kv, 'step_time', 1):.2f} s",
        "RR_JOB": f"{statistics.fmean(r['ray_job_secs'] for r in rr):.0f} s",
        "KV_JOB": f"{statistics.fmean(r['ray_job_secs'] for r in kv):.0f} s",
        "KV_RUN1_HIT": f"{100 * kv1['agg']['hit_rate_mean']:.0f}%",
    }
    text = (HERE / "slime-demo-kv.template.sh").read_text()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    if "@" in "".join(line for line in text.splitlines() if line.startswith("CMP_") or line.startswith("HIT_MEAN")):
        raise SystemExit("unfilled placeholder left")
    out = HERE / "slime-demo-kv.sh"
    out.write_text(text)
    out.chmod(0o755)
    print(json.dumps(values, indent=2))


if __name__ == "__main__":
    main()
