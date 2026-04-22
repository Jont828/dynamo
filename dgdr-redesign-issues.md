# DGDR Redesign - GitHub Issues Draft

## Parent Issue

```
Title: feat: Redesign DGDR for a Unified Dynamo Kubernetes Deployment Experience

## Overview
This issue tracks the redesign of DynamoGraphDeploymentRequest (DGDR) to provide a 
streamlined, SLA-driven deployment experience. The goal is to simplify the user 
experience while maintaining flexibility for advanced use cases.

## Motivation
- Reduce friction for users deploying LLM inference workloads
- Remove boilerplate configuration requirements (e.g., baseConfigRef)
- Improve observability during profiling
- Better integration with AIC for configuration generation
- Automatic hardware discovery

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Work Items
- [ ] Implement DGDR v1beta1 spec with field groups schema
- [ ] Use AIC naive-generate as base DGD configuration  
- [ ] Make SLAs optional with dynamic default presets
- [ ] Move hardware discovery to Dynamo Operator
- [ ] Add progress UI for profiling during DGDR run
- [ ] Surface errors/issues with clear status messages
- [ ] Update docs to recommend DGDR over profiler CLI
- [ ] Hook up AIC UI for optimization mode selection
- [ ] Refactor profiling to separate component

## Success Criteria
- [ ] Users can deploy models with minimal configuration
- [ ] Hardware is automatically detected
- [ ] Profiling progress is visible and errors are clear
- [ ] Documentation reflects DGDR as primary workflow

cc @Anish-Maddipoti @sozercan @BenHamm
```

---

## Sub-Issue 1: v1beta1 Spec with Field Groups

```
Title: feat: Implement DGDR v1beta1 spec with field groups schema

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Design and implement the v1beta1 spec for DynamoGraphDeploymentRequest based on the 
field groups and full DGDR schema outlined in the design doc.

## Tasks

- [ ] Take field groups + full DGDR schema from design doc
- [ ] Work up a v1beta1 spec with the new structure
- Assignee: @Jont828

## Acceptance Criteria
- [ ] Enhancement doc is clear and detailed
- [ ] v1beta1 spec is implemented in `deploy/operator/api/v1beta1/`
- [ ] Existing functionality migrated to new field structure

## Related Files
- `deploy/operator/api/v1alpha1/dynamographdeploymentrequest_types.go`
- `deploy/operator/api/v1beta1/dynamographdeploymentrequest_types.go`
```

---

## Sub-Issue 2: AIC naive-generate as Base DGD

```
Title: feat: Use AIC naive-generate as base DGD configuration

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Replace `config_modifier.load_default_config()` with AIC's `generate` function to 
produce the base DGD config. Remove `baseConfigRef` field.

## Motivation
- Eliminates hardcoded paths like `DEFAULT_SGLANG_CONFIG_PATH`
- Works with MOE models without manual `ConfigMapRef` setup
- AIC already knows how to generate valid base configs

## Tasks
- [ ] Move `utils` subdir from Profiler to AIC (or remove it)
  - Assignee: @tedzhouhk
- [ ] Remove `baseConfigRef` field from DGDR spec
- [ ] Call AIC `generate` as the first step in profiling pipeline
- [ ] Update `ConfigModifierProtocol` to receive AIC-generated config

## Acceptance Criteria
- [ ] Profiling works without `baseConfigRef`
- [ ] MOE models work out-of-the-box
- [ ] No hardcoded DGD template paths

## Related Files
- `benchmarks/profiler/utils/`
- `benchmarks/profiler/utils/config_modifiers/`
```

---

## Sub-Issue 3: Optional SLAs with Dynamic Defaults

```
Title: feat: Make SLAs optional with dynamic default presets

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Allow users to omit SLA targets (TTFT, ITL) in DGDR specs. When omitted, the system 
should use dynamic default presets based on model and hardware characteristics.

## Tasks
- [ ] Define default preset calculation logic (math/heuristics)
  - Assignee: @tedzhouhk
- [ ] Update DGDR spec to make SLA fields optional
- [ ] Implement default value injection in controller
- [ ] Document how defaults are computed

## Acceptance Criteria
- [ ] DGDR works without explicit SLA targets
- [ ] Reasonable defaults are applied based on model/hardware
- [ ] Users can still override with explicit values
```

---

## Sub-Issue 4: Hardware Discovery in Operator

```
Title: feat: Move hardware discovery to Dynamo Operator

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Move GPU and cluster hardware discovery logic from the Profiler into the Dynamo 
Operator/DGDR controller, and expose it as a structured object the Profiler can consume.

## Tasks

### Cluster Hardware Discovery
- [ ] Integrate DCGM (or equivalent if research finds something more appropriate) into the Operator/Profiler runtime
  - Reference: [NVIDIA k8s-device-plugin GPU Feature Discovery](https://github.com/NVIDIA/k8s-device-plugin/tree/main/docs/gpu-feature-discovery)
  - Reference: [ROCm device-metrics-exporter](https://github.com/ROCm/device-metrics-exporter) for AMD GPUs (DCGM more standardized for NVIDIA)

### Collect Hardware Information
- [ ] GPU model (e.g., H100, L40S)
- [ ] Count per node
- [ ] Memory per GPU
- [ ] Any other relevant details:
  - [ ] Whether it's RDMA / RDMA device setup
  - [ ] Whether it's AWS / EFA

### Hardware Snapshot Object
- [ ] Expose this as a structured "hardware snapshot" object the Profiler can consume

### Move Discovery Logic
- [ ] Move GPU discovery logic out of the Profiler and into the Operator/DGDR controller
- Assignee: @hhzhang16

### Hardware → AIC System String Mapping
- [ ] Define a mapping table from detected hardware → AIC system string
  - H100 SXM → `h100_sxm`
  - H200 SXM → `h200_sxm`
  - etc.
- [ ] Implement this mapping in the controller/operator

### Extended Hardware Detection
- [ ] Integrate k8s-device-plugin helm chart, DCGM, other hardware detection
- Assignee: @Devi-V

### Cleanup
- [ ] Remove sweep from DGDR

### Hardware Overrides
- [ ] Add ability to override auto-detected hardware
- Reference: https://github.com/ai-dynamo/enhancements/pull/62#discussion_r2737770332

### Cloud Provider Flag
- [ ] Investigate adding a cloud provider flag (TBD)

## Acceptance Criteria
- [ ] Hardware discovered automatically from cluster nodes
- [ ] Profiler receives hardware snapshot from controller (not doing its own discovery)
- [ ] AIC system strings derived correctly from detected hardware
- [ ] Users can override hardware detection when needed
```

---

## Sub-Issue 5: Profiling Progress UI

```
Title: feat: Add progress UI for profiling during DGDR run

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Implement a progress indicator that shows users the current status of profiling 
during DGDR execution.

## Tasks
- [ ] Design and implement progress UI for profiling phases
- [ ] Add clear status messages for each profiling stage
- Assignee: @Jont828

- [ ] Assist with wording/messaging for progress states
- Assignee: @tedzhouhk

## Profiling Phases to Display
- Initializing
- SweepingPrefill
- SweepingDecode
- SelectingConfig
- BuildingCurves
- GeneratingDGD
- Done

## Acceptance Criteria
- [ ] Users can see current profiling phase
- [ ] Clear messages indicate progress and completion
- [ ] Errors are surfaced with actionable information
```

---

Sub issue 6 already exists (https://github.com/ai-dynamo/dynamo/issues/5470#issue-3819704898)

Can just tag #5470 as a sub issue.

<!-- ## Sub-Issue 6: Error/Status Messaging

```
Title: feat: Surface errors/issues with clear status messages

## Parent Issue
Part of: feat: DGDR Redesign - SLA-Driven Deployment Experience

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Improve error handling and messaging to show users what/when things are working or 
not working during DGDR lifecycle.

## Tasks
- [ ] Audit current error paths in DGDR controller
- [ ] Add clear, actionable error messages
- [ ] Ensure status conditions reflect actual state
- [ ] Document common errors and resolutions
- Assignee: TBD

## Acceptance Criteria
- [ ] Users see clear messages when things fail
- [ ] Status conditions accurately reflect errors
- [ ] Troubleshooting is straightforward
``` -->

---

## Sub-Issue 7: Documentation Updates

```
Title: feat: Update docs to recommend DGDR over profiler CLI

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Remove mentions of the standalone profiler CLI from documentation. Users should use 
DGDR as the primary method for profiling, especially if they want Planner integration.

## Tasks
- [ ] Audit profiling docs for CLI references
- [ ] Update to recommend DGDR-based profiling
- [ ] Add migration guide if needed
- Assignee: @dagil-nvidia

## Files to Update
- `docs/components/profiler/README.md`
- `docs/components/profiler/profiler_examples.md`
- Any other docs mentioning `profile_sla.py` directly

## Acceptance Criteria
- [ ] Docs recommend DGDR as primary profiling method
- [ ] CLI usage is deprecated or removed from docs
```

---

## Sub-Issue 8: AIC UI Integration

```
Title: feat: Hook up AIC UI for optimization mode selection

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Integrate the existing AIC UI to allow users to explore and choose between latency, 
throughput, and hybrid optimization modes during profiling.

## Tasks
- [ ] Hook up existing AIC UI code
- [ ] Allow users to visualize trade-offs
- [ ] Enable selection of optimization mode
- Assignee: TBD

## Acceptance Criteria
- [ ] Users can access UI during/after profiling
- [ ] Clear visualization of latency vs throughput trade-offs
- [ ] Selected mode is applied to deployment
```

---

## Sub-Issue 9: Refactor Profiling Component

```
Title: feat: Refactor profiling to separate component

## Parent Issue
Part of: #6129

## Design Proposal
https://docs.google.com/document/d/1SXH51OxNpJ5QlJSw7Xs4-02RO7l5_a8Dh5AFY6cu6p8/edit?usp=sharing

## Description
Move `benchmarks/profiler/` to its own dedicated component under `components/`.

## Current Location
```
benchmarks/profiler/
├── README.md
├── profile_endpoint.py
├── profile_sla.py
├── utils/
├── deploy/
└── webui/
```

## Proposed Location
```
components/src/profiler/
├── ...
```

## Tasks
- [ ] Create component structure under `components/src/profiler/`
- [ ] Migrate profiler code
- [ ] Update imports and CI/CD
- Assignee: @dagil-nvidia

## Acceptance Criteria
- [ ] Profiler code under `components/`
- [ ] All functionality works unchanged
- [ ] Tests pass after migration
```

---

## gh CLI Commands (after editing)

```bash
# Create parent issue first
gh issue create --repo ai-dynamo/dynamo --title "feat: DGDR Redesign - SLA-Driven Deployment Experience" --body-file parent-issue.md

# Create sub-issues (replace PARENT_NUM with parent issue number)
gh issue create --repo ai-dynamo/dynamo --title "feat: Implement DGDR v1beta1 spec with field groups schema" --body-file issue-1.md
# ... repeat for each sub-issue

# Link sub-issues to parent (GitHub sub-issue feature)
# This can be done via the GitHub UI or with the GraphQL API
```
