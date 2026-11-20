"""Print one ReTool trajectory turn by turn, or list good candidates for the demo.

Usage:
  python3 -I trajectory.py <run-dir> --sample N [code-lines output-lines]   # print sample N
  python3 -I trajectory.py <run-dir> --pick [K]      # list K candidates (default 15)

Needs <run-dir>/turns.joined.jsonl (tools/analyze_retool.py writes it) and the turn logs'
"final" records for the reward. Lines stay under 100 columns.
"""

import json
import pathlib
import re
import sys

PREFIX = re.compile(r"^Solve the following math problem step by step\..*?where \$Answer is the answer to the problem\.\s*", re.S)
SUFFIX = re.compile(r"\s*Remember to put your answer on its own line after \"Answer:\"\.\s*$", re.S)
WIDTH = 96


def load(rd):
    turns = [json.loads(line) for line in (rd / "turns.joined.jsonl").read_text().splitlines() if line.strip()]
    finals = {}
    for f in sorted((rd / "turn-logs").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line) if line.strip() else {}
            except json.JSONDecodeError:  # a partial last line of a live log
                continue
            if rec.get("kind") == "final":
                finals[rec["sample"]] = rec
    by_sample = {}
    for t in turns:
        by_sample.setdefault(t["sample"], []).append(t)
    for ts in by_sample.values():
        ts.sort(key=lambda t: t["turn"])
    return by_sample, finals


def problem(question):
    return " ".join(SUFFIX.sub("", PREFIX.sub("", question or "")).split())


def clip(text, width=WIDTH):
    text = text.replace("\t", "    ")
    return text if len(text) <= width else text[: width - 1] + "…"


def code_lines(code, n=3):
    lines = [line.rstrip() for line in (code or "").splitlines() if line.strip() and not line.strip().startswith("#")]
    out = lines[:n]
    if len(lines) > n:
        out.append(f"… {len(lines) - n} more line{'s' if len(lines) - n != 1 else ''}")
    return out


def output_lines(output, n=2):
    lines = [line.rstrip() for line in (output or "").splitlines() if line.strip()]
    if lines and lines[0] == "Output:":
        lines = lines[1:]
    out = lines[:n]
    if len(lines) > n:
        out.append(f"… {len(lines) - n} more line{'s' if len(lines) - n != 1 else ''}")
    return out or ["(no output)"]


def render(ts, final, n_code=3, n_out=2):
    """Lines for one trajectory: a header, the question, and one block of lines per turn."""
    t0 = ts[0]
    engines = sorted({t["engine"] for t in ts})
    where = f"all on {engines[0]}" if len(engines) == 1 else "on " + ", ".join(engines)
    score = final.get("score") if final else None
    same = "=" if final and final.get("acc") else "≠"
    head = (f"step {t0['step']} · sample {t0['sample']} · {len(ts)} requests, {where} · "
            f"answer {final.get('pred') if final else '?'} {same} label {t0.get('label')}, reward {score:+.1f}")
    q = problem(t0.get("question"))
    qlines = [clip("Q: " + q[:WIDTH - 4], WIDTH)]
    if len(q) > WIDTH - 4:
        qlines.append(clip("   " + q[WIDTH - 4:], WIDTH))
    blocks = []
    for t in ts:
        cached, prompt = t["cached_tokens"] or 0, t["prompt_tokens"] or 0
        share = f"{100 * cached / prompt:.0f}%" if prompt else "–"
        if t.get("action") == "code":
            what = f"wrote {t['output_tokens']:,} tokens, then a tool call"
        elif t.get("action") == "answer":
            what = f"wrote {t['output_tokens']:,} tokens: Answer: \\boxed{{{t.get('answer')}}}"
        else:
            what = f"wrote {t['output_tokens']:,} tokens ({t.get('tool_outcome') or t.get('loop')})"
        lines = [clip(f"turn {t['turn'] + 1} → {t['engine']} · prompt {prompt:,} · cached {cached:,} ({share}) · {what}")]
        if t.get("action") == "code":
            lines += [clip("   │ " + c) for c in code_lines(t.get("code"), n_code)]
            lines += [clip("   ⇢ " + o) for o in output_lines(t.get("output"), n_out)]
        blocks.append(lines)
    return head, qlines, blocks


def pick(by_sample, finals, k):
    rows = []
    for s, ts in by_sample.items():
        f = finals.get(s)
        if not f or len(ts) < 4 or len(ts) > 6 or not f.get("acc"):
            continue
        if len({t["engine"] for t in ts}) != 1 or any(t.get("tool_outcome") not in (None, "ok") for t in ts):
            continue
        codes = [t for t in ts if t.get("action") == "code"]
        long_code = max((len((t.get("code") or "").splitlines()) for t in codes), default=0)
        reuse = min(((t["cached_tokens"] or 0) / t["prompt_tokens"] for t in ts[1:] if t["prompt_tokens"]), default=0)
        qlen = len(problem(ts[0].get("question")))
        rows.append((long_code + qlen / 60, s, ts[0]["step"], len(ts), long_code, qlen, reuse))
    rows.sort()
    print("score   sample step requests max-code-lines question-chars min-reuse")
    for r in rows[:k]:
        print(f"{r[0]:6.1f} {r[1]:7d} {r[2]:4d} {r[3]:8d} {r[4]:14d} {r[5]:14d} {100 * r[6]:8.0f}%")


def main():
    rd = pathlib.Path(sys.argv[1])
    by_sample, finals = load(rd)
    if sys.argv[2] == "--pick":
        pick(by_sample, finals, int(sys.argv[3]) if len(sys.argv) > 3 else 15)
        return
    s = int(sys.argv[3])
    n_code, n_out = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (3, 2)
    head, qlines, blocks = render(by_sample[s], finals.get(s), n_code, n_out)
    print(head)
    print("\n".join(qlines))
    for lines in blocks:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
