# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Docker image build/push targets for dynamo container images.
#
# Usage:
#   make docker-build-frontend                          # build frontend image
#   make docker-build-all                               # build all images
#   make docker-push-all                                # push all images
#   make docker-build-frontend REGISTRY=myregistry TAG=v1.0  # override defaults

# === Configurable variables ===
REGISTRY      ?= docker.io/jont828
TAG           ?= main
PLATFORM      ?= linux/amd64
CUDA_VERSION  ?= 12.9
EPP_IMAGE     ?= registry.k8s.io/gateway-api-inference-extension/epp:v0.5.1

# trtllm requires CUDA 13.1
TRTLLM_CUDA_VERSION ?= 13.1

# === Derived image names ===
FRONTEND_IMAGE       = $(REGISTRY)/dynamo-frontend:$(TAG)
SGLANG_RUNTIME_IMAGE = $(REGISTRY)/sglang-runtime:$(TAG)
TRTLLM_RUNTIME_IMAGE = $(REGISTRY)/trtllm-runtime:$(TAG)
PLANNER_IMAGE        = $(REGISTRY)/dynamo-planner:$(TAG)

# === Rendered Dockerfile paths ===
CONTAINER_DIR = container
RENDER_PY     = python $(CONTAINER_DIR)/render.py

# Helper to normalize platform for rendered filename (e.g. linux/amd64 -> amd64)
PLATFORM_SHORT = $(lastword $(subst /, ,$(PLATFORM)))

.PHONY: docker-build-frontend docker-build-sglang-runtime docker-build-trtllm-runtime docker-build-planner
.PHONY: docker-push-frontend docker-push-sglang-runtime docker-push-trtllm-runtime docker-push-planner
.PHONY: docker-build-all docker-push-all

# =============================================================================
# Build targets
# =============================================================================

docker-build-frontend:
	$(RENDER_PY) --target=frontend --framework=dynamo --platform=$(PLATFORM) --cuda-version=$(CUDA_VERSION) --output-short-filename
	docker buildx build --progress=plain --platform $(PLATFORM) --load \
		--build-arg EPP_IMAGE=$(EPP_IMAGE) \
		-f $(CONTAINER_DIR)/rendered.Dockerfile \
		-t $(FRONTEND_IMAGE) .

docker-build-sglang-runtime:
	$(RENDER_PY) --target=runtime --framework=sglang --platform=$(PLATFORM) --cuda-version=$(CUDA_VERSION)
	docker buildx build --progress=plain --platform $(PLATFORM) --load \
		-f $(CONTAINER_DIR)/sglang-runtime-cuda$(CUDA_VERSION)-$(PLATFORM_SHORT)-rendered.Dockerfile \
		-t $(SGLANG_RUNTIME_IMAGE) .

docker-build-trtllm-runtime:
	$(RENDER_PY) --target=runtime --framework=trtllm --platform=$(PLATFORM) --cuda-version=$(TRTLLM_CUDA_VERSION)
	docker buildx build --progress=plain --platform $(PLATFORM) --load \
		-f $(CONTAINER_DIR)/trtllm-runtime-cuda$(TRTLLM_CUDA_VERSION)-$(PLATFORM_SHORT)-rendered.Dockerfile \
		-t $(TRTLLM_RUNTIME_IMAGE) .

docker-build-planner:
	$(RENDER_PY) --target=planner --framework=dynamo --platform=$(PLATFORM) --cuda-version=$(CUDA_VERSION) --output-short-filename
	docker buildx build --progress=plain --platform $(PLATFORM) --load \
		-f $(CONTAINER_DIR)/rendered.Dockerfile \
		-t $(PLANNER_IMAGE) .

docker-build-all: docker-build-frontend docker-build-sglang-runtime docker-build-trtllm-runtime docker-build-planner

# =============================================================================
# Push targets
# =============================================================================

docker-push-frontend:
	docker push $(FRONTEND_IMAGE)

docker-push-sglang-runtime:
	docker push $(SGLANG_RUNTIME_IMAGE)

docker-push-trtllm-runtime:
	docker push $(TRTLLM_RUNTIME_IMAGE)

docker-push-planner:
	docker push $(PLANNER_IMAGE)

docker-push-all: docker-push-frontend docker-push-sglang-runtime docker-push-trtllm-runtime docker-push-planner
