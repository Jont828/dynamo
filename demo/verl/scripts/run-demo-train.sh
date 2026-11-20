#!/usr/bin/env bash
# GRPO on GSM8K with verl's trainer and the Dynamo rollout backend, on one 8-GPU node:
#   - verl FSDP actor + reference policy on all 8 GPUs (colocated with the engines)
#   - 4 dynamo.vllm engines (TP=2) behind one Dynamo frontend with the KV-aware router
#   - engines sleep while the trainer updates, then receive the new weights over CUDA IPC
# Validation on the GSM8K test split runs before training and every TEST_FREQ steps.
#
#   RUN_NAME=demo-1 STEPS=15 bash run-demo-train.sh [extra Hydra overrides]
# Writes $RUNS_DIR/$RUN_NAME/{driver.log,STATUS,summary.txt,rollouts/,val/,kv-metrics.jsonl}.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

PRESET="${PRESET:-dynamo_trainer_v1_colocate}"
MODEL="${MODEL:-$MODELS_DIR/Qwen2.5-7B-Instruct}"
ROUTER_MODE="${ROUTER_MODE:-kv}"
TP="${TP:-2}"
ROLLOUT_N="${ROLLOUT_N:-8}"
STEPS="${STEPS:-15}"
TEST_FREQ="${TEST_FREQ:-5}"
VAL_BEFORE_TRAIN="${VAL_BEFORE_TRAIN:-True}"
LR="${LR:-1e-6}"
MAX_PROMPT="${MAX_PROMPT:-512}"
MAX_RESPONSE="${MAX_RESPONSE:-1024}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.6}"
TRAIN_BS="${TRAIN_BS:-64}"
MINI_BS="${MINI_BS:-$((TRAIN_BS / 4))}"
# Hard cap on the training process, so a stalled run never holds the node for more than an hour.
MAX_MINUTES="${MAX_MINUTES:-55}"

RUN_NAME="${RUN_NAME:-demo-grpo-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="$RUNS_DIR/$RUN_NAME"
mkdir -p "$RUN_DIR/dynamo-logs" "$RUN_DIR/workers"
echo "running" > "$RUN_DIR/STATUS"
date -u +%s > "$RUN_DIR/started_at"
cat > "$RUN_DIR/config.txt" <<EOF
preset=$PRESET
model=$MODEL
router_mode=$ROUTER_MODE
tp=$TP engines=$((8 / TP))
rollout_n=$ROLLOUT_N train_batch_size=$TRAIN_BS mini_batch=$MINI_BS
steps=$STEPS test_freq=$TEST_FREQ val_before_train=$VAL_BEFORE_TRAIN
lr=$LR max_prompt=$MAX_PROMPT max_response=$MAX_RESPONSE
max_minutes=$MAX_MINUTES
EOF
echo "run dir: $RUN_DIR"
cat "$RUN_DIR/config.txt"
bash "$SCRIPT_DIR/cleanup-orphans.sh"

export VERL_USE_EXTERNAL_MODULES="$EXTERNAL_MODULES"
export VERL_DYNAMO_WORKER_METRICS_DIR="$RUN_DIR/workers"
# Ray workers block-buffer stdout when it isn't a TTY, which holds verl's per-step metric lines until exit.
export PYTHONUNBUFFERED=1
python3 "$VERL_DIR/recipe/dynamo/metrics_sidecar.py" \
    --endpoints-glob "$VERL_DYNAMO_WORKER_METRICS_DIR/*.endpoints" \
    --output "$RUN_DIR/kv-metrics.jsonl" \
    --label "dynamo-$ROUTER_MODE" \
    --interval 10 > "$RUN_DIR/sidecar.log" 2>&1 &
METRICS_SIDECAR_PID=$!
nvidia-smi --query-gpu=timestamp,index,memory.used --format=csv,noheader,nounits -lms 2000 > "$RUN_DIR/gpu-mem.csv" 2>/dev/null &
GPU_SAMPLER_PID=$!
finish() {
    local rc=$?
    kill "$METRICS_SIDECAR_PID" "$GPU_SAMPLER_PID" 2>/dev/null || true
    grep -q '^done' "$RUN_DIR/STATUS" 2>/dev/null || echo "done rc=$rc" > "$RUN_DIR/STATUS"
}
trap finish EXIT

cd "$VERL_DIR"
rc=0
timeout -k 60 "${MAX_MINUTES}m" python3 -m verl.trainer.main_ppo \
    --config-path "$VERL_DIR/recipe/dynamo/config" --config-name "$PRESET" \
    "ray_kwargs.ray_init.runtime_env.env_vars.VERL_USE_EXTERNAL_MODULES='$EXTERNAL_MODULES'" \
    algorithm.adv_estimator=grpo \
    algorithm.use_kl_in_reward=False \
    data.train_files="$DATA_DIR/gsm8k/train.parquet" \
    data.val_files="$DATA_DIR/gsm8k/test.parquet" \
    data.train_batch_size="$TRAIN_BS" \
    data.max_prompt_length="$MAX_PROMPT" \
    data.max_response_length="$MAX_RESPONSE" \
    data.filter_overlong_prompts=True \
    data.truncation=error \
    actor_rollout_ref.model.path="$MODEL" \
    actor_rollout_ref.model.use_remove_padding=True \
    actor_rollout_ref.model.enable_gradient_checkpointing=True \
    actor_rollout_ref.actor.optim.lr="$LR" \
    actor_rollout_ref.actor.ppo_mini_batch_size="$MINI_BS" \
    actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=4 \
    actor_rollout_ref.actor.use_kl_loss=True \
    actor_rollout_ref.actor.kl_loss_coef=0.001 \
    actor_rollout_ref.actor.kl_loss_type=low_var_kl \
    actor_rollout_ref.actor.entropy_coeff=0 \
    actor_rollout_ref.actor.fsdp_config.param_offload=False \
    actor_rollout_ref.actor.fsdp_config.optimizer_offload=False \
    actor_rollout_ref.ref.fsdp_config.param_offload=True \
    actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu=8 \
    actor_rollout_ref.rollout.name=dynamo \
    actor_rollout_ref.rollout.mode=async \
    actor_rollout_ref.rollout.tensor_model_parallel_size="$TP" \
    actor_rollout_ref.rollout.gpu_memory_utilization="$GPU_MEM_UTIL" \
    actor_rollout_ref.rollout.n="$ROLLOUT_N" \
    actor_rollout_ref.rollout.calculate_log_probs=True \
    actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=8 \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.router_mode="$ROUTER_MODE" \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.thunderagent.enabled=false \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.request_engine_data=true \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.request_completion_token_ids=true \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.request_timeout_s=1800 \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.free_engine_on_train=true \
    ++actor_rollout_ref.rollout.engine_kwargs.dynamo.enable_worker_system_metrics=true \
    'trainer.logger=["console"]' \
    trainer.project_name=verl-dynamo-demo \
    trainer.experiment_name="$RUN_NAME" \
    trainer.n_gpus_per_node=8 \
    trainer.nnodes=1 \
    trainer.val_before_train="$VAL_BEFORE_TRAIN" \
    trainer.test_freq="$TEST_FREQ" \
    trainer.save_freq=-1 \
    trainer.total_training_steps="$STEPS" \
    trainer.default_local_dir="$RUN_DIR/ckpt" \
    trainer.rollout_data_dir="$RUN_DIR/rollouts" \
    trainer.validation_data_dir="$RUN_DIR/val" \
    ++ray_kwargs.ray_init.runtime_env.env_vars.VERL_DYNAMO_LOG_DIR="$RUN_DIR/dynamo-logs" \
    ++ray_kwargs.ray_init.runtime_env.env_vars.VERL_DYNAMO_WORKER_METRICS_DIR="$RUN_DIR/workers" \
    "++ray_kwargs.ray_init.runtime_env.env_vars.PYTHONUNBUFFERED='1'" \
    "$@" > "$RUN_DIR/driver.log" 2>&1 || rc=$?

python3 "$SCRIPT_DIR/summarize_run.py" "$RUN_DIR" --expect-steps "$STEPS" --expect-ranks 8 \
    --orphan-grace "${ORPHAN_GRACE:-30}" > "$RUN_DIR/summary.txt" 2>&1 || rc=$((rc ? rc : 1))
bash "$SCRIPT_DIR/cleanup-orphans.sh" >> "$RUN_DIR/summary.txt" 2>&1 || true
echo "done rc=$rc" > "$RUN_DIR/STATUS"
exit "$rc"
