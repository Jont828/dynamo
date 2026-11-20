#!/usr/bin/env bash
# Kill Dynamo/vLLM/etcd/NATS processes orphaned by a finished run (parent PID 1).
# A finished multi-shard run can leave vLLM EngineCore + TP workers holding GPU memory
# after the recipe's dynamo.vllm wrappers exit. A live run's processes always have a
# living parent, so they are never selected. Each process is signalled by explicit PID.
#   bash cleanup-orphans.sh           # SIGTERM, then SIGKILL survivors after 20 s
#   bash cleanup-orphans.sh --check   # list only; exit 1 if any orphans exist
set -euo pipefail

PATTERN='VLLM::|_dynamo_vllm_with_control|dynamo\.frontend|dynamo\.vllm|multiprocessing\.resource_tracker|(^|[ /])etcd( |$)|nats-server'

list_orphans() {
    ps -eo pid=,ppid=,stat=,args= | awk -v pat="$PATTERN" '$2 == 1 && $3 !~ /^Z/ { pid = $1; $1 = $2 = $3 = ""; if ($0 ~ pat) print pid }'
}

mapfile -t pids < <(list_orphans)
if [ "${#pids[@]}" -eq 0 ]; then
    echo "[cleanup] no orphaned Dynamo/vLLM processes"
    exit 0
fi
echo "[cleanup] ${#pids[@]} orphaned process(es):"
ps -o pid=,etime=,args= -p "$(IFS=,; echo "${pids[*]}")" | cut -c1-120
if [ "${1:-}" = "--check" ]; then
    exit 1
fi

for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
for _ in $(seq 1 20); do
    mapfile -t pids < <(list_orphans)
    [ "${#pids[@]}" -eq 0 ] && break
    sleep 1
done
for pid in "${pids[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
sleep 2
echo "[cleanup] done; GPU memory used (MiB): $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | tr '\n' ' ')"
