# Beat 09: did KV-aware routing help? KV-aware vs round-robin (results agent; charts since 2026-11-19).
# Data: build/09_compare.data.sh (fill/09_compare.py): real `diff` output, the example hit from sample 958's turn 2,
# run1's routing view (summary.json), and the real output of tools/compare_bars.py, which charts
# runs/compare-kv-rr.json in three screens: what KV-aware routing changed in the engines, whether the run got
# faster, and where each step's slowest trajectory spent its time.

COMPARE_TITLE="Did KV-Aware Routing Help? KV-Aware vs Round-Robin"

# compare_screen NARRATION CMD HOLD LINE...: a clean screen under the pinned header, one narration line, the
# command, its output (LINE...), and HOLD seconds to read the charts. A chart screen is at most 31 rows tall
# (fill/09_compare.py checks the charts' rows), so the header stays on the top row.
compare_screen() {
    local nar="$1" cmd="$2" hold_s="$3"
    shift 3
    clear_screen
    step_header_bars "📊" "$COMPARE_TITLE"
    narrate "$nar"
    show_command "$cmd"
    printf '%s\n' "$@"
    nap "$hold_s"
}

beat_09() {
    beat_header "📊" "$COMPARE_TITLE" \
        "What KV-aware routing saved, and whether that made the run faster than a round-robin baseline."
    card "Prefix cache, hits, and turns that stay" \
        "Each engine keeps a ${BOLD}prefix cache${NC}: the KV cache (attention keys and values) of requests it served. A new prompt that starts with the same tokens reuses them instead of recomputing them (prefill)." \
        "A ${BOLD}hit${NC}: sample ${COMPARE_HIT_SAMPLE}'s turn 2 sent ${COMPARE_HIT_PROMPT} prompt tokens and found ${COMPARE_HIT_CACHED} (${COMPARE_HIT_PCT}%) on worker-0. It computed only ${COMPARE_HIT_COMPUTED}: the sandbox output and a part-filled ${COMPARE_BLOCK}-token block. ${BOLD}Hit rate${NC}: cached ÷ all prompt tokens." \
        "A follow-up turn ${BOLD}stayed${NC} if it went to its previous turn's engine. With KV routing ${COMPARE_STAYED_KV}% stayed and ${COMPARE_MOVED_KV}% moved: the router also weighs load; engines' load gaps often exceeded a turn's history (median ${COMPARE_LOAD_SPREAD} vs ${COMPARE_HISTORY} blocks)."
    narrate "The baseline reran the same RayJob on the same node, engines, and problems. Only the router changed:"
    show_command "$COMPARE_DIFF_CMD"
    printf '%s\n' "${COMPARE_DIFF_OUT[@]}"
    nap "$PAUSE_MED"

    compare_screen "First, the work: what KV-aware routing changed inside the engines, over all ${COMPARE_STEPS} steps." \
        "$COMPARE_WORK_CMD" 9 "${COMPARE_WORK_OUT[@]}"
    compare_screen "Then the time: did that make the run any faster?" \
        "$COMPARE_TIME_CMD" 8 "${COMPARE_TIME_OUT[@]}"
    compare_screen "Why not? Each rollout waits for its slowest trajectory. Where those ${COMPARE_STEPS} spent their time:" \
        "$COMPARE_SLOWEST_CMD" 1 "${COMPARE_SLOWEST_OUT[@]}"
    printf '\n'
    narrate "Verdict: KV-aware routing nearly halved the prefill, not the run time. The run is decode-bound:" \
            "its slowest trajectories mostly generate tokens, and a cache hit only saves prefill." \
            "Step-to-step swings came from long replies and ${COMPARE_TIMEOUT_S} s sandbox timeouts (${COMPARE_TIMEOUTS_RR} in round-robin's, ${COMPARE_TIMEOUTS_KV} in KV-aware's)." \
            "Caveats: one run per router, and both used page size 16, which KV routing needs" \
            "(about ${COMPARE_PAGE16_PCT}% slower decode than the default, in an earlier test on a smaller model)."
    nap "$PAUSE_LONG"
}
