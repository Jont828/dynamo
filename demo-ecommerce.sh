#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# ============================================================================
# E-Commerce Black Friday Demo for NVIDIA Dynamo
# ============================================================================
#
# This script demonstrates the end-to-end Dynamo workflow — from a single DGDR
# to a fully deployed, auto-scaling inference service. It simulates:
#
#   1. Normal Shopping: Customers browsing product recommendations (short queries)
#   2. Black Friday Launch: Flash deals trigger a traffic spike (long comparison queries)
#   3. Recovery: Sale winds down, traffic normalizes
#
# The demo showcases:
#   - DGDR as a single entrypoint: model + SLA targets → Dynamo handles the rest
#   - Automatic profiling to find optimal GPU configuration
#   - SLA Planner auto-scaling prefill/decode workers during traffic shifts
#
# HOW SCALING WORKS:
# The SLA Planner uses "correction factors" based on observed vs target latency:
#   - If observed TTFT/ITL exceeds targets, the correction factor increases
#   - Higher correction factors reduce the "effective capacity" of workers
#   - When throughput exceeds effective capacity, new replicas are added
#
# Example: If ITL target is 30ms but observed ITL is 43ms:
#   - Correction factor = 43/30 = 1.43
#   - Effective decode capacity is reduced by 1.43x
#   - This triggers scale-up even if raw throughput is below GPU capacity
#
# NOTE: New workers take 3-8 minutes to become ready due to:
#   - Model loading from HuggingFace/cache
#   - torch.compile optimization (30-60 seconds)
#   - DeepGemm kernel warmup (1-2 minutes)
#
# Prerequisites:
#   - Kubernetes cluster with Dynamo 1.0.1+ installed
#   - kube-prometheus-stack installed and running
#   - NVIDIA GPUs available in the cluster
#   - aiperf installed (pip install aiperf)
#
# Usage:
#   ./demo-ecommerce.sh [OPTIONS]
#
# Quick Start:
#   # Full demo: profiling + deployment + load test
#   ./demo-ecommerce.sh --full-demo
#
#   # Just run load test against existing deployment
#   ./demo-ecommerce.sh --load-test-only
#
set -e

# =============================================================================
# Demo Configuration
# =============================================================================
NAMESPACE="${NAMESPACE:-dynamo-demo}"
IMAGE_TAG="${IMAGE_TAG:-1.0.1}"
MODEL="${MODEL:-Qwen/Qwen3-32B}"
BACKEND="${BACKEND:-vllm}"
DEPLOYMENT_NAME="ecommerce-assistant"

# Demo timing (in seconds)
PHASE1_DURATION=120    # Normal shopping
PHASE2_DURATION=180    # Black Friday launch (burst + long queries)
PHASE3_DURATION=120    # Sustained peak shopping
PHASE4_DURATION=120    # Wind-down
COOLDOWN_DURATION=90   # Observe scale-down

# Traffic patterns
# Phase 1: Normal - shoppers browsing product recommendations
NORMAL_RPS=5
SHORT_ISL=500          # "Show me deals on headphones"
SHORT_OSL=100

# Phase 2-3: Black Friday - shoppers need detailed comparisons
BURST_RPS=150          # High enough to exceed H100 capacity (~3248 tokens/s)
LONG_ISL=4000          # "Compare these 5 laptops, check compatibility..."
LONG_OSL=500

# SLA Targets (aligned with blog post)
TTFT_TARGET=500        # 500ms Time To First Token
ITL_TARGET=30          # 30ms Inter-Token Latency

# Hardware constraints
TOTAL_GPUS=""          # Empty = auto-detect all cluster GPUs
MODEL_CACHE_PVC=""     # PVC name for pre-downloaded model weights
MODEL_CACHE_MOUNT_PATH=""  # Mount path for model cache PVC (e.g. /home/dynamo/.cache/huggingface)
MODEL_CACHE_MODEL_PATH=""  # Path to model within PVC (e.g. hub/models--Qwen--Qwen3-32B/snapshots/<rev>)

# Flags
FULL_DEMO=false
LOAD_TEST_ONLY=false
SKIP_PROFILING=false
DRY_RUN=false
OPEN_GRAFANA=false
RECORD_DEMO=false
STRESS_TEST=true        # Use concurrency burst mode for maximum load (recommended for demo)

# Port forwarding
LOCAL_PORT=8000
GRAFANA_PORT=3000
PORT_FORWARD_PID=""
GRAFANA_PID=""
FRONTEND_SVC=""

# Output
OUTPUT_DIR=""
DEMO_LOG=""

# Colors and Emojis for demo-friendly output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

# =============================================================================
# Demo Output Functions (Themed for E-Commerce Scenario)
# =============================================================================
banner() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}🛒  NVIDIA Dynamo - E-Commerce AI Assistant Demo  🛒${NC}        ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}     ${MAGENTA}DGDR → Profiling → Deployment → SLA Planner${NC}              ${CYAN}║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_phase() {
    local emoji="$1"
    local phase="$2"
    local desc="$3"
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${emoji}  ${BOLD}${phase}${NC}"
    echo -e "   ${desc}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

log_info() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${BLUE}[$timestamp]${NC} ℹ️  $1" >&2
    echo "[$timestamp] INFO: $1" >> "$DEMO_LOG" 2>/dev/null || true
}

log_success() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${GREEN}[$timestamp]${NC} ✅ $1" >&2
    echo "[$timestamp] SUCCESS: $1" >> "$DEMO_LOG" 2>/dev/null || true
}

log_warning() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${YELLOW}[$timestamp]${NC} ⚠️  $1" >&2
    echo "[$timestamp] WARNING: $1" >> "$DEMO_LOG" 2>/dev/null || true
}

log_error() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${RED}[$timestamp]${NC} ❌ $1" >&2
    echo "[$timestamp] ERROR: $1" >> "$DEMO_LOG" 2>/dev/null || true
}

log_metric() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${MAGENTA}[$timestamp]${NC} 📊 $1"
}

log_scaling() {
    local timestamp=$(date +%H:%M:%S)
    echo -e "${CYAN}[$timestamp]${NC} 🔄 $1"
}

show_pod_status() {
    local prefill_count decode_count
    prefill_count=$(kubectl get pods -n "$NAMESPACE" \
        -l "nvidia.com/dynamo-sub-component-type=prefill" \
        --field-selector=status.phase=Running \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')
    decode_count=$(kubectl get pods -n "$NAMESPACE" \
        -l "nvidia.com/dynamo-sub-component-type=decode" \
        --field-selector=status.phase=Running \
        --no-headers 2>/dev/null | wc -l | tr -d ' ')

    log_scaling "Workers: Prefill=${prefill_count:-0} | Decode=${decode_count:-0}"
}

# =============================================================================
# Help and Argument Parsing
# =============================================================================
show_help() {
    cat << 'EOF'
E-Commerce Black Friday Demo for NVIDIA Dynamo

This demo showcases the end-to-end Dynamo workflow using an e-commerce AI
assistant scenario. You provide a DGDR with your model + SLA targets, and
Dynamo handles profiling, optimal config selection, and deployment automatically.

The demo simulates:
  • Normal shopping traffic (product recommendations, quick lookups)
  • Black Friday launch (traffic spike + detailed comparison queries)
  • SLA-driven auto-scaling of prefill/decode workers

USAGE:
    ./demo-ecommerce.sh [OPTIONS]

DEMO MODES:
    --full-demo          Run complete demo: DGDR → profiling → deployment → load test
    --load-test-only     Run load test against existing deployment
    --deploy-only        Deploy without running load test
    --cleanup            Remove all demo resources

OPTIONS:
    --namespace NS       Kubernetes namespace (default: dynamo-demo)
    --image-tag TAG      Dynamo runtime image tag (default: 1.0.1)
    --backend BACKEND    Backend: vllm, sglang, trtllm (default: vllm)
    --open-grafana       Auto-open Grafana dashboard
    --dry-run            Show what would be done without executing
    --help               Show this help message

TIMING OPTIONS:
    --phase1-duration S  Normal shopping duration (default: 120s)
    --phase2-duration S  Black Friday burst duration (default: 180s)
    --phase3-duration S  Sustained peak duration (default: 120s)
    --phase4-duration S  Wind-down duration (default: 120s)

TRAFFIC OPTIONS:
    --normal-rps N       Normal request rate (default: 5)
    --burst-rps N        Burst request rate (default: 150)
    --stress-test        Use concurrency burst mode (RECOMMENDED for demo)
                         This saturates the GPU and reliably triggers scaling.
                         Rate-limited mode may not generate enough load.

EXAMPLES:
    # Quick demo with defaults (recommended: use --stress-test for reliable scaling)
    ./demo-ecommerce.sh --full-demo --stress-test

    # Shorter demo for testing
    ./demo-ecommerce.sh --full-demo --stress-test --phase1-duration 60 --phase2-duration 90

    # Load test only (deployment already exists)
    ./demo-ecommerce.sh --load-test-only --stress-test --namespace my-ns

    # View Grafana during demo
    ./demo-ecommerce.sh --full-demo --open-grafana

GRAFANA DASHBOARD:
    The demo works best when viewed alongside the Dynamo Planner Dashboard.
    Access it at: http://localhost:3000 (after --open-grafana or manual port-forward)
    Dashboard name: "Dynamo Planner Dashboard"

EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --full-demo)
                FULL_DEMO=true
                shift
                ;;
            --load-test-only)
                LOAD_TEST_ONLY=true
                shift
                ;;
            --deploy-only)
                DEPLOY_ONLY=true
                shift
                ;;
            --cleanup)
                CLEANUP=true
                shift
                ;;
            --namespace)
                NAMESPACE="$2"
                shift 2
                ;;
            --image-tag)
                IMAGE_TAG="$2"
                shift 2
                ;;
            --backend)
                BACKEND="$2"
                shift 2
                ;;
            --open-grafana)
                OPEN_GRAFANA=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --phase1-duration)
                PHASE1_DURATION="$2"
                shift 2
                ;;
            --phase2-duration)
                PHASE2_DURATION="$2"
                shift 2
                ;;
            --phase3-duration)
                PHASE3_DURATION="$2"
                shift 2
                ;;
            --phase4-duration)
                PHASE4_DURATION="$2"
                shift 2
                ;;
            --normal-rps)
                NORMAL_RPS="$2"
                shift 2
                ;;
            --burst-rps)
                BURST_RPS="$2"
                shift 2
                ;;
            --total-gpus)
                TOTAL_GPUS="$2"
                shift 2
                ;;
            --model-cache-pvc)
                MODEL_CACHE_PVC="$2"
                shift 2
                ;;
            --model-cache-mount-path)
                MODEL_CACHE_MOUNT_PATH="$2"
                shift 2
                ;;
            --model-cache-model-path)
                MODEL_CACHE_MODEL_PATH="$2"
                shift 2
                ;;
            --skip-profiling)
                SKIP_PROFILING=true
                shift
                ;;
            --stress-test)
                STRESS_TEST=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done

    # Validate mode selection
    local mode_count=0
    [[ "$FULL_DEMO" == true ]] && ((mode_count++)) || true
    [[ "$LOAD_TEST_ONLY" == true ]] && ((mode_count++)) || true
    [[ "$DEPLOY_ONLY" == true ]] && ((mode_count++)) || true
    [[ "$CLEANUP" == true ]] && ((mode_count++)) || true

    if [[ $mode_count -eq 0 ]]; then
        log_error "Please specify a demo mode: --full-demo, --load-test-only, --deploy-only, or --cleanup"
        echo "Use --help for usage information"
        exit 1
    elif [[ $mode_count -gt 1 ]]; then
        log_error "Please specify only one demo mode"
        exit 1
    fi
}

# =============================================================================
# Prerequisites and Setup
# =============================================================================
check_prerequisites() {
    log_info "Checking prerequisites..."

    # kubectl
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl not found. Please install kubectl."
        exit 1
    fi

    # Cluster connectivity
    if ! kubectl cluster-info &> /dev/null; then
        log_error "Cannot connect to Kubernetes cluster."
        exit 1
    fi

    # Dynamo CRDs
    if ! kubectl get crd dynamographdeployments.nvidia.com &> /dev/null; then
        log_error "Dynamo CRDs not found. Please install Dynamo operator."
        exit 1
    fi

    # aiperf (for load testing)
    if ! command -v aiperf &> /dev/null; then
        log_warning "aiperf not found. Install with: pip install aiperf"
        if [[ "$DRY_RUN" != true ]] && { [[ "$LOAD_TEST_ONLY" == true ]] || [[ "$FULL_DEMO" == true ]]; }; then
            log_error "aiperf is required for load testing."
            exit 1
        fi
    fi

    # Check for Prometheus
    if kubectl get svc -n monitoring 2>/dev/null | grep -q prometheus; then
        log_success "Prometheus detected in monitoring namespace"
    else
        log_warning "Prometheus not found. SLA Planner metrics may not work."
        log_warning "Install kube-prometheus-stack: see docs/kubernetes/observability/metrics.md"
    fi

    # GPU resources
    local gpu_count
    gpu_count=$(kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}' | tr ' ' '\n' | awk '{sum+=$1} END {print sum}')
    if [[ -z "$gpu_count" || "$gpu_count" -eq 0 ]]; then
        log_warning "No GPU resources detected."
    else
        log_success "Found $gpu_count GPU(s) available in cluster"
    fi

    log_success "Prerequisites check passed"
}

setup_output_dir() {
    OUTPUT_DIR="/tmp/ecommerce_demo_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$OUTPUT_DIR"
    DEMO_LOG="$OUTPUT_DIR/demo.log"
    touch "$DEMO_LOG"
    log_info "Demo artifacts will be saved to: $OUTPUT_DIR"
}

create_namespace() {
    if ! kubectl get namespace "$NAMESPACE" &> /dev/null; then
        log_info "Creating namespace: $NAMESPACE"
        kubectl create namespace "$NAMESPACE"
    else
        log_info "Using existing namespace: $NAMESPACE"
    fi
}

# =============================================================================
# DGDR Generation and Deployment
# =============================================================================
generate_dgdr() {
    local output_file="$OUTPUT_DIR/ecommerce-assistant-dgdr.yaml"

    log_info "Generating DGDR for E-Commerce AI Assistant..."

    cat > "$output_file" << EOF
# E-Commerce AI Assistant - DynamoGraphDeploymentRequest
# Generated by demo-ecommerce.sh
apiVersion: nvidia.com/v1beta1
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
    isl: 3000        # Average: mix of short and long queries
    osl: 300         # Average: mix of short and long responses

  sla:
    ttft: ${TTFT_TARGET}     # Time To First Token target (ms)
    itl: ${ITL_TARGET}       # Inter-Token Latency target (ms)

  features:
    planner:
      enable_throughput_scaling: true
      enable_load_scaling: false
      mode: disagg
$( [[ -n "$TOTAL_GPUS" ]] && cat <<HWEOF

  hardware:
    totalGpus: ${TOTAL_GPUS}
HWEOF
)
$( [[ -n "$MODEL_CACHE_PVC" ]] && cat <<MCEOF

  modelCache:
    pvcName: ${MODEL_CACHE_PVC}
$( [[ -n "$MODEL_CACHE_MOUNT_PATH" ]] && echo "    pvcMountPath: ${MODEL_CACHE_MOUNT_PATH}" )
$( [[ -n "$MODEL_CACHE_MODEL_PATH" ]] && echo "    pvcModelPath: ${MODEL_CACHE_MODEL_PATH}" )
MCEOF
)
EOF

    log_success "Generated DGDR: $output_file"
    echo "$output_file"
}

deploy_dgdr() {
    local dgdr_file="$1"

    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY RUN] Would apply: $dgdr_file"
        cat "$dgdr_file"
        return 0
    fi

    log_info "Applying DGDR to cluster..."
    kubectl apply -f "$dgdr_file"

    if [[ "$SKIP_PROFILING" == true ]]; then
        log_info "Skipping profiling wait (--skip-profiling)"
        return 0
    fi

    # Monitor profiling progress
    log_phase "🔬" "PROFILING" "Finding optimal GPU configuration..."

    local max_wait=300
    local elapsed=0
    local last_phase=""
    local last_profiling_phase=""

    while [[ $elapsed -lt $max_wait ]]; do
        local phase profiling_phase
        phase=$(kubectl get dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Pending")
        profiling_phase=$(kubectl get dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" -o jsonpath='{.status.profilingPhase}' 2>/dev/null || echo "")

        if [[ "$phase" != "$last_phase" || "$profiling_phase" != "$last_profiling_phase" ]]; then
            case $phase in
                "Pending")
                    log_info "Phase: Pending - Preparing profiling job..."
                    ;;
                "Profiling")
                    case $profiling_phase in
                        "SweepingPrefill")
                            log_info "Phase: Profiling - Sweeping prefill throughput..."
                            ;;
                        "SweepingDecode")
                            log_info "Phase: Profiling - Sweeping decode throughput..."
                            ;;
                        "SelectingConfig")
                            log_info "Phase: Profiling - Selecting optimal configuration..."
                            ;;
                        "BuildingCurves")
                            log_info "Phase: Profiling - Building performance curves..."
                            ;;
                        "GeneratingDGD")
                            log_info "Phase: Profiling - Generating deployment spec..."
                            ;;
                        *)
                            log_info "Phase: Profiling - ${profiling_phase:-analyzing configurations}..."
                            ;;
                    esac
                    ;;
                "Ready")
                    log_success "Profiling complete! Optimal configuration found."
                    # Show selected config if available
                    local selected_config
                    selected_config=$(kubectl get dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" \
                        -o jsonpath='{.status.profilingResults.selectedConfig}' 2>/dev/null || echo "")
                    if [[ -n "$selected_config" ]]; then
                        log_info "Selected config: $selected_config"
                    fi
                    return 0
                    ;;
                "Deploying")
                    log_success "Profiling complete! Deployment created via autoApply."
                    return 0
                    ;;
                "Deployed")
                    log_success "Profiling complete and deployment created!"
                    return 0
                    ;;
                "Failed")
                    log_error "Profiling failed!"
                    kubectl describe dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" | tail -20
                    return 1
                    ;;
            esac
            last_phase="$phase"
            last_profiling_phase="$profiling_phase"
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done

    log_error "Timeout waiting for profiling to complete"
    return 1
}

wait_for_deployment() {
    log_phase "🚀" "DEPLOYMENT" "Waiting for E-Commerce AI Assistant to be ready..."

    # Wait for DGD to be ready
    local max_wait=600
    local elapsed=0

    while [[ $elapsed -lt $max_wait ]]; do
        if kubectl get dgd "$DEPLOYMENT_NAME" -n "$NAMESPACE" &> /dev/null; then
            local ready
            ready=$(kubectl get dgd "$DEPLOYMENT_NAME" -n "$NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")

            if [[ "$ready" == "True" ]]; then
                log_success "Deployment is ready!"
                show_pod_status
                return 0
            fi
        fi

        log_info "Waiting for pods to be ready... (${elapsed}s elapsed)"
        show_pod_status
        sleep 15
        elapsed=$((elapsed + 15))
    done

    log_error "Timeout waiting for deployment"
    return 1
}

# =============================================================================
# Port Forwarding and Grafana
# =============================================================================
setup_port_forward() {
    log_info "Setting up port forwarding to frontend service..."

    # Try to find the frontend service - check multiple naming patterns
    local frontend_svc=""
    
    # First try the expected deployment name pattern
    if kubectl get svc "${DEPLOYMENT_NAME}-frontend" -n "$NAMESPACE" &> /dev/null; then
        frontend_svc="${DEPLOYMENT_NAME}-frontend"
    # Then try to find any frontend service in the namespace
    elif kubectl get svc -n "$NAMESPACE" -o name 2>/dev/null | grep -q "frontend"; then
        frontend_svc=$(kubectl get svc -n "$NAMESPACE" -o name 2>/dev/null | grep "frontend" | grep -v "\-d$\|\-p$" | head -1 | sed 's|service/||')
    fi

    if [[ -z "$frontend_svc" ]]; then
        log_error "No frontend service found in namespace $NAMESPACE"
        log_info "Available services:"
        kubectl get svc -n "$NAMESPACE" 2>/dev/null || true
        return 1
    fi
    
    log_info "Using frontend service: $frontend_svc"
    FRONTEND_SVC="$frontend_svc"  # Store for restart_port_forward

    # Kill existing port-forward if any
    pkill -f "port-forward.*$LOCAL_PORT" 2>/dev/null || true
    sleep 2

    # Use nohup for more robust port forwarding that survives terminal issues
    nohup kubectl port-forward "svc/$frontend_svc" "$LOCAL_PORT:8000" -n "$NAMESPACE" \
        > "$OUTPUT_DIR/port-forward.log" 2>&1 &
    PORT_FORWARD_PID=$!

    # Wait for port-forward to be ready
    for i in {1..30}; do
        if curl -s "http://localhost:$LOCAL_PORT/health" &> /dev/null; then
            log_success "Frontend accessible at http://localhost:$LOCAL_PORT"
            return 0
        fi
        sleep 1
    done

    log_error "Failed to establish port forwarding"
    return 1
}

restart_port_forward() {
    # Restart port forward if it died (common issue during long load tests)
    if ! curl -s "http://localhost:$LOCAL_PORT/health" &> /dev/null; then
        log_warning "Port forward died, restarting..."
        pkill -f "port-forward.*$LOCAL_PORT" 2>/dev/null || true
        sleep 2
        nohup kubectl port-forward "svc/$FRONTEND_SVC" "$LOCAL_PORT:8000" -n "$NAMESPACE" \
            > "$OUTPUT_DIR/port-forward.log" 2>&1 &
        PORT_FORWARD_PID=$!
        sleep 3
        if curl -s "http://localhost:$LOCAL_PORT/health" &> /dev/null; then
            log_success "Port forward restarted successfully"
        else
            log_error "Failed to restart port forward"
            return 1
        fi
    fi
    return 0
}

setup_grafana() {
    if [[ "$OPEN_GRAFANA" != true ]]; then
        return 0
    fi

    log_info "Setting up Grafana access..."

    # Find Grafana service
    local grafana_svc=""
    if kubectl get svc prometheus-grafana -n monitoring &> /dev/null; then
        grafana_svc="prometheus-grafana"
    elif kubectl get svc grafana -n monitoring &> /dev/null; then
        grafana_svc="grafana"
    fi

    if [[ -z "$grafana_svc" ]]; then
        log_warning "Grafana service not found in monitoring namespace"
        return 0
    fi

    pkill -f "port-forward.*$grafana_svc" 2>/dev/null || true
    sleep 1

    kubectl port-forward "svc/$grafana_svc" "$GRAFANA_PORT:80" -n monitoring \
        > "$OUTPUT_DIR/grafana-port-forward.log" 2>&1 &
    GRAFANA_PID=$!

    sleep 3
    if curl -s "http://localhost:$GRAFANA_PORT" &> /dev/null; then
        log_success "Grafana accessible at http://localhost:$GRAFANA_PORT"
        log_info "Navigate to: Dashboards → Dynamo Planner Dashboard"
        log_info "Default credentials: admin / prom-operator"
    fi
}

cleanup_port_forwards() {
    [[ -n "$PORT_FORWARD_PID" ]] && kill "$PORT_FORWARD_PID" 2>/dev/null || true
    [[ -n "$GRAFANA_PID" ]] && kill "$GRAFANA_PID" 2>/dev/null || true
}

# =============================================================================
# Load Test Phases (E-Commerce Scenario)
# =============================================================================
run_aiperf_phase() {
    local phase_name="$1"
    local rps="$2"
    local duration="$3"
    local isl="$4"
    local osl="$5"
    local artifact_dir="$6"

    mkdir -p "$artifact_dir"
    
    # Ensure port forward is healthy before starting
    restart_port_forward || return 1

    if [[ "$STRESS_TEST" == true ]]; then
        # Concurrency burst mode - maximum throughput, no rate limiting
        local concurrency=200
        local num_sessions=$((concurrency * 5))
        log_info "🔥 STRESS TEST: Concurrency burst mode"
        log_info "Traffic: ${concurrency} concurrent | ISL: ${isl} | OSL: ${osl} | Duration: ${duration}s"
        log_info "This will saturate the GPU(s) and trigger SLA violations"
        log_info "Expected: TTFT/ITL will exceed targets → correction factor increases → scale-up triggered"
        log_info "Note: New workers take 3-8 minutes to warm up (model loading + torch.compile)"

        aiperf profile \
            --model "$MODEL" \
            --tokenizer "$MODEL" \
            --endpoint-type chat \
            --url "localhost:$LOCAL_PORT" \
            --streaming \
            --synthetic-input-tokens-mean "$isl" \
            --output-tokens-mean "$osl" \
            --concurrency "$concurrency" \
            --benchmark-duration "$duration" \
            --goodput "time_to_first_token:${TTFT_TARGET} inter_token_latency:${ITL_TARGET}" \
            --artifact-dir "$artifact_dir" \
            --ui-type simple \
            2>&1 | tee "$artifact_dir/aiperf.log" || {
                log_warning "Phase completed with warnings"
            }
    else
        # Rate-limited mode - realistic traffic pattern
        local request_count=$(echo "$rps * $duration" | bc | cut -d. -f1)
        log_info "Traffic: ${rps} req/s | ISL: ${isl} | OSL: ${osl} | Duration: ${duration}s"
        log_info "Expected requests: ~$request_count"

        aiperf profile \
            --model "$MODEL" \
            --tokenizer "$MODEL" \
            --endpoint-type chat \
            --url "localhost:$LOCAL_PORT" \
            --streaming \
            --synthetic-input-tokens-mean "$isl" \
            --output-tokens-mean "$osl" \
            --request-rate "$rps" \
            --concurrency 100 \
            --request-count "$request_count" \
            --goodput "time_to_first_token:${TTFT_TARGET} inter_token_latency:${ITL_TARGET}" \
            --artifact-dir "$artifact_dir" \
            --ui-type simple \
            2>&1 | tee "$artifact_dir/aiperf.log" || {
                log_warning "Phase completed with warnings"
            }
    fi

    show_pod_status
}

run_ecommerce_load_test() {
    log_phase "🛒" "BLACK FRIDAY LOAD TEST" "Simulating e-commerce traffic patterns"

    local test_dir="$OUTPUT_DIR/load_test"
    mkdir -p "$test_dir"

    # Initial state
    log_info "Initial cluster state:"
    show_pod_status
    echo ""

    # =========================================================================
    # PHASE 1: Normal Shopping
    # =========================================================================
    log_phase "😊" "PHASE 1: NORMAL SHOPPING" \
        "Customers browsing product recommendations - short queries, low traffic"

    echo -e "${CYAN}Sample Query:${NC} \"Show me the best deals on wireless headphones\""
    echo -e "${CYAN}Expected Response:${NC} Brief product recommendation (~100 tokens)"
    echo ""

    run_aiperf_phase "normal_ops" \
        "$NORMAL_RPS" "$PHASE1_DURATION" "$SHORT_ISL" "$SHORT_OSL" \
        "$test_dir/phase1_normal"

    log_info "Transition pause - collecting metrics..."
    sleep 15

    # =========================================================================
    # PHASE 2: Black Friday Launch!
    # =========================================================================
    log_phase "⚠️" "PHASE 2: BLACK FRIDAY LAUNCH!" \
        "Flash deals go live - traffic SPIKES, queries get LONGER"

    echo ""
    echo -e "${RED}${BOLD}🚨 BLACK FRIDAY DEALS ARE LIVE!${NC}"
    echo -e "${RED}${BOLD}   Shoppers flooding the site for deal comparisons...${NC}"
    echo ""
    echo -e "${CYAN}Sample Query:${NC} \"Compare these 5 gaming laptops under \$1500. I need"
    echo -e "              good battery life for travel, at least 16GB RAM, and"
    echo -e "              compatibility with my existing USB-C dock. Also suggest"
    echo -e "              any bundle deals with peripherals.\""
    echo -e "${CYAN}Expected Response:${NC} Detailed product comparison (~500 tokens)"
    echo ""

    log_info "🔄 Planner should detect increased load and scale UP workers..."
    echo ""

    run_aiperf_phase "blackfriday_burst" \
        "$BURST_RPS" "$PHASE2_DURATION" "$LONG_ISL" "$LONG_OSL" \
        "$test_dir/phase2_blackfriday"

    log_info "Transition pause - observing scaling response..."
    sleep 20

    # =========================================================================
    # PHASE 3: Sustained Peak Shopping
    # =========================================================================
    log_phase "📈" "PHASE 3: SUSTAINED PEAK" \
        "Deal comparisons and cart optimization with mixed query lengths"

    echo -e "${YELLOW}Shoppers still comparing deals, but some quick price checks too${NC}"
    echo ""

    # Mix of short and long queries at moderate-high rate
    local mixed_rps=$((BURST_RPS * 3 / 4))
    local mixed_isl=$(( (SHORT_ISL + LONG_ISL) / 2 ))
    local mixed_osl=$(( (SHORT_OSL + LONG_OSL) / 2 ))

    run_aiperf_phase "sustained_load" \
        "$mixed_rps" "$PHASE3_DURATION" "$mixed_isl" "$mixed_osl" \
        "$test_dir/phase3_sustained"

    log_info "Transition pause..."
    sleep 15

    # =========================================================================
    # PHASE 4: Wind-Down
    # =========================================================================
    log_phase "😌" "PHASE 4: WIND-DOWN" \
        "Sale ending - traffic returning to normal"

    echo -e "${GREEN}Carts checked out, deals winding down...${NC}"
    echo ""

    log_info "🔄 Planner should detect decreased load and scale DOWN workers..."
    echo ""

    run_aiperf_phase "recovery" \
        "$NORMAL_RPS" "$PHASE4_DURATION" "$SHORT_ISL" "$SHORT_OSL" \
        "$test_dir/phase4_recovery"

    # =========================================================================
    # COOLDOWN: Observe Scale-Down
    # =========================================================================
    log_phase "📉" "COOLDOWN" \
        "Observing graceful scale-down behavior"

    log_info "Waiting ${COOLDOWN_DURATION}s to observe worker scale-down..."

    local cooldown_elapsed=0
    while [[ $cooldown_elapsed -lt $COOLDOWN_DURATION ]]; do
        show_pod_status
        sleep 30
        cooldown_elapsed=$((cooldown_elapsed + 30))
    done

    log_success "Load test complete!"
}

# =============================================================================
# Results Summary
# =============================================================================
print_results_summary() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}                    ${BOLD}DEMO RESULTS SUMMARY${NC}                        ${CYAN}║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${BOLD}Demo Configuration:${NC}"
    echo "  Namespace:    $NAMESPACE"
    echo "  Model:        $MODEL"
    echo "  Backend:      $BACKEND"
    echo "  SLA Targets:  TTFT ≤ ${TTFT_TARGET}ms, ITL ≤ ${ITL_TARGET}ms"
    echo ""

    echo -e "${BOLD}Traffic Scenario:${NC}"
    echo "  Phase 1 (Normal):       ${NORMAL_RPS} req/s, ISL=${SHORT_ISL}, OSL=${SHORT_OSL}"
    echo "  Phase 2 (Black Friday): ${BURST_RPS} req/s, ISL=${LONG_ISL}, OSL=${LONG_OSL}"
    echo "  Phase 3 (Sustained):    $((BURST_RPS * 3 / 4)) req/s, mixed ISL/OSL"
    echo "  Phase 4 (Wind-Down):    ${NORMAL_RPS} req/s, ISL=${SHORT_ISL}, OSL=${SHORT_OSL}"
    echo ""

    echo -e "${BOLD}Final Cluster State:${NC}"
    show_pod_status
    echo ""

    if [[ -f "$OUTPUT_DIR/pod_scaling.log" ]]; then
        echo -e "${BOLD}Pod Scaling History:${NC}"
        cat "$OUTPUT_DIR/pod_scaling.log"
        echo ""
    fi

    echo -e "${BOLD}Artifacts:${NC}"
    echo "  Demo log:      $DEMO_LOG"
    echo "  AIPerf results: $OUTPUT_DIR/load_test/"
    echo ""

    echo -e "${BOLD}Next Steps:${NC}"
    echo "  1. View detailed metrics in Grafana:"
    echo "     kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring"
    echo "     Open: http://localhost:3000 → Dynamo Planner Dashboard"
    echo ""
    echo "  2. Check planner logs:"
    echo "     kubectl logs -f deployment/${DEPLOYMENT_NAME}-planner -n $NAMESPACE"
    echo ""
    echo "  3. Analyze AIPerf results:"
    echo "     ls -la $OUTPUT_DIR/load_test/*/summary.json"
    echo ""
    echo "  4. Cleanup when done:"
    echo "     $0 --cleanup --namespace $NAMESPACE"
    echo ""
}

# =============================================================================
# Cleanup
# =============================================================================
cleanup_resources() {
    log_phase "🧹" "CLEANUP" "Removing demo resources from namespace: $NAMESPACE"

    # Delete DGDR (cascades to DGD)
    if kubectl get dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" &> /dev/null; then
        log_info "Deleting DGDR: $DEPLOYMENT_NAME"
        kubectl delete dgdr "$DEPLOYMENT_NAME" -n "$NAMESPACE" --ignore-not-found
    fi

    # Delete any remaining DGD
    if kubectl get dgd "$DEPLOYMENT_NAME" -n "$NAMESPACE" &> /dev/null; then
        log_info "Deleting DGD: $DEPLOYMENT_NAME"
        kubectl delete dgd "$DEPLOYMENT_NAME" -n "$NAMESPACE" --ignore-not-found
    fi

    # Delete ConfigMaps
    kubectl delete configmap "dgdr-output-${DEPLOYMENT_NAME}" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
    kubectl delete configmap "planner-profile-data" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true

    # Wait for pods to terminate
    log_info "Waiting for pods to terminate..."
    sleep 30

    # Check if namespace is empty (except for default resources)
    local pod_count
    pod_count=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
    if [[ "$pod_count" -eq 0 ]]; then
        log_success "All demo pods terminated"
    else
        log_warning "Some pods still running in namespace"
        kubectl get pods -n "$NAMESPACE"
    fi

    log_success "Cleanup complete!"
}

# =============================================================================
# Background Pod Monitoring
# =============================================================================
start_pod_monitor() {
    local log_file="$OUTPUT_DIR/pod_scaling.log"

    (
        while true; do
            local timestamp=$(date +%H:%M:%S)
            local prefill decode
            prefill=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=prefill" --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
            decode=$(kubectl get pods -n "$NAMESPACE" -l "nvidia.com/dynamo-sub-component-type=decode" --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
            echo "$timestamp Prefill=$prefill Decode=$decode" >> "$log_file"
            sleep 15
        done
    ) &
    MONITOR_PID=$!
}

stop_pod_monitor() {
    [[ -n "$MONITOR_PID" ]] && kill "$MONITOR_PID" 2>/dev/null || true
}

# =============================================================================
# Main Entry Point
# =============================================================================
main() {
    parse_args "$@"

    banner

    # Setup
    setup_output_dir
    check_prerequisites

    # Cleanup on exit
    trap 'cleanup_port_forwards; stop_pod_monitor' EXIT

    # Handle cleanup mode
    if [[ "$CLEANUP" == true ]]; then
        cleanup_resources
        exit 0
    fi

    # Create namespace
    create_namespace

    # Deploy if needed
    if [[ "$FULL_DEMO" == true ]] || [[ "$DEPLOY_ONLY" == true ]]; then
        local dgdr_file
        dgdr_file=$(generate_dgdr)

        deploy_dgdr "$dgdr_file" || exit 1

        # In dry-run mode, just show what would be deployed and exit
        if [[ "$DRY_RUN" == true ]]; then
            log_success "[DRY RUN] Demo validated successfully!"
            echo ""
            echo "To run the actual demo:"
            echo "  $0 --full-demo"
            exit 0
        fi

        wait_for_deployment || exit 1

        if [[ "$DEPLOY_ONLY" == true ]]; then
            log_success "Deployment complete!"
            echo ""
            echo "To run the load test:"
            echo "  $0 --load-test-only --namespace $NAMESPACE"
            exit 0
        fi
    fi

    # Load test
    if [[ "$FULL_DEMO" == true ]] || [[ "$LOAD_TEST_ONLY" == true ]]; then
        setup_port_forward || exit 1
        setup_grafana
        start_pod_monitor

        run_ecommerce_load_test

        stop_pod_monitor
        print_results_summary
    fi

    echo ""
    log_success "🛒  E-Commerce Demo Complete! 🛒"
    echo ""
}

main "$@"