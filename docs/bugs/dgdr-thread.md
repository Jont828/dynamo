Anish Maddipoti  [6:08 PM]
Some feedback on Dynamo relevant to the work we're collaborating on:

- DGDR is effectively non-functional for most users. The deploy-by-intent path supports only 6 GPU SKUs, all SXM variants plus L40S, with no published list, PCIe variants are excluded, blocking most cloud and colocation deployments. Beyond SKU restrictions, the AIC profiler crashes with a NaN handling bug in pareto_analysis.py, causing DGDR to reach Failed status after 4 retries on both minimal and explicit hardware configs. The supported SKU list should be published in DGDR docs, PCIe variants added, and the NaN bug fixed before GA.
- Documentation lacks a clear structure and navigation hierarchy. The docs/ directory continues to mix high-level guides with component-specific implementation details, with no defined path from Concepts to Quick Start to Reference. Version mismatches across the container (1.0.0), PyPI (1.0.1), and GitHub README (sglang-runtime:1.0.1) add to the confusion. Critical configuration options remain explained in prose across multiple pages rather than consolidated in reference tables.

Hannah Zhang  [12:33 PM]
Related to this, I was brainstorming some future work, and there's overlap to what you mentioned here, Anish. Would v much appreciate help from the Microsoft/external side:

- Having a consolidated DGDR preflight workflow. Today the validation is pretty spread out. Having a single “tell me what will fail before I wait 15 minutes” path would help
- Finish the DGDR v1beta1 cleanup and make it the single source of truth. The API already has a lot of good structure, but the controller still relies on some legacy code like legacy annotation for DGD creation and the conversion layer still calls out omitted fields
- We need to do a full docs sweep and update/cleanup
[12:34 PM]I can't make the meeting today and I'll be OOO early next week but I'll watch the recording once I'm back
Anish Maddipoti  [4:48 PM]
Needs Improvement:

- DGDR only supports 6 GPU SKUs, all SXM variants plus L40S. PCIe GPUs (H100-PCIe, A100-PCIe, A30, L4) are excluded, blocking DGDR for most cloud and colocation users. The supported list is only discoverable by hitting validation errors. The docs example uses a format (H100-SXM5-80GB) that doesn’t match what the webhook accepts (h100_sxm). Recommended Fix:
- Publish the supported GPU SKU list in the DGDR documentation
- Add PCIe variants (h100_pcie, a100_pcie, l4, etc.)
- Fix the docs example to use a valid value (e.g., h100_sxm not H100-SXM5-80GB)
- DGDR hands-off deployment: Non-functional. GPU auto-discovery succeeds (H200 SXM correctly detected from node labels), and the profiling job starts running Pareto analysis across backends (vLLM, TRT-LLM) with multiple parallelism configs. However, the AIC profiler crashes with KeyError: "None of [Index([nan, nan, nan, nan]...)]" in pareto_analysis.py:510 — a NaN handling bug in the rapid search ranking. After 4 retries the DGDR reaches Failed status. Tested with both minimal spec and explicit hardware config (gpuSku: h200_sxm). 
Jonathan Tong  [5:17 PM]
This p much what I said in the meeting but will add in the thread:

- Have DGDR as the first class entry point for getting to a working deployment of large models like Qwen235 or R1 without needing human input
- We a list of recipes that are hand tuned. I'd like to be able to get those working with DGDR such that w/ some overrides as needed, we can get DGDR to produce the same or similar DGD as the hand tuned model. Meaning DGDR is functionality is a superset of existing DGD
- Document any bugs or things that we run into for deployment flow such that a user isn't discovering a bug that we already knew about. If we hit a bug, a user will likely hit the same too and we need to anticipate that

Jonathan Tong  [3:23 PM]
Follow up on the proposal for a "support matrix" for DGDR. I'm thinking we can start with all of the existing hand tuned recipes. For a crawl, walk, run iteration with DGDR, we can ensure that for each of the models,
1 .Crawl: We can deploy the model successfully with DGDR, doesn't need to be optimal but just has to work
2. Walk: We can refactor the hand tuned DGD into a DGDR with overrides such that the DGDR + overrides will produce a similar/same DGD as the hand tuned recipe
3. Run: With minimal overrides, the DGDR can produce a similar DGD as the hand tuned