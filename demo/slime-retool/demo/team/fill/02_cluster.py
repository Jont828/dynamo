"""Beat 02 (Step 1: prepare the AKS cluster) data: writes build/02_cluster.data.sh from snapshots.

Usage: $PY -I fill/02_cluster.py

Every cluster fact is a read-only snapshot in demo/team/snapshots/ (written by snapshots/capture.sh on
2026-11-19; each file names its command and date); the run's GPU split comes from the kit's
manifests/dgd.yaml and manifests/rayjob.yaml. Shown on screen as saved, tabs expanded:
  helm-list-filtered.txt   helm list -A --filter '...' | cut -f1,2,5,6
  nodes-gpu.txt            kubectl get nodes -l nvidia.com/gpu.present=true -L ...instance-type,...gpu.count
  amlfs-table.txt          az resource show ... --resource-type Microsoft.StorageCache/amlFilesystems -o table
  lustre-csi-rollout.txt   kubectl rollout status -n kube-system daemonset/csi-azurelustre-node-noble
Used to build and check the install commands (versions, flags) and the narration's numbers:
  helm-list-all.txt, helm-values-*.txt, aks-cluster.json, aks-nodepools.json, amlfs.json,
  amlfs-subnet.json, lustre-csi-install.txt, node-gpu-labels.txt, scheduler-evidence.txt

The install commands are emulated (this cluster already runs them all), so they have no output on
screen. Their chart sources and flags follow the projects' docs: Dynamo's AKS, install, and Azure Lustre
pages (docs/fern/pages/kubernetes/installation/), NFD's and KAI's Helm instructions, Grove's install
guide, and the azurelustre-csi-driver install script. Every version and flag value here comes from the
snapshots and is asserted.
"""

import json
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # -I leaves it off sys.path
from _common import KIT, TEAM, Data  # noqa: E402

SNAP = TEAM / "snapshots"
MAXW = 118  # a command line after the 2-column prompt, or an output line


def snap(name):
    """(screen command, output lines) of a snapshot; tabs expanded the way a terminal shows them."""
    text = (SNAP / name).read_text()
    head, sep, body = text.partition("# ---\n")
    assert sep, f"{name}: no '# ---' line"
    screen = re.search(r"^# screen: (.*)$", head, re.M).group(1)
    assert re.search(r"^# date: +2026-11-19T", head, re.M), f"{name}: unexpected date"
    lines = [line.expandtabs(8).rstrip() for line in body.rstrip("\n").splitlines()]
    return screen, lines


def snap_json(name):
    """All JSON documents in a snapshot's output, in order."""
    _, lines = snap(name)
    text, docs, dec, i = "\n".join(lines), [], json.JSONDecoder(), 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        doc, i = dec.raw_decode(text, i)
        docs.append(doc)
    return docs


def helm_values(release):
    """The user-supplied values of a release as text (YAML), checked by substring."""
    _, lines = snap(f"helm-values-{release}.txt")
    assert lines[0] == "USER-SUPPLIED VALUES:", lines[0]
    return "\n".join(lines[1:])


def main():
    # --- versions: one row per release from `helm list -A` ------------------------------------------
    _, rows = snap("helm-list-all.txt")
    releases = {}
    for row in rows[1:]:
        cols = row.split()
        # NAME NAMESPACE REVISION <updated: 4 or 5 fields> STATUS CHART [APP VERSION]
        status_at = cols.index("deployed")
        releases[cols[0]] = {"ns": cols[1], "chart": cols[status_at + 1]}

    def chart_version(release, chart_name):
        rel = releases[release]
        assert rel["chart"].startswith(chart_name + "-"), rel
        return rel["ns"], rel["chart"][len(chart_name) + 1:]

    ns_gpu, v_gpu = chart_version("gpu-operator", "gpu-operator")
    ns_nfd, v_nfd = chart_version("nfd", "node-feature-discovery")
    ns_ray, v_ray = chart_version("kuberay-operator", "kuberay-operator")
    ns_kai, v_kai = chart_version("kai-scheduler", "kai-scheduler")
    ns_grove, v_grove = chart_version("grove", "grove-charts")
    ns_dyn, v_dyn = chart_version("dynamo-platform", "dynamo-platform")
    ns_net, v_net = chart_version("network-operator", "network-operator")
    assert (v_gpu, v_nfd, v_ray, v_kai, v_grove, v_dyn) == (
        "v26.3.2", "0.18.3", "1.7.1", "v0.17.2", "v0.1.0-alpha.17", "1.6.1"), (v_gpu, v_nfd, v_ray, v_kai, v_grove, v_dyn)
    assert (ns_gpu, ns_nfd, ns_ray, ns_kai, ns_grove, ns_dyn) == (
        "gpu-operator", "node-feature-discovery", "kuberay", "kai-scheduler", "dynamo-system", "dynamo-system")
    assert ns_net == "nvidia-network-operator" and v_net == "26.1.1", (ns_net, v_net)

    # --- non-default values that the commands show -------------------------------------------------
    gpu_vals = helm_values("gpu-operator")
    assert re.search(r"^nfd:\n  enabled: false$", gpu_vals, re.M), "gpu-operator: nfd.enabled"
    driver = re.search(r"^driver:\n(?:  .*\n)*?  version: (\S+)$", gpu_vals, re.M).group(1)
    assert driver == "580.126.20", driver
    dyn_vals = helm_values("dynamo-platform")
    assert re.search(r"^  grove:\n    enabled: true\n    install: false$", dyn_vals, re.M), "grove flags"
    assert re.search(r"^  kai-scheduler:\n    enabled: true\n    install: false$", dyn_vals, re.M), "kai flags"
    assert helm_values("kuberay-operator").strip() == "null", "kuberay-operator has values now"

    labels = dict(line.split("=", 1) for line in snap("node-gpu-labels.txt")[1] if "=" in line)
    assert labels["nvidia.com/cuda.driver-version.full"] == driver
    gm = re.fullmatch(r"NVIDIA-(H100)-(\d+)GB-HBM3", labels["nvidia.com/gpu.product"])
    assert gm, labels["nvidia.com/gpu.product"]
    gpu_name = f"{gm.group(1)} {gm.group(2)} GB"

    # --- AKS --------------------------------------------------------------------------------------
    (aks,) = snap_json("aks-cluster.json")
    k8s = aks["kubernetesVersion"]
    assert k8s == "1.34.8", k8s
    pools = {p["name"]: p for p in snap_json("aks-nodepools.json")}
    gpu_pool = pools["ndh100pool"]
    assert gpu_pool["vmSize"] == "Standard_ND96isr_H100_v5" and gpu_pool["count"] == 4
    assert gpu_pool["gpuProfile"] == {"driver": "None"} and gpu_pool["osSku"] == "Ubuntu2404"
    assert pools["nodepool1"]["gpuProfile"] is None
    vm, pool_count, os_sku = gpu_pool["vmSize"], gpu_pool["count"], gpu_pool["osSku"]
    vm_short = vm.replace("Standard_", "").replace("_", " ")  # "ND96isr H100 v5"

    # --- Azure Managed Lustre ---------------------------------------------------------------------
    (amlfs,) = snap_json("amlfs.json")
    sku, tib, mbps = amlfs["sku"], amlfs["storageCapacityTiB"], amlfs["throughputProvisionedMBps"]
    assert (sku, tib, mbps, amlfs["zones"]) == ("AMLFS-Durable-Premium-500", 16.0, 8000, ["1"]), amlfs
    assert amlfs["maintenanceWindow"] == {"dayOfWeek": "Sunday", "timeOfDayUTC": "22:00"}
    assert amlfs["filesystemSubnetName"].endswith("subnet amlfs-subnet")
    per_tib = int(re.search(r"Premium-(\d+)$", sku).group(1))
    assert per_tib * tib == mbps, (per_tib, tib, mbps)
    (subnet,) = snap_json("amlfs-subnet.json")
    prefix_len = subnet["addressPrefix"].split("/")[1]
    assert subnet["name"] == "amlfs-subnet" and prefix_len == "28", subnet
    _, csi = snap("lustre-csi-install.txt")
    csi_text = "\n".join(csi)
    imgs = set(re.findall(r"azurelustre-csi:(v[\d.]+)-(?:noble|jammy)", csi_text))
    assert imgs == {"v0.4.0"} and "last-applied=(present: kubectl apply)" in csi_text, imgs
    csi_version = imgs.pop()

    # --- who scheduled the DGD's Pods: Grove + KAI (narration) ------------------------------------
    _, sched = snap("scheduler-evidence.txt")
    dgd_rows = [r.split() for r in sched if r.startswith("slime-sglang-")]
    assert len(dgd_rows) == 5 and all(r[1] == "kai-scheduler" for r in dgd_rows)
    assert len({r[3] for r in dgd_rows}) == 1, "the DGD's Pods weren't one PodGang"

    # --- the run's GPU split on its one node (closing narration), from the kit's manifests --------
    dgd = yaml.safe_load((KIT / "manifests" / "dgd.yaml").read_text())
    engine_gpus = sum(int(c["podTemplate"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
                      for c in dgd["spec"]["components"] if c["type"] == "worker")
    head = yaml.safe_load((KIT / "manifests" / "rayjob.yaml").read_text())["spec"]["rayClusterSpec"]["headGroupSpec"]
    train_gpus = int(head["template"]["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/gpu"])
    assert engine_gpus + train_gpus == int(labels["nvidia.com/gpu.count"]) == 8, (engine_gpus, train_gpus)

    # --- the emulated install commands --------------------------------------------------------------
    nodepool = [
        "az aks nodepool add --resource-group $RESOURCE_GROUP --cluster-name $CLUSTER --name ndh100pool \\",
        f"    --node-vm-size {vm} --node-count {pool_count} --os-sku {os_sku} --gpu-driver None",
    ]
    nfd = [
        f"helm install nfd oci://registry.k8s.io/nfd/charts/node-feature-discovery --version {v_nfd} \\",
        f"    -n {ns_nfd} --create-namespace",
    ]
    nvidia_repo = ["helm repo add nvidia https://helm.ngc.nvidia.com/nvidia"]
    gpu_op = [
        f"helm install gpu-operator nvidia/gpu-operator --version {v_gpu} -n {ns_gpu} --create-namespace \\",
        f"    --set nfd.enabled=false --set driver.version={driver}",
    ]
    kuberay_repo = ["helm repo add kuberay https://ray-project.github.io/kuberay-helm/"]
    kuberay = [f"helm install kuberay-operator kuberay/kuberay-operator --version {v_ray} -n {ns_ray} --create-namespace"]
    kai = [
        f"helm install kai-scheduler oci://ghcr.io/kai-scheduler/kai-scheduler/kai-scheduler --version {v_kai} \\",
        f"    -n {ns_kai} --create-namespace",
    ]
    grove = [
        f"helm install grove oci://ghcr.io/ai-dynamo/grove/grove-charts --version {v_grove} \\",
        f"    -n {ns_grove} --create-namespace",
    ]
    dynamo = [
        f"helm install dynamo-platform https://helm.ngc.nvidia.com/nvidia/ai-dynamo/charts/dynamo-platform-{v_dyn}.tgz \\",
        f"    -n {ns_dyn} --set global.grove.enabled=true --set global.kai-scheduler.enabled=true",
    ]
    amlfs_cmd = [
        "az amlfs create --name $AMLFS_NAME --resource-group $RESOURCE_GROUP --location $LOCATION \\",
        f"    --sku {sku} --storage-capacity {tib:g} --zones \"[{amlfs['zones'][0]}]\" \\",
        "    --filesystem-subnet $AMLFS_SUBNET_ID --maintenance-window "
        f"\"{{dayOfWeek:{amlfs['maintenanceWindow']['dayOfWeek']},"
        f"timeOfDayUtc:'{amlfs['maintenanceWindow']['timeOfDayUTC']}'}}\"",
    ]
    csi_cmd = [
        "curl -skSL https://raw.githubusercontent.com/kubernetes-sigs/azurelustre-csi-driver/main/deploy/install-driver.sh \\",
        f"    | bash -s {csi_version}",
    ]

    # --- the real state, as saved -------------------------------------------------------------------
    helm_cmd, helm_out = snap("helm-list-filtered.txt")
    nodes_cmd, nodes_out = snap("nodes-gpu.txt")
    amlfs_show_cmd, amlfs_out = snap("amlfs-table.txt")
    rollout_cmd, rollout_out = snap("lustre-csi-rollout.txt")
    assert [r.split()[0] for r in helm_out] == [
        "NAME", "dynamo-platform", "gpu-operator", "grove", "kai-scheduler", "kuberay-operator", "nfd"], helm_out
    assert len(nodes_out) == 1 + pool_count and all(r.split()[-1] == "8" for r in nodes_out[1:]), nodes_out
    assert amlfs_out[2].split() == [sku, f"{tib:.1f}", str(mbps)], amlfs_out
    assert rollout_out == ['daemon set "csi-azurelustre-node-noble" successfully rolled out'], rollout_out
    # The on-screen az command, split where a person would type the line breaks.
    head, _, query = amlfs_show_cmd.partition(" --query ")
    assert head == ("az resource show -g $RESOURCE_GROUP -n $AMLFS_NAME "
                    "--resource-type Microsoft.StorageCache/amlFilesystems"), head
    amlfs_show = [head + " \\", "    --query " + query]

    cmds = {
        "NODEPOOL": nodepool, "NFD": nfd, "NVIDIA_REPO": nvidia_repo, "GPU_OP": gpu_op,
        "KUBERAY_REPO": kuberay_repo, "KUBERAY": kuberay, "KAI": kai, "GROVE": grove, "DYNAMO": dynamo,
        "AMLFS": amlfs_cmd, "CSI": csi_cmd, "SHOW_HELM": [helm_cmd], "SHOW_NODES": [nodes_cmd],
        "SHOW_AMLFS": amlfs_show, "SHOW_CSI": [rollout_cmd],
    }
    for key, lines in cmds.items():
        for line in lines:
            assert len(line) <= MAXW, f"{key}: {len(line)} columns: {line}"
    for lines in (helm_out, nodes_out, amlfs_out, rollout_out):
        for line in lines:
            assert len(line) <= 120, f"{len(line)} columns: {line}"

    d = Data(__file__)
    d.source(*(f"demo/team/snapshots/{p.name}" for p in sorted(SNAP.iterdir()) if p.suffix in (".txt", ".json")),
             "manifests/dgd.yaml", "manifests/rayjob.yaml")
    for k, v in cmds.items():
        d.array(f"CLUSTER_CMD_{k}", v)
    d.array("CLUSTER_OUT_HELM", helm_out)
    d.array("CLUSTER_OUT_NODES", nodes_out)
    d.array("CLUSTER_OUT_AMLFS", amlfs_out)
    d.array("CLUSTER_OUT_CSI", rollout_out)
    d.set("CLUSTER_K8S", k8s)
    d.set("CLUSTER_VM", vm)
    d.set("CLUSTER_VM_SHORT", vm_short)
    d.set("CLUSTER_POOL_COUNT", pool_count)
    d.set("CLUSTER_GPUS_PER_NODE", int(labels["nvidia.com/gpu.count"]))
    d.set("CLUSTER_GPU_NAME", gpu_name)
    d.set("CLUSTER_DRIVER", driver)
    d.set("CLUSTER_V_GPU_OPERATOR", v_gpu)
    d.set("CLUSTER_V_KUBERAY", v_ray)
    d.set("CLUSTER_V_DYNAMO", v_dyn)
    d.set("CLUSTER_V_NETWORK_OPERATOR", v_net)
    d.set("CLUSTER_AMLFS_SKU", sku)
    d.set("CLUSTER_AMLFS_SKU_SHORT", sku.replace('AMLFS-Durable-', ''))
    d.set("CLUSTER_AMLFS_TIB", f"{tib:g}")
    d.set("CLUSTER_AMLFS_MBPS", f'{mbps:,}')
    d.set("CLUSTER_AMLFS_SUBNET_PREFIX", '/' + prefix_len)
    d.set("CLUSTER_CSI_VERSION", csi_version)
    d.set("CLUSTER_ENGINE_GPUS", engine_gpus)
    d.set("CLUSTER_TRAIN_GPUS", train_gpus)
    d.set("CLUSTER_SNAPSHOT_DATE", "2026-11-19")
    d.write()


if __name__ == "__main__":
    main()
