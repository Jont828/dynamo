# beats/00_title.sh: beat 00, the title card.
# Data: build/00_title.data.sh, from fill/00_title.py (the run's date, steps, duration, and GPUs). The
# duration is the RayJob's own clock: creationTimestamp to status.endTime in runs/rr/run1/rayjob.yaml.

beat_00() {
    local title="Let's Train Qwen3-0.6B on GSM8K with Slime and Dynamo!" sub1 sub2
    sub1="A replay of a real AKS run (${TITLE_DATE}): ${TITLE_STEPS} RL steps in ${TITLE_DURATION}, on ${TITLE_GPUS} of one node's ${TITLE_NODE_GPUS} H100 GPUs."
    sub2="Nothing is rerun: all output is real, from saved files."

    beat_header "🚀" "$title" "$sub1" "$sub2"
}
