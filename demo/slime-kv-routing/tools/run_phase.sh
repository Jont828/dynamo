#!/usr/bin/env bash
# Run one routing mode: deploy its DGD, check that both engines registered,
# then run the tutorial's RayJob N times against it and save every log.
#
# Usage: bash tools/run_phase.sh <phase> <dgd-manifest> [runs]
#   phase   output folder name under runs/ (for example rr or kv)
#   runs    RayJobs to run against the same DGD (default 3)
# Env: NODE (pin target), NS (default slime-kv), CTX (default h100),
#      KEEP_DGD=1 to leave the DGD running afterwards.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-python3}"   # any Python 3.10+ with PyYAML
NODE="${NODE:-aks-ndh100pool-82423760-vmss000003}"
NS="${NS:-slime-kv}"
CTX="${CTX:-h100}"
PHASE="$1"
DGD="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
RUNS="${3:-3}"
OUT="$KIT/runs/$PHASE"
mkdir -p "$OUT/applied"

k() { kubectl --context "$CTX" -n "$NS" "$@"; }
log() { echo "[$(date -u +%H:%M:%S)] $PHASE: $*"; }
pyq() { (cd /tmp && "$PY" -I -c "$1"); }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

scrape_frontend() {  # file
    k get --raw "/api/v1/namespaces/$NS/services/slime-sglang-frontend:8000/proxy/metrics" > "$1" 2>/dev/null \
        || echo "# scrape failed at $(now_iso)" > "$1"
}

# --- Render the pinned manifests that this phase applies --------------------
(cd /tmp && "$PY" -I "$KIT/tools/pin.py" "$DGD" "$NODE") > "$OUT/applied/dgd.yaml"
(cd /tmp && "$PY" -I "$KIT/tools/pin.py" "$KIT/manifests/tutorial/rayjob.yaml" "$NODE") > "$OUT/applied/rayjob.yaml"
cp "$DGD" "$OUT/applied/dgd.source.yaml"

# --- Deploy the engines and the frontend ------------------------------------
# ALLOW_UPDATE=1 applies over a running DGD (the control phase changes only the
# frontend), then waits for a frontend Pod created after the apply.
if k get dgd slime-sglang >/dev/null 2>&1 && [ "${ALLOW_UPDATE:-0}" != "1" ]; then
    log "a DGD named slime-sglang already exists; delete it first"
    exit 2
fi
log "apply DGD from $DGD, pinned to $NODE"
DGD_APPLY="$(now_iso)"
T0=$(date -u +%s)
k apply -f "$OUT/applied/dgd.yaml"
if [ "${ALLOW_UPDATE:-0}" = "1" ]; then
    for _ in $(seq 1 120); do
        fe="$(k get pods -l dynamo.nvidia.com/slime-role=frontend -o json | DGD_APPLY="$DGD_APPLY" pyq "
import json, os, sys
d = json.load(sys.stdin)
pods = [p for p in d['items'] if not p['metadata'].get('deletionTimestamp')]
new = [p for p in pods if p['metadata']['creationTimestamp'] >= os.environ['DGD_APPLY']
       and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', []))]
print('ok' if len(pods) == 1 and len(new) == 1 else 'wait')")"
        [ "$fe" = "ok" ] && break
        sleep 5
    done
fi
k wait --for=condition=Ready dgd/slime-sglang --timeout=15m
T1=$(date -u +%s)
log "DGD Ready after $((T1 - T0)) s"
printf 'dgd_apply=%s\ndgd_ready_secs=%s\n' "$DGD_APPLY" "$((T1 - T0))" > "$OUT/dgd-ready.txt"
k get pods -l dynamo.nvidia.com/slime-role -o json > "$OUT/pods-at-ready.json"

# --- Both engines must register a model card (guards against #17409) -------
WORKER_PODS="$(k get pods -l 'dynamo.nvidia.com/slime-role in (worker-0,worker-1)' -o jsonpath='{.items[*].metadata.name}')"
n=0
for _ in $(seq 1 60); do
    k get dynamoworkermetadatas -o json > "$OUT/dwm.json"
    n=$(pyq "
import json
pods = set('$WORKER_PODS'.split())
d = json.load(open('$OUT/dwm.json'))
print(sum(1 for it in d['items'] if it['metadata']['name'] in pods and it['spec']['data'].get('model_cards')))")
    [ "$n" -ge 2 ] && break
    sleep 5
done
if [ "$n" -lt 2 ]; then
    log "only $n of 2 engines registered a model card after 5 min; stopping (see dwm.json)"
    exit 3
fi
log "both engines registered with the frontend"
pyq "
import json
pods = set('$WORKER_PODS'.split())
d = json.load(open('$OUT/dwm.json'))
for it in d['items']:
    if it['metadata']['name'] in pods:
        topics = sorted({c.split('/topic/')[1].split('/')[0] for c in it['spec']['data'].get('event_channels', {})})
        print('  ', it['metadata']['name'], 'event topics:', topics, 'event sources:', len(it['spec']['data'].get('event_sources') or []))
"
for w in worker-0 worker-1; do
    k logs -l "dynamo.nvidia.com/slime-role=$w" --tail=-1 2>/dev/null \
        | grep -E 'sglang sidecar started|discovered SGLang ZMQ KV-event' | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-220 | sed "s/^/   $w: /" || true
done
if [ "${REQUIRE_KV_EVENTS:-0}" = "1" ]; then
    kv=$(pyq "
import json
pods = set('$WORKER_PODS'.split())
d = json.load(open('$OUT/dwm.json'))
print(sum(1 for it in d['items'] if it['metadata']['name'] in pods
          and any('/topic/kv-events/' in c for c in it['spec']['data'].get('event_channels', {}))))")
    if [ "$kv" -lt 2 ]; then
        log "only $kv of 2 engines advertise a kv-events channel; stopping before training"
        exit 5
    fi
    log "both engines advertise a kv-events channel"
fi

# --- Run the RayJob N times --------------------------------------------------
for r in $(seq 1 "$RUNS"); do
    RD="$OUT/run$r"
    mkdir -p "$RD"
    START="$(now_iso)"
    echo "start=$START" > "$RD/window.txt"
    scrape_frontend "$RD/frontend-metrics-before.txt"
    log "run $r: apply RayJob"
    k apply -f "$OUT/applied/rayjob.yaml"
    status=""
    for _ in $(seq 1 240); do  # up to 20 min
        status="$(k get rayjob slime-grpo -o jsonpath='{.status.jobStatus}/{.status.jobDeploymentStatus}' 2>/dev/null || true)"
        case "$status" in
            SUCCEEDED/*|FAILED/*|STOPPED/*|*/Failed) break ;;
        esac
        sleep 5
    done
    END="$(now_iso)"
    echo "end=$END" >> "$RD/window.txt"
    echo "status=$status" >> "$RD/window.txt"
    log "run $r: RayJob $status"
    sleep 3
    scrape_frontend "$RD/frontend-metrics-after.txt"
    k get rayjob slime-grpo -o yaml > "$RD/rayjob.yaml"
    k logs job/slime-grpo --tail=-1 > "$RD/driver.log" 2>&1 || true
    k logs -l dynamo.nvidia.com/slime-role=frontend --since-time="$START" --tail=-1 > "$RD/frontend.log" 2>&1 || true
    for w in worker-0 worker-1; do
        k logs -l "dynamo.nvidia.com/slime-role=$w" --since-time="$START" --tail=-1 > "$RD/$w.log" 2>&1 || true
    done
    k get events --sort-by=.lastTimestamp > "$RD/events.txt" 2>&1 || true
    k delete rayjob slime-grpo --wait=true
    for _ in $(seq 1 60); do
        [ -z "$(k get pods -l ray.io/node-type=head -o name 2>/dev/null)" ] && break
        sleep 5
    done
    case "$status" in
        SUCCEEDED/*) ;;
        *) log "run $r did not succeed; stopping the phase"; exit 4 ;;
    esac
done

k get pods -l dynamo.nvidia.com/slime-role -o json > "$OUT/pods-at-end.json"
if [ "${KEEP_DGD:-0}" != "1" ]; then
    log "delete DGD"
    k delete dgd slime-sglang --wait=true
    for _ in $(seq 1 60); do
        [ -z "$(k get pods -l dynamo.nvidia.com/slime-role -o name 2>/dev/null)" ] && break
        sleep 5
    done
fi
log "done"
