<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sample Backend Examples

The sample backend exercises Dynamo request paths without downloading a model or using a GPU.

## Local Launchers

- `launch/agg.sh`: Aggregated text generation.
- `launch/disagg.sh`: Disaggregated text generation.
- `launch/multimodal_agg.sh`: Aggregated multimodal serving.
- `launch/multimodal_disagg.sh`: Disaggregated multimodal serving.
- `launch/agg_diffusion.sh`: CPU-only image generation through the diffusion API.

## Kubernetes

Apply `deploy/agg_diffusion.yaml` to run the CPU-only diffusion example as a
`DynamoGraphDeployment`. Replace `my-registry/dynamo:my-tag` with an image containing the current
Dynamo Python package and runtime.
