"""Data for beats/05_slime.sh (Step 4: the ReTool task code that Slime runs).

Usage: $PY -I fill/05_slime.py   (run.sh and build.sh run it for you)

Kit root = slime-retool/. Sources:
  functions/generate_with_retool.py  the code shown in the read-only vim view: generate() and reward_func()
                                     (lines 403-690), the parts we skip shown as closed vim folds; the file's
                                     line and byte counts for vim's "FILE" NL, NB message
  functions/tool_sandbox.py          TOOL_CONFIGS: max turns
  manifests/rayjob.yaml              the stop string; asserted: the ConfigMap's mount path on PYTHONPATH,
                                     DYNAMO_ROLLOUT_URL, the custom generate and reward paths
  tools/make_functions_configmap.sh  asserted: the ConfigMap holds the three functions/ files
  runs/deploy.console.log            output of applying manifests/functions.yaml
  upstream/image/...                 the built-in generate, the custom-generate flag, ReTool's example file,
                                     and DAPO's +1/-1 score (asserted, so the card and callouts stay true)

The view shows real lines with their real line numbers. Every shown line is asserted to be the file's
line at that number (and check_typed passes on them, in order), every fold's text is computed from the
folded lines the way vim's foldtext() does, and rows and folds cover lines 403-690 exactly once, in order.
"""

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # -I leaves it off sys.path
from _common import KIT, Data, check_typed, read_lines  # noqa: E402

GEN_REL = "functions/generate_with_retool.py"
VIM_TW = 114   # lib.sh: 120 columns minus the 2-column sign column and the 4-column line number

# The rows of the view, from generate() to the end of the file: (first line's exact text, lines kept).
# Everything between two kept runs becomes one closed fold.
KEEP = [
    ("async def generate(args, sample: Sample, sampling_params) -> Sample:", 2),
    ("    state = GenerateState(args)", 11),
    ('    for turn in range(TOOL_CONFIGS["max_turns"]):', 1),
    ("        # Use token IDs instead of text", 11),
    ("        output, timing = await _stream_generate(args, url, payload)  # PATCH(stream): was post(url, payload)", 1),
    ("        response += cur_response", 9),
    ("        next_obs, done, tool = await execute_predictions(cur_response)", 6),
    ('        obs_tokens_ids = state.tokenizer(next_obs, add_special_tokens=False)["input_ids"]', 1),
    ("        response_token_ids += obs_tokens_ids", 2),
    ("    return sample", 8),                       # generate's end, then the lines up to reward_func
    ("async def reward_func(args, sample, **kwargs):", 2),
    ("    # Get tool call count as num_turns", 10),
]
FIRST = "async def generate(args, sample: Sample, sampling_params) -> Sample:"
REWARD = "async def reward_func(args, sample, **kwargs):"

# The real line numbers each callout marks, by exact text (each text must be a unique line).
MARKS = {
    "C1": ["async def generate(args, sample: Sample, sampling_params) -> Sample:",
           '    base_url = os.environ.get("DYNAMO_ROLLOUT_URL") or f"http://{args.sglang_router_ip}:{args.sglang_router_port}"',
           "    url = f\"{base_url.rstrip('/')}/generate\""],
    "C2": ['    for turn in range(TOOL_CONFIGS["max_turns"]):',
           "        current_token_ids = prompt_tokens_ids + response_token_ids",
           "        payload = {",
           '            "input_ids": current_token_ids,',
           '            "sampling_params": per_turn_sampling_params,',
           '            "return_logprob": True,  # Request log probabilities for training'],
    "C3": ["        output, timing = await _stream_generate(args, url, payload)  # PATCH(stream): was post(url, payload)"],
    "C4": ["        response += cur_response",
           "        response_token_ids += cur_response_token_ids",
           "        sample.append_response_tokens(",
           "            tokens=cur_response_token_ids,",
           "            log_probs=cur_log_probs,",
           "            trainable=True,"],
    "C5": ["        next_obs, done, tool = await execute_predictions(cur_response)",
           "        if done:",
           '            _turn_log(record | {"loop": "answer"})'],
    "C6": ['        obs_tokens_ids = state.tokenizer(next_obs, add_special_tokens=False)["input_ids"]',
           "        response_token_ids += obs_tokens_ids",
           "        sample.append_response_tokens(args, tokens=obs_tokens_ids, trainable=False)"],
    "R": ['    num_turns = getattr(sample, "tool_call_count", 0)',
          "    result = math_dapo_compute_score(solution_str, ground_truth, strict_box_verify=True)",
          '    if result["score"] < 0:',
          "        tool_call_reward = (num_turns - 2) / 2 * 0.1",
          '        result["score"] = min(-0.6, result["score"] + tool_call_reward)'],
}


def unique_line(lines, text):
    """1-based number of the one line equal to text."""
    hits = [i + 1 for i, line in enumerate(lines) if line == text]
    if len(hits) != 1:
        raise SystemExit(f"05_slime.py: expected exactly one line {text!r} in {GEN_REL}, found {hits}")
    return hits[0]


def foldtext(lines, first, last):
    """What vim shows for a closed fold of lines first..last (1-based), as vim's foldtext() builds it.

    "+-" + one dash per level (1 here) + the count in 3 columns + " lines: " + the first non-blank line,
    with its leading white space and the first comment leader ('#', Python's commentstring) and the
    white space after it removed. vim fills the rest of the row with '-' (fillchars fold:-); the beat does.
    """
    text = next((lines[k - 1].strip() for k in range(first, last + 1) if lines[k - 1].strip()), "")
    text = re.sub(r"#\s*", "", text, count=1)
    return f"+--{last - first + 1:3d} lines: {text}"


def build_view(lines, keep, start, end):
    """Rows of a folded view of lines start..end: (kind 'L'/'F', first line, last line, text)."""
    runs = sorted((unique_line(lines, text), unique_line(lines, text) + count - 1) for text, count in keep)
    assert runs[0][0] == start, (runs[0], start)
    rows, at = [], start
    for first, last in runs:
        if first < at:
            raise SystemExit(f"05_slime.py: kept runs overlap or are out of order at line {first}")
        if first > at:
            rows.append(("F", at, first - 1, foldtext(lines, at, first - 1)))
        for n in range(first, last + 1):
            rows.append(("L", n, n, lines[n - 1]))
        at = last + 1
    if at <= end:
        rows.append(("F", at, end, foldtext(lines, at, end)))
    covered = [n for _, a, b, _ in rows for n in range(a, b + 1)]
    assert covered == list(range(start, end + 1)), "the view doesn't cover its lines exactly once, in order"
    for kind, n, _, text in rows:
        if kind == "L":
            assert lines[n - 1] == text, (n, text)
            assert len(text) <= VIM_TW, f"line {n} is wider than the {VIM_TW}-column text area"
    return rows


def main():
    d = Data(__file__)
    d.source(GEN_REL, "functions/tool_sandbox.py", "manifests/rayjob.yaml", "tools/make_functions_configmap.sh",
             "runs/deploy.console.log", "upstream/image/slime/rollout/sglang_rollout.py",
             "upstream/image/slime/utils/arguments.py", "upstream/image/slime/rollout/rm_hub/math_dapo_utils.py",
             "upstream/image/examples/retool/generate_with_retool.py")
    gen_path = KIT / GEN_REL
    src = gen_path.read_text()
    lines = read_lines(GEN_REL)

    # --- the view: generate() through the end of the file ----------------------------------------
    start = unique_line(lines, FIRST)
    rows = build_view(lines, KEEP, start, len(lines))
    shown = [r[3] for r in rows if r[0] == "L"]
    check_typed(shown, GEN_REL, "05_slime.py")
    d.array("SLIME_VV_KIND", [r[0] for r in rows])
    d.array("SLIME_VV_NUM", [r[1] for r in rows])
    d.array("SLIME_VV_LAST", [r[2] for r in rows])
    # vim's ruler column for the cursor on a line's first non-blank character ("0-1" on an empty line)
    d.array("SLIME_VV_COL", [(len(r[3]) - len(r[3].lstrip()) + 1 if r[3].strip() else 0) if r[0] == "L" else 1
                             for r in rows])
    d.array("SLIME_VV_TXT", [r[3] for r in rows])
    d.set("SLIME_GEN_FILE", GEN_REL)
    d.set("SLIME_GEN_NLINES", len(lines))
    d.set("SLIME_GEN_NBYTES", len(src.encode()))
    d.set("SLIME_GEN_LN", start)
    d.set("SLIME_REWARD_LN", unique_line(lines, REWARD))

    shown_nums = {r[1] for r in rows if r[0] == "L"}
    for name, texts in MARKS.items():
        nums = [unique_line(lines, t) for t in texts]
        missing = [n for n in nums if n not in shown_nums]
        assert not missing, f"callout {name} marks lines the view folds away: {missing}"
        d.set(f"SLIME_MARK_{name}", " ".join(map(str, nums)))

    # --- numbers the callouts quote ---------------------------------------------------------------
    sb = (KIT / "functions" / "tool_sandbox.py").read_text()

    def cfg(key):
        m = re.search(rf'^\s+"{key}":\s*("?)([^",]+)\1,', sb, re.M)
        assert m, key
        return m.group(2)

    d.set("SLIME_MAX_TURNS", int(cfg("max_turns")))

    rj = (KIT / "manifests" / "rayjob.yaml").read_text()
    cp = int(re.search(r"^\s+--context-parallel-size (\d+)$", rj, re.M).group(1))
    mt = int(re.search(r"^\s+--max-tokens-per-gpu (\d+)$", rj, re.M).group(1))
    assert "max_context_length = args.context_parallel_size * args.max_tokens_per_gpu" in src
    assert cp * mt == 18432, (cp, mt)
    stops = re.search(r"^\s+--rollout-stop (.+)$", rj, re.M).group(1)
    assert "'</tool_call>'" in stops, stops
    d.set("SLIME_STOP", "</tool_call>")
    funcs_dir = re.search(r'PYTHONPATH: "[^"]*:(/opt/slime-functions)"', rj).group(1)
    assert re.search(rf"mountPath: {re.escape(funcs_dir)}$", rj, re.M)
    assert funcs_dir == "/opt/slime-functions"   # "Step 5 mounts it on PYTHONPATH"
    assert 'DYNAMO_ROLLOUT_URL: "http://slime-sglang-frontend:8000"' in rj
    assert 'os.environ.get("DYNAMO_ROLLOUT_URL")' in src
    assert "--custom-generate-function-path generate_with_retool.generate" in rj
    assert "--custom-rm-path generate_with_retool.reward_func" in rj

    # Reward: DAPO's compute_score gives +1/-1; reward_func's tool-call term and its cap.
    up = KIT / "upstream" / "image"
    dapo = (up / "slime" / "rollout" / "rm_hub" / "math_dapo_utils.py").read_text()
    assert "reward = 1.0 if correct else -1.0" in dapo
    assert "tool_call_reward = (num_turns - 2) / 2 * 0.1" in src   # = 0.05 x (tool calls - 2)
    assert 'result["score"] = min(-0.6, result["score"] + tool_call_reward)' in src
    d.set("SLIME_REWARD_STEP", "0.05")
    d.set("SLIME_REWARD_FREE", 2)
    d.set("SLIME_REWARD_CAP", "−0.6")

    # The card: Slime's built-in generate, the custom-generate flag, and ReTool's example file.
    rollout = (up / "slime" / "rollout" / "sglang_rollout.py").read_text()
    assert '"""Generate using traditional SGLang router with token-based workflow"""' in rollout
    assert "generate_func = load_function(custom_func_path) if custom_func_path is not None else generate" in rollout
    arguments = (up / "slime" / "utils" / "arguments.py").read_text()
    assert '"--custom-generate-function-path"' in arguments and "e.g. multi-turn, function calling" in arguments
    example = up / "examples" / "retool" / "generate_with_retool.py"
    assert FIRST in example.read_text()
    d.set("SLIME_EXAMPLE_PATH", "examples/retool/generate_with_retool.py")

    # --- the ConfigMap ------------------------------------------------------------------------------
    script = (KIT / "tools" / "make_functions_configmap.sh").read_text()
    cm_name = re.search(r"kubectl create configmap (\S+) --dry-run=client -o yaml", script).group(1)
    files = re.findall(r'--from-file="\$KIT/(functions/[\w.]+)"', script)
    assert files == ["functions/generate_with_retool.py", "functions/tool_sandbox.py",
                     "functions/weight_permissions.py"], files
    assert f"name: {cm_name}" in rj and cm_name == "slime-retool-functions"
    for f in files:
        assert (KIT / f).exists(), f
    applied = [l for l in (KIT / "runs" / "deploy.console.log").read_text().splitlines() if l.startswith("configmap/")]
    assert applied == [f"configmap/{cm_name} created"], applied
    d.set("SLIME_CM_APPLY_OUT", applied[0])

    d.write()
    print(f"view: {len(rows)} rows ({sum(r[0] == 'F' for r in rows)} folds) for lines {start}-{len(lines)}")


if __name__ == "__main__":
    main()
