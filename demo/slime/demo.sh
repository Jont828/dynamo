#!/usr/bin/env bash
# Narrated terminal demo: slime GRPO training with Dynamo-managed SGLang rollouts on AKS.
#
# Assumes README steps 1-3 are done (platform prereqs, storage, prepared model/data, Dynamo
# example ConfigMap). Deploys the DGD, runs the RayJob, shows the evidence, then cleans up.
#   asciinema rec --idle-time-limit 3 --window-size 160x45 -c ./demo.sh recordings/slime-dynamo-grpo.cast
set -uo pipefail
cd "$(dirname "$0")"

NS=slime

say() { printf '\n'; printf '\033[1;36m# %s\033[0m\n' "$@"; sleep "${SAY_PAUSE:-3}"; }
run() { printf '\033[1;32m$\033[0m \033[1m%s\033[0m\n' "$*"; eval "$@" 2> >(grep -v -E '^Warning: \[azurepolicy' >&2); }
abort() { printf '\n\033[1;31m# Step failed; stopping the demo.\033[0m\n'; exit 1; }
hr() { printf '\033[2m%s\033[0m\n' "------------------------------------------------------------------------"; }

# Condenses the ~2.5k-line driver log to the integration milestones.
read -r -d '' FILTER <<'PY'
import re, sys
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.startswith("ready: "):
        print("  " + line)
    elif "Detected external SGLang engines" in line:
        urls = re.findall(r"'url': '([^']+)'", line)
        print("  slime discovered external engines: " + ", ".join(urls))
    elif "Router launched at" in line:
        print("  slime router: " + line.split("Router launched at ")[1].split(",")[0] + " (engine registry only; rollouts go to Dynamo)")
    elif "Saving model in HuggingFace format to" in line:
        version = line.rsplit("weight_v", 1)[1].lstrip("0")
        print(f"  policy v{version}: trainer writes {line.split('format to ')[1]}")
    elif "Timer update_weights end" in line:
        print("    written " + line.split("update_weights end ")[1] + "; both engines reload it via /update_weights_from_disk")
    else:
        m = re.search(r"rollout (\d+): \{'rollout/response_lengths': ([0-9.]+).*'rollout/raw_reward': ([0-9.]+)", line)
        s = re.search(r"step (\d+): \{.*'train/train_rollout_logprob_abs_diff': ([0-9.e-]+), 'train/grad_norm': ([0-9.e-]+)", line)
        if m:
            print(f"  rollout {m.group(1)}: 64 samples generated through Dynamo, mean reward {float(m.group(3)):.3f}, mean response {float(m.group(2)):.0f} tokens")
        elif s:
            print(f"  train step {s.group(1)}: grad norm {float(s.group(3)):.2f}, |trainer - rollout logprob| {float(s.group(2)):.4f}")
        elif re.search(r"Job '.*' (succeeded|failed)", line):
            print("  " + line.split("-- ", 1)[-1])
    sys.stdout.flush()
PY

clear
say "slime + NVIDIA Dynamo on AKS: RL training where Dynamo serves the rollouts." \
    "Trainer: slime (Megatron) in a KubeRay RayJob. Inference: 2 SGLang engines in a Dynamo DynamoGraphDeployment (DGD)." \
    "Model: Qwen3-0.6B, GRPO on GSM8K, 10 rollout -> train -> weight-sync loops."

say "1) Cluster prerequisites: H100 node pool, Dynamo platform 1.6.1 and the KubeRay operator (both Helm installs)."
run "kubectl get nodes -l agentpool=ndh100pool -L node.kubernetes.io/instance-type,nvidia.com/gpu.count"
run "helm list -A | grep -E '^NAME|dynamo-platform|kuberay'"

say "2) Shared storage: one Azure Managed Lustre sub-directory mounted at /data by the trainer AND the engines." \
    "prepare-job.yaml filled it once (download, HF -> Megatron conversion); weight syncs will land in /data/weight-updates."
run "kubectl -n $NS get pvc slime-dynamo-data"
run "kubectl -n $NS logs job/slime-dynamo-prepare | tail -6"

say "3) The inference side: Dynamo's examples/rl/slime/dynamo.yaml, rendered with 1.6.1 images." \
    "Our only changes mount /data into the SGLang workers and load the model from it:"
run "grep -n 'slime-dynamo-aks' dynamo-dgd.yaml"
run "kubectl -n $NS apply -f dynamo-dgd.yaml" || abort
say "The operator creates a frontend plus worker-0/worker-1 (1 GPU each). Each worker runs stock SGLang with the Dynamo sidecar."
run "kubectl -n $NS wait --for=condition=Ready dgd/slime-sglang --timeout=15m" || abort
run "kubectl -n $NS get dgd slime-sglang && kubectl -n $NS get pods -l app.kubernetes.io/part-of=slime-sglang -o wide"

say "The guide's readiness checks, from inside the namespace: both native SGLang engines, then the Dynamo frontend."
WORKER0=$(kubectl -n $NS get pod -l dynamo.nvidia.com/slime-role=worker-0 -o name)
run "kubectl -n $NS exec $WORKER0 -- sh -c 'for u in slime-sglang-engine-0:30000/health_generate slime-sglang-engine-1:30000/health_generate slime-sglang-rollout:8000/health; do echo \"\$u -> \$(curl -s -o /dev/null -w %{http_code} http://\$u)\"; done'"

say "4) The training side: a KubeRay RayJob. Its entrypoint (train.sh) adds slime's model/GRPO args and execs" \
    "Dynamo's launch-slime.sh unchanged. launch-slime.sh passes the engine list and the Dynamo streaming generator."
run "grep -n -E 'entrypoint|PYTHONPATH|DYNAMO_ROLLOUT_URL|num-gpus' rayjob/rayjob.yaml"
run "grep -n -E 'update-weight|post-write|launch-slime' rayjob/train.sh"
run "kubectl -n $NS get configmap dynamo-slime-example -o jsonpath='{.data.launch-slime\.sh}' | tail -8"
run "kubectl apply -k rayjob" || abort

say "KubeRay starts a Ray head pod (1 GPU for the Megatron trainer) and a submitter pod that runs 'ray job submit'."
until kubectl -n $NS logs job/slime-dynamo >/dev/null 2>&1; do sleep 2; done
run "kubectl -n $NS get rayjob slime-dynamo && kubectl -n $NS get pods -o wide | grep -E '^NAME|^slime-dynamo-[a-z0-9]{5}(-head-[a-z0-9]{5})? '"

say "Streaming the driver log, condensed to the integration milestones:"
hr
printf '\033[1;32m$\033[0m \033[1m%s\033[0m\n' "kubectl -n $NS logs -f job/slime-dynamo | <milestone filter>"
kubectl -n $NS logs -f job/slime-dynamo | python3 -u -c "$FILTER"
hr
say "|trainer - rollout logprob| ~ 0.01 every step: the engines behind Dynamo served exactly the policy the trainer had just synced."
run "kubectl -n $NS wait --for=jsonpath='{.status.jobStatus}'=SUCCEEDED rayjob/slime-dynamo --timeout=5m && kubectl -n $NS get rayjob slime-dynamo" || abort

say "5) Evidence from the Dynamo side. The frontend routed all 640 rollout requests (10 x 64) round-robin across both workers:"
FRONTEND=$(kubectl -n $NS get pod -l dynamo.nvidia.com/slime-role=frontend -o name)
run "kubectl -n $NS logs $FRONTEND | perl -pe 's/\\e\\[[0-9;]*m//g' | grep 'Selected worker' | grep -o -E 'router_mode=\"[a-z-]+\" worker_id=[0-9]+' | sort | uniq -c"
say "...and each SGLang engine served its share and reloaded every policy version from /data (startup sync + 1 per step):"
for role in worker-0 worker-1; do
  POD=$(kubectl -n $NS get pod -l dynamo.nvidia.com/slime-role=$role -o name)
  run "kubectl -n $NS logs $POD | grep -c 'POST /generate HTTP/1.1\" 200'   # $role: requests from its Dynamo sidecar"
  run "kubectl -n $NS logs $POD | grep -c 'POST /update_weights_from_disk HTTP/1.1\" 200'   # $role: policy reloads"
done

say "Done: 10 GRPO steps, every rollout served through Dynamo, every policy update applied to both engines." \
    "Cleaning up the RayJob (its Ray cluster goes with it) and the DGD; /data keeps the model for the next run."
run "kubectl -n $NS delete rayjob slime-dynamo"
run "kubectl -n $NS delete -f dynamo-dgd.yaml"
