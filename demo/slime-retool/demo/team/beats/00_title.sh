# beats/00_title.sh: beat 00, the title card.
# Data: build/00_title.data.sh, from fill/00_title.py (the run's steps, duration, GPUs, and date).

beat_00() {
    local title="Let's Train Qwen3-4B on ReTool with Slime and Dynamo!" sub1 sub2
    sub1="A replay of one real run on AKS (${TITLE_DATE}): ${TITLE_STEPS} RL steps in ${TITLE_DURATION}, on one node with ${TITLE_GPUS} × H100 GPUs."
    sub2="Nothing is rerun: all output comes from the saved runs."

    beat_header "🚀" "$title" "$sub1" "$sub2"
}
