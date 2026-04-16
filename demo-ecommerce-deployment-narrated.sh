#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# ============================================================================
# Narrated E-Commerce DGDR → Full Deployment Demo for NVIDIA Dynamo
# ============================================================================
#
# Live demo script that applies a real DGDR with autoApply: true and watches
# the entire lifecycle in real-time:
#
#   DGDR → Profiling → DGD Generated → Pods Deploying → Model Loaded → Serving
#
# The script uses a "watch" style display that refreshes kubectl output with
# a ticking age column, so the audience sees the system progressing live.
#
# At the end, it port-forwards to the frontend and sends a streaming
# chat/completions request to prove the model is live and serving.
#
# Requirements:
#   - Kubernetes cluster with the Dynamo operator installed
#   - Sufficient GPUs for the model
#   - kubectl, python3, curl available locally
#
# Usage:
#   ./demo-ecommerce-deployment-narrated.sh --namespace dynamo-system
#   ./demo-ecommerce-deployment-narrated.sh --namespace dynamo-system --model Qwen/Qwen3-32B
#   ./demo-ecommerce-deployment-narrated.sh --no-cleanup   # keep DGDR after demo
#
set -e

# =============================================================================
# Configuration
# =============================================================================
NAMESPACE="${NAMESPACE:-dynamo-system}"
MODEL="${MODEL:-Qwen/Qwen3-32B}"
BACKEND="${BACKEND:-vllm}"
TTFT_TARGET=500
ITL_TARGET=30
DGDR_NAME="ecommerce-shopping-assistant"
AUTO_APPLY="${AUTO_APPLY:-true}"
DO_CLEANUP=true
PORT_FORWARD_PORT=8000

# Neofetch-style cluster info display (shown after banner if non-empty).
# Each entry is "Key: Value". Add, remove, or reorder freely.
# Pass --cluster-info "Key: Value" repeatedly to build the list from CLI,
# or set this array directly in the script.
# NOTE: Rebuilt after argument parsing so --model/--dgdr-file values are reflected.
CLUSTER_FETCH_TEMPLATE=true
CLUSTER_FETCH_EXTRA=()

# Model cache PVC configuration
PVC_NAME="${PVC_NAME:-model-cache}"
PVC_MOUNT_PATH="${PVC_MOUNT_PATH:-/home/dynamo/.cache/huggingface}"
PVC_MODEL_PATH="${PVC_MODEL_PATH:-hub/models--Qwen--Qwen3-32B/snapshots/9216db5781bf21249d130ec9da846c4624c16137}"
DGDR_IMAGE="${DGDR_IMAGE:-}"
DGDR_FILE=""

# Colors and styles
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
WHITE='\033[1;37m'
BLUE='\033[1;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Narration color — cyan, matching 769101.cast reference style
NAR='\033[0;36m'

# =============================================================================
# Parse Arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace)       NAMESPACE="$2"; shift 2 ;;
        --model)           MODEL="$2"; shift 2 ;;
        --backend)         BACKEND="$2"; shift 2 ;;
        --dgdr-name)       DGDR_NAME="$2"; shift 2 ;;
        --ttft)            TTFT_TARGET="$2"; shift 2 ;;
        --itl)             ITL_TARGET="$2"; shift 2 ;;
        --port)            PORT_FORWARD_PORT="$2"; shift 2 ;;
        --pvc-name)        PVC_NAME="$2"; shift 2 ;;
        --pvc-mount-path)  PVC_MOUNT_PATH="$2"; shift 2 ;;
        --pvc-model-path)  PVC_MODEL_PATH="$2"; shift 2 ;;
        --image)           DGDR_IMAGE="$2"; shift 2 ;;
        --dgdr-file)       DGDR_FILE="$2"; shift 2 ;;
        --cluster-info)    CLUSTER_FETCH_EXTRA+=("$2"); CLUSTER_FETCH_TEMPLATE=false; shift 2 ;;
        --no-cleanup)      DO_CLEANUP=false; shift ;;
        --auto-apply)      AUTO_APPLY=true; shift ;;
        --no-auto-apply)   AUTO_APPLY=false; shift ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Live narrated demo: DGDR -> Profiling -> Deployment -> Serving"
            echo ""
            echo "Options:"
            echo "  --namespace NS     Kubernetes namespace (default: dynamo-system)"
            echo "  --model MODEL      Model name (default: Qwen/Qwen3-32B)"
            echo "  --backend BE       Backend: vllm, sglang (default: vllm)"
            echo "  --dgdr-name NAME   DGDR resource name (default: ecommerce-shopping-assistant)"
            echo "  --ttft MS          TTFT SLA target in ms (default: 500)"
            echo "  --itl MS           ITL SLA target in ms (default: 30)"
            echo "  --port PORT        Local port for port-forward (default: 8000)"
            echo "  --pvc-name NAME    PVC name for model cache (default: model-cache)"
            echo "  --pvc-mount-path P Mount path for model cache PVC (default: /home/dynamo/.cache/huggingface)"
            echo "  --pvc-model-path P Relative model path inside PVC (default: hub/models--Qwen--Qwen3-32B/snapshots/...)"
            echo "  --image IMAGE      Container image override for DGDR"
            echo "  --dgdr-file FILE   Use a pre-built DGDR YAML file instead of constructing one."
            echo "                       Overrides --model, --backend, --dgdr-name, --pvc-*, etc."
            echo "                       The script extracts metadata.name and spec.model from the file."
            echo "  --auto-apply       Auto-deploy after profiling (default)"
            echo "  --no-auto-apply    Skip auto-deploy; profiling only"
            echo "  --cluster-info STR Neofetch info line (repeatable). e.g.:"
            echo "                       --cluster-info 'Platform: Azure AKS'"
            echo "                       --cluster-info 'GPUs: 32× H100 80GB'"
            echo "  --no-cleanup       Don't delete DGDR on exit"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# =============================================================================
# Output Helpers
# =============================================================================
narrate() {
    echo -e "\n${NAR}# $1${NC}"
}

show_command() {
    echo -e "${GREEN}❯${NC} $1"
}

step_header() {
    local emoji="$1"
    local title="$2"
    echo ""
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${MAGENTA}${BOLD}${emoji} ${title}${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

type_yaml() {
    while IFS= read -r line; do
        echo "$line"
        sleep 0.06
    done <<< "$1"
}

# Pause between sections for readability
pause() {
    sleep "${1:-2}"
}

# =============================================================================
# Cleanup
# =============================================================================
PF_PID=""

cleanup() {
    # Kill port-forward if running
    if [[ -n "$PF_PID" ]]; then
        kill "$PF_PID" 2>/dev/null || true
        wait "$PF_PID" 2>/dev/null || true
    fi

    if [[ "$DO_CLEANUP" == true ]]; then
        echo ""
        echo -e "${DIM}# Cleaning up DGDR (${DGDR_NAME})...${NC}"
        kubectl delete dgdr "$DGDR_NAME" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
    else
        echo ""
        echo -e "${DIM}# DGDR '${DGDR_NAME}' left in place (--no-cleanup).${NC}"
    fi
}
trap cleanup EXIT

# =============================================================================
# Preflight checks
# =============================================================================
for tool in kubectl python3 curl; do
    if ! command -v "$tool" &>/dev/null; then
        echo -e "${RED}Error: $tool is required but not found in PATH.${NC}"
        exit 1
    fi
done

if ! kubectl get crd dynamographdeploymentrequests.nvidia.com &>/dev/null; then
    echo -e "${RED}Error: Dynamo operator CRDs not found. Is the operator installed?${NC}"
    exit 1
fi

# =============================================================================
# DGDR YAML
# =============================================================================
if [[ -n "$DGDR_FILE" ]]; then
    if [[ ! -f "$DGDR_FILE" ]]; then
        echo -e "${RED}Error: DGDR file not found: ${DGDR_FILE}${NC}"
        exit 1
    fi
    DGDR_YAML="$(cat "$DGDR_FILE")"
    # Extract metadata.name and spec.model from the file for display/cleanup
    DGDR_NAME=$(python3 -c "
import yaml, sys
doc = yaml.safe_load(sys.stdin)
print(doc.get('metadata',{}).get('name',''))
" <<< "$DGDR_YAML")
    MODEL=$(python3 -c "
import yaml, sys
doc = yaml.safe_load(sys.stdin)
print(doc.get('spec',{}).get('model',''))
" <<< "$DGDR_YAML")
    BACKEND=$(python3 -c "
import yaml, sys
doc = yaml.safe_load(sys.stdin)
print(doc.get('spec',{}).get('backend',''))
" <<< "$DGDR_YAML")
    echo -e "${DIM}# Using DGDR from file: ${DGDR_FILE}${NC}"
    echo -e "${DIM}#   name=${DGDR_NAME}  model=${MODEL}  backend=${BACKEND}${NC}"
else
    DGDR_YAML="apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: ${DGDR_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: ecommerce-shopping-assistant
    demo: deployment-narrated
spec:
  model: ${MODEL}
  backend: ${BACKEND}
  searchStrategy: rapid
  autoApply: ${AUTO_APPLY}
$( [[ -n "$DGDR_IMAGE" ]] && echo "  image: ${DGDR_IMAGE}" || true )

  modelCache:
    pvcName: ${PVC_NAME}
    pvcMountPath: ${PVC_MOUNT_PATH}
    pvcModelPath: ${PVC_MODEL_PATH}

  workload:
    isl: 3000
    osl: 300

  sla:
    ttft: ${TTFT_TARGET}
    itl: ${ITL_TARGET}

  features:
    planner:
      enable_throughput_scaling: true
      enable_load_scaling: true
      max_gpu_budget: 32
      mode: disagg"
fi

# Rebuild CLUSTER_FETCH now that MODEL/BACKEND are finalized (--dgdr-file may have changed them)
if [[ "$CLUSTER_FETCH_TEMPLATE" == true ]]; then
    CLUSTER_FETCH=(
        "dynamo@aks-ndh100-cluster"
        "---"
        "Model: ${MODEL}"
        "Platform: Azure Kubernetes Service (AKS)"
        "K8s: v1.31 · NVIDIA GPU Operator"
        "Nodes: 4× Standard_ND_H100_v5"
        "GPUs: 32× NVIDIA H100 80GB SXM"
        "VRAM: 2,560 GB total"
        "Storage: Azure Managed Lustre (AMLFS)"
        "Backend: ${BACKEND} (disaggregated prefill/decode)"
        "SLA: TTFT ≤ ${TTFT_TARGET}ms · ITL ≤ ${ITL_TARGET}ms"
    )
else
    CLUSTER_FETCH=("${CLUSTER_FETCH_EXTRA[@]}")
fi

# =============================================================================
# BANNER
# =============================================================================
clear
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${BOLD}🛒  NVIDIA Dynamo — One YAML to Production  🛒${NC}                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${MAGENTA}kubectl apply → Go make coffee → It's live${NC}                 ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# =============================================================================
# Neofetch-style cluster info (optional)
# =============================================================================
if [[ ${#CLUSTER_FETCH[@]} -gt 0 ]]; then
    pause 2

    # Azure logo with 256-color escapes (each line is 34 visible chars)
    LOGO_LINES=(
        "                                  "
        $'\033[38;5;25m          ,gp\033[38;5;45m9@@@B>"\033[38;5;81mgGg           \033[0m'
        $'\033[38;5;25m          @@@\\\033[38;5;81m~@g@@@@@@A          \033[0m'
        $'\033[38;5;25m         g@@@@\033[38;5;81m\'@@@@B>"\033[38;5;74m_~,         \033[0m'
        $'\033[38;5;25m        /@@@@@@\033[38;5;81m"\033[38;5;38m~\033[38;5;74m_g@@@@@@         \033[0m'
        $'\033[38;5;25m        @@@@@@@p\033[38;5;74m@@@@@@@@@@        \033[0m'
        $'\033[38;5;25m       @@@@@@@@@,\033[38;5;74m@@@@@@@@@\\       \033[0m'
        $'\033[38;5;25m      {@@@@@@@@@ \033[38;5;74m\'@@@@P"\033[38;5;39m~@@       \033[0m'
        $'\033[38;5;25m     ,@@@@@@@@@\'  \033[38;5;39m,g@@@@@@@@      \033[0m'
        $'\033[38;5;25m     @@@@@@@@@F    \033[38;5;39m@@@@@@@@@A     \033[0m'
        $'\033[38;5;25m    g@@@@B\033[38;5;32m%@@@@@@l\033[38;5;25m@\033[38;5;39m\'@@@@@@@P"\033[38;5;6m,    \033[0m'
        $'\033[38;5;25m   ;@@@@@@@g\033[38;5;32m<@@@@@\033[38;5;25m\'@\033[38;5;39mTB>\033[38;5;6m_g@@@@@    \033[0m'
        $'\033[38;5;25m   @@@@@@@@@? \033[38;5;32m"@@@@\033[38;5;25mVg\033[38;5;6m@@@@@@@@@@   \033[0m'
        $'\033[38;5;25m  @@@@@@@@@W    \033[38;5;32m`Q@h\033[38;5;25m@,\033[38;5;6m@@@@@@@@@\\  \033[0m'
        $'\033[38;5;25m  8BBBBBBBD        \033[38;5;32m%.\033[38;5;25mF\033[38;5;6mBBBBBBBBBP  \033[0m'
        "                                  "
    )
    LOGO_WIDTH=34

    # Render: logo on left, info on right
    local_logo_count=${#LOGO_LINES[@]}
    local_info_count=${#CLUSTER_FETCH[@]}
    # Info starts a few lines down to vertically center against the logo
    local_info_offset=2
    local_max=$((local_logo_count > (local_info_count + local_info_offset) ? local_logo_count : (local_info_count + local_info_offset)))

    echo ""
    for ((i=0; i<local_max; i++)); do
        # Left side: logo line (or blank padding)
        if [[ $i -lt $local_logo_count ]]; then
            logo_line="${LOGO_LINES[$i]}"
        else
            logo_line="$(printf '%*s' "$LOGO_WIDTH" '')"
        fi

        # Right side: info line (offset down to center)
        local_info_idx=$((i - local_info_offset))
        if [[ $local_info_idx -ge 0 && $local_info_idx -lt $local_info_count ]]; then
            info="${CLUSTER_FETCH[$local_info_idx]}"
            if [[ "$info" == "---" ]]; then
                info_formatted="${DIM}────────────────────────────────────────────────────${NC}"
            elif [[ "$info" == *"@"* && "$local_info_idx" -eq 0 ]]; then
                # Title line: neofetch-style user@host in bold blue
                local_user="${info%%@*}"
                local_host="${info#*@}"
                info_formatted="${BLUE}${local_user}${NC}@${BLUE}${local_host}${NC}"
            elif [[ "$info" == *": "* ]]; then
                info_key="${info%%: *}"
                info_val="${info#*: }"
                info_formatted="${BLUE}${info_key}${NC}: ${info_val}"
            else
                info_formatted="${BOLD}${WHITE}${info}${NC}"
            fi
        else
            info_formatted=""
        fi

        printf "   "
        echo -e -n "$logo_line"
        printf "  "
        echo -e "$info_formatted"
    done
    echo ""

    pause 4
fi

pause 3

narrate "Welcome! This demo shows the complete Dynamo deployment lifecycle."
echo ""
echo "   You do ONE thing:  kubectl apply -f dgdr.yaml"
echo "   Dynamo does the rest:"
echo ""
echo "   📝 DGDR  →  🔬 Profile  →  📋 Generate DGD  →  🚀 Deploy  →  ✅ Serve"
echo ""
echo "   Go make coffee. Come back to a production-ready model."

pause 5


# =============================================================================
# STEP 1: The DGDR — Your Single Entrypoint
# =============================================================================
step_header "📝" "Step 1: The DGDR — Your Only Input"

narrate "This is the entire config you need to write."
narrate "Model + SLA targets + strategy. That's it."
echo ""

pause 2

echo -e "${DIM}# ecommerce-shopping-assistant-dgdr.yaml${NC}"
type_yaml "$DGDR_YAML"

pause 3

echo ""
echo -e "   ${BOLD}What you're telling Dynamo:${NC}"
echo -e "   - ${CYAN}model${NC}: ${MODEL}"
echo -e "   - ${CYAN}sla.ttft${NC}: ${TTFT_TARGET}ms - First token in under half a second"
echo -e "   - ${CYAN}sla.itl${NC}: ${ITL_TARGET}ms - Smooth ${ITL_TARGET}ms streaming"
echo -e "   - ${CYAN}searchStrategy${NC}: rapid - Fast profiling"
echo -e "   - ${CYAN}autoApply${NC}: ${AUTO_APPLY} - $(if [[ "$AUTO_APPLY" == "true" ]]; then echo "Deploy automatically when profiling is done"; else echo "Profiling only, manual approval required"; fi)"
echo -e "   - ${CYAN}modelCache${NC}: Pre-downloaded model from PVC (no HuggingFace at deploy time)"
echo -e "   - ${CYAN}planner.mode${NC}: disagg - Separate prefill/decode scaling"

pause 3

echo ""
echo -e "   ${BOLD}What you're NOT writing:${NC}"
echo "   - No Dockerfiles, no Helm charts, no resource calculations"
echo "   - No TP/PP/DP parallelism config - the profiler finds it"
echo "   - No replica counts - the SLA planner handles scaling"

pause 5


# =============================================================================
# STEP 2: Apply — The Only kubectl You'll Run
# =============================================================================
step_header "🚀" "Step 2: Apply — The Only kubectl You'll Run"

narrate "One command. Then you're done."
echo ""

pause 2

show_command "kubectl apply -f ecommerce-shopping-assistant-dgdr.yaml"
echo "$DGDR_YAML" | kubectl apply -f - 2>&1

pause 3

echo ""
show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE}"
kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" 2>&1

pause 2

narrate "DGDR accepted. The operator takes it from here."
narrate "You could close your laptop now. But let's watch what happens."

pause 4


# =============================================================================
# STEP 3: Profiling — Dynamo Finds the Optimal Config
# =============================================================================
step_header "🔬" "Step 3: Profiling — Dynamo Finds the Optimal Config"

narrate "The profiler is analyzing ${MODEL} on your GPUs."
narrate "It sweeps parallelization strategies and picks the cheapest"
narrate "config that meets your SLA targets."
echo ""

pause 2

echo "   What's happening behind the scenes:"
echo "   1. Detect GPU hardware (H100 SXM, etc.)"
echo "   2. Sweep prefill parallelization (TP, PP, DP)"
echo "   3. Sweep decode parallelization"
echo "   4. Select most cost-efficient config meeting SLA"
echo "   5. Build performance curves for the SLA planner"
echo "   6. Generate the full DynamoGraphDeployment spec"

pause 4

narrate "Watching profiling progress (live)..."
echo ""

# ---------------------------------------------------------------------------
# Poll DGDR status with in-place refresh (watch style)
# ---------------------------------------------------------------------------
PROFILING_MAX_WAIT=900
profiling_elapsed=0
last_profiling_phase=""
profiler_pod=""
_watch_first_iteration=true

while [[ $profiling_elapsed -lt $PROFILING_MAX_WAIT ]]; do
    phase=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Pending")
    profiling_phase=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.profilingPhase}' 2>/dev/null || echo "")

    # Clear screen and redraw (watch style)
    if [[ "$_watch_first_iteration" != true ]]; then
        printf '\e[H\e[2J'
    fi
    _watch_first_iteration=false

    echo -e "${BOLD}  🔬 Step 3: Profiling — Dynamo Finds the Optimal Config${NC}"
    echo -e "  ${DIM}Every 5s · ${profiling_elapsed}s elapsed${NC}"
    echo ""

    # --- Watch header ---

    # Show the DGDR table
    show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE}"
    kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" 2>/dev/null || true
    echo ""

    # Track phase transitions
    if [[ "$profiling_phase" != "$last_profiling_phase" && -n "$profiling_phase" ]]; then
        last_profiling_phase="$profiling_phase"
    fi

    # Show current phase status (always visible, updates in-place)
    case "$last_profiling_phase" in
        "Initializing")
            echo -e "   ${MAGENTA}▸ Initializing${NC} — Detecting hardware, resolving model architecture..."
            ;;
        "SweepingPrefill")
            echo -e "   ${MAGENTA}▸ Sweeping Prefill${NC} — Testing TP/PP combinations for prefill latency..."
            if [[ -z "$profiler_pod" ]]; then
                profiler_pod=$(kubectl get pods -n "$NAMESPACE" -l "job-name=profile-${DGDR_NAME}" --no-headers 2>/dev/null \
                    | awk '{print $1}' | head -1)
            fi
            if [[ -n "$profiler_pod" ]]; then
                kubectl logs "$profiler_pod" -c profiler -n "$NAMESPACE" --tail=3 2>/dev/null \
                    | grep -iE "prefill|sweep|candidate|TTFT" | tail -2 \
                    | sed 's/^/      /' || true
            fi
            ;;
        "SweepingDecode")
            echo -e "   ${MAGENTA}▸ Sweeping Decode${NC} — Testing parallelization for decode throughput..."
            if [[ -n "$profiler_pod" ]]; then
                kubectl logs "$profiler_pod" -c profiler -n "$NAMESPACE" --tail=3 2>/dev/null \
                    | grep -iE "decode|sweep|candidate|ITL" | tail -2 \
                    | sed 's/^/      /' || true
            fi
            ;;
        "SelectingConfig")
            echo -e "   ${MAGENTA}▸ Selecting Config${NC} — Filtering candidates against SLA, picking cheapest..."
            if [[ -n "$profiler_pod" ]]; then
                kubectl logs "$profiler_pod" -c profiler -n "$NAMESPACE" --tail=5 2>/dev/null \
                    | grep -iE "select|pick|cost|filter|config|SLA" | tail -3 \
                    | sed 's/^/      /' || true
            fi
            ;;
        "BuildingCurves")
            echo -e "   ${MAGENTA}▸ Building Curves${NC} — Creating performance interpolation data for planner..."
            if [[ -n "$profiler_pod" ]]; then
                kubectl logs "$profiler_pod" -c profiler -n "$NAMESPACE" --tail=3 2>/dev/null \
                    | grep -iE "interpolat|curve|planner|profile" | tail -2 \
                    | sed 's/^/      /' || true
            fi
            ;;
        "GeneratingDGD")
            echo -e "   ${MAGENTA}▸ Generating DGD${NC} — Building the full deployment spec..."
            if [[ -n "$profiler_pod" ]]; then
                kubectl logs "$profiler_pod" -c profiler -n "$NAMESPACE" --tail=3 2>/dev/null \
                    | grep -iE "DGD|final_config|planner|inject" | tail -2 \
                    | sed 's/^/      /' || true
            fi
            ;;
        "Done")
            echo -e "   ${GREEN}✅ Profiling complete!${NC}"
            ;;
    esac

    # Check for profiling completion — operator moves to Deploying or Ready
    if [[ "$phase" == "Deploying" || "$phase" == "Deployed" || "$phase" == "Ready" ]]; then
        echo ""
        echo -e "   ${GREEN}✅ Profiling complete - phase is now: ${BOLD}${phase}${NC}"
        echo ""
        break
    fi

    if [[ "$phase" == "Failed" ]]; then
        echo ""
        echo -e "   ${RED}❌ DGDR failed!${NC}"
        kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.conditions}' 2>/dev/null \
            | python3 -c "
import json,sys
try:
    for c in json.loads(sys.stdin.read()):
        if c.get('status')!='True' or c.get('type')=='Succeeded':
            print(f\"   {c.get('type','?')}: {c.get('message','?')}\")
except: pass
" 2>/dev/null || true
        exit 1
    fi

    sleep 5
    profiling_elapsed=$((profiling_elapsed + 5))
done

if [[ $profiling_elapsed -ge $PROFILING_MAX_WAIT ]]; then
    echo -e "${YELLOW}Profiling still running after ${PROFILING_MAX_WAIT}s - continuing to watch...${NC}"
fi

pause 3


# =============================================================================
# STEP 4: The Generated DGD — What Dynamo Built For You
# =============================================================================
step_header "📋" "Step 4: The Generated DGD — What Dynamo Built For You"

narrate "The profiler generated a complete DynamoGraphDeployment."
narrate "This is the spec Dynamo is deploying - you didn't write any of it."
echo ""

pause 2

# Get the DGD name
DGD_NAME=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.dgdName}' 2>/dev/null || echo "")

if [[ -n "$DGD_NAME" ]]; then
    show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE} -o jsonpath='{.status.profilingResults.selectedConfig}'"
    echo ""

    # Extract and display trimmed DGD spec from profilingResults
    kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" \
        -o jsonpath='{.status.profilingResults.selectedConfig}' 2>/dev/null \
        | python3 -c "
import json, sys

try:
    dgd = json.loads(sys.stdin.read())

    spec = dgd.get('spec', {})
    services = spec.get('services', {})

    print(f'   apiVersion:  {dgd.get(\"apiVersion\",\"?\")}')
    print(f'   kind:        DynamoGraphDeployment')
    print(f'   name:        {dgd.get(\"metadata\",{}).get(\"name\",\"?\")}')
    bf = spec.get('backendFramework', '')
    if bf:
        print(f'   backend:     {bf}')
    print()
    print('   services:')
    hdr = '   {:<40s} {:>10s} {:>6s} {:>10s}'.format('Service', 'Replicas', 'GPUs', 'Type')
    sep = '   {:<40s} {:>10s} {:>6s} {:>10s}'.format('-'*40, '-'*10, '-'*6, '-'*10)
    print(hdr)
    print(sep)

    for svc_name, svc in services.items():
        ct = svc.get('componentType', '')
        sct = svc.get('subComponentType', '')
        replicas = svc.get('replicas', '?')
        gpu = svc.get('resources', {}).get('limits', {}).get('gpu', '-') if svc.get('resources') else '-'

        label = svc_name
        if sct:
            label += ' (' + sct + ')'

        print('   {:<40s} {:>10s} {:>6s} {:>10s}'.format(label, str(replicas), str(gpu), ct))

    print()
    total_gpu = 0
    for svc in services.values():
        r = svc.get('replicas', 0) or 0
        g = int(svc.get('resources', {}).get('limits', {}).get('gpu', '0') or '0')
        total_gpu += r * g
    if total_gpu > 0:
        print(f'   Total GPU allocation: {total_gpu} GPUs')
except Exception as e:
    print(f'   (Could not parse DGD spec: {e})')
" 2>/dev/null || echo "   (DGD spec not yet available)"

else
    echo -e "${DIM}# DGD name not available yet - profiler may still be writing results.${NC}"
    DGD_NAME=$(kubectl get dgd -n "$NAMESPACE" --no-headers -o custom-columns=':metadata.name' 2>/dev/null | head -1)
fi

pause 3

echo ""
echo "   +---------------------------------------------------------+"
echo "   |              DynamoGraphDeployment                      |"
echo "   |                                                         |"
echo "   |   +----------+    +----------+    +----------+         |"
echo "   |   | Frontend |    | Prefill  |    |  Decode  |         |"
echo "   |   | (router) |--->| Worker   |--->|  Worker  |         |"
echo "   |   +----------+    +----------+    +----------+         |"
echo "   |                                                         |"
echo "   |   +----------+                                         |"
echo "   |   |   SLA    |  Watches TTFT/ITL, auto-scales          |"
echo "   |   | Planner  |  prefill & decode independently         |"
echo "   |   +----------+                                         |"
echo "   +---------------------------------------------------------+"

pause 3

if [[ "$AUTO_APPLY" == "true" ]]; then
    narrate "With autoApply: true, Dynamo is already deploying this."
    narrate "You didn't have to review or approve anything."
else
    narrate "With autoApply: false, Dynamo profiled and generated the DGD."
    narrate "You can review the spec, then manually apply it when ready."
fi

pause 5


# =============================================================================
# STEP 5+: Deployment — Only when autoApply is true
# =============================================================================
if [[ "$AUTO_APPLY" == "false" ]]; then
    # --- autoApply: false — show the generated DGD and stop ---
    step_header "📋" "Step 5: Review — Profiling Complete"

    narrate "Profiling is done. The DGD spec has been generated."
    narrate "With autoApply: false, you review and deploy when ready."
    echo ""

    pause 2

    if [[ -n "$DGD_NAME" ]]; then
        show_command "kubectl get dgd ${DGD_NAME} -n ${NAMESPACE} -o yaml"
        kubectl get dgd "$DGD_NAME" -n "$NAMESPACE" -o yaml 2>/dev/null | head -80 || true
    else
        narrate "DGD name not yet available. Check with:"
        show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE} -o jsonpath='{.status.dgdName}'"
    fi

    pause 3

    echo ""
    echo -e "   ${BOLD}To deploy manually:${NC}"
    if [[ -n "$DGD_NAME" ]]; then
        echo -e "   kubectl patch dgdr ${DGDR_NAME} -n ${NAMESPACE} --type merge -p '{\"spec\":{\"autoApply\":true}}'"
    fi
    echo ""
    echo -e "   ${BOLD}Or apply the DGD directly:${NC}"
    echo -e "   kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE} -o jsonpath='{.status.profilingResults.selectedConfig}' | kubectl apply -f -"

    pause 5

    # --- Summary for autoApply: false ---
    step_header "🎉" "Done — Profiling Complete"

    echo ""
    echo -e "   What ${BOLD}you${NC} did:"
    echo ""
    echo "   1. Wrote a DGDR (model name + SLA targets)"
    echo "   2. kubectl apply"
    echo ""
    echo "   That's it. Two steps."

    pause 3

    echo ""
    echo -e "   What ${BOLD}Dynamo${NC} did:"
    echo ""
    echo "   1. 🔬 Profiled the model on your GPUs"
    echo "   2. 📋 Generated optimal DGD (TP, PP, replicas, planner curves)"
    echo "   3. ⏸️  Waiting for your approval to deploy"

    pause 3

    echo ""
    echo -e "   ${BOLD}Next: review the DGD, then set autoApply: true to deploy. ☕${NC}"
    echo ""
    echo -e "   ${DIM}TTFT target: ${TTFT_TARGET}ms  |  ITL target: ${ITL_TARGET}ms  |  Mode: disagg${NC}"
    echo ""

    if [[ "$DO_CLEANUP" == true ]]; then
        echo -e "   ${DIM}(DGDR will be cleaned up on exit. Use --no-cleanup to keep it.)${NC}"
    else
        echo -e "   ${DIM}(DGDR '${DGDR_NAME}' left in place for further exploration.)${NC}"
    fi

    echo ""
    echo "   Learn more: https://github.com/ai-dynamo/dynamo"
    echo ""
    exit 0
fi


# =============================================================================
# STEP 5: Deployment — Watching Workers Come Online
# =============================================================================
step_header "🚀" "Step 5: Deployment — Watching Workers Come Online"

narrate "Dynamo created the DGD and is spinning up workers."
narrate "Let's watch the pods come online and load the model."
echo ""

pause 2

echo "   Startup time breakdown:"
echo -e "   ${GREEN}•${NC} Model download from HuggingFace (if not cached): 1-3 min"
echo -e "   ${GREEN}•${NC} torch.compile optimization: 30-60 sec"
echo -e "   ${GREEN}•${NC} DeepGemm kernel warmup: 1-2 min"
echo ""

pause 3

# ---------------------------------------------------------------------------
# Watch DGDR phase transition through Deploying -> Deployed
# Uses clear-screen for in-place refresh (watch style)
# ---------------------------------------------------------------------------
DEPLOY_MAX_WAIT=900
deploy_elapsed=0

# Determine the DGD name if we don't have it yet
if [[ -z "$DGD_NAME" ]]; then
    DGD_NAME=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.dgdName}' 2>/dev/null || echo "")
fi

# Clear screen for watch-style refresh
_deploy_first_iteration=true

while [[ $deploy_elapsed -lt $DEPLOY_MAX_WAIT ]]; do
    dgdr_phase=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")

    # Clear screen and redraw (watch style)
    if [[ "$_deploy_first_iteration" != true ]]; then
        printf '\e[H\e[2J'
    fi
    _deploy_first_iteration=false

    echo -e "${BOLD}  🚀 Step 5: Deployment — Watching Workers Come Online${NC}"
    echo -e "  ${DIM}Every 5s · ${deploy_elapsed}s elapsed${NC}"
    echo ""

    # --- DGDR status ---
    show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE}"
    kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" 2>/dev/null || true
    echo ""

    # --- DGD status ---
    if [[ -n "$DGD_NAME" ]]; then
        show_command "kubectl get dgd ${DGD_NAME} -n ${NAMESPACE}"
        kubectl get dgd "$DGD_NAME" -n "$NAMESPACE" 2>/dev/null || true
        echo ""
    fi

    # --- Pod status ---
    pod_label=""
    if [[ -n "$DGD_NAME" ]]; then
        pod_label="nvidia.com/dynamo-graph-deployment-name=${DGD_NAME}"
    else
        pod_label="nvidia.com/dynamo-component-pod=true"
    fi

    show_command "kubectl get pods -n ${NAMESPACE} -l '${pod_label}'"
    pod_output=$(kubectl get pods -n "$NAMESPACE" -l "$pod_label" --no-headers 2>/dev/null || echo "")
    if [[ -n "$pod_output" ]]; then
        kubectl get pods -n "$NAMESPACE" -l "$pod_label" 2>/dev/null || true
    else
        echo "   No pods found yet..."
    fi
    echo ""

    # --- Status summary ---
    if [[ -n "$pod_output" ]]; then
        total_pods=$(echo "$pod_output" | wc -l | tr -d ' ')
        ready_pods=$(echo "$pod_output" | grep -c "1/1" || echo 0)
        echo -e "   ${CYAN}Pods: ${BOLD}${ready_pods}/${total_pods} ready${NC}"

        # --- Current state description ---
        if echo "$pod_output" | grep -q "ContainerCreating"; then
            echo -e "   ${MAGENTA}▸ Containers creating${NC} — Pulling images, mounting volumes..."
        elif echo "$pod_output" | grep -q "Init:"; then
            echo -e "   ${MAGENTA}▸ Init containers running${NC} — Setting up environment..."
        elif echo "$pod_output" | grep -q "PodInitializing"; then
            echo -e "   ${MAGENTA}▸ Pod initializing${NC} — Main container starting..."
        elif [[ "$ready_pods" -lt "$total_pods" ]]; then
            echo -e "   ${MAGENTA}▸ Workers running${NC} — Loading model weights..."
        elif [[ "$ready_pods" -eq "$total_pods" ]]; then
            echo -e "   ${GREEN}▸ All workers ready${NC}"
        fi

        # --- Loading progress bar (single aggregated line) ---
        if [[ "$ready_pods" -lt "$total_pods" ]]; then
            # Sample one not-ready worker pod for loading progress
            sample_pod=$(echo "$pod_output" | grep -iE "worker|decode|prefill" | grep "0/1" | head -1 | awk '{print $1}')
            if [[ -n "$sample_pod" ]]; then
                # Grab more log lines to find the slow progress line
                # TRT-LLM logs interleave fast per-rank safetensors (48/48 instantly)
                # with slow overall weight loading (0/2452 concurrently)
                shard_line=$(kubectl logs "$sample_pod" -n "$NAMESPACE" --tail=50 2>/dev/null \
                    | grep -iE "Loading weights concurrently|Loading.*model.*weight|Loading.*checkpoint" | tail -1 || true)
                # Fallback to any loading line if the specific one isn't found
                if [[ -z "$shard_line" ]]; then
                    shard_line=$(kubectl logs "$sample_pod" -n "$NAMESPACE" --tail=50 2>/dev/null \
                        | grep -iE "Loading.*safetensor|shard|Completed" | tail -1 || true)
                fi
                if [[ -n "$shard_line" ]]; then
                    # Extract progress from tqdm format: "  5%|▌  | 123/2452 [00:10<02:30]"
                    # Use head -1 to get the first (overall) percentage, not a sub-progress
                    shard_pct=$(echo "$shard_line" | grep -oP '\d+(?=%)' | head -1 || echo "")
                    shard_frac=$(echo "$shard_line" | grep -oP '\d+/\d+' | head -1 || echo "")
                    pct="${shard_pct:-0}"
                    if [[ "$pct" -gt 0 ]] 2>/dev/null; then
                        filled=$((pct * 30 / 100))
                        empty=$((30 - filled))
                        bar=""
                        bar_empty=""
                        for ((b=0; b<filled; b++)); do bar+="█"; done
                        for ((b=0; b<empty; b++)); do bar_empty+="░"; done
                        echo -e "   Loading model: [${GREEN}${bar}${NC}${bar_empty}] ${BOLD}${pct}%${NC} | ${shard_frac} shards across ${total_pods} workers"
                    elif [[ -n "$shard_frac" ]]; then
                        echo -e "   ${DIM}Loading model: ${shard_frac} shards...${NC}"
                    fi
                fi
            fi
        fi
    fi

    # --- Check for completion ---
    if [[ "$dgdr_phase" == "Deployed" ]]; then
        echo ""
        echo -e "   ${GREEN}✅ Deployment complete — all workers are healthy!${NC}"
        echo ""
        break
    fi

    if [[ "$dgdr_phase" == "Failed" ]]; then
        echo ""
        echo -e "   ${RED}❌ Deployment failed!${NC}"
        kubectl describe dgdr "$DGDR_NAME" -n "$NAMESPACE" 2>/dev/null | tail -20 | sed 's/^/   /'
        exit 1
    fi

    # Re-fetch DGD name if we didn't have it
    if [[ -z "$DGD_NAME" ]]; then
        DGD_NAME=$(kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" -o jsonpath='{.status.dgdName}' 2>/dev/null || echo "")
    fi

    sleep 5
    deploy_elapsed=$((deploy_elapsed + 5))
done

if [[ $deploy_elapsed -ge $DEPLOY_MAX_WAIT ]]; then
    echo -e "${YELLOW}Deployment still in progress after ${DEPLOY_MAX_WAIT}s${NC}"
    echo "   The pods may still be loading the model. Continuing with demo..."
    echo ""
fi

pause 3


# =============================================================================
# STEP 6: Verify — Everything Is Healthy
# =============================================================================
step_header "✅" "Step 6: Verify — Everything Is Healthy"

narrate "Let's confirm the full deployment is ready."
echo ""

pause 2

show_command "kubectl get dgdr ${DGDR_NAME} -n ${NAMESPACE}"
kubectl get dgdr "$DGDR_NAME" -n "$NAMESPACE" 2>&1

pause 2

echo ""
if [[ -n "$DGD_NAME" ]]; then
    show_command "kubectl get dgd ${DGD_NAME} -n ${NAMESPACE}"
    kubectl get dgd "$DGD_NAME" -n "$NAMESPACE" 2>&1
fi

pause 2

echo ""
pod_label="nvidia.com/dynamo-graph-deployment-name=${DGD_NAME}"
show_command "kubectl get pods -n ${NAMESPACE} -l '${pod_label}' -o wide"
kubectl get pods -n "$NAMESPACE" -l "$pod_label" -o wide 2>/dev/null || true

pause 2

# Show per-service status from the DGD
echo ""
if [[ -n "$DGD_NAME" ]]; then
    show_command "kubectl get dgd ${DGD_NAME} -n ${NAMESPACE} -o jsonpath='{.status.services}'"
    kubectl get dgd "$DGD_NAME" -n "$NAMESPACE" -o jsonpath='{.status.services}' 2>/dev/null \
        | python3 -c "
import json, sys
try:
    svcs = json.loads(sys.stdin.read())
    for name, s in svcs.items():
        ready = s.get('readyReplicas', s.get('availableReplicas', '?'))
        total = s.get('replicas', '?')
        kind = s.get('componentKind', '')
        print(f'   {name}: {ready}/{total} ready  ({kind})')
except: pass
" 2>/dev/null || true
fi

pause 2

echo ""
echo -e "   🛒 ${BOLD}E-COMMERCE AI ASSISTANT IS LIVE!${NC}"

pause 4


# =============================================================================
# STEP 7: Talk to It — Proof It's Real
# =============================================================================
step_header "💬" "Step 7: Talk to It — Proof It's Real"

narrate "Let's send a real request to the model and see it respond."
narrate "Port-forwarding to the frontend service..."
echo ""

pause 2

# Find the frontend service
frontend_svc=""
if [[ -n "$DGD_NAME" ]]; then
    frontend_svc=$(kubectl get svc -n "$NAMESPACE" -l "nvidia.com/dynamo-graph-deployment-name=${DGD_NAME}" --no-headers -o name 2>/dev/null \
        | grep -i frontend | head -1 | sed 's|service/||')
fi
if [[ -z "$frontend_svc" ]]; then
    frontend_svc=$(kubectl get svc -n "$NAMESPACE" --no-headers -o name 2>/dev/null \
        | grep -i frontend | head -1 | sed 's|service/||')
fi

if [[ -z "$frontend_svc" ]]; then
    echo -e "   ${YELLOW}Could not find frontend service. Skipping live request demo.${NC}"
    echo "   You can port-forward manually and use:"
    echo "   curl http://localhost:8000/v1/chat/completions ..."
    echo ""
else
    show_command "kubectl port-forward svc/${frontend_svc} ${PORT_FORWARD_PORT}:8000 -n ${NAMESPACE} &"
    kubectl port-forward "svc/$frontend_svc" "${PORT_FORWARD_PORT}:8000" -n "$NAMESPACE" >/dev/null 2>&1 &
    PF_PID=$!

    # Wait for port-forward to be connectable (up to 30 seconds)
    echo -n "   Waiting for port-forward"
    pf_ready=false
    for _i in $(seq 1 30); do
        if curl -s -o /dev/null --connect-timeout 1 "http://localhost:${PORT_FORWARD_PORT}/v1/models" 2>/dev/null; then
            pf_ready=true
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""

    if [[ "$pf_ready" != true ]]; then
        echo -e "   ${YELLOW}Port-forward failed. Skipping live request.${NC}"
        kill "$PF_PID" 2>/dev/null || true
        PF_PID=""
    else
        echo -e "   Port-forward active on localhost:${PORT_FORWARD_PORT}"

        pause 2

        narrate "Sending a streaming chat/completions request..."
        echo ""

        CHAT_PROMPT="You just got deployed on Kubernetes with NVIDIA Dynamo in a single YAML file! Write a short, enthusiastic one-sentence celebration."

        show_command "curl http://localhost:${PORT_FORWARD_PORT}/v1/chat/completions -d '{\"model\":\"${MODEL}\", \"stream\":true, ...}'"
        echo ""
        echo -e -n "   ${BOLD}Model response:${NC} "

        curl -s -N "http://localhost:${PORT_FORWARD_PORT}/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"${MODEL}\",
                \"messages\": [{\"role\": \"user\", \"content\": \"${CHAT_PROMPT}\"}],
                \"stream\": true,
                \"max_tokens\": 200,
                \"chat_template_kwargs\": {\"enable_thinking\": false}
            }" 2>/dev/null \
            | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data: ') and line != 'data: [DONE]':
        try:
            d = json.loads(line[6:])
            c = d.get('choices', [{}])[0].get('delta', {}).get('content', '')
            if c:
                print(c, end='', flush=True)
        except:
            pass
print()
" 2>/dev/null || echo -e "${YELLOW}(request failed - model may still be warming up)${NC}"

        echo ""

        # Kill port-forward
        kill "$PF_PID" 2>/dev/null || true
        wait "$PF_PID" 2>/dev/null || true
        PF_PID=""
    fi
fi

pause 2


# =============================================================================
# SUMMARY
# =============================================================================
step_header "🎉" "Done. That Was It."

echo ""
echo -e "   What ${BOLD}you${NC} did:"
echo ""
echo "   1. Wrote a DGDR (model name + SLA targets)"
echo "   2. kubectl apply"
echo ""
echo "   That's it. Two steps."

pause 3

echo ""
echo -e "   What ${BOLD}Dynamo${NC} did:"
echo ""
echo "   1. 🔬 Profiled the model on your GPUs"
echo "   2. 📋 Generated optimal DGD (TP, PP, replicas, planner curves)"
echo "   3. 🚀 Deployed prefill workers, decode workers, frontend, planner"
echo "   4. ✅ Brought everything online and healthy"
echo "   5. 💬 Served a real request"

pause 3

echo ""
echo -e "   ${BOLD}One YAML. One command. Go make coffee. ☕${NC}"

pause 3

echo ""
echo "   The SLA Planner is now watching your TTFT/ITL metrics."
echo "   When Black Friday hits and load spikes, it will automatically"
echo "   scale prefill and decode workers to maintain your SLA targets."
echo ""
echo -e "   ${DIM}TTFT target: ${TTFT_TARGET}ms  |  ITL target: ${ITL_TARGET}ms  |  Mode: disagg${NC}"
echo ""

if [[ "$DO_CLEANUP" == true ]]; then
    echo -e "   ${DIM}(DGDR will be cleaned up on exit. Use --no-cleanup to keep it.)${NC}"
else
    echo -e "   ${DIM}(DGDR '${DGDR_NAME}' left in place for further exploration.)${NC}"
fi

echo ""
echo "   Learn more: https://github.com/ai-dynamo/dynamo"
echo ""
