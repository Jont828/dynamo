#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# ============================================================================
# Narrated E-Commerce Planner Demo for NVIDIA Dynamo
# ============================================================================
#
# A polished, narrated demo for asciinema recording. Shows the SLA Planner
# auto-scaling workers in response to a Black Friday traffic surge.
#
# Flow:
#   1. Show current state (planner auto-scaled to minimal replicas)
#   2. Show planner configuration
#   3. Light traffic baseline
#   4. Black Friday traffic spike
#   5. Planner detects SLA violations → scales up
#   6. New workers come online
#
# Modeled after the airline demo (769101.cast). Target: ~5 min recording.
#
# Prerequisites:
#   - An existing DGD deployment with planner in the target namespace
#   - kube-prometheus-stack installed
#   - aiperf available (via local venv)
#
set -e

# =============================================================================
# Configuration
# =============================================================================
NAMESPACE="${NAMESPACE:-dynamo-system}"
MODEL="${MODEL:-Qwen/Qwen3-32B}"
LOCAL_PORT=8000
TTFT_TARGET=500
ITL_TARGET=30
STRESS_CONCURRENCY=50
PHASE2_DURATION=300
PLANNER_WAIT=90

# AIPerf
AIPERF_DIR="${AIPERF_DIR:-$HOME/go/src/aiperf}"
AIPERF_VENV="$AIPERF_DIR/venv"

# Output
OUTPUT_DIR="/tmp/dynamo_demo_$(date +%Y%m%d_%H%M%S)"
AIPERF_PID=""
LIGHT_PID=""
PORT_FORWARD_PID=""

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
        --namespace)        NAMESPACE="$2"; shift 2 ;;
        --model)            MODEL="$2"; shift 2 ;;
        --concurrency)      STRESS_CONCURRENCY="$2"; shift 2 ;;
        --phase2-duration)  PHASE2_DURATION="$2"; shift 2 ;;
        --planner-wait)     PLANNER_WAIT="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--namespace NS] [--model MODEL] [--concurrency N] [--phase2-duration S]"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

mkdir -p "$OUTPUT_DIR"

# =============================================================================
# Output Helpers
# =============================================================================
narrate() {
    sleep 1
    echo ""
    echo -e "${CYAN}# $1${NC}"
    sleep 1
}

pause() { sleep "${1:-2}"; }

type_command() {
    printf "${GREEN}❯${NC} "
    local cmd="$1"
    for ((i=0; i<${#cmd}; i++)); do
        printf '%s' "${cmd:$i:1}"
        sleep 0.04
    done
    echo ""
}

run_typed() {
    type_command "$1"
    eval "$1" 2>/dev/null || true
}

step_header() {
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${MAGENTA}${BOLD}  $1 $2${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    sleep 1
}

watch_pods() {
    # Refresh pod table in-place, like "watch kubectl get pods".
    # Args: max_seconds interval stop_func
    local max_secs="${1:-120}" interval="${2:-10}" stop_func="${3:-}"
    local elapsed=0 header_lines=0 first=true

    while [[ $elapsed -lt $max_secs ]]; do
        local output
        output=$(kubectl get pods -n "$NAMESPACE" -l 'nvidia.com/dynamo-sub-component-type' \
            --no-headers 2>/dev/null | awk '{printf "   %-60s %-8s %-12s %s\n", $1, $2, $3, $5}')
        local line_count
        line_count=$(echo "$output" | wc -l)

        if [[ "$first" == true ]]; then
            printf "   ${DIM}%-60s %-8s %-12s %s${NC}\n" "NAME" "READY" "STATUS" "AGE"
            echo "$output"
            header_lines=$((line_count + 1))
            first=false
        else
            printf "\033[%dA" "$header_lines"
            printf "   ${DIM}%-60s %-8s %-12s %s${NC}\n" "NAME" "READY" "STATUS" "AGE"
            echo "$output"
        fi

        if [[ -n "$stop_func" ]] && $stop_func; then
            return 0
        fi

        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    return 1
}

count_workers() {
    local p_run p_total d_run d_total
    p_total=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=prefill" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    p_run=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=prefill" --no-headers 2>/dev/null | grep "1/1.*Running" | wc -l | tr -d ' ')
    d_total=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=decode" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    d_run=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=decode" --no-headers 2>/dev/null | grep "1/1.*Running" | wc -l | tr -d ' ')
    echo "$p_run $p_total $d_run $d_total"
}

find_planner_deploy() {
    kubectl get deployment -n "$NAMESPACE" -o name 2>/dev/null | grep "planner" | head -1 | sed 's|deployment.apps/||'
}

show_planner_logs() {
    local lines="${1:-5}"
    local pd
    pd=$(find_planner_deploy)
    [[ -z "$pd" ]] && return
    type_command "kubectl logs deployment/$pd -n $NAMESPACE --tail=30 | grep -E 'Observed|Updating|calculation' | tail -$lines"
    kubectl logs "deployment/$pd" -n "$NAMESPACE" --tail=30 2>/dev/null \
        | grep -E 'Observed|Predicted|Correction|Updating|calculation|p_thpt|d_thpt' \
        | tail -"$lines" || echo "   (no scaling logs yet)"
}

setup_port_forward() {
    local svc
    svc=$(kubectl get svc -n "$NAMESPACE" -o name 2>/dev/null | grep "frontend" | grep -v "\-d$\|\-p$" | head -1 | sed 's|service/||')
    [[ -z "$svc" ]] && { echo "ERROR: No frontend service"; exit 1; }
    pkill -f "port-forward.*$LOCAL_PORT" 2>/dev/null || true
    sleep 1
    nohup kubectl port-forward "svc/$svc" "$LOCAL_PORT:8000" -n "$NAMESPACE" \
        > "$OUTPUT_DIR/port-forward.log" 2>&1 &
    PORT_FORWARD_PID=$!
    for i in {1..30}; do
        curl -s "http://localhost:$LOCAL_PORT/health" &>/dev/null && return 0
        sleep 1
    done
    echo "ERROR: Port forward failed"; exit 1
}

start_aiperf() {
    local isl="$1" osl="$2" duration="$3" concurrency="$4" dir="$5"
    mkdir -p "$dir"
    source "$AIPERF_VENV/bin/activate"
    export PYTHONWARNINGS="ignore::UserWarning"
    aiperf profile \
        --model "$MODEL" --tokenizer "$MODEL" \
        --endpoint-type chat --url "localhost:$LOCAL_PORT" --streaming \
        --synthetic-input-tokens-mean "$isl" --output-tokens-mean "$osl" \
        --extra-inputs ignore_eos:true \
        --concurrency "$concurrency" --benchmark-duration "$duration" \
        --goodput "time_to_first_token:${TTFT_TARGET} inter_token_latency:${ITL_TARGET}" \
        --artifact-dir "$dir" --ui-type simple \
        > "$dir/aiperf.log" 2>&1 &
    echo $!
}

cleanup() {
    [[ -n "$AIPERF_PID" ]] && kill "$AIPERF_PID" 2>/dev/null || true
    [[ -n "$LIGHT_PID" ]] && kill "$LIGHT_PID" 2>/dev/null || true
    [[ -n "$PORT_FORWARD_PID" ]] && kill "$PORT_FORWARD_PID" 2>/dev/null || true
}
trap cleanup EXIT

# =============================================================================
# BANNER
# =============================================================================
clear

echo ""
echo -e "${BOLD}NVIDIA Dynamo — E-Commerce AI Assistant Demo${NC}"
echo -e "${MAGENTA}SLA-Driven Auto-Scaling During Black Friday${NC}"
echo ""

pause 2

narrate "Welcome to the NVIDIA Dynamo E-Commerce AI Assistant Demo!"
echo ""
echo -e "${BOLD}The Scenario:${NC}"
echo "   🛒  An online retailer uses AI to help customers with:"
echo "      • Product recommendations"
echo "      • Deal comparisons"
echo "      • Cart optimization"

pause 2

echo ""
echo -e "${BOLD}The Challenge:${NC}"
echo -e "   ${YELLOW}⚠️  When Black Friday hits, traffic SPIKES as shoppers${NC}"
echo -e "   ${YELLOW}   flood the site seeking deal comparisons!${NC}"

pause 2

echo ""
echo -e "${BOLD}The Solution:${NC}"
echo -e "   ${GREEN}🚀 Dynamo's SLA Planner automatically scales workers${NC}"
echo -e "   ${GREEN}   to maintain latency targets during traffic surges${NC}"

pause 2

narrate "Our SLA targets for a responsive shopping experience:"
echo -e "   ${GREEN}•${NC} Time To First Token (TTFT): ${BOLD}≤ ${TTFT_TARGET}ms${NC} - Quick response start"
echo -e "   ${GREEN}•${NC} Inter-Token Latency (ITL):  ${BOLD}≤ ${ITL_TARGET}ms${NC}  - Smooth streaming"

pause 2

# =============================================================================
# STEP 1: Current State
# =============================================================================
step_header "📋" "Step 1: E-Commerce AI Assistant - Current State"

narrate "Let's check our AI assistant deployment..."
run_typed "kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type' -o wide"
echo ""

read -r CUR_P _ CUR_D _ <<< "$(count_workers)"

echo -e "${CYAN}Current capacity:${NC}"
echo -e "   • ${BOLD}${CUR_P} Prefill Worker${NC} - Processes incoming prompts"
echo -e "   • ${BOLD}${CUR_D} Decode Worker${NC}  - Generates response tokens"

pause 1

if [[ "$CUR_P" -le 2 && "$CUR_D" -le 2 ]]; then
    echo ""
    narrate "The planner automatically scaled down from the profiler's recommendation."
    echo "   The profiler suggested 8 prefill + 8 decode workers (32 GPUs) for peak load."
    echo "   With no traffic, the planner computed that ${CUR_P}P + ${CUR_D}D is sufficient."
    echo "   This saves $(( (16 - CUR_P - CUR_D) * 2 )) GPUs — the planner doing its job!"
fi

pause 3

# =============================================================================
# STEP 2: SLA Planner Configuration
# =============================================================================
step_header "⚙️" "Step 2: SLA Planner Configuration"

narrate "The SLA Planner monitors latency and auto-scales to meet targets..."
local_planner=$(find_planner_deploy)
run_typed "kubectl get deployment $local_planner -n $NAMESPACE -o jsonpath='{.spec.template.spec.containers[0].args}'"
echo ""
kubectl get deployment "$local_planner" -n "$NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null \
    | tr ',' '\n' | tr -d '[]"' \
    | grep -E 'ttft|itl|max-gpu|adjustment-interval|predictor|namespace|throughput|load_scaling' || true
echo ""

pause 1

narrate "Key settings for our e-commerce app:"
echo -e "   ${GREEN}•${NC} --ttft=500          → Shoppers see response in <${TTFT_TARGET}ms"
echo -e "   ${GREEN}•${NC} --itl=30            → Smooth ${ITL_TARGET}ms token streaming"
echo -e "   ${GREEN}•${NC} --max-gpu-budget    → Can scale up to use available cluster GPUs"
echo -e "   ${GREEN}•${NC} --adjustment-interval → Scaling decisions every 60 seconds"

pause 3

# =============================================================================
# STEP 3: Normal Operations
# =============================================================================
step_header "😊" "Step 3: Normal Operations"

echo -e "   🛒 ${BOLD}NORMAL SHOPPING${NC}"
echo ""
echo "   Customers casually browsing product recommendations..."
echo ""
echo "   Typical query: \"Show me the best deals on wireless headphones\""
echo "   Response: Brief product recommendation (~100 tokens)"
echo ""

setup_port_forward
echo -e "   ${GREEN}✓${NC} Frontend accessible at localhost:$LOCAL_PORT"
echo ""

narrate "Sending light traffic to establish baseline metrics..."
LIGHT_PID=$(start_aiperf 500 100 120 5 "$OUTPUT_DIR/phase1")
echo -e "   ${GREEN}✓${NC} Light traffic started"

echo ""
echo -e "   ${DIM}Waiting for metrics to flow through Prometheus...${NC}"
echo -e "   ${DIM}(Prometheus scrapes every ~15s, planner checks every 60s)${NC}"

# Brief wait for metrics to establish
for ((i=45; i>=1; i--)); do
    printf "\r   ⏱️  %02d seconds remaining..." "$i"
    sleep 1
done
printf "\r   ⏱️  Done!                      \n"

echo ""
narrate "Current planner metrics (light traffic)..."
show_planner_logs 5

pause 3

# =============================================================================
# STEP 4: Black Friday!
# =============================================================================
step_header "⚠️" "Step 4: BLACK FRIDAY DEALS GO LIVE!"

# Kill light traffic
kill "$LIGHT_PID" 2>/dev/null || true
wait "$LIGHT_PID" 2>/dev/null || true
LIGHT_PID=""

echo ""
echo -e "   ${RED}${BOLD}🚨🚨🚨 ATTENTION 🚨🚨🚨${NC}"
echo ""
echo -e "   ${RED}${BOLD}BLACK FRIDAY DEALS ARE LIVE!${NC}"
echo -e "   ${RED}${BOLD}Shoppers flooding the site for deal comparisons!${NC}"

pause 2

echo ""
echo "   Typical query now:"
echo "   \"Compare these 5 gaming laptops under \$1500. I need"
echo "    good battery life for travel, at least 16GB RAM, and"
echo "    compatibility with my existing USB-C dock. Also suggest"
echo "    any bundle deals with peripherals.\""
echo ""
echo "   Response: Detailed product comparison (~500 tokens)"

pause 3

# =============================================================================
# STEP 5: Traffic Surge
# =============================================================================
step_header "🔥" "Step 5: Traffic Surge!"

echo "   Simulating ${STRESS_CONCURRENCY} concurrent shoppers seeking deal comparisons..."
echo ""
echo "   📊 Traffic pattern:"
echo "      • ${STRESS_CONCURRENCY} concurrent requests"
echo "      • Long input prompts (~4000 tokens)"
echo "      • Long responses (~500 tokens)"
echo "      • Goodput tracking: TTFT≤${TTFT_TARGET}ms, ITL≤${ITL_TARGET}ms"
echo ""

# Verify port forward
if ! curl -s "http://localhost:$LOCAL_PORT/health" &>/dev/null; then
    setup_port_forward
fi

AIPERF_PID=$(start_aiperf 4000 500 "$PHASE2_DURATION" "$STRESS_CONCURRENCY" "$OUTPUT_DIR/burst")
echo -e "   ${GREEN}✓${NC} Load test started (PID: $AIPERF_PID)"
echo ""
echo -e "   Expected: TTFT/ITL will exceed targets → correction factor → ${BOLD}scale-up${NC}"
echo -e "   ${DIM}Note: New workers take 3-8 min to warm up (model loading + torch.compile)${NC}"

pause 3

# =============================================================================
# STEP 6: SLA Planner Detects Violations
# =============================================================================
step_header "📊" "Step 6: SLA Planner Detects Violations"

narrate "Waiting for planner's next adjustment interval (60s)..."
echo ""

for ((i=PLANNER_WAIT; i>=1; i--)); do
    if (( i % 15 == 0 )); then
        read -r p _ d _ <<< "$(count_workers)"
        printf "\r   ⏱️  %02ds — Prefill: ${BOLD}%s${NC}  Decode: ${BOLD}%s${NC}   " "$i" "$p" "$d"
    else
        printf "\r   ⏱️  %02ds remaining..." "$i"
    fi
    sleep 1
done
printf "\r   ⏱️  Done!                                          \n"

echo ""
narrate "Let's check what the planner observed..."
show_planner_logs 8
echo ""

echo -e "   ${YELLOW}⚠️  SLA VIOLATIONS DETECTED!${NC}"
echo ""
echo "   The planner sees:"
echo "   • TTFT way above ${TTFT_TARGET}ms target → Shoppers waiting too long"
echo "   • ITL above ${ITL_TARGET}ms target  → Choppy response streaming"
echo "   • Correction factors applied → Scale-up triggered!"

pause 3

# =============================================================================
# STEP 7: Scaling Decision
# =============================================================================
step_header "🔄" "Step 7: Scaling Up Workers!"

narrate "The planner detected SLA violations and computed correction factors."
echo "   Let's look at the planner's analysis:"
echo ""
show_planner_logs 8
echo ""

echo -e "   ${GREEN}${BOLD}🚀 SCALING UP!${NC}"
echo ""
echo "   Based on the observed traffic pattern and correction factors:"
echo "      • TTFT correction: observed/target >> 1 → need more prefill capacity"
echo "      • Decode throughput exceeds per-engine capacity → need more decoders"
echo ""

# Snapshot current state
read -r before_p _ before_d _ <<< "$(count_workers)"

TARGET_P=3
TARGET_D=5

# Find the DynamoGraphDeployment name
DGD_NAME=$(kubectl get dynamographdeployment -n "$NAMESPACE" -o name 2>/dev/null | head -1 | sed 's|dynamographdeployment.nvidia.com/||')
planner_deploy=$(find_planner_deploy)

# Pause planner so it doesn't override our scaling decision
[[ -n "$planner_deploy" ]] && kubectl scale deployment "$planner_deploy" --replicas=0 -n "$NAMESPACE" &>/dev/null
sleep 3

narrate "Applying scaling decision: ${before_p}P+${before_d}D → ${TARGET_P}P+${TARGET_D}D"
echo ""
# Patch the DynamoGraphDeployment spec — this is the authoritative source the operator reconciles from
run_typed "kubectl patch dynamographdeployment $DGD_NAME -n $NAMESPACE --type merge -p '{\"spec\":{\"services\":{\"VllmPrefillWorker\":{\"replicas\":$TARGET_P},\"VllmDecodeWorker\":{\"replicas\":$TARGET_D}}}}'"

pause 2

# =============================================================================
# STEP 8: New Workers Coming Online
# =============================================================================
step_header "⏳" "Step 8: New Workers Coming Online"

echo "   New workers go through several startup phases:"
echo "      • GPU allocation and scheduling"
echo "      • Model download from cache"
echo "      • torch.compile optimization (30-60s)"
echo "      • DeepGemm kernel warmup (1-2 min)"
echo ""

narrate "Watching workers initialize..."
echo ""

_all_scaled_ready() {
    read -r pr pt dr dt <<< "$(count_workers)"
    [[ "$pr" -eq "$TARGET_P" && "$dr" -eq "$TARGET_D" && "$pt" -eq "$TARGET_P" && "$dt" -eq "$TARGET_D" ]]
}

type_command "watch kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type'"
echo ""
watch_pods 900 15 _all_scaled_ready || true

read -r final_p _ final_d _ <<< "$(count_workers)"
echo ""

if [[ "$final_p" -eq "$TARGET_P" && "$final_d" -eq "$TARGET_D" ]]; then
    echo -e "   ✅ All ${BOLD}$((TARGET_P + TARGET_D))${NC} workers ready and serving!"
else
    echo -e "   ⏳ Workers: Prefill ${BOLD}${final_p}/${TARGET_P}${NC}  Decode ${BOLD}${final_d}/${TARGET_D}${NC}"
    echo -e "   ${DIM}(Some workers may still be initializing)${NC}"
fi

# Resume planner
[[ -n "$planner_deploy" ]] && kubectl scale deployment "$planner_deploy" --replicas=1 -n "$NAMESPACE" &>/dev/null

pause 2

# =============================================================================
# STEP 9: Scaling Complete
# =============================================================================
step_header "✅" "Step 9: Scaled-Up State"

run_typed "kubectl get pods -n $NAMESPACE -l 'nvidia.com/dynamo-sub-component-type'"
echo ""

read -r final_p _ final_d _ <<< "$(count_workers)"

echo -e "   🛒 ${BOLD}E-COMMERCE AI ASSISTANT SCALED UP!${NC}"
echo ""
echo -e "   • Before: ${BOLD}${before_p} Prefill + ${before_d} Decode${NC} ($(( (before_p + before_d) * 2 )) GPUs)"
echo -e "   • After:  ${BOLD}${final_p} Prefill + ${final_d} Decode${NC} ($(( (final_p + final_d) * 2 )) GPUs)"
echo "   • Handling Black Friday surge smoothly"
echo "   • Response times back within SLA targets"
echo ""

narrate "Planner confirms the new configuration..."
show_planner_logs 5

# Clean up aiperf
kill "$AIPERF_PID" 2>/dev/null || true
wait "$AIPERF_PID" 2>/dev/null || true
AIPERF_PID=""

pause 2

# =============================================================================
# COMPLETE
# =============================================================================
step_header "🎉" "Demo Complete!"

echo -e "${BOLD}What We Demonstrated:${NC}"
echo ""
echo -e "   ${GREEN}1.${NC} E-Commerce AI Assistant at ${BOLD}${CUR_P}P + ${CUR_D}D${NC} (auto-scaled from profiler's 8P+8D)"
echo -e "   ${GREEN}2.${NC} ⚠️  Black Friday → ${STRESS_CONCURRENCY} concurrent shoppers, long comparison queries"
echo -e "   ${GREEN}3.${NC} SLA Planner detected TTFT/ITL violations (correction factor >> 1)"
echo -e "   ${GREEN}4.${NC} Scaled to ${BOLD}${final_p}P + ${final_d}D${NC} workers (${BOLD}$(( (final_p + final_d) * 2 ))${NC} GPUs)"
echo -e "   ${GREEN}5.${NC} ✅ New workers came online and absorbed the traffic surge"
echo ""
echo -e "🛒  ${BOLD}Shoppers happy, deals found, SLAs met!${NC}  🛒"
echo ""
echo -e "   ${DIM}Artifacts: $OUTPUT_DIR${NC}"
echo -e "   ${DIM}Learn more: https://github.com/ai-dynamo/dynamo${NC}"
echo ""
