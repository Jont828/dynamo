#!/usr/bin/env bash
# RayJob entrypoint (runs on the Ray head as the job driver).
#
# Dynamo's examples/rl/slime/launch-slime.sh supplies only the Dynamo wiring (external engine list,
# streaming generator, Dynamo frontend URL). This script adds the slime training arguments it
# leaves to the user, then execs launch-slime.sh unchanged from the Dynamo ConfigMap.
set -euo pipefail

cd /root/slime
source scripts/models/qwen3-0.6B.sh

export SLIME_HOME=/root/slime
export DYNAMO_ENGINE_ADDRS="slime-sglang-engine-0:30000 slime-sglang-engine-1:30000"
export DYNAMO_ROLLOUT_URL="http://slime-sglang-rollout:8000"

# The guide's readiness checks: both native SGLang engines and the Dynamo frontend.
for url in http://slime-sglang-engine-0:30000/health_generate \
           http://slime-sglang-engine-1:30000/health_generate \
           http://slime-sglang-rollout:8000/health; do
  curl --fail-with-body -sS -o /dev/null "$url"
  echo "ready: $url"
done

ARGS=(
  "${MODEL_ARGS[@]}"
  # Checkpoints and data (prepared by prepare-job.yaml on the shared volume)
  --hf-checkpoint /data/Qwen3-0.6B
  --ref-load /data/Qwen3-0.6B_torch_dist
  --prompt-data /data/gsm8k/train.parquet
  --input-key messages
  --label-key label
  --apply-chat-template
  --apply-chat-template-kwargs '{"enable_thinking": false}'
  --rollout-shuffle
  --rm-type math
  # Short loop: 10 x (64-sample rollout -> 1 train step -> weight sync to both engines)
  --num-rollout "${NUM_ROLLOUT:-10}"
  --rollout-batch-size 8
  --n-samples-per-prompt 8
  --global-batch-size 64
  --rollout-max-response-len 1024
  --rollout-temperature 1
  # Trainer: one GPU on the Ray GPU worker; the SGLang engines are the Dynamo workers
  --actor-num-nodes 1
  --actor-num-gpus-per-node 1
  --tensor-model-parallel-size 1
  --pipeline-model-parallel-size 1
  --context-parallel-size 1
  --use-dynamic-batch-size
  --max-tokens-per-gpu 9216
  # GRPO (score centering is not supported with streaming rollouts)
  --advantage-estimator grpo
  --kl-loss-coef 0.00
  --entropy-coef 0.00
  --eps-clip 0.2
  --eps-clip-high 0.28
  --optimizer adam
  --lr 1e-6
  --lr-decay-style constant
  --weight-decay 0.1
  --adam-beta1 0.9
  --adam-beta2 0.98
  --attention-dropout 0.0
  --hidden-dropout 0.0
  --accumulate-allreduce-grads-in-fp32
  --attention-softmax-in-fp32
  --attention-backend flash
  # Weight sync: full HF checkpoint on the shared volume, reloaded by each engine via
  # /update_weights_from_disk. Unlike NCCL, this does not need matching torch/NCCL builds in the
  # slime and Dynamo images, and it uses only stock SGLang endpoints (stock SGLang 0.5.21, in
  # Dynamo 1.6.x, rejects slime's sessionless NCCL updates: ai-dynamo/dynamo#16245).
  --update-weight-mode full
  --update-weight-transport disk
  --update-weight-disk-dir /data/weight-updates
  # The engines run as uid 1000; make the 0600 safetensors shards readable before they reload.
  --custom-update-weight-post-write-path slime_dynamo_hooks.make_weights_readable
)

exec bash /opt/dynamo-example/launch-slime.sh "${ARGS[@]}" "$@"
