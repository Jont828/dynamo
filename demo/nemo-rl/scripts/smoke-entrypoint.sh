#!/usr/bin/env bash
# Smoke RayJob entrypoint (k8s/rayjob-smoke.yaml). Runs NeMo RL CI's first Dynamo overlay test
# unchanged: tests/functional/grpo_dp_single_controller.sh with ++policy.generation.dynamo=true
# (Qwen/Qwen3-0.6B, 2 GRPO steps, 2 GPUs), then CI's metric checks and check_dynamo_overlay.sh.
# KubeRay submits it with `ray job submit`, so RAY_ADDRESS is set and NeMo RL's init_ray() attaches
# to KubeRay's cluster. Extra arguments are passed to the test as NeMo RL overrides.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"
RUN_NAME="${RUN_NAME:-smoke-$(date -u +%Y%m%d-%H%M%S)}"
RUN_DIR="$RUNS_DIR/$RUN_NAME"
mkdir -p "$RUN_DIR"

echo "Ray cluster: $("$NEMO_RL_PYTHON" -c 'import ray; ray.init(address="auto", log_to_driver=False); r = ray.cluster_resources(); print(f"{len(ray.nodes())} node(s), {r.get("GPU", 0):.0f} GPUs, {r.get("CPU", 0):.0f} CPUs")' 2>/dev/null)"
echo "RAY_ADDRESS=${RAY_ADDRESS:-unset}   run: $RUN_NAME   copied to $RUN_DIR (on the cache volume)"
echo

# The image ships without a uv cache (STRIP_UV_CACHE=1), and every venv is already built: keep the
# test's `uv run` calls from trying to sync.
export UV_NO_SYNC=1
cd "$NEMO_RL_DIR"
# NeMo RL's .dockerignore leaves tests/functional/ out of the image (CI mounts a checkout instead), so put
# CI's two scripts back where the test expects them; they are byte-identical copies from the same commit.
mkdir -p tests/functional
install -m 0755 "$SCRIPT_DIR/ci-grpo_dp_single_controller.sh" tests/functional/grpo_dp_single_controller.sh
install -m 0755 "$SCRIPT_DIR/ci-check_dynamo_overlay.sh" tests/functional/check_dynamo_overlay.sh
rc=0
bash tests/functional/grpo_dp_single_controller.sh ++policy.generation.dynamo=true "$@" || rc=$?

cp -r tests/functional/grpo_dp_single_controller/. "$RUN_DIR/" 2>/dev/null || true
echo
echo "smoke test exit code: $rc   (run log: $RUN_DIR/run.log)"
exit "$rc"
