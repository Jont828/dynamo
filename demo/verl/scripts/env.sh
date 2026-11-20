# shellcheck shell=bash
# Shared settings for the verl + Dynamo GRPO demo. Source this file; do not execute it.
# Defaults are the Lustre layout used by demo-exec.sh (build shared with kit-1.5.1 under
# /shared/verl-dynamo-151). The KubeRay manifests (k8s/setup-job.yaml, k8s/rayjob.yaml) override
# SHARED_ROOT, RUNS_DIR, MODELS_DIR, DATA_DIR and VERL_DIR to put everything on the /cache volume.

export DYNAMO_IMAGE="${DYNAMO_IMAGE:-nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.1}"
export VERL_RECIPE_REPO="${VERL_RECIPE_REPO:-https://github.com/verl-project/verl-recipe.git}"
# verl-recipe main on 2026-10-12; its last dynamo/ change is #156 (2026-09-22).
export VERL_RECIPE_COMMIT="${VERL_RECIPE_COMMIT:-242c73fcadf331e7214c4b48f67423f45239cea8}"
# Core verl selected by that commit's dynamo/REQUIRED_VERL.txt (asserted by build-env.sh).
export VERL_COMMIT="${VERL_COMMIT:-6cbca9ce7208100d11b4d1b06eccf098cc9e76aa}"
# The guide's snapshot, used only for the old-pin import check.
export OLD_VERL_COMMIT="${OLD_VERL_COMMIT:-d82d2777b5dc3e96a8a45168d02660312707ab98}"
export FLASH_ATTN_VERSION="${FLASH_ATTN_VERSION:-2.8.3.post1}"
# The recipe README's tested TransferQueue version for the V1 trainer.
export TRANSFER_QUEUE_VERSION="${TRANSFER_QUEUE_VERSION:-0.1.9}"

# Separate tree on the shared RWX volume, so the 1.4.2 build under /shared/verl-dynamo stays intact.
export SHARED_ROOT="${SHARED_ROOT:-/shared/verl-dynamo-151}"
export SRC_DIR="${SRC_DIR:-$SHARED_ROOT/src}"
export VERL_DIR="${VERL_DIR:-$SRC_DIR/verl}"
export WHEELHOUSE="${WHEELHOUSE:-$SHARED_ROOT/wheelhouse}"
export ENV_DIR="${ENV_DIR:-$SHARED_ROOT/env}"
export RUNS_DIR="${RUNS_DIR:-/shared/verl-dynamo-demo/runs}"
export KITPY_DIR="${KITPY_DIR:-$SHARED_ROOT/kitpy}"
# Models, datasets, and the sm_90 flash-attn wheel from the 1.4.2 build are reused read-only.
export MODELS_DIR="${MODELS_DIR:-/shared/verl-dynamo/models}"
export DATA_DIR="${DATA_DIR:-/shared/verl-dynamo/data}"
export OLD_WHEELHOUSE="${OLD_WHEELHOUSE:-/shared/verl-dynamo/wheelhouse}"

# Directory holding these scripts (the ConfigMap mount inside the cluster).
export SCRIPTS_DIR="${SCRIPTS_DIR:-/opt/verl-dynamo-demo}"

# Recipe registration plus a shim that surfaces the recipe's weight-update acknowledgements,
# which recipe main logs at DEBUG. Pass it through Ray's runtime_env as a quoted Hydra string.
export EXTERNAL_MODULES="${EXTERNAL_MODULES:-recipe.dynamo.register,verl_dynamo_kitlog}"
