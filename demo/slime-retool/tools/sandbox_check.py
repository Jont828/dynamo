"""Compare upstream ReTool's sandbox with the kit's patched one on the SFT data's code blocks.

Usage (inside a Pod with the Slime image, both sandboxes copied in):
  python3 sandbox_check.py <upstream_dir> <patched_dir> <ReTool-SFT parquet> [n_exec]

For every <code>...</code> block in the SFT assistant messages it reports:
  - how many blocks the upstream and patched safety checks refuse, and why;
  - how many blocks wrap their code in a ```python fence inside <code>;
and for the first n_exec blocks (default 200) it runs the code three ways:
  - upstream: the block exactly as upstream extracts it (fences included), upstream sandbox;
  - patched sandbox, fences included;
  - patched sandbox, fences stripped (what the patched generate_with_retool.py sends).
Prints counts only; it never prints the code.
"""

import asyncio
import collections
import importlib.util
import re
import sys
import time

import pandas as pd

FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def strip_code_fences(code: str) -> str:
    blocks = FENCE_RE.findall(code)
    if blocks:
        return "\n".join(block.strip("\n") for block in blocks)
    return code


def load(name, directory):
    spec = importlib.util.spec_from_file_location(name, f"{directory}/tool_sandbox.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(result: str) -> str:
    if result.startswith("Error: Code contains dangerous pattern") or result.startswith("Error: Import of"):
        return "refused"
    if "SyntaxError" in result or "IndentationError" in result:
        return "syntax error"
    if "timed out" in result:
        return "timeout"
    if result.startswith("Error"):
        return "runtime error"
    if not result.strip():
        return "no output"
    return "ok"


async def run_all(sandbox, codes, concurrency=32):
    sem = asyncio.Semaphore(concurrency)
    results = [None] * len(codes)

    async def one(i, code):
        async with sem:
            t0 = time.monotonic()
            results[i] = (await sandbox.execute_code(code), time.monotonic() - t0)

    t0 = time.monotonic()
    await asyncio.gather(*(one(i, c) for i, c in enumerate(codes)))
    return results, time.monotonic() - t0


def main():
    old_dir, new_dir, parquet = sys.argv[1:4]
    n_exec = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    old, new = load("sandbox_old", old_dir), load("sandbox_new", new_dir)
    old_box = old.PythonSandbox(timeout=20)
    new_box = new.PythonSandbox(timeout=new.TOOL_CONFIGS["python_timeout"])

    df = pd.read_parquet(parquet)
    blocks = []
    for messages in df["messages"]:
        for m in messages:
            if m["role"] == "assistant":
                blocks += [b.strip() for b in re.findall(r"<code>(.*?)</code>", m["content"], re.DOTALL)]
    fenced = sum(1 for b in blocks if "```" in b)
    print(f"SFT rows {len(df)}, code blocks {len(blocks)}, fenced inside <code>: {fenced} ({100 * fenced / len(blocks):.1f}%)")

    for label, box, transform in [("upstream check", old_box, lambda c: c), ("patched check", new_box, strip_code_fences)]:
        reasons = collections.Counter()
        for b in blocks:
            ok, msg = box._check_code_safety(transform(b))
            if not ok:
                reasons[re.sub(r"'[^']*'", "'X'", msg) if "Import of" not in msg else msg] += 1
        refused = sum(reasons.values())
        print(f"{label}: refuses {refused} of {len(blocks)} ({100 * refused / len(blocks):.1f}%)")
        for reason, n in reasons.most_common(12):
            print(f"    {n:5d}  {reason}")

    sample = blocks[:n_exec]
    for label, box, transform in [
        ("upstream, fences kept", old_box, lambda c: c),
        ("patched, fences kept", new_box, lambda c: c),
        ("patched, fences stripped", new_box, strip_code_fences),
    ]:
        results, wall = asyncio.run(run_all(box, [transform(c) for c in sample]))
        counts = collections.Counter(outcome(r) for r, _ in results)
        times = sorted(t for _, t in results)
        print(
            f"{label}: {dict(counts)}; {len(sample)} runs in {wall:.1f} s wall "
            f"(per run median {times[len(times) // 2]:.2f} s, max {times[-1]:.2f} s)"
        )


if __name__ == "__main__":
    main()
