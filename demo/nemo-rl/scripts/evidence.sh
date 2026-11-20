#!/usr/bin/env bash
# Raw Dynamo evidence from a finished run, read from its run.log on the cache volume
# (k8s/evidence-job.yaml; no kubectl exec). The frontend runs inside the RayJob's head pod and logs to the
# driver's output, so its lines are in run.log rather than in a pod log of their own.
#   RUN_DIR=/cache/runs/<run> bash evidence.sh      (default: the newest grpo-* run)
set -uo pipefail

RUN_DIR="${RUN_DIR:-$(ls -td /cache/runs/grpo-* 2>/dev/null | head -1)}"
if [ ! -s "$RUN_DIR/run.log" ]; then
    echo "no run.log under ${RUN_DIR:-/cache/runs}" >&2
    exit 1
fi
cd "$RUN_DIR"
show() {  # print a command, then run it
    printf '\n\e[1;32m$\e[0m %s\n' "$1"
    bash -c "$1"
}
printf 'run: %s   (%s lines in run.log)\n' "$(basename "$RUN_DIR")" "$(wc -l < run.log | tr -d ' ')"

# The managed runtime's own lines: the engines it waited for, and each engine's KV-event publisher.
show "grep -E '\\[Dynamo\\] (frontend .* routes to|engine publishes)' run.log | sed -E 's/^.*(\\[Dynamo\\])/\\1/'"
# The router's applied-event counter, read from the frontend's /metrics at shutdown (non-zero statuses only).
show "grep -o '\\[Dynamo\\] router applied KV events: .*' run.log | grep -o -E '\"[a-z_]+\": [1-9][0-9.]*'"
# The frontend's KV router: one 'Selected worker' line per routing decision.
show "grep 'Selected worker' run.log | sed -E 's/.*(router_mode=\"[a-z]+\").* (worker_id=[0-9]+).*/\\1 \\2/' | sort | uniq -c"
# One 'route request completed' line per finished request; overlap_blocks is how many of the prompt's
# KV blocks the router found cached on the engine it picked.
show "grep 'route request completed request.outcome=\"success\"' run.log | grep -o 'overlap_blocks=[0-9]*' | sed -E 's/=[1-9][0-9]*\$/>0/' | sort | uniq -c"
