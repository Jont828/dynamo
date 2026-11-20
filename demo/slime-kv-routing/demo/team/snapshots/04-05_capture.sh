#!/usr/bin/env bash
# Read-only snapshots for the two applies whose output was never saved: the engine Services (beat 04,
# "Step 3: Serve an Inference Endpoint with Dynamo") and the slime-functions ConfigMap (beat 05, "Step 4:
# Set Up the Training Side with Slime"). The kit created both in one `kubectl apply` at setup
# (2026-11-14 03:13:26-27 UTC, README "Rerun"), before its runs, and kept no output. So each beat shows
# the apply, a dim note saying so (lib.sh unsaved_note), then the object's real state from this snapshot.
# Both shown snapshots come from one run of this script, so their AGE columns agree.
#
# Every query here is a get: no apply, delete, exec, install, or scale. Same format as capture.sh: the
# command as it appears on screen ("# screen:"), the command actually run ("# ran:"), the UTC time, "# ---",
# then the raw output. The fill scripts read only the part after "# ---".
# (Integration pass, 2026-11-19: replaces 05_capture.sh, adding the engine Services' get.)
#
# Usage: bash snapshots/04-05_capture.sh   (from demo/team; needs kubectl with the h100 context)
set -euo pipefail
cd "$(dirname "$0")"
CTX=h100
NS=slime-kv
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

snap() {
  local file="$1" screen="$2" ran="$3" out
  out=$(bash -c "$ran" 2>&1)
  {
    printf '# screen: %s\n' "$screen"
    printf '# ran:    %s\n' "$ran"
    printf '# date:   %s (UTC), read-only\n' "$NOW"
    printf '# ---\n'
    printf '%s\n' "$out"
  } > "$file"
  echo "wrote $file ($(printf '%s\n' "$out" | wc -l | tr -d ' ') lines)"
}

# --- shown on screen ---------------------------------------------------------------------------------
snap 04_engine-services-get.txt \
  "kubectl get svc -n \$NAMESPACE slime-sglang-engine-0 slime-sglang-engine-1" \
  "kubectl --context $CTX -n $NS get svc slime-sglang-engine-0 slime-sglang-engine-1"

snap 05_configmap.txt \
  "kubectl get configmap -n \$NAMESPACE slime-functions" \
  "kubectl --context $CTX -n $NS get configmap slime-functions"

# --- context only (not shown): when and how the ConfigMap was created, and its data (the fill script
# checks that the data is the tutorial's file, byte for byte) ---------------------------------------------
snap 05_configmap-created.txt \
  "(not shown) the ConfigMap's creation time and the client that created it" \
  "kubectl --context $CTX -n $NS get configmap slime-functions -o jsonpath='{.metadata.creationTimestamp}{\"\\n\"}{range .metadata.managedFields[*]}{.manager} {.operation} {.time}{\"\\n\"}{end}'"

snap 05_configmap-data.txt \
  "(not shown) the ConfigMap's data, as JSON" \
  "kubectl --context $CTX -n $NS get configmap slime-functions -o jsonpath='{.data}'"
