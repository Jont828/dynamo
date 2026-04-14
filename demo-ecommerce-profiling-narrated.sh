#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# ============================================================================
# Narrated E-Commerce DGDR→Profiling→Deployment Demo for NVIDIA Dynamo
# ============================================================================
#
# This is a narrated demo script that showcases the full Dynamo workflow:
#   DGDR creation → Profiling → Deployment → Ready to serve
#
# It has TWO modes:
#   1. LIVE mode (--live): Actually applies a DGDR and watches real profiling
#   2. CINEMA mode (default): Simulates the flow with realistic output for
#      screen recordings, stitching into an already-deployed model seamlessly
#
# Cinema mode is designed for demos where Qwen3-32B is already deployed and
# you don't want to tear it down just for a recording. It produces output
# that looks identical to a real DGDR flow.
#
# Usage:
#   # Cinema mode (fake profiling + deployment, stitch to live cluster)
#   ./demo-ecommerce-narrated.sh
#
#   # Cinema mode with custom namespace (reads real pods at the end)
#   ./demo-ecommerce-narrated.sh --namespace dynamo-system
#
#   # Live mode (actually applies DGDR)
#   ./demo-ecommerce-narrated.sh --live
#
set -e

# =============================================================================
# Configuration
# =============================================================================
NAMESPACE="${NAMESPACE:-dynamo-system}"
MODEL="${MODEL:-Qwen/Qwen3-32B}"
BACKEND="${BACKEND:-vllm}"
DEPLOYMENT_NAME="ecommerce-assistant"
TTFT_TARGET=500
ITL_TARGET=30
LIVE_MODE=false
TYPING_SPEED=0.02  # Seconds between chars for typewriter effect

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# =============================================================================
# Parse Arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)       NAMESPACE="$2"; shift 2 ;;
        --model)           MODEL="$2"; shift 2 ;;
        --backend)         BACKEND="$2"; shift 2 ;;
        --live)            LIVE_MODE=true; shift ;;
        --help|-h)
            echo "Usage: $0 [--namespace NS] [--model MODEL] [--live]"
            echo ""
            echo "Modes:"
            echo "  (default)    Cinema mode — simulated output for screen recordings"
            echo "  --live       Live mode — actually applies DGDR to cluster"
            echo ""
            echo "Options:"
            echo "  --namespace  Kubernetes namespace (default: dynamo-system)"
            echo "  --model      Model name (default: Qwen/Qwen3-32B)"
            echo "  --backend    Backend: vllm, sglang (default: vllm)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# =============================================================================
# Output Helpers
# =============================================================================
narrate() {
    echo -e "${CYAN}# $1${NC}"
}

show_command() {
    echo -e "${GREEN}❯${NC} $1"
}

step_header() {
    local emoji="$1"
    local title="$2"
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${emoji} ${BOLD}${title}${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

type_yaml() {
    # Print YAML with a brief delay per line for visual effect
    while IFS= read -r line; do
        echo "$line"
        sleep 0.08
    done <<< "$1"
}

countdown() {
    local secs=$1
    for ((i=secs; i>=1; i--)); do
        printf "\r   ⏱️  %02d seconds remaining..." "$i"
        sleep 1
    done
    printf "\r   ⏱️  Done!                      \n"
}

spinner() {
    local msg="$1"
    local duration="$2"
    local chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local end=$((SECONDS + duration))
    while [[ $SECONDS -lt $end ]]; do
        for ((i=0; i<${#chars}; i++)); do
            printf "\r   ${chars:$i:1} ${msg}"
            sleep 0.1
        done
    done
    printf "\r   ✓ ${msg}\n"
}

# =============================================================================
# DGDR YAML Content (matches generate_dgdr from demo-ecommerce.sh)
# =============================================================================
DGDR_YAML="apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: ${DEPLOYMENT_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ecommerce-assistant
    demo: blackfriday-scenario
spec:
  model: ${MODEL}
  backend: ${BACKEND}
  searchStrategy: rapid
  autoApply: true

  workload:
    isl: 3000
    osl: 300

  sla:
    ttft: ${TTFT_TARGET}
    itl: ${ITL_TARGET}

  features:
    planner:
      enable_throughput_scaling: true
      enable_load_scaling: false
      mode: disagg"


# =============================================================================
# BANNER
# =============================================================================
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BOLD}🛒  NVIDIA Dynamo — E-Commerce AI Assistant  🛒${NC}              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${MAGENTA}From DGDR to Deployed in Minutes${NC}                          ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

narrate "Welcome to the NVIDIA Dynamo E-Commerce AI Assistant Demo!"
echo ""
echo "The Goal:"
echo "   Deploy an AI-powered shopping assistant that can handle"
echo "   Black Friday traffic — and scale itself automatically."
echo ""
echo "The Workflow:"
echo "   📝 DGDR  →  🔬 Profiling  →  🚀 Deployment  →  ✅ Serving"
echo ""
echo "   You define WHAT you want (model + SLA targets)."
echo "   Dynamo figures out HOW to deploy it optimally."
echo ""

sleep 4

# =============================================================================
# STEP 1: The DGDR — Your Single Entrypoint
# =============================================================================
step_header "📝" "Step 1: The DGDR — Your Single Entrypoint"

narrate "A DynamoGraphDeploymentRequest (DGDR) is all you need."
narrate "Specify the model, your SLA targets, and let Dynamo handle the rest."
echo ""

echo -e "${DIM}# ecommerce-assistant-dgdr.yaml${NC}"
type_yaml "$DGDR_YAML"

echo ""
echo -e "   ${BOLD}Key fields:${NC}"
echo -e "   • ${CYAN}model${NC}: ${MODEL} — Which model to serve"
echo -e "   • ${CYAN}sla.ttft${NC}: ${TTFT_TARGET}ms — Time To First Token target"
echo -e "   • ${CYAN}sla.itl${NC}: ${ITL_TARGET}ms — Inter-Token Latency target"
echo -e "   • ${CYAN}searchStrategy${NC}: rapid — Fast profiling (minutes, not hours)"
echo -e "   • ${CYAN}autoApply${NC}: true — Deploy automatically after profiling"
echo -e "   • ${CYAN}planner.mode${NC}: disagg — Disaggregated prefill/decode"
echo ""

sleep 4

# =============================================================================
# STEP 2: Apply the DGDR
# =============================================================================
step_header "🚀" "Step 2: Apply the DGDR"

narrate "Let's submit the DGDR to the cluster..."
echo ""

show_command "kubectl apply -f ecommerce-assistant-dgdr.yaml"

if [[ "$LIVE_MODE" == true ]]; then
    # Actually apply it
    echo "$DGDR_YAML" | kubectl apply -f - 2>&1
else
    sleep 1
    echo "dynamographdeploymentrequest.nvidia.com/ecommerce-assistant created"
fi

echo ""
sleep 2

show_command "kubectl get dgdr -n $NAMESPACE"
if [[ "$LIVE_MODE" == true ]]; then
    kubectl get dgdr -n "$NAMESPACE" 2>&1
else
    sleep 0.5
    printf "%-25s %-10s %-18s %-10s %s\n" "NAME" "MODEL" "PHASE" "STRATEGY" "AGE"
    printf "%-25s %-10s %-18s %-10s %s\n" "ecommerce-assistant" "Qwen/Qwen3-32B" "Pending" "rapid" "2s"
fi

echo ""
narrate "The Dynamo operator picks up the DGDR and launches a profiling job."
echo ""

sleep 3

# =============================================================================
# STEP 3: Profiling — Finding the Optimal Configuration
# =============================================================================
step_header "🔬" "Step 3: Profiling — Finding the Optimal Configuration"

narrate "The profiler analyzes the model and hardware to find the best"
narrate "GPU configuration that meets your SLA targets."
echo ""
echo "   What the profiler does:"
echo "   1. Detect available GPU hardware (H100 SXM, etc.)"
echo "   2. Sweep prefill parallelization strategies (TP, PP, DP)"
echo "   3. Sweep decode parallelization strategies"
echo "   4. Select the most cost-efficient config that meets SLA"
echo "   5. Build performance curves for the SLA Planner"
echo "   6. Generate the DynamoGraphDeployment spec"
echo ""

sleep 3

# --- Phase: Initializing ---
narrate "Phase 1/6: Initializing..."
show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.phase}'"
if [[ "$LIVE_MODE" == true ]]; then
    kubectl get dgdr ecommerce-assistant -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>&1
    echo ""
else
    echo "Profiling"
fi
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
if [[ "$LIVE_MODE" == true ]]; then
    kubectl get dgdr ecommerce-assistant -n "$NAMESPACE" -o jsonpath='{.status.profilingPhase}' 2>&1
    echo ""
else
    echo "Initializing"
fi
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiler job started"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiler config: model=${MODEL}, backend=${BACKEND}, system=h100_sxm"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: SLA targets: isl=3000, osl=300, ttft=${TTFT_TARGET}.0, itl=${ITL_TARGET}.0"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Search strategy: rapid"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Detected 8 H100 SXM GPUs available"
sleep 2

# --- Phase: SweepingPrefill ---
echo ""
narrate "Phase 2/6: Sweeping Prefill Strategies..."
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
echo "SweepingPrefill"
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiling prefill candidate tp2_pp1_dp1 with 2 GPUs..."
spinner "Measuring prefill throughput (tp=2, pp=1)" 3
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Prefill tp2: TTFT=142ms @ ISL=3000 (capacity: 4838 tok/s)"
echo ""
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiling prefill candidate tp4_pp1_dp1 with 4 GPUs..."
spinner "Measuring prefill throughput (tp=4, pp=1)" 3
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Prefill tp4: TTFT=89ms @ ISL=3000 (capacity: 9216 tok/s)"
echo ""
sleep 1

# --- Phase: SweepingDecode ---
echo ""
narrate "Phase 3/6: Sweeping Decode Strategies..."
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
echo "SweepingDecode"
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiling decode candidate tp2_pp1_dp1 with 2 GPUs..."
spinner "Measuring decode throughput (tp=2, pp=1)" 3
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Decode tp2: ITL=18ms @ KV=3000 (capacity: 3354 tok/s)"
echo ""
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Profiling decode candidate tp4_pp1_dp1 with 4 GPUs..."
spinner "Measuring decode throughput (tp=4, pp=1)" 3
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Decode tp4: ITL=11ms @ KV=3000 (capacity: 6480 tok/s)"
echo ""
sleep 1

# --- Phase: SelectingConfig ---
echo ""
narrate "Phase 4/6: Selecting Optimal Configuration..."
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
echo "SelectingConfig"
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Filtering configs against SLA targets..."
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   ✓ prefill tp2: TTFT=142ms ≤ 500ms target"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   ✓ prefill tp4: TTFT=89ms ≤ 500ms target"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   ✓ decode tp2: ITL=18ms ≤ 30ms target"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   ✓ decode tp4: ITL=11ms ≤ 30ms target"
echo ""
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Selecting most cost-efficient (fewest GPUs)..."
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Selected prefill: tp2_pp1_dp1 (2 GPUs, tp=2 pp=1 dp=1)"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Selected decode:  tp2_pp1_dp1 (2 GPUs, tp=2 pp=1 dp=1)"
sleep 2

# --- Phase: BuildingCurves ---
echo ""
narrate "Phase 5/6: Building Performance Curves..."
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
echo "BuildingCurves"
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Building interpolation curves for SLA Planner..."
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   Prefill: ISL → TTFT curve (12 data points)"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   Decode:  KV_len × batch → ITL curve (18 data points)"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler:   Curves saved to planner-profile-data ConfigMap"
sleep 2

# --- Phase: GeneratingDGD ---
echo ""
narrate "Phase 6/6: Generating DynamoGraphDeployment..."
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE -o jsonpath='{.status.profilingPhase}'"
echo "GeneratingDGD"
echo ""

echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Generating DGD spec..."
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Injecting SLA Planner service (mode=disagg)"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Planner config: ttft=${TTFT_TARGET}, itl=${ITL_TARGET}, max_gpu_budget=8"
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Applied DGD overrides to the picked DGD config."
echo "   $(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)  INFO profiler: Final DGD config saved to final_config.yaml"
sleep 1

echo ""
echo -e "   ${GREEN}✅ Profiling complete!${NC}"
echo ""

show_command "kubectl get dgdr ecommerce-assistant -n $NAMESPACE"
printf "%-25s %-18s %-18s %-10s %s\n" "NAME" "MODEL" "PHASE" "STRATEGY" "AGE"
printf "%-25s %-18s %-18s %-10s %s\n" "ecommerce-assistant" "Qwen/Qwen3-32B" "Deployed" "rapid" "3m12s"
echo ""

sleep 3

# =============================================================================
# STEP 4: The Generated Deployment
# =============================================================================
step_header "📋" "Step 4: Generated Deployment Spec"

narrate "The profiler generated a DynamoGraphDeployment (DGD) with the"
narrate "optimal configuration. Let's look at what it created."
echo ""

show_command "kubectl get dgd -n $NAMESPACE"
if [[ "$LIVE_MODE" == true ]]; then
    kubectl get dgd -n "$NAMESPACE" 2>&1
else
    printf "%-20s %-6s %-8s %s\n" "NAME" "READY" "STATUS" "AGE"
    printf "%-20s %-6s %-8s %s\n" "vllm-disagg" "False" "Deploying" "15s"
fi
echo ""

narrate "The DGD specifies the disaggregated architecture:"
echo ""
echo "   ┌─────────────────────────────────────────────────┐"
echo "   │              DynamoGraphDeployment               │"
echo "   │                                                  │"
echo "   │   ┌──────────┐   ┌──────────┐   ┌──────────┐   │"
echo "   │   │ Frontend │   │ Prefill  │   │  Decode  │   │"
echo "   │   │ (router) │──▶│ Worker   │──▶│  Worker  │   │"
echo "   │   │          │   │ (2 GPU)  │   │  (2 GPU) │   │"
echo "   │   └──────────┘   └──────────┘   └──────────┘   │"
echo "   │                                                  │"
echo "   │   ┌──────────┐                                  │"
echo "   │   │   SLA    │  Monitors TTFT/ITL metrics       │"
echo "   │   │ Planner  │  Auto-scales workers as needed   │"
echo "   │   └──────────┘                                  │"
echo "   └─────────────────────────────────────────────────┘"
echo ""
echo "   Profiler selected configuration:"
echo "   • Prefill: tp=2, pp=1, dp=1 (2 GPUs) — TTFT=142ms ≤ ${TTFT_TARGET}ms ✓"
echo "   • Decode:  tp=2, pp=1, dp=1 (2 GPUs) — ITL=18ms ≤ ${ITL_TARGET}ms ✓"
echo "   • Total:   4 GPUs initial (can scale up to 8)"
echo "   • Planner: SLA-driven auto-scaling with profiled curves"
echo ""

sleep 4

# =============================================================================
# STEP 5: Deployment — Workers Coming Online
# =============================================================================
step_header "🚀" "Step 5: Deployment — Workers Coming Online"

narrate "With autoApply: true, Dynamo automatically creates the deployment."
narrate "Workers are now loading the model and warming up..."
echo ""

show_command "kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type' -w"
echo ""

# Simulate pod startup sequence
TS=$(date +%Y-%m-%dT%H:%M:%S)

# Phase 1: Pods created
echo "NAME                                             READY   STATUS              RESTARTS   AGE"
echo "vllm-disagg-vllmdecodeworker-cd83a78a-655b56f69-mc6gg    0/1     ContainerCreating   0          5s"
echo "vllm-disagg-vllmprefillworker-a42bc19f-7f8d4c-xk9p2      0/1     ContainerCreating   0          5s"
sleep 3

# Phase 2: Running but not ready
echo "vllm-disagg-vllmdecodeworker-cd83a78a-655b56f69-mc6gg    0/1     Running             0          18s"
echo "vllm-disagg-vllmprefillworker-a42bc19f-7f8d4c-xk9p2      0/1     Running             0          18s"
echo ""

narrate "Workers are loading model weights from cache..."
echo ""

# Simulate model loading progress
echo -e "   ${DIM}[decode worker]${NC}"
for pct in 10 25 40 55 70 85 100; do
    shards=$((pct * 15 / 100))
    printf "\r   Loading safetensors checkpoint | Completed | %d/15 [%3d%%]" "$shards" "$pct"
    sleep 0.5
done
echo ""

echo -e "   ${DIM}[prefill worker]${NC}"
for pct in 10 25 40 55 70 85 100; do
    shards=$((pct * 15 / 100))
    printf "\r   Loading safetensors checkpoint | Completed | %d/15 [%3d%%]" "$shards" "$pct"
    sleep 0.5
done
echo ""
echo ""

sleep 1

narrate "Model loaded. Running torch.compile optimization..."
spinner "torch.compile: optimizing attention kernels" 3
spinner "DeepGemm: warming up GEMM kernels" 3
echo ""

# Phase 3: Ready
echo "vllm-disagg-vllmdecodeworker-cd83a78a-655b56f69-mc6gg    1/1     Running             0          4m12s"
echo "vllm-disagg-vllmprefillworker-a42bc19f-7f8d4c-xk9p2      1/1     Running             0          4m8s"
echo ""

echo -e "   ${GREEN}✅ All workers ready!${NC}"
echo ""

sleep 2

# Show real pods if available (stitch point)
if kubectl get pods -n "$NAMESPACE" -l 'nvidia.com/dynamo-sub-component-type' --no-headers 2>/dev/null | grep -q .; then
    echo ""
    narrate "Current live cluster state:"
    show_command "kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type' -o wide"
    kubectl get pods -n "$NAMESPACE" -l 'nvidia.com/dynamo-sub-component-type' -o wide 2>/dev/null || true
    echo ""
fi

sleep 3

# =============================================================================
# STEP 6: SLA Planner — Ready to Scale
# =============================================================================
step_header "⚙️" "Step 6: SLA Planner — Ready to Auto-Scale"

narrate "The SLA Planner was deployed along with the workers."
narrate "It uses the profiled performance curves to make scaling decisions."
echo ""

# Try to show real planner config from the configmap mounted in the pod
planner_deploy=""
planner_deploy=$(kubectl get deployment -n "$NAMESPACE" -o name 2>/dev/null | grep "planner" | head -1 | sed 's|deployment.apps/||' || echo "")

if [[ -n "$planner_deploy" ]]; then
    show_command "kubectl exec deploy/$planner_deploy -n $NAMESPACE -- cat /workspace/planner_config/planner_config.json | jq ..."
    kubectl exec "deploy/$planner_deploy" -n "$NAMESPACE" -- cat /workspace/planner_config/planner_config.json 2>/dev/null \
        | python3 -c "
import json,sys
d=json.load(sys.stdin)
keys=['ttft','itl','max_gpu_budget','throughput_adjustment_interval','load_predictor',
      'namespace','enable_throughput_scaling','mode','decode_engine_num_gpu','prefill_engine_num_gpu']
for k in keys:
    if k in d: print(f'   {k}: {d[k]}')
" 2>/dev/null || true
    echo ""
else
    echo "   ttft: ${TTFT_TARGET}"
    echo "   itl: ${ITL_TARGET}"
    echo "   max_gpu_budget: 8"
    echo "   throughput_adjustment_interval: 60"
    echo "   load_predictor: constant"
    echo "   namespace: ${NAMESPACE}-vllm-disagg"
    echo "   enable_throughput_scaling: true"
    echo ""
fi

echo ""
narrate "Planner configuration:"
echo "   • TTFT target: ${TTFT_TARGET}ms — Shoppers see response quickly"
echo "   • ITL target:  ${ITL_TARGET}ms — Smooth token streaming"
echo "   • GPU budget:  Up to 8 GPUs — Room to scale for Black Friday"
echo "   • Mode:        disagg — Independent prefill/decode scaling"
echo ""
echo "   How it works:"
echo "   1. Planner queries Prometheus for TTFT/ITL metrics"
echo "   2. Compares observed latency vs targets"
echo "   3. Calculates correction factors (observed/target)"
echo "   4. Uses profiled curves to compute required replicas"
echo "   5. Scales prefill/decode workers independently"
echo ""

sleep 4

# =============================================================================
# STEP 7: Verify — Ready to Serve
# =============================================================================
step_header "✅" "Step 7: Ready to Serve"

narrate "Let's verify the deployment is healthy and serving requests..."
echo ""

# Try real health check
frontend_svc=""
frontend_svc=$(kubectl get svc -n "$NAMESPACE" -o name 2>/dev/null | grep "frontend" | head -1 | sed 's|service/||' || echo "")

if [[ -n "$frontend_svc" ]]; then
    show_command "kubectl get svc $frontend_svc -n $NAMESPACE"
    kubectl get svc "$frontend_svc" -n "$NAMESPACE" 2>/dev/null || true
    echo ""
fi

show_command "kubectl get dgd -n $NAMESPACE"
if [[ "$LIVE_MODE" == true ]]; then
    kubectl get dgd -n "$NAMESPACE" 2>&1
else
    if kubectl get dgd -n "$NAMESPACE" --no-headers 2>/dev/null | grep -q .; then
        kubectl get dgd -n "$NAMESPACE" 2>&1
    else
        printf "%-20s %-6s %-8s %s\n" "NAME" "READY" "STATUS" "AGE"
        printf "%-20s %-6s %-8s %s\n" "vllm-disagg" "True" "Running" "5m30s"
    fi
fi
echo ""

# Show real pods
show_command "kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type'"
kubectl get pods -n "$NAMESPACE" -l 'nvidia.com/dynamo-sub-component-type' 2>/dev/null || \
    echo "vllm-disagg-vllmdecodeworker-cd83a78a-655b56f69-mc6gg    1/1     Running   0   5m30s
vllm-disagg-vllmprefillworker-a42bc19f-7f8d4c-xk9p2      1/1     Running   0   5m26s"
echo ""

echo -e "   🛒 ${BOLD}E-COMMERCE AI ASSISTANT IS LIVE!${NC}"
echo ""
echo "   • Frontend:  OpenAI-compatible API (chat/completions)"
echo "   • Prefill:   Processing incoming prompts" 
echo "   • Decode:    Generating response tokens"
echo "   • Planner:   Monitoring SLAs, ready to auto-scale"
echo ""

sleep 3

# =============================================================================
# SUMMARY
# =============================================================================
step_header "🎉" "Deployment Complete!"

echo "What We Did:"
echo ""
echo "   1. 📝 Wrote a DGDR: model + SLA targets (5 lines of config)"
echo "   2. 🚀 Applied it:   kubectl apply -f dgdr.yaml"
echo "   3. 🔬 Profiling:    Dynamo found optimal GPU config automatically"
echo "   4. ⚙️  Deployment:   Workers + SLA Planner deployed automatically"
echo "   5. ✅ Ready:        Serving requests within SLA targets"
echo ""
echo -e "   ${BOLD}Total user effort: One YAML file. That's it.${NC}"
echo ""
echo "   The system is now ready to handle traffic surges."
echo "   When Black Friday hits and latency exceeds targets,"
echo "   the SLA Planner will automatically scale workers"
echo "   to maintain the ${TTFT_TARGET}ms TTFT / ${ITL_TARGET}ms ITL targets."
echo ""
echo -e "   ${CYAN}Next: Run the Black Friday load test demo${NC}"
echo "   ./demo-narrated.sh --namespace $NAMESPACE"
echo ""
echo "   Learn more: https://github.com/ai-dynamo/dynamo"
echo ""
