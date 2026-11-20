# shellcheck shell=bash
# Shared settings for the NeMo RL + Dynamo demo. Source this file; do not execute it.
# The manifests (k8s/setup-job.yaml, k8s/rayjob*.yaml) mount the cache volume at /cache and these
# scripts at /opt/nemo-rl-demo.

# NeMo RL's source and its driver venv, as the image ships them. The image strips the uv cache, so run
# the venv's python, never plain `uv run`.
export NEMO_RL_DIR="${NEMO_RL_DIR:-/opt/nemo-rl}"
export NEMO_RL_PYTHON="${NEMO_RL_PYTHON:-/opt/nemo_rl_venv/bin/python}"
# NeMo RL's per-actor venvs. ai-dynamo lives in the vLLM worker's (the `vllm` extra), not in the driver's.
export NEMO_RL_VENV_DIR="${NEMO_RL_VENV_DIR:-/opt/ray_venvs}"
VLLM_WORKER_VENV="$NEMO_RL_VENV_DIR/nemo_rl.models.generation.vllm.vllm_worker_async.VllmAsyncGenerationWorker"
MEGATRON_WORKER_VENV="$NEMO_RL_VENV_DIR/nemo_rl.models.policy.workers.megatron_policy_worker.MegatronPolicyWorker"
export VLLM_WORKER_VENV MEGATRON_WORKER_VENV

# On the cache volume: Hugging Face models and datasets, Megatron's converted checkpoints
# ($HF_HOME/nemo_rl), vLLM's compile cache, and one directory per run.
export HF_HOME="${HF_HOME:-/cache/hf-home}"
export RUNS_DIR="${RUNS_DIR:-/cache/runs}"
export SCRIPTS_DIR="${SCRIPTS_DIR:-/opt/nemo-rl-demo}"

# The demo's config: NeMo RL's own Dynamo example (grpo_math_1B_megatron_single_controller.yaml plus
# policy.generation.dynamo: true), which trains Qwen/Qwen2.5-1.5B on OpenMathInstruct-2.
export CONFIG="${CONFIG:-examples/configs/grpo_math_1B_dynamo_single_controller.yaml}"
