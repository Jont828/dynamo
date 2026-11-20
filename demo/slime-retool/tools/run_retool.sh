#!/usr/bin/env bash
# Deploy the Dynamo DGD, run Slime's ReTool RayJob against it, and save everything
# the analysis and the replay demo need.
#
# Usage:
#   bash tools/run_retool.sh deploy [label]         # ConfigMap, Services, DGD -> runs/<label>/ (default deploy);
#                                                   # checks registration. DGD=<file> picks the manifest
#                                                   # (default manifests/dgd.yaml; manifests/dgd-rr.yaml
#                                                   # is the round-robin baseline).
#   bash tools/run_retool.sh rayjob <label> <steps> # one RayJob -> runs/<label>/ (e.g. smoke 1, run1 10)
#   bash tools/run_retool.sh teardown               # delete the RayJob and the DGD; keep namespace and PV
#
# Env: NODE (pin target), NS (default slime-retool), CTX (default h100), PY (Python with PyYAML).
#
# Long runs outgrow kubelet's container log rotation (about 10 MiB per file), and
# `kubectl logs` returns only the newest file, so this script streams the frontend,
# engine, and driver logs with -f from the start of each phase (*.stream.log) and
# also saves a final `kubectl logs` (*.final.log) for comparison.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-python3}"   # any Python 3.10+ with PyYAML
NODE="${NODE:-aks-ndh100pool-82423760-vmss000003}"
NS="${NS:-slime-retool}"
CTX="${CTX:-h100}"
ENGINES=(worker-0 worker-1 worker-2 worker-3)

k() { kubectl --context "$CTX" -n "$NS" "$@"; }
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
pyq() { (cd /tmp && "$PY" -I -c "$1"); }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
pin() { (cd /tmp && "$PY" -I "$KIT/tools/pin.py" "$1" "$NODE"); }

scrape_frontend() {  # file
    k get --raw "/api/v1/namespaces/$NS/services/slime-sglang-frontend:8000/proxy/metrics" > "$1" 2>/dev/null \
        || echo "# scrape failed at $(now_iso)" > "$1"
}

# Timestamped `kubectl get pods` every 2 s, for the replay's startup frames.
snap_pods() {  # file seconds
    local end=$(( $(date +%s) + $2 ))
    while [ "$(date +%s)" -lt "$end" ]; do
        { echo "=== $(now_iso)"; k get pods -o wide 2>&1; } >> "$1"
        sleep 2
    done
}

# Follow one long-running Pod's log by label. If the stream drops while the Pod still
# runs, resume from 10 s before the drop (a few duplicate lines, no gap) and mark it.
stream_label() {  # label-selector file since
    local since="$3"
    while true; do
        k logs -f -l "$1" --since-time="$since" --tail=-1 --max-log-requests=1 >> "$2" 2>&1 || true
        [ -f "$2.stop" ] && break
        since="$(date -u -v-10S +%Y-%m-%dT%H:%M:%SZ)"
        echo "### stream restarted at $(now_iso), resuming from $since" >> "$2"
        sleep 2
    done
}

deploy() {
    local OUT="$KIT/runs/$1"
    local DGD_FILE="${DGD:-$KIT/manifests/dgd.yaml}"
    DGD_FILE="$(cd "$(dirname "$DGD_FILE")" && pwd)/$(basename "$DGD_FILE")"  # pin.py runs from /tmp
    if [ -e "$OUT/dgd-ready.txt" ]; then log "$OUT already holds a deploy; pick another label"; exit 2; fi
    mkdir -p "$OUT"
    log "deploy $DGD_FILE -> $OUT"
    pin "$DGD_FILE" > "$OUT/dgd.applied.yaml"
    bash "$KIT/tools/make_functions_configmap.sh" >/dev/null
    cp "$KIT/manifests/functions.yaml" "$OUT/functions.applied.yaml"
    if k get dgd slime-sglang >/dev/null 2>&1; then
        log "a DGD named slime-sglang already exists; run teardown first"
        exit 2
    fi
    k apply -f "$KIT/manifests/functions.yaml" -f "$KIT/manifests/engine-services.yaml"
    local applied; applied="$(now_iso)"
    local t0; t0=$(date -u +%s)
    log "apply DGD, pinned to $NODE"
    k apply -f "$OUT/dgd.applied.yaml"
    snap_pods "$OUT/pods-watch.txt" 900 &
    local snap=$!
    k wait --for=condition=Ready dgd/slime-sglang --timeout=20m
    local t1; t1=$(date -u +%s)
    kill "$snap" 2>/dev/null || true
    log "DGD Ready after $((t1 - t0)) s"
    printf 'dgd_apply=%s\ndgd_ready=%s\ndgd_ready_secs=%s\n' "$applied" "$(now_iso)" "$((t1 - t0))" > "$OUT/dgd-ready.txt"
    k get pods -l dynamo.nvidia.com/slime-role -o json > "$OUT/pods-at-ready.json"
    k get dgd slime-sglang -o yaml > "$OUT/dgd-status.yaml"

    # Every engine must register a model card and a kv-events channel before training.
    local pods n kv
    pods="$(k get pods -l 'dynamo.nvidia.com/slime-role in (worker-0,worker-1,worker-2,worker-3)' -o jsonpath='{.items[*].metadata.name}')"
    for _ in $(seq 1 60); do
        k get dynamoworkermetadatas -o json > "$OUT/dwm.json"
        n=$(pyq "
import json
pods = set('$pods'.split())
d = json.load(open('$OUT/dwm.json'))
print(sum(1 for it in d['items'] if it['metadata']['name'] in pods and it['spec']['data'].get('model_cards')))")
        [ "$n" -ge 4 ] && break
        sleep 5
    done
    [ "$n" -ge 4 ] || { log "only $n of 4 engines registered a model card after 5 min (see dwm.json)"; exit 3; }
    kv=$(pyq "
import json
pods = set('$pods'.split())
d = json.load(open('$OUT/dwm.json'))
print(sum(1 for it in d['items'] if it['metadata']['name'] in pods
          and any('/topic/kv-events/' in c for c in it['spec']['data'].get('event_channels', {}))))")
    [ "$kv" -ge 4 ] || { log "only $kv of 4 engines advertise a kv-events channel; stopping"; exit 5; }
    log "all 4 engines registered a model card and advertise a kv-events channel"
    # Map each engine Pod to the worker_id that the frontend's routing log uses.
    pyq "
import json
d = json.load(open('$OUT/dwm.json'))
pods = json.load(open('$OUT/pods-at-ready.json'))['items']
role = {p['metadata']['name']: p['metadata']['labels']['dynamo.nvidia.com/slime-role'] for p in pods}
out = {}
for it in d['items']:
    name = it['metadata']['name']
    if role.get(name, '').startswith('worker-'):
        ids = sorted({ep['instance_id'] for ep in it['spec']['data'].get('endpoints', {}).values() if ep.get('endpoint') == 'generate'})
        out[role[name]] = {'pod': name, 'worker_ids': ids}
json.dump(out, open('$OUT/workers.json', 'w'), indent=2)
for w, v in sorted(out.items()): print('  ', w, v['pod'], v['worker_ids'])
"
    for w in "${ENGINES[@]}"; do
        k logs -l "dynamo.nvidia.com/slime-role=$w" --tail=-1 2>/dev/null \
            | sed 's/\x1b\[[0-9;]*m//g' | grep -E 'sglang sidecar started|discovered SGLang ZMQ KV-event' \
            | cut -c1-200 | sed "s/^/   $w: /" || true
    done | tee "$OUT/sidecar-kv-events.txt"
}

rayjob() {
    local label="$1" steps="$2"
    local RD="$KIT/runs/$label"
    if [ -e "$RD/window.txt" ]; then log "$RD already holds a run; pick another label"; exit 2; fi
    mkdir -p "$RD"
    sed -e "s/--num-rollout 10\$/--num-rollout $steps/" \
        -e "s#/data/turn-logs/run1#/data/turn-logs/$label#" \
        "$KIT/manifests/rayjob.yaml" > "$RD/rayjob.source.yaml"
    grep -q -- "--num-rollout $steps\$" "$RD/rayjob.source.yaml" || { log "could not set --num-rollout"; exit 2; }
    pin "$RD/rayjob.source.yaml" > "$RD/rayjob.applied.yaml"
    if k get rayjob slime-retool >/dev/null 2>&1; then log "RayJob slime-retool exists; delete it first"; exit 2; fi

    local START; START="$(now_iso)"
    echo "start=$START" > "$RD/window.txt"
    scrape_frontend "$RD/frontend-metrics-before.txt"
    k get pods -o json > "$RD/pods-before.json"

    # Stream the DGD's logs for the whole run.
    stream_label dynamo.nvidia.com/slime-role=frontend "$RD/frontend.stream.log" "$START" &
    local streams=($!)
    for w in "${ENGINES[@]}"; do
        stream_label "dynamo.nvidia.com/slime-role=$w" "$RD/$w.stream.log" "$START" &
        streams+=($!)
    done

    log "$label: apply RayJob ($steps steps)"
    k apply -f "$RD/rayjob.applied.yaml"
    snap_pods "$RD/pods-watch.txt" 420 &
    local snap=$!

    # The submitter Job's Pod prints the driver log; follow it once it runs, and
    # resume only while it still runs (a finished Pod would replay the whole log).
    (
        for _ in $(seq 1 180); do
            [ "$(k get pods -l job-name=slime-retool -o jsonpath='{.items[0].status.phase}' 2>/dev/null)" = Running ] && break
            sleep 5
        done
        since="$START"
        while true; do
            k logs -f job/slime-retool --since-time="$since" --tail=-1 >> "$RD/driver.stream.log" 2>&1 || true
            [ "$(k get pods -l job-name=slime-retool -o jsonpath='{.items[0].status.phase}' 2>/dev/null)" = Running ] || break
            since="$(date -u -v-10S +%Y-%m-%dT%H:%M:%SZ)"
            echo "### stream restarted at $(now_iso), resuming from $since" >> "$RD/driver.stream.log"
            sleep 2
        done
    ) &
    streams+=($!)

    local status="" limit=$(( steps <= 2 ? 360 : 900 ))  # x10 s: 60 min, or 150 min for the full run
    for _ in $(seq 1 "$limit"); do
        status="$(k get rayjob slime-retool -o jsonpath='{.status.jobStatus}/{.status.jobDeploymentStatus}' 2>/dev/null || true)"
        case "$status" in SUCCEEDED/*|FAILED/*|STOPPED/*|*/Failed) break ;; esac
        sleep 10
    done
    local END; END="$(now_iso)"
    printf 'end=%s\nstatus=%s\n' "$END" "$status" >> "$RD/window.txt"
    log "$label: RayJob $status"
    sleep 5
    scrape_frontend "$RD/frontend-metrics-after.txt"
    k get rayjob slime-retool -o yaml > "$RD/rayjob.yaml"
    k get pods -o json > "$RD/pods-at-end.json"
    k get events --sort-by=.lastTimestamp > "$RD/events.txt" 2>&1 || true
    k logs job/slime-retool --tail=-1 > "$RD/driver.final.log" 2>&1 || true
    k logs -l dynamo.nvidia.com/slime-role=frontend --since-time="$START" --tail=-1 > "$RD/frontend.final.log" 2>&1 || true
    for w in "${ENGINES[@]}"; do
        k logs -l "dynamo.nvidia.com/slime-role=$w" --since-time="$START" --tail=-1 > "$RD/$w.final.log" 2>&1 || true
    done
    # Ray's own logs from the head, before KubeRay deletes the cluster.
    local head; head="$(k get pods -l ray.io/node-type=head -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    if [ -n "$head" ]; then
        k exec "$head" -c ray-head -- tar czf - -C /tmp/ray/session_latest logs > "$RD/ray-logs.tgz" 2>/dev/null || true
    fi
    # Turn logs, read through the inspection Pod that mounts the same volume.
    mkdir -p "$RD/turn-logs"
    k exec slime-inspect -- tar cf - -C "/data/turn-logs/$label" . 2>/dev/null | tar xf - -C "$RD/turn-logs" || true
    log "$label: turn logs: $(cat "$RD"/turn-logs/*.jsonl 2>/dev/null | wc -l | tr -d ' ') lines"

    kill "$snap" 2>/dev/null || true
    for f in "$RD/frontend.stream.log" "$RD"/worker-*.stream.log; do touch "$f.stop"; done
    k delete rayjob slime-retool --wait=true
    for _ in $(seq 1 60); do
        [ -z "$(k get pods -l ray.io/node-type=head -o name 2>/dev/null)" ] && break
        sleep 5
    done
    for p in "${streams[@]}"; do kill "$p" 2>/dev/null || true; done
    # Only this namespace's followers (other sessions may follow the same labels elsewhere).
    pkill -f -- "-n $NS logs -f" 2>/dev/null || true
    sleep 3
    rm -f "$RD"/*.stop
    case "$status" in SUCCEEDED/*) log "$label: done" ;; *) log "$label: did not succeed"; exit 4 ;; esac
}

teardown() {
    k delete rayjob slime-retool --ignore-not-found --wait=true
    k delete dgd slime-sglang --ignore-not-found --wait=true
    for _ in $(seq 1 60); do
        [ -z "$(k get pods -l dynamo.nvidia.com/slime-role -o name 2>/dev/null)" ] && break
        sleep 5
    done
    log "RayJob and DGD deleted; namespace $NS and PV pv-lustre-slime-retool kept"
}

case "${1:-}" in
    deploy) deploy "${2:-deploy}" ;;
    rayjob) rayjob "$2" "$3" ;;
    teardown) teardown ;;
    *) sed -n '2,15p' "$0"; exit 1 ;;
esac
