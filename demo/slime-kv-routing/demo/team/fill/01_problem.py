"""Beat 01 (the problem) data: writes build/01_problem.data.sh from kit files and snapshots only.

Usage: $PY -I fill/01_problem.py

Sources (kit root = slime-kv-routing/):
  runs/rr/run1/driver.log             the two answers Slime logged in step 0 (sglang_rollout.py:545 "First
                                      rollout sample", line 1905: Darla, label 1350, reward 0; :583 "Finish
                                      rollout", line 1914: John's zoo, label 114, reward 1), each the prompt as
                                      the chat template built it plus the model's response
  runs/rr/run1/summary.json           each step's raw_reward (the share of 64 answers that scored 1)
  manifests/tutorial/rayjob.yaml      --rm-type math, --num-rollout, the trainer's GPU, the dataset path
  manifests/tutorial/dgd.yaml         the engines and their GPUs
  manifests/tutorial/setup-job.yaml   the dataset it downloads (zhuzilin/gsm8k), unchanged
  demo/team/snapshots/node-gpu-labels.txt    the node's VM size and GPUs (GPU Operator labels)
  demo/team/snapshots/01_gsm8k-size.txt      zhuzilin/gsm8k's train split: 7,473 rows (Hugging Face API)
  demo/team/snapshots/01_gsm8k-first-row.txt its columns (question, answer, label, messages) and the system
                                      prompt in messages (Hugging Face API)
  demo/team/snapshots/01_qwen3-chat-template.txt   Qwen3's chat template adds no system prompt of its own,
                                      and with enable_thinking false it ends the prompt with an empty
                                      <think></think>: that block is prompt, not answer

The screen shows each logged text split into its chat parts (system, problem, answer) without the chat
template's tokens and without blank lines. The wrong answer is shown whole. The right one is cut to fit:
EXCERPT lists verbatim fragments, each row a list; ' … ' marks every place where text was cut (asserted
against the response). Every row fits in 120 columns with the beat's gutter.
"""

import ast
import json
import pathlib
import re
import sys
import textwrap

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, RUNS, TEAM, Data, fmt_int  # noqa: E402

RUN = RUNS / "rr" / "run1"
SNAP = TEAM / "snapshots"

GUTTER = 11              # "  problem  " in the beat
WIDTH = 120 - GUTTER - 1  # transcript text, so a row fits in 119
NOTE_COL = 56            # problem_row's note column (beat side), counted from the row's start
WRONG_LINE, RIGHT_LINE = 1905, 1914

SYSTEM = "You are a helpful assistant. Please put the answer within \\boxed{}."
THINK_OFF = "<think>\n\n</think>\n\n"   # the chat template's generation prompt when thinking is off

# The right answer (John's zoo, 46 lines of text with blanks), cut to 7 rows. Each row is a list of
# verbatim fragments of the response, in order.
EXCERPT = [
    ["Let's break down the information and solve the problem step-by-step:"],
    ["- John has 15 snakes: $ s = 15 $"],
    ["- John has twice as many monkeys as snakes: $ m = 2s = 30 $"],
    ["- John has 5 fewer lions than monkeys: $ l = m - 5 = 30 - 5 = 25 $"],
    ["- John has 8 more pandas than lions: $ p = l + 8 = 25 + 8 = 33 $"],
    ["- John has 1/3 as many dogs as pandas: $ d = \\frac{1}{3}p = \\frac{1}{3}(33) = 11 $"],
    ["\\text{Total animals} = 15 + 30 + 25 + 33 + 11", "103 + 11 = 114", "### ✅ Final Answer:", "\\boxed{114}"],
]

ESC_RE = re.compile(r"\x1b\[[0-9;]*m")
SAMPLE_RE = re.compile(
    r"^\(RolloutManager pid=\d+\) \[(2026-11-14 \d\d:\d\d:\d\d)\] sglang_rollout\.py:(\d+) - "
    r"(First rollout sample|Finish rollout): (\[.*\]), label: (.*), reward: (\S+)$")


def dw(s):
    """Display width the way lib.sh's _dwidth counts it: wide symbols and 4-byte characters take 2."""
    wide = "⌚⌛〈〉⏩⏪⏫⏬⏰⏳◽◾☔☕♈♉♊♋♌♍♎♏♐♑♒♓♿⚓⚡⚪⚫⚽⚾⛄⛅⛎⛔⛪⛲⛳⛵⛺⛽✅✊✋✨❌❎❓❔❕❗➕➖➗➰➿⬛⬜⭐⭕"
    w = 0
    for c in s:
        if c in "\ufe0f\u200d":
            continue
        w += 2 if (c in wide or ord(c) > 0xFFFF) else 1
    return w


def wrap(text, width=WIDTH):
    lines = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
    assert all(dw(line) <= width for line in lines), lines
    return lines


def last_boxed(text):
    """The content of the last \\boxed{...}, as Slime's math grader extracts it (math_utils.last_boxed_only_string,
    then remove_boxed)."""
    i = text.rfind("\\boxed")
    assert i >= 0, "no \\boxed"
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                inner = text[i:j + 1]
                assert inner.startswith("\\boxed{")
                return inner[len("\\boxed{"):-1]
    raise AssertionError("unbalanced \\boxed")


def logged_sample(lines, n):
    """Line n of driver.log: (time, source line, kind, system, problem, response, label, reward)."""
    m = SAMPLE_RE.match(ESC_RE.sub("", lines[n - 1]))
    assert m, f"driver.log:{n} isn't a logged sample"
    when, src, kind, lst, label, reward = m.groups()
    (text,) = ast.literal_eval(lst)
    head = "<|im_start|>system\n"
    assert text.startswith(head), text[:40]
    system, _, rest = text[len(head):].partition("<|im_end|>\n<|im_start|>user\n")
    problem, _, rest = rest.partition("<|im_end|>\n<|im_start|>assistant\n")
    assert rest.startswith(THINK_OFF) and rest.endswith("<|im_end|>"), (rest[:30], rest[-20:])
    response = rest[len(THINK_OFF):-len("<|im_end|>")]
    assert "<|im_" not in problem + response
    return when, int(src), kind, system, problem, response, label, reward


def nonblank_lines(text):
    return [line.rstrip() for line in text.split("\n") if line.strip()]


def excerpt_rows(text, rows):
    """Rows of verbatim fragments → display rows. Fragments must appear in order. Where the text between two
    fragments (or before the first, or after the last) has anything but whitespace, a cut is marked: ' … '
    inside a row, ' …' at the end of the earlier row between rows, '… ' before the first fragment."""
    pos, out, first = 0, [], True
    for row in rows:
        line = ""
        for k, frag in enumerate(row):
            assert "\n" not in frag, frag
            at = text.find(frag, pos)
            assert at >= 0, f"fragment not found (in order): {frag[:60]!r}"
            cut = text[pos:at].strip() != ""
            if first:
                line = ("… " if cut else "") + frag
                first = False
            elif k == 0:
                if cut:
                    out[-1] += " …"
                line = frag
            else:
                line += (" … " if cut else " ") + frag
            pos = at + len(frag)
        out.append(line)
    if text[pos:].strip():
        out[-1] += " …"
    return out


def main():
    lines = (RUN / "driver.log").read_text(encoding="utf-8").split("\n")
    wrong = logged_sample(lines, WRONG_LINE)
    right = logged_sample(lines, RIGHT_LINE)

    # Both come from step 0: after the initial weight sync, before step 0's metrics.
    sync_end = next(i for i, l in enumerate(lines, 1) if "Timer update_weights end" in l)
    perf0 = next(i for i, l in enumerate(lines, 1) if "rollout_metrics.py" in l and " - perf 0: " in l)
    assert sync_end < WRONG_LINE < RIGHT_LINE < perf0, (sync_end, perf0)
    assert "First rollout sample" not in "".join(lines[WRONG_LINE:RIGHT_LINE - 1])
    assert (wrong[0], wrong[1], wrong[2]) == ("2026-11-14 03:53:24", 545, "First rollout sample"), wrong[:3]
    assert (right[0], right[1], right[2]) == ("2026-11-14 03:53:24", 583, "Finish rollout"), right[:3]

    # --- the system prompt: the dataset's, the same on both ----------------------------------------
    assert wrong[3] == right[3] == SYSTEM, (wrong[3], right[3])
    first_row = json.loads((SNAP / "01_gsm8k-first-row.txt").read_text().partition("# ---\n")[2])
    assert [f["name"] for f in first_row["features"]] == ["question", "answer", "label", "messages"]
    msgs = first_row["rows[0]"]["row"]["messages"]
    assert msgs[0] == {"content": SYSTEM, "role": "system"} and msgs[1]["role"] == "user", msgs
    template = (SNAP / "01_qwen3-chat-template.txt").read_text().partition("# ---\n")[2]
    assert "{{- '<think>\\n\\n</think>\\n\\n' }}" in template and "enable_thinking is false" in template
    assert "template contains helpful/boxed: False False" in template
    size = json.loads((SNAP / "01_gsm8k-size.txt").read_text().partition("# ---\n")[2])
    train = next(s for s in size["size"]["splits"] if s["split"] == "train")
    assert train["dataset"] == "zhuzilin/gsm8k" and train["num_rows"] == 7473, train
    setup = (KIT / "manifests" / "tutorial" / "setup-job.yaml").read_text()
    assert "hf download --repo-type dataset zhuzilin/gsm8k --local-dir /data/datasets/gsm8k" in setup

    # --- the wrong answer: Darla's bill, shown whole ---------------------------------------------------
    _, _, _, _, w_problem, w_response, w_label, w_reward = wrong
    assert w_problem.startswith("Darla needs to pay $4/watt") and (w_label, w_reward) == ("1350", "0")
    w_pred = last_boxed(w_response)
    assert w_pred == "550" and 4 * 300 + 150 == int(w_label) and 400 + 150 == int(w_pred)
    w_rows = nonblank_lines(w_response)
    assert w_rows[0] == "Steel owes:" and len(w_rows) == 7, w_rows   # sic: the model's text
    boxed_row = next(i for i, r in enumerate(w_rows) if "\\boxed{" in r)
    assert all(dw(r) <= NOTE_COL - GUTTER - 2 for r in w_rows), "a row runs into the note column"

    # --- the right answer: John's zoo, cut to fit ------------------------------------------------------
    _, _, _, _, r_problem, r_response, r_label, r_reward = right
    assert r_problem.startswith("John wants to start a zoo.") and (r_label, r_reward) == ("114", "1")
    r_pred = last_boxed(r_response)
    assert r_pred == "114" == r_label and 15 + 30 + 25 + 33 + 11 == 114
    r_rows = excerpt_rows(r_response, EXCERPT)
    assert r_rows[0].endswith(" …") and r_rows[-1].endswith(" …") and len(r_rows) == 7, r_rows
    assert all(dw(r) <= WIDTH for r in r_rows), [dw(r) for r in r_rows]
    r_text_lines = len(r_response.split("\n"))

    # --- the reward: Slime's built-in math grader ---------------------------------------------------
    rayjob = yaml.safe_load((KIT / "manifests" / "tutorial" / "rayjob.yaml").read_text())
    entry = rayjob["spec"]["entrypoint"]
    assert "--rm-type math" in entry and "--custom-rm-path" not in entry
    assert "--prompt-data /data/datasets/gsm8k/train.parquet --input-key messages --label-key label" in entry
    assert "--apply-chat-template-kwargs '{\"enable_thinking\": false}'" in entry
    steps = int(re.search(r"--num-rollout (\d+)", entry).group(1))
    per_step = int(re.search(r"--rollout-batch-size (\d+)", entry).group(1)) * int(
        re.search(r"--n-samples-per-prompt (\d+)", entry).group(1))

    summary = json.loads((RUN / "summary.json").read_text())
    rewards = [s["raw_reward"] for s in summary["steps"]]
    right_per_step = [round(r * per_step) for r in rewards]
    assert all(abs(n / per_step - r) < 1e-9 for n, r in zip(right_per_step, rewards)), rewards
    assert len(rewards) == steps == 10 and per_step == 64
    n_right, n_all = sum(right_per_step), per_step * steps
    assert (n_right, n_all) == (380, 640) and abs(summary["agg"]["reward_mean"] - n_right / n_all) < 1e-9
    first, last = f"{rewards[0]:.3f}", f"{rewards[-1]:.3f}"
    assert (first, last, right_per_step[0]) == ("0.703", "0.703", 45), (first, last)

    # --- what runs where -----------------------------------------------------------------------------
    dgd = yaml.safe_load((KIT / "manifests" / "tutorial" / "dgd.yaml").read_text())
    workers = [c for c in dgd["spec"]["components"] if c["type"] == "worker"]
    gpus = {int(c["podTemplate"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"]) for c in workers}
    assert len(workers) == 2 and gpus == {1}, (len(workers), gpus)
    cluster = rayjob["spec"]["rayClusterSpec"]
    assert "workerGroupSpecs" not in cluster, "the Ray cluster has workers now"
    train_gpus = int(cluster["headGroupSpec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
    assert train_gpus == 1 and "--actor-num-gpus-per-node 1" in entry
    labels_txt = (SNAP / "node-gpu-labels.txt").read_text().partition("# ---\n")[2]
    node = dict(line.split("=", 1) for line in labels_txt.splitlines() if "=" in line)
    vm, node_gpus = node["node.kubernetes.io/instance-type"], int(node["nvidia.com/gpu.count"])
    gm = re.fullmatch(r"NVIDIA-(H100)-(\d+)GB-HBM3", node["nvidia.com/gpu.product"])
    assert vm == "Standard_ND96isr_H100_v5" and node_gpus == 8 and gm
    # Slime's two Ray actors in the log: the rollout manager (CPU) sends requests and grades; the trainer.
    assert "(RolloutManager pid=" in lines[WRONG_LINE - 1]
    assert any("(MegatronTrainRayActor pid=" in l for l in lines[sync_end - 3:sync_end])

    d = Data(__file__)
    d.source("runs/rr/run1/driver.log", "runs/rr/run1/summary.json", "manifests/tutorial/rayjob.yaml",
             "manifests/tutorial/dgd.yaml", "manifests/tutorial/setup-job.yaml",
             "demo/team/snapshots/node-gpu-labels.txt", "demo/team/snapshots/01_gsm8k-size.txt",
             "demo/team/snapshots/01_gsm8k-first-row.txt", "demo/team/snapshots/01_qwen3-chat-template.txt")
    d.set("PROBLEM_GUTTER", GUTTER)
    d.set("PROBLEM_NOTE_COL", NOTE_COL)
    d.set("PROBLEM_N_TRAIN", fmt_int(train["num_rows"]))
    d.set("PROBLEM_DATASET", "zhuzilin/gsm8k")
    d.set("PROBLEM_LOG_LINES", f"{WRONG_LINE} and {RIGHT_LINE}")
    d.set("PROBLEM_SYSTEM", SYSTEM)
    d.array("PROBLEM_WRONG_Q", wrap(w_problem))
    d.array("PROBLEM_WRONG_A", w_rows)
    d.set("PROBLEM_WRONG_BOXED_ROW", boxed_row)
    d.set("PROBLEM_WRONG_PRED", w_pred)
    d.set("PROBLEM_WRONG_LABEL", w_label)
    d.set("PROBLEM_WRONG_REWARD", int(w_reward))
    d.set("PROBLEM_WRONG_FIX", "4 × 300 + 150")
    d.array("PROBLEM_RIGHT_Q", wrap(r_problem))
    d.array("PROBLEM_RIGHT_A", r_rows)
    d.set("PROBLEM_RIGHT_LINES", r_text_lines)
    d.set("PROBLEM_RIGHT_PRED", r_pred)
    d.set("PROBLEM_RIGHT_LABEL", r_label)
    d.set("PROBLEM_RIGHT_REWARD", int(r_reward))
    d.set("PROBLEM_N_RIGHT", n_right)
    d.set("PROBLEM_N_ANSWERS", n_all)
    d.set("PROBLEM_REWARD_FIRST", first)
    d.set("PROBLEM_REWARD_LAST", last)
    d.set("PROBLEM_STEPS", steps)
    d.set("PROBLEM_ENGINES", len(workers))
    d.set("PROBLEM_TRAIN_GPUS", train_gpus)
    d.set("PROBLEM_VM", vm)
    d.set("PROBLEM_NODE_GPUS", node_gpus)
    d.set("PROBLEM_GPU_NAME", f"{gm.group(1)} {gm.group(2)} GB")
    d.write()


if __name__ == "__main__":
    main()
