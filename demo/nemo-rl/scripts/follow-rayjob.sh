#!/usr/bin/env bash
# Local helper for validation runs (not part of the demo): follow a RayJob's submitter log into a file,
# reattaching if the stream drops, and exit when the RayJob reaches a terminal state. Also stops early on
# pod-level failures (image pull, crash loop) so a broken run doesn't wait for activeDeadlineSeconds.
#   bash scripts/follow-rayjob.sh <rayjob> <out.log>
set -u
NS="${NS:-nemo-rl-demo}"
job="$1"
out="$2"
k() { kubectl --context "${CONTEXT:-h100}" -n "$NS" "$@"; }
field() { k get rayjob "$job" -o jsonpath="{.status.$1}" 2>/dev/null; }

: > "$out"
while :; do
    state="$(field jobDeploymentStatus)"
    case "$state" in Complete|Failed) break ;; esac
    if bad="$(k get pods -o jsonpath='{range .items[*]}{.metadata.name} {.status.containerStatuses[*].state.waiting.reason}{"\n"}{end}' 2>/dev/null \
            | grep -E 'ErrImagePull|ImagePullBackOff|CrashLoopBackOff|CreateContainerError')"; then
        echo "POD FAILURE: $bad" | tee -a "$out"
        break
    fi
    if k get job "$job" >/dev/null 2>&1; then
        printed="$(wc -l < "$out" | tr -d ' ')"
        k logs -f "job/$job" --pod-running-timeout=20m 2>/dev/null | awk -v skip="$printed" 'NR > skip { print; fflush() }' >> "$out"
    fi
    sleep 10
done
echo "rayjob $job: deployment=$(field jobDeploymentStatus) job=$(field jobStatus) at $(date -u +%T)"
k get rayjob "$job"
