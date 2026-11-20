"""Beat 01 (the problem) data: writes build/01_problem.data.sh from kit files only.

Usage: $PY -I fill/01_problem.py

Sources (kit root = slime-retool/):
  runs/run1/turns.joined.jsonl        the example trajectory, sample 1481 (step 5): its prompt, both turns'
                                      text, the code it ran, and the sandbox output; and sample 1485, a wrong
                                      attempt at the same problem in the same step
  runs/run1/turn-logs/*.jsonl         the "final" records: each sample's reward, answer, and tool calls
  runs/setup/dataset-rows.txt         the number of DAPO-Math problems
  manifests/setup-job.yaml            the dataset and checkpoint the run downloaded
  upstream/image/examples/retool/README.md   SFT on ReTool-SFT, then RL; the published SFT checkpoint and
                                      the model its SFT starts from (Qwen3-4B-Instruct-2507)
  functions/generate_with_retool.py   the signatures shown, the <interpreter> wrapping of sandbox output
  functions/tool_sandbox.py           PythonSandbox, the turn cap, the sandbox's memory and time limits
  manifests/dgd.yaml, manifests/rayjob.yaml   engines and GPUs on each side, the head-only Ray cluster,
                                      the stop string, the number of steps
  demo/team/snapshots/node-gpu-labels.txt     the node's VM size and GPUs (GPU Operator labels)

Every excerpt of the model's text is a list of verbatim fragments, asserted in order against the turn's
text and joined with " … " where text was cut. The transcript is wrapped to WIDTH columns; the beat adds a
gutter of GUTTER columns, so every line fits in 120.
"""

import json
import pathlib
import re
import sys
import textwrap

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, RUNS, TEAM, Data, fmt_int  # noqa: E402

RUN = RUNS / "run1"

GUTTER = 11          # "  sandbox  " in the beat
WIDTH = 120 - GUTTER - 1
EXAMPLE = 1481       # short, correct, 2 turns; beat 07 walks sample 958
WRONG = 1485         # the same problem, same step, answered 60

# Turn 1 of the example, cut to what fits in 4 lines. Each fragment is verbatim.
T1_FRAGMENTS = [
    "a number divisible by 15 must be divisible by both 3 and 5. The units digit is already 5, ensuring "
    "divisibility by 5. For divisibility by 3, the sum of the digits (hundreds, tens, and units) must be a "
    "multiple of 3.",
    "I'll iterate through all possible values of h (1-9) and t (0-9), count those where (h + t) mod 3 equals "
    "1. This can be efficiently calculated using Python code.",
]
T1_MAX_LINES = 4


def wrap(text, width=WIDTH):
    lines = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    assert all(len(line) <= width for line in lines), lines
    return lines


def excerpt(text, fragments):
    """Join verbatim fragments of text in order; ' … ' marks each cut, including one at the start."""
    pos, parts = 0, []
    for frag in fragments:
        at = text.find(frag, pos)
        assert at >= 0, f"fragment not found (in order): {frag[:60]!r}"
        gap = text[pos:at]
        if parts and gap.strip() == "":
            parts.append(" " + frag)  # the next sentence follows directly
        else:
            parts.append(("… " if not parts else " … ") + frag)
        pos = at + len(frag)
    return "".join(parts)


def load_turns(samples):
    turns = {s: [] for s in samples}
    keys = tuple(f'"sample": {s},' for s in samples)
    with (RUN / "turns.joined.jsonl").open() as f:
        for line in f:
            if any(k in line for k in keys):
                rec = json.loads(line)
                if rec["sample"] in turns:
                    turns[rec["sample"]].append(rec)
    for ts in turns.values():
        ts.sort(key=lambda r: r["turn"])
    return turns


def load_finals(samples):
    finals = {}
    for path in sorted((RUN / "turn-logs").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:  # a partial last line of a live log
                continue
            if rec.get("kind") == "final" and rec["sample"] in samples:
                finals[rec["sample"]] = rec
    assert set(finals) == set(samples), finals.keys()
    return finals


def check_labels_are_integers():
    labels = {}
    with (RUN / "turns.joined.jsonl").open() as f:
        for line in f:
            if '"turn": 0,' in line:
                rec = json.loads(line)
                if rec["turn"] == 0:
                    labels[rec["group"]] = rec["label"]
    assert len(labels) == 320, len(labels)
    assert all(re.fullmatch(r"-?\d+", lab.strip()) for lab in labels.values())


def reward_text(score):
    return f"+{score:g}" if score > 0 else "−" + f"{-score:g}"


def main():
    turns = load_turns([EXAMPLE, WRONG])
    finals = load_finals([EXAMPLE, WRONG])
    ex, wr = turns[EXAMPLE], turns[WRONG]
    fex, fwr = finals[EXAMPLE], finals[WRONG]

    # --- the example: one problem, two turns, right -----------------------------------------------
    assert len(ex) == 2 and [t["turn"] for t in ex] == [0, 1]
    t1, t2 = ex
    step, group, wv = t1["step"], t1["group"], t1["weight_version"]
    assert (step, group, wv) == (5, 185, "6"), (step, group, wv)
    assert all((t["step"], t["group"], t["weight_version"]) == (step, group, wv) for t in ex + wr)
    assert t1["action"] == "code" and t1["tool_outcome"] == "ok"
    assert t1["finish"] == "stop" and t1["matched"] == "</tool_call>"
    assert t2["action"] == "answer" and t2["answer"] == "30"
    assert fex["acc"] is True and fex["score"] == 1.0 and fex["pred"] == "30" == fex["label"]
    assert fex["requests"] == 2 and fex["tool_calls"] == 1

    question = t1["question"]
    paras = question.split("\n\n")
    assert len(paras) == 3, paras
    instruction, problem, reminder = paras
    assert instruction.startswith("Solve the following math problem step by step. The last line of your response")
    assert instruction.endswith("where $Answer is the answer to the problem.")
    assert problem == "How many positive three-digit integers with a $5$ in the units place are divisible by $15$?"
    assert reminder == 'Remember to put your answer on its own line after "Answer:".'
    prompt_lines = wrap(instruction, 100) + [problem, reminder]  # 100 keeps "Answer: \boxed{...}" together
    assert prompt_lines[1].startswith("Answer: \\boxed{$Answer}"), prompt_lines
    problem_row = len(prompt_lines) - 2
    label = t1["label"]
    assert label == "30"

    text1 = t1["text"]
    t1_lines = wrap(excerpt(text1, T1_FRAGMENTS))
    assert len(t1_lines) <= T1_MAX_LINES, t1_lines

    m = re.search(r"<tool_call>\n(\{.*\})\n</tool_call>$", text1, re.S)
    assert m, "turn 1 doesn't end in a tool call"
    call_json = m.group(1)
    call = json.loads(call_json)
    assert call["name"] == "code_interpreter" and call["arguments"]["code"] == t1["code"]
    call_shown = call_json if len(call_json) <= WIDTH else call_json[: WIDTH - 1] + "…"
    call_lines = ["<tool_call>", call_shown, "</tool_call>"]

    code_lines = t1["code"].split("\n")
    assert code_lines == [
        "count = 0",
        "for h in range(1, 10):",
        "    for t in range(0, 10):",
        "        if (h + t) % 3 == 1:",
        "            count += 1",
        "print(count)",
    ], code_lines
    assert t1["output"] == "Output:\n30", t1["output"]
    gen_src = (KIT / "functions" / "generate_with_retool.py").read_text()
    obs_line = '            next_obs = f"\\n\\n<interpreter>\\n{result}\\n</interpreter>\\n\\n"'
    assert obs_line in gen_src.splitlines(), "execute_predictions no longer wraps output this way"
    obs_lines = ["<interpreter>"] + t1["output"].split("\n") + ["</interpreter>"]

    text2 = t2["text"]
    assert text2.endswith("<|im_end|>"), text2[-20:]
    body, _, answer = text2[: -len("<|im_end|>")].rpartition("\n\n\n")
    assert answer == "Answer: \\boxed{30}", answer
    t2_lines = wrap(body) + [answer]
    assert len(t2_lines) == 3, t2_lines

    # --- the wrong attempt: same problem, same step ----------------------------------------------
    assert fwr["acc"] is False and fwr["pred"] == "60" and fwr["label"] == "30"
    assert fwr["tool_calls"] == 1 and abs(fwr["score"] - (-1 + 0.05 * (1 - 2))) < 1e-9, fwr
    assert wr[0]["question"] == question and wr[0]["action"] == "code"
    wcode = wr[0]["code"].split("\n")
    for line in ("a_1 = 105", "a_n = 990", "d = 15", "n = (a_n - a_1) // d + 1", "print(n)"):
        assert line in wcode, (line, wcode)
    assert wr[0]["output"] == "Output:\n60", wr[0]["output"]
    assert (990 - 105) // 15 + 1 == 60 and len([n for n in range(105, 1000, 15) if n % 10 == 5]) == 30
    alt_lines = [
        "Another trajectory for the same problem ran  n = (a_n - a_1) // d + 1  with a_1 = 105, a_n = 990, d = 15:",
        f"all 60 three-digit multiples of 15, not just the 30 ending in 5. It answered \\boxed{{{fwr['pred']}}}: "
        f"reward {reward_text(fwr['score'])}.",
    ]
    assert all(len(line) <= WIDTH for line in alt_lines), [len(x) for x in alt_lines]

    # --- the data, the checkpoint, the code ------------------------------------------------------
    check_labels_are_integers()
    rows = int((KIT / "runs" / "setup" / "dataset-rows.txt").read_text().strip())
    assert rows == 17398, rows
    setup = (KIT / "manifests" / "setup-job.yaml").read_text()
    readme = (KIT / "upstream" / "image" / "examples" / "retool" / "README.md").read_text()
    dataset, ckpt, sft_data = "zhuzilin/dapo-math-17k", "font-info/qwen3-4b-sft-SGLang-RL", "JoeYing/ReTool-SFT"
    assert dataset in setup and ckpt in setup, "setup Job changed"
    assert ckpt in readme and sft_data in readme and "if you want to skip SFT" in readme
    # The SFT starts from the instruction-tuned model, not a raw base model (README step 2, "For SFT").
    sft_base = "Qwen3-4B-Instruct-2507"
    assert f"--hf-checkpoint /root/Qwen/{sft_base}" in readme and f"hf download Qwen/{sft_base}" in readme

    sigs = [
        ("async def generate(args, sample: Sample, sampling_params) -> Sample:", gen_src),
        ("async def reward_func(args, sample, **kwargs):", gen_src),
        ("class PythonSandbox:", (KIT / "functions" / "tool_sandbox.py").read_text()),
    ]
    for sig, src in sigs:
        assert sig in src.splitlines(), sig
    sandbox = sigs[2][1]
    max_turns = int(re.search(r'"max_turns": (\d+)', sandbox).group(1))
    timeout = int(re.search(r'"python_timeout": (\d+)', sandbox).group(1))
    mem = re.search(r'"python_memory_limit": "(\d+)GB"', sandbox).group(1)
    assert (max_turns, timeout, mem) == (16, 20, "4"), (max_turns, timeout, mem)

    dgd = yaml.safe_load((KIT / "manifests" / "dgd.yaml").read_text())
    workers = [c for c in dgd["spec"]["components"] if c["type"] == "worker"]
    gpus = {int(c["podTemplate"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"]) for c in workers}
    assert len(workers) == 4 and gpus == {1}, (len(workers), gpus)
    rayjob = yaml.safe_load((KIT / "manifests" / "rayjob.yaml").read_text())
    cluster = rayjob["spec"]["rayClusterSpec"]
    assert "workerGroupSpecs" not in cluster, "the Ray cluster has workers now"
    train_gpus = int(cluster["headGroupSpec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
    entry = rayjob["spec"]["entrypoint"]
    steps = int(re.search(r"--num-rollout (\d+)", entry).group(1))
    assert "'</tool_call>'" in entry and train_gpus == 4 and steps == 10, (train_gpus, steps)

    labels_txt = (TEAM / "snapshots" / "node-gpu-labels.txt").read_text().partition("# ---\n")[2]
    node = dict(line.split("=", 1) for line in labels_txt.splitlines() if "=" in line)
    vm = node["node.kubernetes.io/instance-type"]
    node_gpus = int(node["nvidia.com/gpu.count"])
    gm = re.fullmatch(r"NVIDIA-(H100)-(\d+)GB-HBM3", node["nvidia.com/gpu.product"])
    assert vm == "Standard_ND96isr_H100_v5" and node_gpus == 8 == len(workers) + train_gpus and gm
    gpu_name = f"{gm.group(1)} {gm.group(2)} GB"

    d = Data(__file__)
    d.source("runs/run1/turns.joined.jsonl", "runs/run1/turn-logs/", "runs/setup/dataset-rows.txt",
             "manifests/setup-job.yaml", "upstream/image/examples/retool/README.md",
             "functions/generate_with_retool.py", "functions/tool_sandbox.py", "manifests/dgd.yaml",
             "manifests/rayjob.yaml", "demo/team/snapshots/node-gpu-labels.txt")
    d.set("PROBLEM_GUTTER", GUTTER)
    d.set("PROBLEM_N_PROBLEMS", fmt_int(rows))
    d.set("PROBLEM_DATASET", dataset)
    d.set("PROBLEM_CKPT", ckpt)
    d.set("PROBLEM_SFT_BASE", sft_base)
    d.set("PROBLEM_SFT_DATA", sft_data)
    d.set("PROBLEM_EX_SAMPLE", EXAMPLE)
    d.set("PROBLEM_EX_STEP", step)
    d.set("PROBLEM_EX_TURNS", len(ex))
    d.set("PROBLEM_LABEL", label)
    d.set("PROBLEM_PRED", fex["pred"])
    d.set("PROBLEM_REWARD", reward_text(fex["score"]))
    d.array("PROBLEM_PROMPT", prompt_lines)
    d.set("PROBLEM_PROBLEM_ROW", problem_row)
    d.array("PROBLEM_T1", t1_lines)
    d.array("PROBLEM_CALL", call_lines)
    d.array("PROBLEM_CODE", code_lines)
    d.array("PROBLEM_OBS", obs_lines)
    d.array("PROBLEM_T2", t2_lines)
    d.array("PROBLEM_ALT", alt_lines)
    d.set("PROBLEM_ALT_REWARD", reward_text(fwr["score"]))
    d.array("PROBLEM_SIGS", [s for s, _ in sigs])
    d.set("PROBLEM_MAX_TURNS", max_turns)
    d.set("PROBLEM_SANDBOX_MEM", mem + " GB")
    d.set("PROBLEM_SANDBOX_SECS", timeout)
    d.set("PROBLEM_ENGINES", len(workers))
    d.set("PROBLEM_TRAIN_GPUS", train_gpus)
    d.set("PROBLEM_STEPS", steps)
    d.set("PROBLEM_VM", vm)
    d.set("PROBLEM_NODE_GPUS", node_gpus)
    d.set("PROBLEM_GPU_NAME", gpu_name)
    d.write()


if __name__ == "__main__":
    main()
