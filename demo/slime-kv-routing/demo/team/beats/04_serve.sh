# beats/04_serve.sh: beat 04, Step 3: serve an inference endpoint with Dynamo.
# The DGD typed in vim: its kind at normal speed, fast-forwarded to the engine's image and command
# (typed at normal speed), a callout on the frontend (DYN_SGLANG_ENABLE_GENERATE, round-robin) and
# one on the engine command (SGLang plus Dynamo's sidecar), the --host and sidecar-endpoint lines as
# closed folds, worker-1 folded as "same as worker-0". Then the engine Services (why Slime needs them,
# and how they differ from the operator's): their apply ran at setup with no saved output, so a dim note
# says so and `kubectl get svc` shows them from a read-only snapshot. Then the DGD's apply, the engines
# starting (fast-forwarded on the Pods' recorded times, to Ready at 73 s), and each sidecar's start line,
# explained before it runs.
# Data: build/04_serve.data.sh, from fill/04_serve.py. Plan: plans/B_storage_serve.md.

# serve_type MODE A B: type dgd.yaml's buffer lines A..B (1-based, inclusive) at MODE speed.
# A flag and its value marked in SERVE_HIDE (--host 0.0.0.0, the sidecar's 127.0.0.1 endpoint) go
# in as a closed fold instead, so tools/redact_cast.py finds no address to flag in the public cast.
serve_type() {
    local mode="$1" a="$2" b="$3" i j k
    i=$a
    while (( i <= b )); do
        k=${SERVE_HIDE[i-1]}
        j=$i
        if (( k > 0 )); then
            while (( j < b )) && (( ${SERVE_HIDE[j]} == k )); do j=$(( j + 1 )); done
            vim_fold_in "${SERVE_HIDE_NOTE[k-1]}" "${SERVE_DGD[@]:i-1:j-i+1}"
        else
            while (( j < b )) && (( ${SERVE_HIDE[j]} == 0 )); do j=$(( j + 1 )); done
            vim_type "$mode" "${SERVE_DGD[@]:i-1:j-i+1}"
        fi
        i=$(( j + 1 ))
    done
    return 0
}

# serve_pods_render T: one frame of the wait (T: seconds since the apply). Each Pod's line follows its
# Ready time in runs/rr/pods-at-ready.json; the first frame holds PAUSE_SHORT so it can be read.
serve_pods_render() {
    local t="$1" n=0 w r
    if (( t >= SERVE_FE_READY_T )); then
        fl "   frontend   ${OK}✓ ready at ${SERVE_FE_READY_T} s${NC}"; n=1
    else
        fl "   frontend   ${STATUS}▸ starting...${NC}"
    fi
    for w in 0 1; do
        if (( w == 0 )); then r=$SERVE_W0_READY_T; else r=$SERVE_W1_READY_T; fi
        if (( t >= r )); then
            fl "   worker-${w}   ${OK}✓ ready at ${r} s${NC}"; n=$(( n + 1 ))
        else
            fl "   worker-${w}   ${STATUS}▸ starting SGLang: loading Qwen3-0.6B from /data, then capturing CUDA graphs...${NC}"
        fi
    done
    fl ""
    if (( n == 3 )); then
        fl "   ${OK}✅ All 3 Pods ready: both engines can generate${NC}"
    else
        fl "   ${STATUS}▸ ${n}/3 Pods ready${NC}"
    fi
    ff_elapsed "$t"
    if (( t == SERVE_CREATED_T )); then FF_TICK_NOW=$PAUSE_SHORT; fi
    return 0
}

# serve_vim_dgd: "write" dgd.yaml.
serve_vim_dgd() {
    narrate "Write the DynamoGraphDeployment (DGD): a Dynamo frontend and two SGLang engines, one GPU each:"
    vim_open "$SERVE_DGD_FILE"
    serve_type normal 1 "$SERVE_LN_KIND"
    serve_type fast $(( SERVE_LN_KIND + 1 )) $(( SERVE_LN_W0 - 1 ))
    vim_callout "$SERVE_LN_FRONTEND" $(( SERVE_LN_W0 - 1 )) \
        "The ${BOLD}frontend${NC}: Dynamo's HTTP entry point and router. DYN_SGLANG_ENABLE_GENERATE=1 adds SGLang's /generate route, which Slime calls." \
        "No router setting, so it routes ${BOLD}round-robin${NC}: each request to the next engine in turn."
    serve_type fast "$SERVE_LN_W0" $(( SERVE_LN_W0_IMAGE - 1 ))
    serve_type normal "$SERVE_LN_W0_IMAGE" "$SERVE_LN_W0_LAUNCH"
    serve_type fast $(( SERVE_LN_W0_LAUNCH + 1 )) "$SERVE_LN_W0_SIDECAR_ARGS"
    vim_callout "$SERVE_LN_W0_IMAGE" "$SERVE_LN_W0_SIDECAR_ARGS" \
        "Each ${BOLD}engine${NC} is SGLang's own server, started from Dynamo's SGLang image, on 1 GPU." \
        "--sidecar: SGLang also starts ${BOLD}Dynamo's sidecar${NC}, which connects to the engine on port 30001 and registers it with the frontend."
    serve_type fast $(( SERVE_LN_W0_SIDECAR_ARGS + 1 )) $(( SERVE_LN_W1 - 1 ))
    vim_collapse_begin "worker-1: same as worker-0, except the name and label"
    serve_type collapse "$SERVE_LN_W1" "${#SERVE_DGD[@]}"
    vim_collapse_end
    vim_close
}

# serve_services: why Slime needs a Service per engine; the first one; creating them. The kit applied
# them at setup, before this run, and kept no output: unsaved_note says so, then their real state today.
serve_services() {
    printf '\n'
    narrate "Slime also calls each engine directly, on SGLang's port 30000, to read its settings and load new weights." \
            "The operator's Services for the workers expose only Dynamo's port 9090, so the tutorial adds one per engine:"
    show_command "head -n ${SERVE_SVC_HEAD_N} ${SERVE_SVC_FILE}"
    printf '%s\n' "${SERVE_SVC_HEAD_OUT[@]}"
    nap "$PAUSE_MED"
    show_command "kubectl apply -n \$NAMESPACE -f ${SERVE_SVC_FILE}"
    unsaved_note "$SERVE_SETUP_AT"
    show_command "$SERVE_SVC_GET_CMD" "  # on ${SERVE_SVC_SNAP_DATE}: still there"
    printf '%s\n' "${SERVE_SVC_GET_OUT[@]}"
    nap "$PAUSE_MED"
}

# serve_deploy TITLE: apply the DGD, wait for Ready, check the sidecars.
serve_deploy() {
    local title="$1"
    printf '\n'
    narrate "Then create the DGD:"
    show_command "kubectl apply -n \$NAMESPACE -f ${SERVE_DGD_FILE}"
    printf '%s\n' "$SERVE_DGD_APPLY_OUT"
    nap "$PAUSE_MED"

    clear_screen
    step_header_bars "🚀" "$title"
    narrate "Wait for the DGD, fast-forwarding. Each engine is ready once its probe, /health_generate, can generate:"
    show_command "kubectl wait -n \$NAMESPACE --for=condition=Ready dgd/${SERVE_DGD_NAME} --timeout=15m"
    ff_run serve_pods_render "$SERVE_CREATED_T" "$SERVE_READY_T"
    nap "$PAUSE_MED"
    frame_replace "$SERVE_WAIT_OUT"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "Ready after ${SERVE_READY_T} s. Last check: each sidecar logs this once it reaches its engine, just before registering it:"
    show_command_lines \
        "for w in 0 1; do kubectl logs -n \$NAMESPACE -l dynamo.nvidia.com/slime-role=worker-\$w --tail=-1 \\" \
        "    | grep -o 'sglang sidecar started.*' | sed \"s/^/worker-\$w: /\"; done"
    printf '%s\n' "${SERVE_SIDECAR_OUT[@]}"
    read_hold 2 "${SERVE_SIDECAR_OUT[0]}"
    nap "$PAUSE_SHORT"
}

beat_04() {
    local title="Step 3: Serve an Inference Endpoint with Dynamo"
    beat_header "🚀" "$title" \
        "The loop starts from the base model as is. Dynamo serves it, instead of Slime launching its own engines."
    serve_vim_dgd
    serve_services
    serve_deploy "$title"
}
