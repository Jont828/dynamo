"""Beat 07 (trajectory) data: writes build/07_trajectory.data.sh from kit files only.

Usage: $PY -I fill/07_trajectory.py

Kit root = slime-retool/. Sources:
  tools/trajectory.py runs/run1 --sample 958 3 1   run here; its stdout is the beat's output, unchanged
  runs/run1/turns.joined.jsonl     sample 958's turns: text, step, group, weight version, engine, tokens
  runs/run1/turn-logs/*.jsonl      the "final" records: group 119's 8 attempts and their rewards
  functions/tool_sandbox.py        TOOL_CONFIGS["max_turns"] (requests per trajectory)
  manifests/rayjob.yaml            --context-parallel-size x --max-tokens-per-gpu (the token cap)

Every quoted phrase in the per-turn notes is asserted to appear in that turn's text, and every number
the narration uses is computed here and asserted against the tool's own output where it shows one.
"""

import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import Data  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TEAM = HERE.parent
KIT = TEAM.parent.parent
RUN = KIT / "runs" / "run1"
OUT = TEAM / "build" / "07_trajectory.data.sh"
SOURCES = ["tools/trajectory.py", "runs/run1/turns.joined.jsonl", "runs/run1/turn-logs", "functions/tool_sandbox.py", "manifests/rayjob.yaml"]

SAMPLE = 958
N_CODE, N_OUT = 3, 1  # code lines and sandbox-output lines per turn: 3 shows every code block whole
CMD = f"python3 tools/trajectory.py runs/run1 --sample {SAMPLE} {N_CODE} {N_OUT}"

# One note per turn, from the model's own text. Each entry: (note, phrases that must appear verbatim in
# that turn's text). Paraphrase is unquoted; quotes are exact. No backslashes: the notes go through echo.
NOTES = [
    ('It sets up n^2 < 10^7, so n < sqrt(10^7). "Let\'s compute that." Then it calls the tool.',
     ["Let's compute that.", "n^2 < 10^7", "sqrt{10^7}", '"name": "code_interpreter"']),
    ('"But we need the floor of this value." "To confirm, let\'s check the squares of 3162 and 3163:"',
     ["But we need the floor of this value", "To confirm, let's check the squares of 3162 and 3163:"]),
    ("No text at all: just the tool call for 3163, the second square it announced.",
     ["n = 3163"]),
    ('3162^2 < 10^7 < 3163^2, so 3162. Then, "just to be thorough", it checks floor(sqrt(10^7)) too.',
     ["just to be thorough", "is indeed 3162", "math.floor(math.sqrt(10**7))"]),
    ('"I think that\'s thorough." It answers 3162, which matches the label: reward +1.',
     ["I think that's thorough.", "Answer: \\boxed{3162}"]),
]
# Turn 3 has no prose at all: its whole text is the tool call.
NO_PROSE_TURN = 3


def main():
    # --- The tool's real output, for exactly the command shown -------------------------------------
    out = subprocess.run([sys.executable, "-I", "tools/trajectory.py", "runs/run1", "--sample", str(SAMPLE),
                          str(N_CODE), str(N_OUT)], cwd=KIT, check=True, capture_output=True, text=True).stdout
    lines = out.rstrip("\n").split("\n")
    head, rest = lines[0], lines[1:]
    q_lines = [l for l in rest[:2] if l.startswith("Q: ") or (l.startswith("   ") and not l.startswith("   │"))]
    body = rest[len(q_lines):]
    starts = [i for i, l in enumerate(body) if l.startswith("turn ")]
    assert starts and starts[0] == 0, body[:3]
    assert len(starts) == 5, starts
    assert not any("more line" in l for l in body), "a code block or output was clipped; raise N_CODE/N_OUT"
    assert max(len(l) for l in lines) <= 96, "trajectory.py's layout is 96 columns"

    # --- The turns behind it --------------------------------------------------------------------------
    turns = [json.loads(l) for l in (RUN / "turns.joined.jsonl").read_text().splitlines()
             if f'"sample": {SAMPLE},' in l]
    turns = sorted((t for t in turns if t["sample"] == SAMPLE), key=lambda t: t["turn"])
    assert len(turns) == 5 and [t["turn"] for t in turns] == list(range(5))
    step = turns[0]["step"]
    group = turns[0]["group"]
    assert {t["step"] for t in turns} == {step} and {t["group"] for t in turns} == {group}
    engines = {t["engine"] for t in turns}
    assert engines == {"worker-0"}, engines
    versions = {t["weight_version"] for t in turns}
    assert versions == {str(step + 1)}, versions  # rollout k runs on v(k+1)
    for t, (note, phrases) in zip(turns, NOTES):
        for p in phrases:
            assert p in t["text"], (t["turn"], p)
        assert "\\" not in note, note
        for n in re.findall(r"\d{4,}", note):
            assert n in t["text"] or n in (t.get("code") or ""), (t["turn"], n)
    prose = re.sub(r"<tool_call>.*?</tool_call>", "", turns[NO_PROSE_TURN - 1]["text"], flags=re.S).strip()
    assert prose == "", prose
    assert turns[-1]["action"] == "answer" and turns[-1]["answer"] == turns[0]["label"] == "3162"
    for i, t in enumerate(turns):  # the tool's header lines show the same numbers
        b = body[starts[i]]
        assert f"prompt {t['prompt_tokens']:,} · cached {t['cached_tokens']:,}" in b, b

    # Which problem of the step, and which attempt of the group.
    groups_per_step = 32
    first_group = step * groups_per_step
    problem_ordinal = group - first_group + 1
    joined = [json.loads(l) for l in (RUN / "turns.joined.jsonl").read_text().splitlines() if l.strip()]
    step_groups = sorted({t["group"] for t in joined if t["step"] == step})
    assert step_groups == list(range(first_group, first_group + groups_per_step)), step_groups[:3]
    group_samples = sorted({t["sample"] for t in joined if t["group"] == group})
    assert len(group_samples) == 8, group_samples
    attempt = group_samples.index(SAMPLE) + 1
    step_trajectories = len({t["sample"] for t in joined if t["step"] == step})
    assert step_trajectories == 256, step_trajectories
    n_steps = len({t["step"] for t in joined})
    assert n_steps == 10, n_steps

    # The group's 8 rewards (the turn logs' final records).
    finals = {}
    for f in sorted((RUN / "turn-logs").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line) if line.strip() else {}
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "final":
                finals[rec["sample"]] = rec
    scores = [finals[s]["score"] for s in group_samples]
    right = sum(1 for s in group_samples if finals[s]["acc"])
    assert finals[SAMPLE]["score"] == 1.0

    # Follow-ups (turns 2-5): the share of each prompt found in worker-0's cache.
    follow = [100 * t["cached_tokens"] / t["prompt_tokens"] for t in turns[1:]]
    hit_lo, hit_hi = round(min(follow)), round(max(follow))
    assert (hit_lo, hit_hi) == (93, 98), (hit_lo, hit_hi)
    for t in turns[1:]:
        assert t["prev_engine"] == "worker-0"

    # The caps the rollout code enforces (the model sees neither).
    sandbox = (KIT / "functions" / "tool_sandbox.py").read_text()
    max_turns = int(re.search(r'"max_turns":\s*(\d+)', sandbox).group(1))
    rayjob = (KIT / "manifests" / "rayjob.yaml").read_text()
    cp = int(re.search(r"--context-parallel-size (\d+)", rayjob).group(1))
    per_gpu = int(re.search(r"--max-tokens-per-gpu (\d+)", rayjob).group(1))
    assert "--rollout-max-context-len" not in rayjob  # so the cap is cp x per_gpu (generate_with_retool.py)
    token_cap = cp * per_gpu

    duration = turns[-1]["t_end"] - turns[0]["t_start"]

    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(problem_ordinal, f"{problem_ordinal}th")
    step_ord = {1: "1st", 2: "2nd", 3: "3rd"}.get(step + 1, f"{step + 1}th")
    scalars = {
        "TRAJECTORY_CMD": CMD,
        "TRAJECTORY_HEAD": head,
        "TRAJECTORY_SAMPLE": SAMPLE,
        "TRAJECTORY_STEP": step,
        "TRAJECTORY_STEP_ORD": step_ord,            # step 3 is the 4th of steps 0-9
        "TRAJECTORY_NSTEPS": n_steps,
        "TRAJECTORY_STEP_TRAJ": step_trajectories,
        "TRAJECTORY_VERSION": f"v{step + 1}",
        "TRAJECTORY_PROBLEM_NTH": ordinal,          # step 3's 24th problem
        "TRAJECTORY_ATTEMPT": attempt,              # attempt 7 of 8
        "TRAJECTORY_GROUP_SIZE": len(group_samples),
        "TRAJECTORY_NCODE": N_CODE,
        "TRAJECTORY_NOUT": N_OUT,
        "TRAJECTORY_ENGINE": "worker-0",
        "TRAJECTORY_HIT_LO": hit_lo,
        "TRAJECTORY_HIT_HI": hit_hi,
        "TRAJECTORY_NTURNS": len(turns),
        "TRAJECTORY_GROUP_RIGHT": right,
        "TRAJECTORY_MAX_REQUESTS": max_turns,
        "TRAJECTORY_TOKEN_CAP": f"{token_cap:,}",
        "TRAJECTORY_SECS": f"{duration:.1f}",
    }
    arrays = {
        "TRAJECTORY_Q": q_lines,
        "TRAJECTORY_LINES": body,
        "TRAJECTORY_TURN_START": starts,
        "TRAJECTORY_NOTES": [n for n, _ in NOTES],
    }
    assert right == 8 and len(set(scores)) == 1, scores  # the beat's last line says so

    d = Data(__file__)
    d.source(*SOURCES)
    for k, v in scalars.items():
        d.set(k, v)
    for k, v in arrays.items():
        d.array(k, v)
    d.write()
    print(f"wrote {OUT.relative_to(TEAM)}: {len(lines)} output lines, step {step}, {ordinal} problem, "
          f"attempt {attempt} of {len(group_samples)}, v{step + 1}, follow-ups {hit_lo}-{hit_hi}% cached, "
          f"group {right}/8 right, {duration:.1f} s")


if __name__ == "__main__":
    main()
