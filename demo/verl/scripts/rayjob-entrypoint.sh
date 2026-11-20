#!/usr/bin/env bash
# RayJob entrypoint (k8s/rayjob.yaml). KubeRay submits it to the Ray head with `ray job submit`, so it
# runs as the Ray job driver: RAY_ADDRESS points at the KubeRay cluster, and verl's main_ppo joins it
# instead of starting a private Ray. Its stdout is what `kubectl logs -f job/<rayjob>` shows: the
# watcher's one-line-per-event view while training, then the results report.
#   STEPS=15 TEST_FREQ=5 RUN_NAME=... bash rayjob-entrypoint.sh [extra Hydra overrides]
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
export RUN_NAME="${RUN_NAME:-grpo-$(date -u +%Y%m%d-%H%M%S)}"
RUN_DIR="$RUNS_DIR/$RUN_NAME"
mkdir -p "$RUNS_DIR"

echo "Ray cluster: $(python3 -c 'import ray; ray.init(log_to_driver=False); r = ray.cluster_resources(); print(f"{len(ray.nodes())} node(s), {r.get("GPU", 0):.0f} GPUs, {r.get("CPU", 0):.0f} CPUs")' 2>/dev/null)"
echo "run: $RUN_NAME   logs and results: $RUN_DIR (on the cache volume)"
echo

# The training run writes its full log to $RUN_DIR/driver.log; this job's output stays readable.
bash "$SCRIPT_DIR/run-demo-train.sh" "$@" > "$RUNS_DIR/$RUN_NAME.console.log" 2>&1 &
train_pid=$!
for _ in $(seq 1 120); do
    [ -f "$RUN_DIR/started_at" ] && break
    kill -0 "$train_pid" 2>/dev/null || break
    sleep 1
done
if [ -f "$RUN_DIR/started_at" ]; then
    python3 "$SCRIPT_DIR/watch_run.py" "$RUN_DIR"
fi
wait "$train_pid"
rc=$?

echo
if [ -f "$RUN_DIR/driver.log" ]; then
    python3 "$SCRIPT_DIR/report_run.py" "$RUN_DIR"
else
    echo "training did not start; launcher output:"
    tail -50 "$RUNS_DIR/$RUN_NAME.console.log"
fi
exit "$rc"
