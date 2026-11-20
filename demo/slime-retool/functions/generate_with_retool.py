# Adapted from https://github.com/volcengine/verl/blob/cb809d66e46dfd3342d008628891a14a054fa424/recipe/retool/retool.py
#
# Patched copy for the slime-retool-kv-aks kit (Slime ReTool rollouts served by Dynamo with
# KV-aware routing). The original is /root/slime/examples/retool/generate_with_retool.py in
# slimerl/slime:nightly-dev-20261027a-cu130, identical to THUDM/slime main at 79aa41a8.
# Each change is marked PATCH(...) and listed with its reason in the kit README:
#   PATCH(stream)    each turn streams through the Dynamo frontend; Dynamo 1.6.x rejects
#                    non-streaming /generate. The stream is folded back into the
#                    non-streaming result that the rest of this file reads.
#   PATCH(prompt)    render one system turn (with the tool spec) and one user turn.
#   PATCH(fences)    run the code inside a ```python fence, not the fence itself.
#   PATCH(semaphore) hold the tool semaphore once per code run (see tool_sandbox.py).
#   PATCH(turnlog)   one JSON line per turn, and one per finished sample, for replay.
#   PATCH(reward)    reward_func accepts a message-list prompt (Slime issue #1829).
import asyncio
import json
import logging
import os
import re
import socket
import time
import uuid
from typing import Any

try:
    from jinja2 import Template
except ImportError as e:
    raise ImportError("Jinja2 is required. Please install it with: pip install jinja2") from e

from slime.rollout.sglang_rollout import GenerateState
from slime.rollout.streaming_utils import SGLangStreamAccumulator
from slime.utils import http_utils
from slime.utils.types import Sample

# Import reward models
try:
    from slime.rollout.rm_hub.math_dapo_utils import compute_score as math_dapo_compute_score
except ImportError as e:
    raise ImportError("MathDapo is not installed") from e

# Import tool sandbox functionality
from tool_sandbox import SEMAPHORE, TOOL_CONFIGS, tool_registry

logger = logging.getLogger(__name__)

# Jinja2 template for tool-enabled conversations
TOOL_TEMPLATE = """<|im_start|>system
{%- if messages[0]['role'] == 'system' %}
{{- messages[0]['content'] }}
{%- else %}
You are a helpful assistant.
{%- endif %}
{%- if tools %}
# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{%- for tool in tools %}
{{- tool | tojson }}
{%- endfor %}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>
{%- endif %}
<|im_end|>
{%- for message in messages %}
{%- if message['role'] == 'user' %}
<|im_start|>user
{{- message['content'] }}<|im_end|>
{%- elif message['role'] == 'assistant' %}
<|im_start|>assistant
{{- message['content'] }}<|im_end|>
{%- endif %}
{%- endfor %}
<|im_start|>assistant
"""


def format_conversation_with_tools(
    prompt: str, tools: list[dict[str, Any]] = None, system_prompt: str = None, messages: list[dict[str, Any]] = None
) -> str:
    """Format conversation using Jinja2 template with tool support"""
    template = Template(TOOL_TEMPLATE)

    # Prepare messages
    messages_to_render = []

    # Always add system message - use provided one or default
    if system_prompt:
        system_content = system_prompt
    else:
        system_content = (
            "You are a helpful assistant that can use Python "
            "tools to solve mathematical problems. When you need "
            "to perform calculations, use the code_interpreter "
            "tool to execute code and get results."
        )

    messages_to_render.append({"role": "system", "content": system_content})

    # Add user message if provided
    if prompt:
        messages_to_render.append({"role": "user", "content": prompt})

    # Add assistant responses from previous turns if provided
    if messages:
        messages_to_render.extend(messages)

    # Render template
    formatted_text = template.render(messages=messages_to_render, tools=tools or [])

    # PATCH(prompt): the "{%-" and "{{-" whitespace control above also strips the newline
    # after each role tag, so the model saw "<|im_start|>userSolve ..." and a prompt that
    # ends at "<|im_start|>assistant". Qwen's chat template, which the SFT checkpoint was
    # trained on, writes "<|im_start|>user\n". Put the newline back.
    formatted_text = re.sub(r"<\|im_start\|>(system|user|assistant)(?!\n)", r"<|im_start|>\1\n", formatted_text)

    return formatted_text


def _question_text(prompt: Any) -> str:
    """PATCH(prompt): the user question from Slime's prompt field.

    DAPO-Math-17k stores each prompt as a message list. Upstream ran with
    --apply-chat-template, so the prompt arrived here as Qwen's rendered chat text and was
    wrapped in a second template ("<|im_start|>user<|im_start|>user ..."). This kit runs
    without that flag, so the prompt arrives as the message list; take the user's text.
    """
    if isinstance(prompt, str):
        return prompt
    users = [m.get("content") for m in prompt if isinstance(m, dict) and m.get("role") == "user"]
    if not users or not isinstance(users[-1], str):
        raise ValueError(f"expected a user message with text content, got {str(prompt)[:200]}")
    return users[-1]


_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def _strip_code_fences(code: str) -> str:
    """PATCH(fences): the code inside ```python fences, joined; otherwise the input.

    The SFT data (JoeYing/ReTool-SFT) writes every code block as <code>```python ...```</code>,
    so the checkpoint does too. Upstream passed the fence to the sandbox, where it is a
    SyntaxError. verl's ReTool recipe, which this file adapts, strips it the same way.
    """
    blocks = _FENCE_RE.findall(code)
    if blocks:
        return "\n".join(block.strip("\n") for block in blocks)
    return code


def postprocess_predictions(prediction: str):
    """Extract action and content from prediction string"""
    # Check for Answer: \boxed{...} format (only format we need for math_dapo)
    # Use a more robust regex that handles nested braces
    answer_pattern = r"Answer:\s*\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}"
    answer_match = re.search(answer_pattern, prediction, re.DOTALL)
    if answer_match:
        content = answer_match.group(1).strip()
        return "answer", content

    # Then check for <tool_call> tags (new format from Jinja2 template)
    tool_call_pattern = r"<tool_call>\s*(\{.*?\})\s*</tool_call>"
    tool_call_match = re.search(tool_call_pattern, prediction, re.DOTALL)
    if tool_call_match:
        try:
            import json

            # Clean up the JSON string by removing newlines and extra
            # whitespace
            json_str = tool_call_match.group(1)
            # Replace newlines in string values with \n
            json_str = json_str.replace("\n", "\\n")
            tool_call_data = json.loads(json_str)
            tool_name = tool_call_data.get("name")
            arguments = tool_call_data.get("arguments", {})

            if tool_name == "code_interpreter":
                code = arguments.get("code", "")
                if code.strip():
                    return "code", code
        except (json.JSONDecodeError, KeyError, AttributeError):
            pass

    # Then check for <code> tags
    code_pattern = r"<code>(.*?)</code>"
    code_match = re.search(code_pattern, prediction, re.DOTALL)
    if code_match:
        content = _strip_code_fences(code_match.group(1)).strip()  # PATCH(fences)
        return "code", content

    # Finally check for ```python code blocks (lowest priority)
    python_code_pattern = r"```python\s*(.*?)\s*```"
    python_code_match = re.search(python_code_pattern, prediction, re.DOTALL)
    if python_code_match:
        content = python_code_match.group(1).strip()
        return "code", content

    return None, ""


def postprocess_responses(resp: str) -> str:
    """Post-process response to ensure tag completeness"""
    # Handle <tool_call> tags (new format from Jinja2 template)
    if "<tool_call>" in resp:
        # Find the last occurrence of <tool_call>...</tool_call>
        tool_call_pattern = r"<tool_call>\s*\{.*?\}\s*</tool_call>"
        matches = list(re.finditer(tool_call_pattern, resp, re.DOTALL))
        if matches:
            last_match = matches[-1]
            return resp[: last_match.end()]

    # Handle <code> tags
    if "</code>" in resp:
        return resp.split("</code>")[0] + "</code>"

    # Handle ```python code blocks
    if "```python" in resp:
        # Find the last occurrence of ```python...```
        python_pattern = r"```python\s*.*?```"
        matches = list(re.finditer(python_pattern, resp, re.DOTALL))
        if matches:
            last_match = matches[-1]
            return resp[: last_match.end()]

    # Handle Answer: \boxed{...} format (only format we need for math_dapo)
    if "Answer:" in resp and "\\boxed{" in resp:
        # Find the last occurrence of Answer: \boxed{...} with nested braces support
        answer_pattern = r"Answer:\s*\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}"
        matches = list(re.finditer(answer_pattern, resp, re.DOTALL))
        if matches:
            last_match = matches[-1]
            return resp[: last_match.end()]

    return resp


def _tool_outcome(result: str) -> str:
    """PATCH(turnlog): classify a sandbox result for the turn log."""
    if result.startswith("Error: Code contains dangerous pattern") or result.startswith("Error: Import of"):
        return "refused"
    if result.startswith("Error: Code execution timed out"):
        return "timeout"
    if result.startswith("Error: Memory usage too high"):
        return "memory"
    if result.startswith("Error") or "\nErrors:\n" in result:
        return "error"
    return "ok"


async def execute_predictions(prediction: str) -> str:
    """Execute predictions and return results"""
    action, content = postprocess_predictions(prediction)
    # PATCH(turnlog): also return what happened, for the turn log.
    tool = {"action": action, "tool_outcome": None, "tool_wait_s": None, "tool_run_s": None, "code": None, "output": None}

    if action == "code":
        # Content is already the Python code (extracted by
        # postprocess_predictions)
        code = content.strip()
        if code:
            # PATCH(semaphore): the only acquire per code run; tool_sandbox no longer
            # takes a second permit. Time the wait and the run separately.
            t0 = time.monotonic()
            async with SEMAPHORE:
                t1 = time.monotonic()
                result = await tool_registry.execute_tool("code_interpreter", {"code": code})
            t2 = time.monotonic()
            next_obs = f"\n\n<interpreter>\n{result}\n</interpreter>\n\n"
            done = False
            tool.update(
                tool_outcome=_tool_outcome(result),
                tool_wait_s=round(t1 - t0, 4),
                tool_run_s=round(t2 - t1, 4),
                code=_clip(code, 2000),
                output=_clip(result, 2000),
            )
        else:
            next_obs = "\n\n<interpreter>\nError: No Python code found" "\n</interpreter>\n\n"
            done = False
            tool["tool_outcome"] = "no_code"
    elif action == "answer":
        next_obs = ""
        done = True
        tool["answer"] = _clip(content, 300)
    else:
        next_obs = (
            "\nMy previous action is invalid. "
            "If I want to execute code, I should put the code between "
            "<code> and </code>. "
            "If I want to give the final answer, I should use the format "
            "'Answer: \\boxed{answer}'. Let me try again.\n"
        )
        done = False
        tool["tool_outcome"] = "invalid"

    return next_obs, done, tool


_MAX_ATTEMPTS = 60  # the same retry budget as slime.utils.http_utils.post


async def _stream_generate(args, url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """PATCH(stream): send one turn as a streaming /generate request.

    Dynamo 1.6.x serves SGLang's token-in /generate only as a stream (a non-streaming
    request gets HTTP 400), and upstream ReTool posted non-streaming requests. This
    follows slime.rollout.sglang_streaming_rollout.generate_streaming: the same HTTP client
    and SGLangStreamAccumulator, so incremental and cumulative streams both work. It
    returns {"meta_info": ...} like the non-streaming response: the terminal chunk's
    meta_info (finish_reason, prompt_tokens, cached_tokens, weight_version, ...) with
    output_token_logprobs rebuilt as [logprob, token_id] for every output token.

    A failed attempt is retried from scratch under a new rid; nothing from it has been
    added to the sample yet. Client errors other than 408 and 429 are not retried.
    """
    client = http_utils._http_client
    assert client is not None, "http client not initialized; call init_http_client first"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        rid = payload["rid"] if attempt == 1 else f"{payload['rid']}-r{attempt}"
        body = dict(payload, rid=rid, stream=True)
        stream = SGLangStreamAccumulator(
            output_mode="incremental" if args.sglang_incremental_streaming_output else "cumulative"
        )
        meta_info: dict[str, Any] = {}
        t0 = time.monotonic()
        first_chunk = None
        try:
            async with client.stream("POST", url, json=body) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode(errors="replace")[:500]
                    error = RuntimeError(f"HTTP {response.status_code} from {url}: {detail}")
                    if response.status_code < 500 and response.status_code not in (408, 429):
                        error.no_retry = True
                    raise error
                async for raw_line in response.aiter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    data_str = raw_line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    chunk = json.loads(data_str)
                    if "meta_info" not in chunk and "error" in chunk:
                        raise RuntimeError(f"stream error from {url}: {data_str[:500]}")
                    if first_chunk is None:
                        first_chunk = time.monotonic()
                    meta_info = stream.add(chunk).meta_info
            if not meta_info.get("finish_reason"):
                raise RuntimeError("SGLang stream ended without a terminal finish_reason")
        except Exception as e:
            if getattr(e, "no_retry", False) or attempt == _MAX_ATTEMPTS:
                raise
            logger.warning(f"retool turn {rid}: {e!r}; retrying (attempt {attempt}/{_MAX_ATTEMPTS})")
            await asyncio.sleep(1)
            continue
        meta_info = dict(meta_info)
        meta_info["output_token_logprobs"] = [[lp, tok] for lp, tok in zip(stream.log_probs, stream.tokens, strict=True)]
        timing = {
            "rid": rid,
            "attempts": attempt,
            "ttft_s": round(first_chunk - t0, 4) if first_chunk is not None else None,
            "req_s": round(time.monotonic() - t0, 4),
        }
        return {"meta_info": meta_info}, timing
    raise AssertionError("unreachable")


def _clip(text: str | None, limit: int) -> str | None:
    """Keep the start and the end of a long string."""
    if text is None or len(text) <= limit:
        return text
    head = limit // 3
    return f"{text[:head]} …[{len(text) - limit} chars cut]… {text[len(text) - (limit - head):]}"


_TURN_LOG = None


def _turn_log(record: dict[str, Any]) -> None:
    """PATCH(turnlog): append one JSON line to $RETOOL_TURN_LOG_DIR/turns-<host>-<pid>.jsonl.

    One "turn" record per /generate request and one "final" record per scored sample.
    Off when RETOOL_TURN_LOG_DIR is unset. The rid in each turn record is the request ID
    on the Dynamo frontend's "Selected worker" log line, which names the engine.
    """
    global _TURN_LOG
    log_dir = os.environ.get("RETOOL_TURN_LOG_DIR")
    if not log_dir:
        return
    if _TURN_LOG is None:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"turns-{socket.gethostname()}-{os.getpid()}.jsonl")
        _TURN_LOG = open(path, "a", buffering=1, encoding="utf-8")
    _TURN_LOG.write(json.dumps(record, ensure_ascii=False) + "\n")


async def generate(args, sample: Sample, sampling_params) -> Sample:
    """Custom generation function supporting tool calls"""
    assert not args.partial_rollout, "Partial rollout is not supported for " "this function at the moment."

    # Retried samples (previously aborted / partial) arrive here with stale
    # rollout state from the first attempt. Clear it so this generation starts
    # clean; otherwise the concatenation below appends new tokens to old ones
    # and downstream `slice_log_prob_with_cp` sees a length mismatch.
    sample.rollout_log_probs = None
    sample.rollout_top_p_token_ids = None
    sample.rollout_top_p_token_offsets = None
    sample.response = ""
    sample.response_length = 0
    sample.loss_mask = []

    state = GenerateState(args)
    # PATCH(stream): send every turn to the Dynamo frontend, not to Slime's own router.
    base_url = os.environ.get("DYNAMO_ROLLOUT_URL") or f"http://{args.sglang_router_ip}:{args.sglang_router_port}"
    url = f"{base_url.rstrip('/')}/generate"

    # Set up the initial prompt with system prompt and tools (outside the loop)
    tool_specs = tool_registry.get_tool_specs()
    question = _question_text(sample.prompt)  # PATCH(prompt)
    prompt = format_conversation_with_tools(prompt=question, tools=tool_specs)

    prompt_tokens_ids = state.tokenizer(prompt, add_special_tokens=False)["input_ids"]
    sample.tokens = list(prompt_tokens_ids)
    response = ""
    response_token_ids = []
    loss_masks = sample.loss_mask
    tool_call_count = 0  # Track actual tool call rounds
    requests = 0  # PATCH(turnlog)

    if args.rollout_max_context_len is not None:
        max_context_length = args.rollout_max_context_len
    else:
        max_context_length = args.context_parallel_size * args.max_tokens_per_gpu

    for turn in range(TOOL_CONFIGS["max_turns"]):
        # Check if total length exceeds max context length
        total_length = len(prompt_tokens_ids) + len(response_token_ids)
        if total_length >= max_context_length:
            sample.status = Sample.Status.TRUNCATED
            break

        # Clamp per-turn max_new_tokens to the remaining context budget so a
        # single turn cannot push total_length past max_context_length. Without
        # this, a turn can append up to rollout_max_response_len tokens on top
        # of a total that was just barely under the cap, producing samples
        # that exceed the training-side max_tokens_per_gpu * cp_size budget
        # and crash the partition/batch code (asserts or OOMs on an oversized
        # partition).
        remaining_budget = max_context_length - total_length
        per_turn_sampling_params = dict(sampling_params)
        per_turn_sampling_params["max_new_tokens"] = min(
            sampling_params.get("max_new_tokens", remaining_budget),
            remaining_budget,
        )

        # Use token IDs instead of text
        current_token_ids = prompt_tokens_ids + response_token_ids
        payload = {
            "input_ids": current_token_ids,
            "sampling_params": per_turn_sampling_params,
            "return_logprob": True,  # Request log probabilities for training
            # PATCH(turnlog): Dynamo takes rid as the request ID, so the frontend's
            # "Selected worker" line for this turn carries it.
            "rid": f"retool-{sample.index}-t{turn}-{uuid.uuid4().hex[:8]}",
        }

        # Log payload to wandb for debugging
        try:
            import wandb

            if wandb.run is not None:
                # Count available tools (from tool_specs)
                available_tools = len(tool_specs)
                # Count tools used in the current response
                tools_used = response.count("<interpreter>")

                wandb.log(
                    {
                        "debug/payload_length": len(prompt + response),
                        "debug/available_tools": available_tools,
                        "debug/tools_used": tools_used,
                        "debug/turn": turn,
                    }
                )
        except ImportError:
            pass  # wandb not available

        t_start = time.time()
        output, timing = await _stream_generate(args, url, payload)  # PATCH(stream): was post(url, payload)
        requests += 1
        meta_info = output["meta_info"]
        finish_reason = meta_info["finish_reason"]
        # PATCH(turnlog): what this turn sent and got back.
        record = {
            "kind": "turn",
            "t_start": round(t_start, 3),
            "t_end": round(time.time(), 3),
            **timing,
            "sample": sample.index,
            "group": sample.group_index,
            "session": sample.session_id,
            "turn": turn,
            "input_len": len(current_token_ids),
            "prompt_tokens": meta_info.get("prompt_tokens"),
            "cached_tokens": meta_info.get("cached_tokens"),
            "output_tokens": len(meta_info["output_token_logprobs"]),
            "finish": finish_reason.get("type"),
            "matched": finish_reason.get("matched"),
            "weight_version": meta_info.get("weight_version"),
        }
        if turn == 0:
            record["question"] = _clip(question, 2000)
            record["label"] = sample.label if isinstance(sample.label, str) else str(sample.label)

        # Handle abort
        if finish_reason["type"] == "abort":
            sample.status = Sample.Status.ABORTED
            _turn_log(record | {"loop": "abort"})
            return sample

        if "output_token_logprobs" in meta_info:
            cur_response_token_ids = [item[1] for item in meta_info["output_token_logprobs"]]
            cur_response = state.tokenizer.decode(cur_response_token_ids)
            cur_log_probs = [item[0] for item in meta_info["output_token_logprobs"]]

        else:
            # sglang returned text but no output_token_logprobs — we cannot
            # recover per-token logprobs for this turn, which would desync
            # rollout_log_probs from response_token_ids and blow up
            # `slice_log_prob_with_cp` downstream. Abort the sample so the
            # fully_async rollout manager returns the whole group to the
            # buffer for retry instead of poisoning the trainer.
            sample.status = Sample.Status.ABORTED
            return sample

        response += cur_response
        response_token_ids += cur_response_token_ids
        sample.append_response_tokens(
            args,
            tokens=cur_response_token_ids,
            log_probs=cur_log_probs,
            trainable=True,
            meta_info=output["meta_info"],
        )
        record["logprobs"] = len(cur_log_probs)
        record["text"] = _clip(cur_response, 2400)

        # Check length limit
        if output["meta_info"]["finish_reason"]["type"] == "length":
            _turn_log(record | {"loop": "length"})
            break

        next_obs, done, tool = await execute_predictions(cur_response)
        record.update(tool)
        if done:
            _turn_log(record | {"loop": "answer"})
            break

        # Count tool calls (when we get interpreter output, it means a tool
        # was called)
        if "<interpreter>" in next_obs:
            tool_call_count += 1

        assert next_obs != "", "Next observation should not be empty."
        obs_tokens_ids = state.tokenizer(next_obs, add_special_tokens=False)["input_ids"]
        overflow = len(prompt_tokens_ids) + len(response_token_ids) + len(obs_tokens_ids) - max_context_length
        truncated_by_observation = overflow > 0
        if truncated_by_observation:
            obs_tokens_ids = obs_tokens_ids[: max(0, len(obs_tokens_ids) - overflow)]
        record["obs_tokens"] = len(obs_tokens_ids)

        # Add dummy log probs for observation tokens (they won't be used due to loss_mask=0)
        # Check if maximum tool call count reached
        response_token_ids += obs_tokens_ids
        sample.append_response_tokens(args, tokens=obs_tokens_ids, trainable=False)

        if sample.rollout_log_probs is not None:
            assert len(response_token_ids) == len(
                sample.rollout_log_probs
            ), f"Token/logp length mismatch at turn {turn}: {len(response_token_ids)} tokens vs {len(sample.rollout_log_probs)} logps"

        # Tool output is appended verbatim and can push total_length past
        # max_context_length (the per-turn generation was clamped to the
        # remaining budget, but tool output is unconstrained). Trim tail
        # tokens so the final sample fits the training budget exactly.
        if truncated_by_observation:
            # Resync the text field from the trimmed token list so
            # reward_func's `sample.prompt + sample.response` matches what
            # the model was actually trained on. decode(tokenize(text)) can
            # be lossy on some tokenizers (whitespace / special-token
            # collapse), but reward_func's regex is whitespace-robust and
            # the trainer sees tokens, not text — so the drift is safe.
            response = state.tokenizer.decode(response_token_ids)
            sample.status = Sample.Status.TRUNCATED
            _turn_log(record | {"loop": "truncated_by_observation"})
            break
        response += next_obs

        if tool_call_count >= TOOL_CONFIGS["max_tool_calls"]:
            _turn_log(record | {"loop": "max_tool_calls"})
            break
        _turn_log(record | {"loop": "continue"})

    # Set sample attributes
    sample.tokens = prompt_tokens_ids + response_token_ids
    sample.response_length = len(response_token_ids)
    sample.response = response
    sample.loss_mask = loss_masks

    # Store payload information for wandb logging
    sample.payload_text = prompt + response
    sample.payload_has_system = "<|im_start|>system" in prompt + response
    sample.payload_has_tools = "# Tools" in prompt + response

    # Store tool call count for reward calculation
    sample.tool_call_count = tool_call_count
    sample.retool_requests = requests  # PATCH(turnlog)

    # Set status
    if sample.status is Sample.Status.PENDING:
        match output["meta_info"]["finish_reason"]["type"]:
            case "length":
                sample.status = Sample.Status.TRUNCATED
            case "abort":
                sample.status = Sample.Status.ABORTED
            case "stop":
                sample.status = Sample.Status.COMPLETED

    return sample


# PATCH(stream): Slime cancels this coroutine on abort instead of calling the engines'
# /abort_request, the same as the tutorial's streaming generate function.
generate.abort_mode = "request"


async def reward_func(args, sample, **kwargs):
    """Tool call reward function using math_dapo as primary reward model"""
    if not isinstance(sample, Sample):
        raise TypeError("Sample must be an instance of Sample class.")

    # Build complete solution string
    # PATCH(reward): sample.prompt is DAPO-Math's message list here (this kit drops
    # --apply-chat-template), and `list + str` raised TypeError (Slime issue #1829; the
    # fix in PR #2120 is unmerged). compute_score reads only the last 300 characters.
    solution_str = _question_text(sample.prompt) + sample.response

    # Get ground truth answer - label is a string, not a dict
    ground_truth = sample.label if sample.label is not None else ""

    # Get tool call count as num_turns
    num_turns = getattr(sample, "tool_call_count", 0)

    # use \\boxed{...} answer
    result = math_dapo_compute_score(solution_str, ground_truth, strict_box_verify=True)

    # encourage model to call tools
    if result["score"] < 0:
        tool_call_reward = (num_turns - 2) / 2 * 0.1
        result["score"] = min(-0.6, result["score"] + tool_call_reward)

    if result["pred"] is None:
        result["pred"] = ""

    # PATCH(turnlog): one record per scored sample.
    _turn_log(
        {
            "kind": "final",
            "t": round(time.time(), 3),
            "sample": sample.index,
            "group": sample.group_index,
            "session": sample.session_id,
            "requests": getattr(sample, "retool_requests", None),
            "tool_calls": num_turns,
            "response_length": sample.response_length,
            "status": sample.status.value if hasattr(sample.status, "value") else str(sample.status),
            "score": result["score"],
            "acc": bool(result.get("acc")),
            "pred": _clip(str(result["pred"]), 200),
            "label": ground_truth if isinstance(ground_truth, str) else str(ground_truth),
        }
    )

    return result
