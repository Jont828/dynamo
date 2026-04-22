# Tilt Dev Setup Issues for DGDR Testing

## Context

When developing the DGDR (DynamoGraphDeploymentRequest) controller and profiler with Tilt, several issues arise because Tilt bypasses the Helm chart's setup Jobs and configuration. These issues block online (thorough) profiling from working in a Tilt-based dev environment.

The operator is installed via Tilt (`app.kubernetes.io/managed-by: tilt`), which deploys the operator binary and CRDs directly but skips Helm post-install hooks and Jobs.

---

## Issue 1: Missing MPI SSH Secret

### Symptom

When a DGDR creates a DynamoGraphDeployment (DGD) for profiling, the DGD controller fails immediately with:

```
failed to replicate MPI secret: error getting source secret: Secret "mpi-run-ssh-secret" not found
```

The DGD enters `state: failed` and never creates worker pods.

### Root Cause

The DGD controller (`dynamographdeployment_controller.go`, line ~325) **unconditionally** replicates an MPI SSH secret into the workload namespace before creating any deployment — even for single-node, single-GPU workloads that don't need MPI.

```go
// Always ensure MPI SSH secret is available in this namespace
if r.MPISecretReplicator != nil {
    err := r.MPISecretReplicator.Replicate(ctx, dynamoDeployment.Namespace)
    if err != nil {
        return ReconcileResult{}, fmt.Errorf("failed to replicate MPI secret: %w", err)
    }
}
```

When installed via Helm, a post-install Job (`mpi-run-ssh-keygen-job.yaml`) generates an RSA keypair and creates the `mpi-run-ssh-secret` secret in the operator namespace (`dynamo-system`). The DGD controller then copies it to the workload namespace.

Tilt skips this Job, so the secret never exists.

### Workaround

Manually create the secret:

```bash
ssh-keygen -t rsa -b 2048 -f /tmp/mpi-ssh-key -N "" -q
kubectl create secret generic mpi-run-ssh-secret -n dynamo-system \
  --from-file=private.key=/tmp/mpi-ssh-key \
  --from-file=public.key=/tmp/mpi-ssh-key.pub
rm /tmp/mpi-ssh-key /tmp/mpi-ssh-key.pub
```

### Suggested Fix

Two possible approaches:

1. **Add the SSH keygen Job to the Tiltfile** — run the same script that the Helm chart runs as part of `tilt up`.

2. **Make MPI secret replication conditional** — only replicate when the DGD actually has multi-node services. The `hasMultinode` check already exists at line ~335 but runs *after* the unconditional secret replication:

   ```go
   // Current: unconditional (line 325)
   if r.MPISecretReplicator != nil {
       err := r.MPISecretReplicator.Replicate(...)  // fails here
   }

   // Later: conditional check that should gate the above (line 335)
   hasMultinode := dynamoDeployment.HasAnyMultinodeService()
   ```

   Moving the replication inside the `hasMultinode` guard would fix this for all single-node deployments.

### Files

- `deploy/operator/internal/controller/dynamographdeployment_controller.go` (lines 325-331) — unconditional replication
- `deploy/operator/internal/secret/secret_replicator.go` — replication logic
- `deploy/helm/charts/platform/components/operator/templates/mpi-run-ssh-keygen-job.yaml` — Helm Job that creates the secret
- `deploy/operator/Tiltfile` — Tilt configuration (missing the keygen step)

---

## Issue 2: Missing Webhook TLS CA Bundle

### Symptom

Applying a DGDR fails with:

```
Internal error occurred: failed calling webhook "mdynamographdeploymentrequestv1beta1.kb.io":
failed to call webhook: Post "https://...": tls: failed to verify certificate: x509: certificate signed by unknown authority
```

### Root Cause

The operator generates self-signed TLS certs for the webhook server and stores them in the `webhook-server-cert` secret. But the `MutatingWebhookConfiguration` and `ValidatingWebhookConfiguration` resources don't have the `caBundle` field populated — the API server doesn't trust the self-signed cert.

When installed via Helm with cert-manager, a `cert-manager.io/inject-ca-from` annotation handles this automatically. Tilt doesn't install cert-manager and doesn't inject the CA bundle.

### Workaround

Patch the webhook configurations with the CA from the secret:

```bash
CA_BUNDLE=$(kubectl get secret webhook-server-cert -n dynamo-system -o jsonpath='{.data.ca\.crt}')

# Patch mutating webhook (update indices for number of webhooks)
kubectl patch mutatingwebhookconfiguration dynamo-dynamo-operator-mutating \
  --type='json' -p="[
    {\"op\":\"add\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"},
    {\"op\":\"add\",\"path\":\"/webhooks/1/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"}
  ]"

# Patch validating webhook (update indices for number of webhooks)
kubectl patch validatingwebhookconfiguration dynamo-dynamo-operator-validating \
  --type='json' -p="[
    {\"op\":\"add\",\"path\":\"/webhooks/0/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"},
    {\"op\":\"add\",\"path\":\"/webhooks/1/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"},
    {\"op\":\"add\",\"path\":\"/webhooks/2/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"},
    {\"op\":\"add\",\"path\":\"/webhooks/3/clientConfig/caBundle\",\"value\":\"${CA_BUNDLE}\"}
  ]"
```

### Suggested Fix

Add a Tilt post-deploy step that waits for the `webhook-server-cert` secret to exist and then patches the webhook configurations with the CA bundle. This should run after the operator pod is ready.

### Files

- `deploy/operator/Tiltfile` — needs post-deploy CA injection step
- Webhook configurations are created by Tilt from the operator manifests but lack `caBundle`

---

## Issue 3: Worker Image Derivation for Online Profiling

### Symptom

During online (thorough) profiling, the profiler creates DGDs whose worker pods fail with `ImagePullBackOff`. The worker pods try to pull an image like `docker.io/jont828/vllm-runtime:profiling-phases-v2` which doesn't exist.

### Root Cause

The profiler derives the worker image name from the DGDR's `spec.image` (the frontend image). It replaces `dynamo-frontend` with `vllm-runtime` (or the appropriate backend runtime). If you only built and pushed the frontend image, the derived worker image doesn't exist in the registry.

For example, with `spec.image: docker.io/jont828/dynamo-frontend:profiling-phases-v2`, the profiler generates worker specs using `docker.io/jont828/vllm-runtime:profiling-phases-v2`.

### Workaround

Build and push the vllm-runtime image with the same tag:

```bash
python container/render.py --target=runtime --framework=vllm --output-short-filename
docker build -f container/rendered.Dockerfile -t docker.io/jont828/vllm-runtime:profiling-phases-v2 .
docker push docker.io/jont828/vllm-runtime:profiling-phases-v2
```

Alternatively, use the `spec.overrides.dgd` field to specify a different worker image.

### Files

- Profiler image derivation logic in `components/src/dynamo/profiler/` (enumeration/config generation)
