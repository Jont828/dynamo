# beats/04_serve.sh: beat 04, Step 3: serve an inference endpoint with Dynamo.
# The DGD typed in vim: its header at normal speed, fast-forwarded to the engine's image and
# command, a callout before the frontend's router settings and another before the engine's KV
# flags, worker-1..3 folded. Then why four components, where the engine Services come from (the
# head of engine-services.yaml), the apply, the Pods starting (fast-forwarded on the recorded
# transitions), the DGD Ready, and each sidecar's KV-event line, explained before it runs.
# Data: build/04_serve.data.sh, from fill/04_serve.py. Plan: plans/03-04_storage_serve.md.

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

# serve_pods_render T: one frame of `watch kubectl get pods` (T: seconds since the apply). Each
# Pod's STATUS and READY follow the transitions recorded in runs/deploy/pods-watch.txt, AGE counts
# from its creationTimestamp, and each column is as wide as its widest value + 3 in this frame,
# the way kubectl pads it.
serve_pods_render() {
    local t="$1" i j n=0 np=${#SERVE_POD_NAMES[@]} wn=4 ws=6 fmt line
    local -a st=() rd=()
    for (( i=0; i<np; i++ )); do st[i]=""; rd[i]=""; done
    for (( j=0; j<${#SERVE_TR_T[@]}; j++ )); do
        if (( ${SERVE_TR_T[j]} <= t )); then
            st[${SERVE_TR_I[j]}]=${SERVE_TR_S[j]}
            rd[${SERVE_TR_I[j]}]=${SERVE_TR_R[j]}
        fi
    done
    for (( i=0; i<np; i++ )); do
        if (( ${#SERVE_POD_NAMES[i]} > wn )); then wn=${#SERVE_POD_NAMES[i]}; fi
        if (( ${#st[i]} > ws )); then ws=${#st[i]}; fi
        if [[ "${rd[i]}" == "1/1" ]]; then n=$(( n + 1 )); fi
    done
    fmt="%-$(( wn + 3 ))s%-8s%-$(( ws + 3 ))s%-11s%s"
    printf -v line "$fmt" NAME READY STATUS RESTARTS AGE
    fl "$line"
    for (( i=0; i<np; i++ )); do
        fmt_age $(( t - ${SERVE_POD_CREATED_T[i]} ))
        printf -v line "$fmt" "${SERVE_POD_NAMES[i]}" "${rd[i]}" "${st[i]}" 0 "$AGE_STR"
        fl "$line"
    done
    fl ""
    if (( n == np )); then
        fl "   ${OK}✅ All ${np} Pods ready: all four engines can generate${NC}"
    elif [[ "${st[0]}" == SchedulingGated ]]; then
        fl "   ${STATUS}▸ SchedulingGated: the ${np} Pods wait to be placed as one group (Step 1's Grove and KAI)${NC} — ${n}/${np} ready"
        FF_TICK_NOW=$SERVE_GATED_TICK
    else
        fl "   ${STATUS}▸ Starting the frontend and four engines (Qwen3-4B from /data, CUDA graphs)${NC} — ${n}/${np} ready"
    fi
    fl ""
    ff_elapsed "$t"
}

SERVE_GATED_TICK="${SERVE_GATED_TICK:-0.6}"   # per frame while the Pods are SchedulingGated

# serve_vim_dgd: "write" dgd.yaml.
serve_vim_dgd() {
    narrate "Write the DynamoGraphDeployment (DGD) that runs them:"
    vim_open "$SERVE_DGD_FILE"
    serve_type normal 1 $(( SERVE_LN_FRONTEND - 1 ))
    serve_type fast "$SERVE_LN_FRONTEND" $(( SERVE_LN_W0 - 1 ))
    vim_callout "$SERVE_LN_ENV" $(( SERVE_LN_W0 - 1 )) \
        "The ${BOLD}frontend${NC}, Dynamo's HTTP entry point and router:" \
        "DYN_SGLANG_ENABLE_GENERATE=1: serve SGLang's /generate (token IDs in and out), which Slime calls." \
        "DYN_ROUTER_MODE=kv: ${BOLD}KV-aware routing${NC}, to the engine that already processed most of the prompt, unless it's busier." \
        "DYN_ROUTER_PREDICTED_TTL_SECS=5: for 5 s after routing, assume that engine has the prompt."
    serve_type fast "$SERVE_LN_W0" $(( SERVE_LN_W0_IMAGE - 1 ))
    serve_type normal "$SERVE_LN_W0_IMAGE" "$SERVE_LN_W0_LAUNCH"
    vim_callout "$SERVE_LN_W0_IMAGE" "$SERVE_LN_W0_LAUNCH" \
        "Each ${BOLD}engine${NC} is SGLang's own server, started from Dynamo's SGLang image."
    serve_type fast $(( SERVE_LN_W0_LAUNCH + 1 )) "$SERVE_LN_W0_SIDECAR_ARGS"
    vim_callout "$SERVE_LN_W0_PAGE" "$SERVE_LN_W0_SIDECAR_ARGS" \
        "--page-size 16: keep the ${BOLD}KV cache${NC}, the engine's saved work on past prompts, in 16-token blocks, the router's unit." \
        "--kv-events-config: announce each block the engine stores or drops, on a ZMQ socket." \
        "--sidecar: ${BOLD}Dynamo's sidecar${NC} passes the engine its requests and relays those announcements to the router."
    serve_type fast $(( SERVE_LN_W0_SIDECAR_ARGS + 1 )) $(( SERVE_LN_W1 - 1 ))
    vim_collapse_begin "worker-1..3: same as worker-0, except the name and label"
    serve_type collapse "$SERVE_LN_W1" "${#SERVE_DGD[@]}"
    vim_collapse_end
    vim_close
}

# serve_services: why four components, and the engine Services the apply creates.
serve_services() {
    printf '\n'
    narrate "Why four components, not one with replicas: 4? Each engine needs its own label, so a Service can reach just it:"
    show_command "head -n ${SERVE_SVC_HEAD_N} ${SERVE_SVC_FILE}"
    printf '%s\n' "${SERVE_SVC_HEAD_OUT[@]}"
    nap 3
}

# serve_deploy TITLE: apply, watch the Pods start, check the DGD and the sidecars.
serve_deploy() {
    local title="$1"
    local cmd='watch kubectl get pods -n $NAMESPACE -l dynamo.nvidia.com/slime-role'
    local n1="Fast-forwarding while the Pods start and the engines load the model from /data:"
    printf '\n'
    narrate "Create the four engine Services and the DGD:"
    show_command 'kubectl apply -n $NAMESPACE -f manifests/engine-services.yaml -f manifests/dgd.yaml'
    printf '%s\n' "${SERVE_APPLY_OUT[@]}"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "$n1"
    show_command "$cmd"

    clear_screen
    step_header_bars "🚀" "$title"
    printf '%s\n\n' "${NAR}# ${n1}${NC}"
    print_command "$cmd"
    printf '\n'
    ff_run serve_pods_render "$SERVE_WATCH_FROM_T" "$SERVE_WATCH_TO_T"
    nap "$PAUSE_MED"

    printf '\n'
    narrate "The DGD reports Ready once all five Pods are:"
    show_command "kubectl get dgd -n \$NAMESPACE ${SERVE_DGD_NAME}"
    printf "${DGD_FMT}\n" NAME READY BACKEND AGE
    printf "${DGD_FMT}\n" "$SERVE_DGD_NAME" "$SERVE_DGD_READY" "$SERVE_DGD_BACKEND" "$SERVE_DGD_AGE"
    nap "$PAUSE_SHORT"
    printf '\n'
    narrate "Last check: each engine's sidecar should have found its engine's KV-event stream (the ZMQ socket set above)." \
            "Through it, the router learns what each engine has cached:"
    show_command_lines \
        "for w in 0 1 2 3; do kubectl logs -n \$NAMESPACE -l dynamo.nvidia.com/slime-role=worker-\$w --tail=-1 \\" \
        "    | grep -o 'discovered SGLang ZMQ KV-event sources.*' | sed \"s/^/worker-\$w: /\"; done"
    printf '%s\n' "${SERVE_SIDECAR_OUT[@]}"
    nap 2.5
    printf '\n'
    narrate "The endpoint is up: the frontend's Service, which the operator created, at ${SERVE_FRONTEND_URL}."
}

beat_04() {
    local title="Step 3: Serve an Inference Endpoint with Dynamo"
    beat_header "🚀" "$title" \
        "The loop starts by serving the starting checkpoint as is: Qwen3-4B after ReTool SFT, before any RL step." \
        "Dynamo serves it instead of Slime's own engines: a KV-aware router in front of four 1-GPU SGLang engines."
    serve_vim_dgd
    serve_services
    serve_deploy "$title"
}
