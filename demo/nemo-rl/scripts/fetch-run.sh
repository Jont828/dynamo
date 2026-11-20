#!/usr/bin/env bash
# Local helper for validation (not part of the demo): copy a run's run.log from the cache volume to this
# machine without kubectl exec. A short Job on the cache's node prints the file gzip+base64-encoded (container
# logs rotate at 10 MiB, and the raw log can be larger), and this script decodes its log.
#   bash scripts/fetch-run.sh <run-name> [out-file]      e.g. bash scripts/fetch-run.sh grpo-validate-2engine
set -euo pipefail
NS="${NS:-nemo-rl-demo}"
NODE="${NODE:-$(grep -m1 -o 'aks-ndh100pool-[0-9]*-vmss[0-9]*' "$(dirname "$0")/../k8s/rayjob.yaml")}"
run="$1"
out="${2:-$(dirname "$0")/../runs/$run.run.log}"
k() { kubectl --context "${CONTEXT:-h100}" -n "$NS" "$@"; }

k delete job nemo-rl-fetch-run --ignore-not-found --wait=true >/dev/null
k apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: nemo-rl-fetch-run
  namespace: $NS
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      nodeSelector: {kubernetes.io/hostname: $NODE}
      containers:
        - name: fetch
          image: docker.io/jont828/nemo-rl:main-d51b2842-dynamo
          command: ["bash", "-c", "gzip -c /cache/runs/$run/run.log | base64 -w0"]
          resources: {requests: {cpu: "1", memory: 1Gi}, limits: {cpu: "1", memory: 1Gi}}
          volumeMounts: [{name: cache, mountPath: /cache, readOnly: true}]
      volumes: [{name: cache, persistentVolumeClaim: {claimName: nemo-rl-dynamo-cache}}]
EOF
k wait --for=condition=complete job/nemo-rl-fetch-run --timeout=300s >/dev/null
k logs job/nemo-rl-fetch-run | base64 -d | gunzip > "$out"
k delete job nemo-rl-fetch-run --wait=false >/dev/null
echo "$out: $(wc -l < "$out" | tr -d ' ') lines"
