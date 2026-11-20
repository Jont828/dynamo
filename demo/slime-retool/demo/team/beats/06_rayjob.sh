# beats/06_rayjob.sh: Step 5, launch training on KubeRay: the RayJob, line by line, then its Pods.
#
# Data: build/06_rayjob.data.sh (fill/06_rayjob.py): manifests/rayjob.yaml without its kit-internal
# header comment, split where the beat pauses (RAYJOB_Y_*), the buffer lines each callout marks
# (RAYJOB_E*_FROM/TO), the numbers the callouts quote, the apply output (runs/run1.console.log), and
# the head and submitter Pods (runs/run1/pods-watch.txt, the first snapshot with both ready). The
# submitter's own log (`kubectl logs job/slime-retool`) is left to beat 08, which follows it.

rayjob_manifest() {
    narrate "Write the RayJob: KubeRay starts a Ray cluster, submits the entrypoint, and deletes the cluster when the job ends:"
    vim_open "$RAYJOB_FILE"
    vim_type normal "${RAYJOB_Y_HEAD[@]}"
    vim_callout "$RAYJOB_E1_FROM" "$RAYJOB_E1_TO" \
        "${BOLD}train.py${NC}, Slime's entry point, starts the trainer and the rollout manager as Ray actors and runs the loop. The rest are its flags."
    vim_type fast "${RAYJOB_Y_ENGINES[@]}"
    vim_callout "$RAYJOB_E2_FROM" "$RAYJOB_E2_TO" \
        "Use Step 3's ${RAYJOB_ENGINES} engine Services instead of launching engines. Weight sync by disk: the trainer writes each new version to /data/weight-updates, and the engines load it."
    vim_collapse "Qwen3-4B's shape and checkpoints, for Megatron-LM" "${RAYJOB_Y_MODEL[@]}"
    vim_callout "$RAYJOB_E3_FROM" "$RAYJOB_E3_TO" \
        "${BOLD}Megatron-LM${NC}: NVIDIA's library for training transformers across many GPUs, Slime's training backend."
    vim_type fast "${RAYJOB_Y_DATA[@]}"
    vim_callout "$RAYJOB_E4_FROM" "$RAYJOB_E4_TO" \
        "A ${BOLD}step${NC}: one rollout, one training update, one weight sync (${NUM}${BOLD}${RAYJOB_STEPS}${NC} here). A rollout: ${NUM}${BOLD}${RAYJOB_PROBLEMS} problems × ${RAYJOB_SAMPLES} trajectories${NC} (Slime calls each a ${BOLD}sample${NC}); a problem's ${RAYJOB_SAMPLES} are a ${BOLD}group${NC}, siblings. Global batch ${RAYJOB_GBS}: all ${RAYJOB_PER_STEP} make one optimizer update."
    vim_collapse "the optimizer (Adam), its state offloaded to CPU memory" "${RAYJOB_Y_OPTIM[@]}"
    vim_type fast "${RAYJOB_Y_GRPO[@]}"
    vim_callout "$RAYJOB_E5_FROM" "$RAYJOB_E5_TO" \
        "${BOLD}GRPO${NC} is just an algorithm for turning rewards into weight updates (Slime also has ${RAYJOB_OTHER_ALGOS}). It scores each trajectory against its group's mean and spread; above-average ones become more likely. No critic model needed."
    vim_type fast "${RAYJOB_Y_PARALLEL[@]}"
    vim_callout "$RAYJOB_E6_FROM" "$RAYJOB_E6_TO" \
        "${BOLD}TP ${RAYJOB_TP}${NC}: each layer's weight matrices are split across ${RAYJOB_TP} GPUs. ${BOLD}CP ${RAYJOB_CP}${NC}: each trajectory's tokens are split across ${RAYJOB_CP} GPUs, for up to ${RAYJOB_MAXCTX} tokens. Together, ${RAYJOB_GPUS} GPUs per model copy."
    vim_type fast "${RAYJOB_Y_FUNCS[@]}"
    vim_callout "$RAYJOB_E7_FROM" "$RAYJOB_E7_TO" \
        "Step 4's functions, from the ConfigMap on PYTHONPATH. DYNAMO_ROLLOUT_URL: where generate sends every turn."
    vim_type fast "${RAYJOB_Y_CLUSTER[@]}" "${RAYJOB_Y_VOLUMES[@]}"
    vim_callout "$RAYJOB_E8_FROM" "$RAYJOB_E9_TO" \
        "One head Pod, no workers: ${RAYJOB_GPUS} GPUs for Megatron-LM; ${RAYJOB_CPUS} CPUs and ${RAYJOB_MEM} for the CPU-offloaded optimizer and the sandbox's processes. It mounts /data, Step 2's Azure Managed Lustre volume, and Step 4's ConfigMap at /opt/slime-functions."
    vim_close
}

beat_06() {
    local title="Step 5: Launch Training on KubeRay"
    beat_header "🧠" "$title"
    rayjob_manifest
    narrate "Apply it:"
    show_command 'kubectl apply -n $NAMESPACE -f manifests/rayjob.yaml'
    printf '%s\n' "$RAYJOB_APPLY_OUT"
    nap "$PAUSE_SHORT"
    narrate "KubeRay starts the Ray head Pod, then a submitter Pod that runs ray job submit with the entrypoint:"
    show_command "kubectl get pods -n \$NAMESPACE -l ${RAYJOB_SELECTOR}"
    printf '%s\n' "${RAYJOB_PODS_OUT[@]}"
    nap 3
}
