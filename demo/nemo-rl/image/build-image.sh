#!/usr/bin/env bash
# Build and push the NeMo RL image this demo runs, with BuildKit running inside the cluster.
#
# NeMo RL publishes no public nightly images, so the image is built from NeMo RL main with CI's own
# invocation (.github/workflows/cicd-main.yml -> _build_container.yml: target release,
# build-contexts nemo-rl=., BUILD_DYNAMO=1, MAX_JOBS=4), trimmed of what the demo doesn't use:
#   SKIP_SGLANG_BUILD, SKIP_TRTLLM_BUILD  no SGLang or TRT-LLM worker venvs
#   STRIP_UV_CACHE                        no uv cache in the image: run the venv's python or `uv run --no-sync`
#   NVTE_CUDA_ARCHS=90                    TransformerEngine's torch extension for H100 only
#   CUSTOM_SETUP_FNAME=                   no Apptainer
#
# Prerequisites: a recursive NeMo RL checkout at $NEMO_RL_SRC, and a buildx builder in the cluster:
#   kubectl create namespace buildkit
#   docker buildx create --name nemo-rl-builder --driver kubernetes --bootstrap \
#     --driver-opt 'namespace=buildkit,nodeselector=kubernetes.io/hostname=<node>,requests.cpu=64,requests.memory=384Gi,limits.cpu=64,limits.memory=384Gi'
# Afterwards: docker buildx rm nemo-rl-builder && kubectl delete namespace buildkit
set -euo pipefail

NEMO_RL_SRC="${NEMO_RL_SRC:-$HOME/Go/src/nemo-rl}"
REPO="${REPO:-docker.io/jont828/nemo-rl}"
BUILDER="${BUILDER:-nemo-rl-builder}"
OUT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

cd "$NEMO_RL_SRC"
SHA="$(git rev-parse HEAD)"
TAG="$REPO:main-${SHA:0:8}-dynamo"
echo "nemo-rl $SHA -> $TAG  start=$(date -u +%FT%TZ)"
git submodule status --recursive

docker buildx build --builder "$BUILDER" --platform linux/amd64 --progress plain \
    --file docker/Dockerfile --target release --build-context nemo-rl=. \
    --build-arg BUILD_DYNAMO=1 \
    --build-arg SKIP_SGLANG_BUILD=1 --build-arg SKIP_TRTLLM_BUILD=1 \
    --build-arg STRIP_UV_CACHE=1 \
    --build-arg NVTE_CUDA_ARCHS=90 \
    --build-arg MAX_JOBS=4 \
    --build-arg CUSTOM_SETUP_FNAME= \
    --build-arg NEMO_RL_COMMIT="$SHA" \
    --metadata-file "$OUT_DIR/metadata.json" \
    --tag "$TAG" --push .
echo "pushed $TAG  end=$(date -u +%FT%TZ)"
