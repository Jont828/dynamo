"""Direct smoke test: run the patched ReTool generate() against the Dynamo frontend.

Runs inside a Pod with the Slime image (tokenizer, slime, httpx), with the kit's
functions/ on PYTHONPATH, before any RayJob. No training: the engines serve the SFT
checkpoint. Usage:
  RETOOL_TURN_LOG_DIR=/tmp/smoke-turns DYNAMO_ROLLOUT_URL=http://slime-sglang-frontend:8000 \
    python3 smoke_direct.py <hf_checkpoint> <dapo jsonl> [prompts] [samples_per_prompt]

Prints the go/no-go checks from the kit README:
  1. the first rendered prompt: one system turn with the tool spec, one user turn, no doubled template;
  2. turns happen: more than one request per trajectory on average, code output from the sandbox;
  3. later turns hit the cache: cached tokens close to the previous request's full length;
  4. a logprob for every output token.
"""

import asyncio
import glob
import json
import os
import sys
from argparse import Namespace
from collections import Counter

import httpx

import generate_with_retool as g
from slime.utils import http_utils
from slime.utils.types import Sample


def main():
    ckpt, data = sys.argv[1], sys.argv[2]
    n_prompts = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    n_samples = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    args = Namespace(
        hf_checkpoint=ckpt, partial_rollout=False, rollout_max_context_len=None,
        context_parallel_size=2, max_tokens_per_gpu=9216,
        rollout_temperature=1.0, rollout_top_p=1.0, rollout_top_k=-1, rollout_max_response_len=131072,
        rollout_stop=["</code>", "</tool_call>"], rollout_stop_token_ids=None, rollout_skip_special_tokens=False,
        sglang_server_concurrency=512, sglang_dp_size=None, rollout_num_engines=4, rollout_num_gpus=0,
        sglang_incremental_streaming_output=True, use_score_centering=False, ci_test=False,
        sglang_enable_deterministic_inference=False, use_rollout_routing_replay=False, sglang_speculative_algorithm=None,
    )
    state = g.GenerateState(args)
    rows = [json.loads(line) for _, line in zip(range(n_prompts), open(data))]

    # Check 1: the rendered prompt.
    question = g._question_text(rows[0]["prompt"])
    prompt = g.format_conversation_with_tools(prompt=question, tools=g.tool_registry.get_tool_specs())
    ids = state.tokenizer(prompt, add_special_tokens=False)["input_ids"]
    decoded = state.tokenizer.decode(ids)
    roles = [part.split("\n", 1)[0] for part in decoded.split("<|im_start|>")[1:]]
    print(f"[1] prompt: {len(ids)} tokens, roles {roles}, '# Tools' in system: {'# Tools' in decoded.split('<|im_end|>')[0]}, "
          f"doubled: {'<|im_start|>user<|im_start|>' in decoded or decoded.count('<|im_start|>user') > 1}")
    print("    head:", repr(decoded[:90]), "... tail:", repr(decoded[-70:]))

    sampling = dict(state.sampling_params)

    async def run():
        http_utils._http_client = httpx.AsyncClient(timeout=httpx.Timeout(None), trust_env=False)
        samples = []
        for gi, row in enumerate(rows):
            for si in range(n_samples):
                s = Sample(prompt=row["prompt"], label=row["label"], index=gi * n_samples + si, group_index=gi)
                s.session_id = f"smoke-{gi}-{si}"
                samples.append(s)
        out = await asyncio.gather(*(g.generate(args, s, dict(sampling)) for s in samples))
        rewards = [await g.reward_func(args, s) for s in out]
        await http_utils._http_client.aclose()
        return out, rewards

    out, rewards = asyncio.run(run())
    turns = [json.loads(line) for f in glob.glob(os.path.join(os.environ["RETOOL_TURN_LOG_DIR"], "*.jsonl"))
             for line in open(f)]
    turn_recs = sorted((t for t in turns if t["kind"] == "turn"), key=lambda t: (t["sample"], t["turn"]))

    print(f"[2] {len(out)} trajectories, {len(turn_recs)} requests, {len(turn_recs) / len(out):.2f} per trajectory")
    outcomes = {}
    for t in turn_recs:
        key = t.get("tool_outcome") or t.get("loop")
        outcomes[key] = outcomes.get(key, 0) + 1
    print("    per-request result:", outcomes)
    print("    finish reasons:", dict(Counter(f"{t['finish']}:{t.get('matched')}" for t in turn_recs)))
    for t in turn_recs[:6]:
        print(f"    s{t['sample']} t{t['turn']}: in {t['input_len']}, cached {t['cached_tokens']}, out {t['output_tokens']}, "
              f"{t['finish']}:{t.get('matched')} -> {t.get('tool_outcome')}; code {str(t.get('code'))[:60]!r} -> {str(t.get('output'))[:60]!r}")

    print("[3] cached tokens on turns 2+ vs the previous request's input + output:")
    prev = {}
    ratios = []
    for t in turn_recs:
        p = prev.get(t["sample"])
        if p is not None and t["cached_tokens"] is not None:
            full = p["input_len"] + p["output_tokens"]
            ratios.append(t["cached_tokens"] / full)
            if len(ratios) <= 8:
                print(f"    s{t['sample']} t{t['turn']}: cached {t['cached_tokens']} of previous {full} "
                      f"({100 * t['cached_tokens'] / full:.0f}%), prompt {t['prompt_tokens']}")
        prev[t["sample"]] = t
    if ratios:
        print(f"    turns 2+: {len(ratios)}, median {100 * sorted(ratios)[len(ratios) // 2]:.0f}% of the previous request")

    bad = [s.index for s in out if s.rollout_log_probs is None or len(s.rollout_log_probs) != s.response_length
           or len(s.loss_mask) != s.response_length]
    gen_tokens = sum(t["output_tokens"] for t in turn_recs)
    gen_logprobs = sum(t.get("logprobs", 0) for t in turn_recs)
    print(f"[4] logprobs for {gen_logprobs} of {gen_tokens} output tokens; samples with misaligned logprobs/masks: {bad}")
    print("    rewards:", [round(r["score"], 2) for r in rewards], "| statuses:", sorted({s.status.value for s in out}))


if __name__ == "__main__":
    main()
