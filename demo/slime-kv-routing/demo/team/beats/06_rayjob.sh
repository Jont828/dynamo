# beats/06_rayjob.sh: beat 06, Step 5: launch training on KubeRay.
# A RayJob as a declarative ray job submit; rayjob.yaml typed in vim, its head at normal speed and the
# rest fast, with Qwen3-0.6B's shape and the optimizer settings folded, and callouts on train.py, the
# Dynamo engines and weight sync, Megatron-LM and the checkpoints, the step and its group of siblings,
# GRPO, and the head Pod and its one GPU. Then the apply, the head and submitter Pods (from the run's
# events and the RayJob's status: the run saved no `kubectl get pods`), and the submitter's log, grepped
# to the job's submission and train.py finding both engines. Times are on the RayJob's own clock (from its
# creationTimestamp), as in every beat. Beat 07 starts at the first sync.
# Data: build/06_rayjob.data.sh, from fill/06_rayjob.py. Plan: plans/C_slime_rayjob.md.

# rayjob_manifest: "write" rayjob.yaml: its head at normal speed, the rest fast (VIM_FAST).
rayjob_manifest() {
    local p=$RAYJOB_PROBLEMS a=$RAYJOB_ANSWERS n=$RAYJOB_PER_STEP
    narrate "Write it:"
    vim_open "$RAYJOB_FILE"
    vim_type normal "${RAYJOB_Y_HEAD[@]}"
    vim_callout "$RAYJOB_E1_FROM" "$RAYJOB_E1_TO" \
        "${BOLD}train.py${NC}, Slime's entry point, starts the trainer and a rollout manager as Ray actors, then runs the loop."
    vim_type fast "${RAYJOB_Y_ENGINES[@]}"
    vim_callout "$RAYJOB_E2_FROM" "$RAYJOB_E2_TO" \
        "Use Step 3's engines, by their Services, not new ones. Step 4's generate sends each request to Dynamo's frontend. Weights sync as files on Lustre, with Step 4's hook."
    vim_type fast "${RAYJOB_Y_CKPT[@]}"
    vim_collapse "Qwen3-0.6B's shape for Megatron-LM: ${RAYJOB_SHAPE}, ..." "${RAYJOB_Y_MODEL[@]}"
    vim_callout "$RAYJOB_E3_FROM" "$RAYJOB_E3_TO" \
        "${BOLD}Megatron-LM${NC}, NVIDIA's library for training transformers, is the trainer. It starts from Step 2's torch_dist copy and builds the network from flags: Qwen3-0.6B's shape (folded)."
    vim_type fast "${RAYJOB_Y_DATA[@]}" "${RAYJOB_Y_BATCH[@]}"
    vim_callout "$RAYJOB_E4_FROM" "$RAYJOB_E4_TO" \
        "A ${BOLD}step${NC}: one rollout, one training update, one weight sync; ${RAYJOB_STEPS} here. A rollout: ${p} problems × ${a} answers = ${n}; one problem's ${a} answers form a ${BOLD}group${NC}: ${BOLD}siblings${NC}. All ${n} go into the one update."
    vim_type fast "${RAYJOB_Y_TRAINER[@]}" "${RAYJOB_Y_GRPO[@]}"
    vim_callout "$RAYJOB_E6_FROM" "$RAYJOB_E6_TO" \
        "${BOLD}GRPO${NC} turns rewards into a weight update: answers above their siblings' average become more likely, those below less likely. Slime also offers PPO (which trains a second model as the baseline), GSPO, CISPO, REINFORCE++."
    vim_collapse "Adam, constant learning rate ${RAYJOB_LR}; no dropout; FP32 sums and softmax; FlashAttention" \
        "${RAYJOB_Y_OPTIM[@]}"
    vim_type fast "${RAYJOB_Y_ENV[@]}" "${RAYJOB_Y_CLUSTER[@]}"
    vim_callout "$RAYJOB_E8_FROM" "$RAYJOB_E8_TO" \
        "One head Pod, no workers: ${RAYJOB_GPUS} GPU, ${RAYJOB_CPUS} CPUs, ${RAYJOB_MEM}. Qwen3-0.6B fits on one GPU, so nothing is split across GPUs (the ReTool run used TP 2 × CP 2)." \
        "It mounts /data (Step 2), Step 4's ConfigMap, and ${RAYJOB_SHM} of RAM at /dev/shm for Ray."
    vim_close
}

# rayjob_wait_render T: the wait between the two log lines (T: seconds since the job's submission).
rayjob_wait_render() { ff_elapsed "$1"; }

# rayjob_launch: apply, the Pods KubeRay starts, and the submitter's log up to train.py finding the engines.
rayjob_launch() {
    printf '\n'
    narrate "Apply it:"
    show_command "kubectl apply -n \$NAMESPACE -f ${RAYJOB_FILE}"
    printf '%s\n' "$RAYJOB_APPLY_OUT"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "KubeRay starts the Ray head Pod (ready in ${RAYJOB_HEAD_READY_SECS} s), then a submitter Pod that runs ray job submit." \
            "Follow the job's log, filtered to two milestones:"
    show_command "$RAYJOB_LOG_CMD"
    printf '%s\n' "${RAYJOB_LOG_SUB_PRE}${OK}${RAYJOB_LOG_SUB_MSG}${NC}"
    nap 0.5
    ff_run rayjob_wait_render 0 "$RAYJOB_LOG_GAP"
    frame_replace "${RAYJOB_LOG_ENGINES[@]}"
    nap "$PAUSE_MED"
    printf '\n'
    narrate "train.py is running and has found both engines, ${RAYJOB_FOUND_SECS} s after the RayJob was created."
}

beat_06() {
    beat_header "🧠" "Step 5: Launch Training on KubeRay" \
        "A RayJob is a declarative ray job submit: KubeRay starts a Ray cluster, submits the entrypoint, then cleans up."
    rayjob_manifest
    rayjob_launch
}
