# RL training demos: Dynamo serving the rollouts, on AKS

Five demo kits for reinforcement-learning (RL) training with NVIDIA Dynamo serving the rollouts. In each kit,
a trainer runs GRPO, and Dynamo's frontend and router send every generation request to inference engines. Each
kit has a runbook (`README.md`), the manifests and scripts that ran, a narrated asciinema recording, and a
report of the runs. Everything ran on one AKS cluster in November 2026. This branch is upstream `main` plus this
folder; nothing outside `demo/` changes.

| Kit | What runs | GPUs | Recording | Result |
|---|---|---|---|---|
| [`nemo-rl/`](nemo-rl/) | NeMo RL GRPO on Qwen2.5-1.5B (OpenMathInstruct-2) as one KubeRay RayJob. NeMo RL's driver starts Dynamo's frontend and KV router; two vLLM engines. Dynamo 1.5.1 | 3 | 9:17, live run | Pass: 25 steps; training reward 0.07 → 0.31 |
| [`verl/`](verl/) | verl GRPO on Qwen2.5-7B-Instruct (GSM8K) with the verl-recipe Dynamo backend, as one KubeRay RayJob; four vLLM engines. Dynamo 1.5.1 | 8 | 7:24, live run (and 8:19 of the earlier exec-based flow) | Pass: 15 steps; GSM8K test accuracy 0.784 → 0.911 |
| [`slime/`](slime/) | Slime GRPO on Qwen3-0.6B (GSM8K): the trainer as a KubeRay RayJob, two SGLang engines in a Dynamo DynamoGraphDeployment (DGD). Dynamo 1.6.1 | 3 | 2:42, live run | Pass: 10 steps in about 2.5 minutes |
| [`slime-kv-routing/`](slime-kv-routing/) | The Slime tutorial under five routing configurations, KV-aware and round-robin, three runs each | 3 | 2:12 and 9:17, replays | KV-aware routing works but doesn't speed this workload up |
| [`slime-retool/`](slime-retool/) | Slime's ReTool example (Qwen3-4B, multi-turn math with a Python tool): KV-aware routing against a round-robin baseline | 8 | 2:51 and 12:26, replays | KV routing nearly halved the prompt tokens computed, but the run wasn't measurably faster |

Recording lengths are playback times; the live runs behind them took longer.

## Play a recording

No cluster needed. Install [asciinema](https://docs.asciinema.org/getting-started/) 3.x, then:

```bash
asciinema play demo/slime-retool/demo/team/slime-retool-team-demo.public.cast
```

Every recording in this folder is a `*.public.cast`. The kit READMEs list theirs.

## Run a demo

Each kit's README has the steps. They assume what the cluster had:

- [AKS](https://learn.microsoft.com/azure/aks/) GPU nodes: `Standard_ND96isr_H100_v5` (8 × H100 80 GB) in node pool `ndh100pool`.
- [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/) and the
  [KubeRay operator](https://docs.ray.io/en/latest/cluster/kubernetes/getting-started.html) 1.7.1.
- The [Dynamo platform](https://github.com/ai-dynamo/dynamo/blob/main/docs/fern/pages/kubernetes/installation/install-dynamo.md)
  Helm chart 1.6.1, for the three Slime kits (they deploy a DGD).
- Storage: [Azure Managed Lustre](https://learn.microsoft.com/azure/azure-managed-lustre/) for the Slime kits
  (static PVs; edit `mgs-ip-address` for yours), and an Azure Disk cache PVC (`managed-csi-premium`) for NeMo RL
  and verl.
- On your machine: `kubectl`, `python3` (3.10+, with PyYAML for the Slime kits' tools), and asciinema 3.x to
  record.

What to change for another cluster:

- **Node pins.** The cluster was shared, so some manifests pin Pods to one node by hostname
  (`aks-ndh100pool-82423760-vmss00000N`): NeMo RL's, verl's exec-based Pod, and ReTool's inspect Pod. The two
  Slime KV kits pin through `tools/pin.py`, with the node in `NODE`. Pick a node with enough free GPUs
  (`nemo-rl/tools/gpu_free.py` lists them) and change the pin.
- **kubectl context.** The two Slime KV kits' commands pass `--context h100`, the context name that
  `az aks get-credentials` gives this cluster, and their tools take `CTX`. The other demos use your current
  context.
- **Images.** All are public: Dynamo's runtime images on `nvcr.io`, `slimerl/slime`, and
  `docker.io/jont828/nemo-rl` for NeMo RL, built from NeMo RL `main` with `nemo-rl/image/build-image.sh`, because
  NeMo RL publishes no image with this Dynamo path.
- **Hugging Face token.** Optional: create the `hf-token-secret` Secret the kits mention, with your own token.

## How the recordings are made

- **Live** (`nemo-rl`, `verl`, `slime`): `demo.sh` runs the job on the cluster and narrates it, and you record it
  with `asciinema rec -c ./demo.sh`. It reads no saved logs. NeMo RL and verl then cap the waits with
  `tools/pace_cast.py`.
- **Replays** (`slime-kv-routing`, `slime-retool`): the narration scripts print the output of the recorded runs,
  so recording them needs no cluster and no logs; run the kit's record script. Regenerating a narration script
  (to change its words or data) needs the recorded runs' `runs/` folders, which aren't in this repo.

Before you commit a recording, run `tools/redact_cast.py IN.cast OUT.public.cast` (each kit has a copy). It masks
in-cluster IPs and the AKS node-pool ID, then lists anything else to review: tokens, GUIDs, emails, home
directories. Commit only the `.public.cast`. Each kit's README has the exact commands.

## Not in this repo

- **Run outputs:** each kit's `runs/` folder of job logs, engine logs, metrics scrapes, turn logs and summaries
  (about 390 MB in all). The kits' Rerun steps write new ones; ask Jonathan for the recorded ones.
- **Rendered copies** of the recordings (GIF, MP4, HTML player pages, PNG frames). `agg`, `ffmpeg`, and
  `tools/player_page.py` make them from a `.public.cast`.
- **Unedited and unredacted recordings**, earlier takes, and the working notes behind the demos.
