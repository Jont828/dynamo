#!/usr/bin/env bash
# RayJob entrypoint (k8s/rayjob.yaml). KubeRay submits it to the Ray head with `ray job submit`, so it
# runs as the Ray job driver: RAY_ADDRESS is set, and NeMo RL's init_ray() attaches to KubeRay's cluster
# instead of starting its own. Its stdout is what `kubectl logs -f job/nemo-rl-grpo-demo` shows: one
# line per event and one row per training step while the run goes (watch_run.py), then the results
# (report_run.py). The driver's full output goes to $RUNS_DIR/$RUN_NAME/run.log on the cache volume.
#   STEPS=25 ENGINES=2 RUN_NAME=... bash rayjob-entrypoint.sh [extra NeMo RL overrides]
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
STEPS="${STEPS:-25}"
ENGINES="${ENGINES:-2}"
# Hard cap on the training process, so a stalled run still prints its report before the RayJob's deadline.
MAX_MINUTES="${MAX_MINUTES:-50}"
export RUN_NAME="${RUN_NAME:-grpo-$(date -u +%Y%m%d-%H%M%S)}"
RUN_DIR="$RUNS_DIR/$RUN_NAME"
mkdir -p "$RUN_DIR/logs"

echo "Ray cluster: $("$NEMO_RL_PYTHON" -c 'import ray; ray.init(address="auto", log_to_driver=False); r = ray.cluster_resources(); print(f"{len(ray.nodes())} node(s), {r.get("GPU", 0):.0f} GPUs, {r.get("CPU", 0):.0f} CPUs")' 2>/dev/null)"
echo "run: $RUN_NAME   full log and results: $RUN_DIR (on the cache volume)"
cat > "$RUN_DIR/config.txt" <<EOF
config=$CONFIG
steps=$STEPS
engines=$ENGINES
gpus=$((ENGINES + 1))
overrides=$*
EOF
echo running > "$RUN_DIR/STATUS"
date -u +%s > "$RUN_DIR/started_at"

# NeMo RL's own launcher and Dynamo example config; only the run size, the log location and the loggers
# are set here. The image ships without a uv cache, so run the venv's python rather than `uv run`.
cd "$NEMO_RL_DIR"
timeout -k 60 "${MAX_MINUTES}m" "$NEMO_RL_PYTHON" examples/run_grpo_single_controller.py \
    --config "$CONFIG" \
    cluster.gpus_per_node=$((ENGINES + 1)) \
    policy.generation.colocated.resources.gpus_per_node="$ENGINES" \
    grpo.max_num_steps="$STEPS" \
    logger.log_dir="$RUN_DIR/logs" \
    logger.wandb_enabled=false \
    logger.monitor_gpus=false \
    checkpointing.enabled=false \
    "$@" > "$RUN_DIR/run.log" 2>&1 &
train_pid=$!

python3 "$SCRIPT_DIR/watch_run.py" "$RUN_DIR" &
watch_pid=$!
wait "$train_pid"
rc=$?
echo "done rc=$rc" > "$RUN_DIR/STATUS"
wait "$watch_pid"

echo
python3 "$SCRIPT_DIR/report_run.py" "$RUN_DIR" || rc=$((rc ? rc : 1))
exit "$rc"
