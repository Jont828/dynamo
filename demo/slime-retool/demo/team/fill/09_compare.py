"""Beat 09 (compare) data: writes build/09_compare.data.sh from kit files only.

Usage: $PY -I fill/09_compare.py

Kit root = slime-retool/. Sources:
  diff -I '^#' manifests/dgd.yaml manifests/dgd-rr.yaml        run here; its stdout is shown unchanged
  tools/compare_runs.py runs/run1 runs/rr1 --names KV RR --out <temp file>
                                 run here: its Markdown and its JSON must equal runs/compare-kv-rr.{md,json}, so
                                 the charts show what the two runs' logs give today
  tools/compare_bars.py runs/compare-kv-rr.json --part work|time|slowest
                                 run here, once per chart screen, with --color always (stdout is a pipe here; the
                                 beat types the command without it, because a terminal gets color by default). Its
                                 output is shown unchanged. Checked here: every value printed, against the JSON
                                 (computed independently) and the numbers REPORT.md quotes; every bar
                                 and column proportional to its value from zero; the widths; and the rows, so each
                                 screen fits under the pinned header
  runs/compare-kv-rr.json        the numbers the charts draw
  runs/run1/summary.json         stayed/moved shares, the router's load spread and the history it weighed
  runs/run1/turns.joined.jsonl   sample 958's turns 1 and 2 (the example hit)
  runs/{run1,rr1}/turns.joined.jsonl
                                 the sandbox timeouts in each step's slowest trajectory (tool_outcome "timeout")
  REPORT.md                      page size 16's decode cost, measured in the earlier experiment (section 5)
"""

import json
import math
import pathlib
import re
import subprocess
import sys
import tempfile
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import Data  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TEAM = HERE.parent
KIT = TEAM.parent.parent
RUNS = KIT / "runs"
OUT = TEAM / "build" / "09_compare.data.sh"
SOURCES = ["manifests/dgd.yaml", "manifests/dgd-rr.yaml", "tools/compare_runs.py", "tools/compare_bars.py",
           "runs/compare-kv-rr.md", "runs/compare-kv-rr.json", "runs/run1/summary.json", "runs/run1/turns.joined.jsonl",
           "runs/rr1/turns.joined.jsonl", "REPORT.md"]

DIFF = ["diff", "-I", "^#", "manifests/dgd.yaml", "manifests/dgd-rr.yaml"]
DIFF_CMD = "diff -I '^#' manifests/dgd.yaml manifests/dgd-rr.yaml"
CMP_ARGS = ["tools/compare_runs.py", "runs/run1", "runs/rr1", "--names", "KV", "RR"]
BARS = ["tools/compare_bars.py", "runs/compare-kv-rr.json"]
PARTS = ("work", "time", "slowest")
# Output rows that fit on a screen: 32 rows, less the pinned header (4), the narration line and the command
# (1 each), and the cursor's row; the slowest-trajectory screen also keeps 6 rows for the verdict.
MAX_ROWS = {"work": 25, "time": 25, "slowest": 19}
SAMPLE, TURN = 958, 1  # the example hit: sample 958's second request (turn index 1)
BLOCK = 16
NAMES = {"KV": "KV-aware", "RR": "Round-robin"}
COLOR = {"38;2;57;135;229": "KV", "38;2;217;89;38": "RR"}   # compare_bars.py's two runs
SGR = re.compile(r"\x1b\[([0-9;]*)m")
EIGHTHS = " ▁▂▃▄▅▆▇█"


def half_up(x: float) -> int:
    """Round as REPORT.md does (144.5 -> 145), not Python's half-to-even."""
    return math.floor(x + 0.5)


def signed(x: float) -> str:
    """compare_bars.py's per-step difference: +42, −35 (a real minus sign), 0."""
    return "0" if round(x) == 0 else f"{round(x):+d}".replace("-", "−")


def width(line: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in SGR.sub("", line))


def cells(line: str):
    """(column, run, char) for each character drawn in a run's color."""
    out, col, color = [], 0, ""
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = SGR.fullmatch(part)
        if m:
            color = m.group(1)
            continue
        for ch in part:
            if color in COLOR:
                out.append((col, COLOR[color], ch))
            col += 1
    return out


def bar_rows(colored, plain):
    """The horizontal bars: [(run, half cells, value text, note)], from 'Label  ▄▄▄▖ value  note' rows."""
    rows = []
    for c, p in zip(colored, plain):
        drawn = [ch for _, _, ch in cells(c)]
        m = re.fullmatch(r"\s+(?:[a-z][a-z ()]+?\s{2,})?(KV-aware|Round-robin)\s{2,}([▄▖]*) (\S+(?: s| ms)?)(?:  (\S+))?", p)
        if not m or not drawn or set(drawn) - set("▄▖"):
            continue
        assert "".join(drawn) == m.group(2), p
        halves = 2 * drawn.count("▄") + drawn.count("▖")
        assert drawn.count("▖") <= 1 and (drawn[-1] == "▖" or "▖" not in drawn), p
        rows.append((m.group(1), halves, m.group(3), m.group(4)))
    return rows


def proportional(heights, values, tol, what):
    """Every bar or column is drawn to one zero-based scale: height = s × value, rounded."""
    s = sum(heights) / sum(values)
    for h, v in zip(heights, values):
        assert abs(h - s * v) <= tol, (what, h, round(s * v, 2), heights, values)


def column_heights(colored):
    """A column chart's columns, left to right: {x: [run, eighths summed over its rows]}."""
    cols = {}
    for line in colored:
        seen = {}
        for x, run, ch in cells(line):
            if ch in EIGHTHS[1:] and x not in seen:
                seen[x] = (run, EIGHTHS.index(ch))
        for x, (run, e) in seen.items():
            if x + 1 in seen and seen[x + 1] == seen[x] and x - 1 not in seen:
                cols.setdefault(x, [run, 0])[1] += e          # each column is two cells wide: count it once
    return [cols[x] for x in sorted(cols)]


def main():
    # --- The one change between the runs: real diff output --------------------------------------------
    d = subprocess.run(["/usr/bin/diff", *DIFF[1:]], cwd=KIT, capture_output=True, text=True)
    assert d.returncode == 1, d  # 1 = files differ
    diff_out = d.stdout.rstrip("\n").split("\n")
    assert diff_out[1:] == ["<             value: kv", "<           - name: DYN_ROUTER_PREDICTED_TTL_SECS",
                            "<             value: '5'", "---", ">             value: round-robin"], diff_out

    # --- compare_runs.py still gives the saved Markdown and JSON ---------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        fresh = pathlib.Path(tmp) / "compare.json"
        full = subprocess.run([sys.executable, "-I", *CMP_ARGS, "--out", str(fresh)], cwd=KIT, check=True,
                              capture_output=True, text=True).stdout
        assert full.rstrip("\n") == (RUNS / "compare-kv-rr.md").read_text().rstrip("\n"), \
            "compare_runs.py no longer matches runs/compare-kv-rr.md"
        assert json.loads(fresh.read_text()) == json.loads((RUNS / "compare-kv-rr.json").read_text()), \
            "compare_runs.py no longer matches runs/compare-kv-rr.json"

    c = json.loads((RUNS / "compare-kv-rr.json").read_text())
    K, R = c["KV"], c["RR"]
    steps = sorted(K["steps"], key=int)
    assert steps == sorted(R["steps"], key=int) and len(steps) == 10
    s = json.loads((RUNS / "run1" / "summary.json").read_text())

    # --- The example hit: sample 958, turn 2 (from step 07's trajectory) -------------------------------
    turns_kv = [json.loads(line) for line in (RUNS / "run1" / "turns.joined.jsonl").read_text().splitlines() if line]
    turns = sorted((t for t in turns_kv if t["sample"] == SAMPLE), key=lambda t: t["turn"])
    prev, cur = turns[TURN - 1], turns[TURN]
    history = prev["input_len"] + prev["output_tokens"]          # turn 1's prompt + its output
    obs = prev["obs_tokens"]                                     # the sandbox output appended after it
    assert cur["prompt_tokens"] == history + obs, (cur["prompt_tokens"], history, obs)
    partial = history % BLOCK                                    # the tail that didn't fill a 16-token block
    assert cur["cached_tokens"] == history - partial == BLOCK * (history // BLOCK)
    computed = cur["prompt_tokens"] - cur["cached_tokens"]
    assert computed == obs + partial
    assert cur["engine"] == prev["engine"] == "worker-0"

    # --- Stayed and moved (KV run), and why turns moved --------------------------------------------------
    rv = s["router_view"]
    assert round(100 * s["same_engine"]) == round(100 * K["same_engine"]) == 61

    # --- The charts' numbers, computed here from the JSON ------------------------------------------------
    mean_half = lambda X: sum(X["steps"][k]["finished_by"]["p50"] for k in steps) / len(steps)  # noqa: E731
    crit = lambda X: [X["steps"][k]["critical"] for k in steps]                                 # noqa: E731
    part_sum = lambda X, key: sum(x[key] for x in crit(X))                                      # noqa: E731
    for X in (K, R):   # the four parts of a slowest trajectory add up to its span
        assert abs(sum(part_sum(X, key) for key in ("decode_s", "between_requests_s", "ttft_s",
                                                    "wait_before_first_s")) - part_sum(X, "span_s")) < 0.05
    engines = len(K["per_engine_requests"])
    assert engines == len(R["per_engine_requests"]) == 4
    rollout_diff = [R["steps"][k]["rollout_s"] - K["steps"][k]["rollout_s"] for k in steps]
    swing = [R["steps"][k]["finished_by"]["p100"] - K["steps"][k]["finished_by"]["p100"] for k in steps]
    pairs = {   # (run, value as printed, note) per bar, in the order the charts print them
        "work": [("KV-aware", f"{100 * K['hit_rate']:.1f}%", None), ("Round-robin", f"{100 * R['hit_rate']:.1f}%", None),
                 ("KV-aware", f"{K['computed_tokens'] / 1e6:.2f}M", None),
                 ("Round-robin", f"{R['computed_tokens'] / 1e6:.2f}M", f"×{R['computed_tokens'] / K['computed_tokens']:.2f}"),
                 ("KV-aware", f"{100 * K['same_engine']:.0f}%", None), ("Round-robin", f"{100 * R['same_engine']:.0f}%", None)],
        "time": [("KV-aware", f"{mean_half(K):.1f} s", None), ("Round-robin", f"{mean_half(R):.1f} s", None),
                 ("KV-aware", f"{K['latency']['followup']['ttft_p50_ms']:.0f} ms", None),
                 ("Round-robin", f"{R['latency']['followup']['ttft_p50_ms']:.0f} ms", None),
                 ("KV-aware", f"{K['rollout_sum_s']:,.0f} s", None),
                 ("Round-robin", f"{R['rollout_sum_s']:,.0f} s", f"{100 * (R['rollout_sum_s'] / K['rollout_sum_s'] - 1):+.1f}%")],
        "slowest": [(NAMES[n], f"{part_sum(X, key):,.0f} s", None)
                    for key in ("decode_s", "between_requests_s", "ttft_s", "wait_before_first_s")
                    for n, X in (("KV", K), ("RR", R))],
    }
    values = {   # the bars' values, for the proportionality check
        "work": [[K["hit_rate"], R["hit_rate"]], [K["computed_tokens"], R["computed_tokens"]],
                 [K["same_engine"], R["same_engine"]]],
        "time": [[mean_half(K), mean_half(R)], [K["latency"]["followup"]["ttft_p50_ms"], R["latency"]["followup"]["ttft_p50_ms"]],
                 [K["rollout_sum_s"], R["rollout_sum_s"]]],
        "slowest": [[part_sum(X, key) for key in ("decode_s", "between_requests_s", "ttft_s", "wait_before_first_s")
                     for X in (K, R)]],   # one scale for all eight bars
    }
    columns = {   # per-step columns, KV then RR in each step
        "work": [X["steps"][k]["computed_tokens"] for k in steps for X in (K, R)],
        "time": [X["steps"][k]["rollout_s"] for k in steps for X in (K, R)],
    }

    # --- compare_bars.py: run it, and check everything it prints ---------------------------------------
    charts, cmds = {}, {}
    for part in PARTS:
        run = lambda color: subprocess.run([sys.executable, "-I", *BARS, "--part", part, "--color", color],  # noqa: E731
                                           cwd=KIT, check=True, capture_output=True, text=True).stdout.rstrip("\n").split("\n")
        colored, plain = run("always"), run("never")
        assert [SGR.sub("", line) for line in colored] == plain, f"{part}: color changes the layout"
        assert len(plain) <= MAX_ROWS[part], (part, len(plain))
        assert max(width(line) for line in plain) <= 118, part
        assert plain[0] == "  ██ KV-aware   ██ Round-robin", plain[0]
        bars = bar_rows(colored, plain)
        assert [(r, v, n) for r, _, v, n in bars] == pairs[part], (part, bars, pairs[part])
        flat = [h for _, h, _, _ in bars]
        groups = values[part]
        at = 0
        for g in groups:   # bars that share a scale: each pair, or all eight in the slowest chart
            proportional(flat[at:at + len(g)], g, 1.0, part)
            at += len(g)
        if part in columns:
            top = next(i for i, line in enumerate(plain) if "in each step" in line)   # the column chart's title
            end = next(i for i, line in enumerate(plain) if line.strip().startswith("step "))
            cols = column_heights(colored[top + 1:end])
            assert [run for run, _ in cols] == ["KV", "RR"] * len(steps), cols
            proportional([e for _, e in cols], columns[part], 1.0, part + " columns")
            assert plain[end].split()[1:] == steps, plain[end]
        if part == "work":   # the chance mark sits at 1/engines of the hit-rate bars' 0-100% track
            mark = next(line for line in plain if line.lstrip().startswith("↑"))
            assert mark.strip() == f"↑ {100 / engines:.0f}%: chance alone, with {engines} engines", mark
            start = plain[3].index("▄")                                  # where every bar starts
            track = bars[0][1] / (2 * K["hit_rate"])                     # cells for 100%
            assert abs(mark.index("↑") - start - track / engines) <= 1, (mark.index("↑"), start, track)
        if part == "time":
            diff_row = next(line for line in plain if line.strip().startswith("diff "))
            assert diff_row.split()[1:] == [signed(x) for x in rollout_diff], diff_row
        charts[part] = colored
        cmds[part] = f"python3 {' '.join(BARS)} --part {part}"

    # --- The verdict's numbers --------------------------------------------------------------------------
    # Sandbox timeouts in each step's slowest trajectory, from the turn logs (the slowest-trajectory chart's
    # "between turns (sandbox)" row).
    turns_rr = [json.loads(line) for line in (RUNS / "rr1" / "turns.joined.jsonl").read_text().splitlines() if line]
    timeouts = {}
    for name, X, ts in (("KV", K, turns_kv), ("RR", R, turns_rr)):
        slow = {(int(k), X["steps"][k]["critical"]["sample"]) for k in steps}
        hits = [t for t in ts if (t["step"], t["sample"]) in slow and t.get("tool_outcome") == "timeout"]
        assert all(20 <= t["tool_run_s"] < 21 for t in hits), [t["tool_run_s"] for t in hits]
        timeouts[name] = hits
    # The big per-step swings (25 s or more) each come from the slower run's slowest trajectory generating
    # thousands more tokens, or waiting on more sandbox timeouts: neither depends on the router.
    for k, sw in zip(steps, swing):
        if abs(sw) < 25:
            continue
        slow, fast = (R, K) if sw > 0 else (K, R)
        nslow, nfast = ("RR", "KV") if sw > 0 else ("KV", "RR")
        more_tokens = slow["steps"][k]["critical"]["output_tokens"] - fast["steps"][k]["critical"]["output_tokens"]
        more_timeouts = (sum(1 for t in timeouts[nslow] if t["step"] == int(k))
                         - sum(1 for t in timeouts[nfast] if t["step"] == int(k)))
        assert more_tokens >= 2500 or more_timeouts >= 2, (k, sw, more_tokens, more_timeouts)
    report = (KIT / "REPORT.md").read_text()
    m = re.search(r"which the first experiment measured at about (\d+)% slower decoding than SGLang's default page "
                  r"size 1 on the tutorial's (Qwen3-[\d.]+B)", " ".join(report.split()))
    assert m, "REPORT.md section 5 no longer states page size 16's decode cost"

    scalars = {
        "COMPARE_DIFF_CMD": DIFF_CMD,
        "COMPARE_WORK_CMD": cmds["work"],
        "COMPARE_TIME_CMD": cmds["time"],
        "COMPARE_SLOWEST_CMD": cmds["slowest"],
        "COMPARE_STEPS": len(steps),
        # the example hit
        "COMPARE_HIT_SAMPLE": SAMPLE,
        "COMPARE_HIT_PROMPT": f"{cur['prompt_tokens']:,}",
        "COMPARE_HIT_CACHED": f"{cur['cached_tokens']:,}",
        "COMPARE_HIT_PCT": f"{100 * cur['cached_tokens'] / cur['prompt_tokens']:.0f}",
        "COMPARE_HIT_COMPUTED": computed,
        "COMPARE_BLOCK": BLOCK,
        # stayed / moved
        "COMPARE_STAYED_KV": f"{100 * K['same_engine']:.0f}",
        "COMPARE_MOVED_KV": f"{100 * (1 - K['same_engine']):.0f}",
        "COMPARE_LOAD_SPREAD": half_up(rv["engine_cost_spread_blocks_p50"]),
        "COMPARE_HISTORY": half_up(rv["followup_history_blocks_p50"]),
        # the verdict
        "COMPARE_TIMEOUT_S": 20,
        "COMPARE_TIMEOUTS_KV": len(timeouts["KV"]),
        "COMPARE_TIMEOUTS_RR": len(timeouts["RR"]),
        "COMPARE_PAGE16_PCT": m.group(1),
    }
    # Checked only: the numbers the charts print, as REPORT.md quotes them.
    shown = {
        "hit": f"{100 * K['hit_rate']:.1f} / {100 * R['hit_rate']:.1f}",
        "prefill": f"{K['computed_tokens'] / 1e6:.2f}M / {R['computed_tokens'] / 1e6:.2f}M",
        "prefill_x": f"{R['computed_tokens'] / K['computed_tokens']:.2f}",
        "stayed": f"{100 * K['same_engine']:.0f} / {100 * R['same_engine']:.0f}",
        "half": f"{mean_half(K):.1f} / {mean_half(R):.1f}",
        "half_sum": f"{len(steps) * mean_half(K):.1f} / {len(steps) * mean_half(R):.1f}",
        "ttft": f"{K['latency']['followup']['ttft_p50_ms']:.0f} / {R['latency']['followup']['ttft_p50_ms']:.0f}",
        "rollout": f"{K['rollout_sum_s']:,.0f} / {R['rollout_sum_s']:,.0f}",
        "rollout_pct": f"{100 * (R['rollout_sum_s'] / K['rollout_sum_s'] - 1):+.1f}",
        "swing": f"{signed(min(swing))} / {signed(max(swing))}",
        "rollout_diff": f"{signed(min(rollout_diff))} / {signed(max(rollout_diff))}",
        "decode": f"{part_sum(K, 'decode_s'):.0f} / {part_sum(R, 'decode_s'):.0f}",
        "first_tokens": f"{part_sum(K, 'ttft_s'):.0f} / {part_sum(R, 'ttft_s'):.0f}",
        "between": f"{part_sum(K, 'between_requests_s'):.0f} / {part_sum(R, 'between_requests_s'):.0f}",
    }
    expect = {"hit": "79.0 / 59.7", "prefill": "5.66M / 10.45M", "prefill_x": "1.85", "stayed": "61 / 24",
              "half": "18.7 / 18.8", "half_sum": "186.9 / 187.7", "ttft": "140 / 141", "rollout": "985 / 1,047",
              "rollout_pct": "+6.2", "swing": "−35 / +42", "rollout_diff": "−35 / +42", "decode": "938 / 873",
              "first_tokens": "13 / 18", "between": "24 / 148"}
    assert shown == expect, {k: (shown[k], expect[k]) for k in expect if shown[k] != expect[k]}
    expect = {"COMPARE_HIT_PROMPT": "719", "COMPARE_HIT_CACHED": "672", "COMPARE_HIT_PCT": "93",
              "COMPARE_HIT_COMPUTED": 47, "COMPARE_STAYED_KV": "61", "COMPARE_MOVED_KV": "39",
              "COMPARE_LOAD_SPREAD": 145, "COMPARE_HISTORY": 114, "COMPARE_TIMEOUTS_KV": 1, "COMPARE_TIMEOUTS_RR": 7,
              "COMPARE_PAGE16_PCT": "9", "COMPARE_STEPS": 10}
    for k, v in expect.items():
        assert scalars[k] == v, (k, scalars[k], v)

    arrays = {"COMPARE_DIFF_OUT": diff_out, "COMPARE_WORK_OUT": charts["work"], "COMPARE_TIME_OUT": charts["time"],
              "COMPARE_SLOWEST_OUT": charts["slowest"]}
    d = Data(__file__)
    d.source(*SOURCES)
    for k, v in scalars.items():
        d.set(k, v)
    for k, v in arrays.items():
        d.array(k, v)
    d.write()
    print(f"wrote {OUT.relative_to(TEAM)}: diff {len(diff_out)} lines; charts "
          + ", ".join(f"{p} {len(charts[p])} rows" for p in PARTS)
          + f"; hit {scalars['COMPARE_HIT_CACHED']}/{scalars['COMPARE_HIT_PROMPT']}; timeouts in the slowest "
          f"trajectories {scalars['COMPARE_TIMEOUTS_KV']} (KV) / {scalars['COMPARE_TIMEOUTS_RR']} (RR)")


if __name__ == "__main__":
    main()
