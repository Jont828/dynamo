# Bug: DGDR profiler doubles model path when using `modelCache` without `pvcModelPath`

## Summary

When a `DynamoGraphDeploymentRequest` (DGDR) specifies `modelCache` with only `pvcName` (and optionally `pvcMountPath`) but **without** `pvcModelPath`, the profiler generates a DGD with doubled model paths in worker and frontend CLI args, causing worker crashes.

## Reproduction

### Minimal DGDR that triggers the bug

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: my-deployment
spec:
  model: Qwen/Qwen3-32B
  backend: vllm
  searchStrategy: rapid
  autoApply: true
  modelCache:
    pvcName: model-cache
    # pvcMountPath defaults to /opt/model-cache
    # pvcModelPath is NOT set
```

### Observed behavior

Workers crash with:
```
Exception: Failed to fetch model '/opt/model-cache/opt/model-cache' from HuggingFace.
Is this a valid HuggingFace ID? Error: request error: HTTP status client error (404 Not Found)
for url (https://huggingface.co/api/models//opt/model-cache/opt/model-cache/revision/main)
```

When `pvcMountPath` is explicitly set to `/home/dynamo/.cache/huggingface`, the frontend crashes with:
```
__main__.py: error: argument --model-path: model-path must be a valid directory on disk,
got: /home/dynamo/.cache/huggingface/home/dynamo/.cache/huggingface
```

### Expected behavior

Workers should receive `--model Qwen/Qwen3-32B` (the HF model ID) and the PVC should be mounted at the specified `pvcMountPath` to serve as the HuggingFace cache directory. The frontend should receive `--model-path` pointing to the actual model files on the PVC, or the `--model-name` HF ID if the path isn't a local directory.

## Root cause

The bug is in `components/src/dynamo/profiler/utils/config_modifiers/protocol.py`, in the `build_dgd_config()` method (around line 544).

### Code flow

1. **`rapid.py:_build_k8s_overrides()`** extracts PVC config from the DGDR spec and passes it to the AI Configurator (AIC) as `K8sConfig` overrides.

2. **AIC** calls `build_dgd_config()` with:
   - `model_name = "Qwen/Qwen3-32B"` 
   - `model_path = None` or `model_path = pvc_mount_path` (AIC may set this)
   - `pvc_name = "model-cache"`
   - `pvc_mount_path = "/opt/model-cache"` (default)

3. **`build_dgd_config()`** computes:
   ```python
   effective_model_path = model_path or model_name  
   # If model_path is None → "Qwen/Qwen3-32B"
   # If model_path is pvc_mount_path → "/opt/model-cache"
   ```

4. It then tries to extract `pvc_path` by stripping the mount prefix:
   ```python
   if pvc_name and pvc_mount_path:
       pvc_path = ""
       if effective_model_path and effective_model_path.startswith(pvc_mount_path):
           pvc_path = effective_model_path[len(pvc_mount_path):].strip("/")
   ```
   
   - **Case A** (`effective_model_path = "Qwen/Qwen3-32B"`): `startswith("/opt/model-cache")` is `False`, so `pvc_path = ""`
   - **Case B** (`effective_model_path = "/opt/model-cache"`): `startswith("/opt/model-cache")` is `True`, stripping yields `""`, so `pvc_path = ""`
   
   Both cases fall into the `else` branch.

5. **The buggy `else` branch** (line ~559):
   ```python
   else:
       # Mounts PVC correctly...
       cls._ensure_spec_pvc(cfg2, pvc_name)
       for svc_name, svc in cfg2.spec.services.items():
           cls._ensure_service_volume_mount(svc, pvc_name, pvc_mount_path)
       # ...but passes effective_model_path to update_model
       result = cls.update_model(
           cfg2.model_dump(),
           model_name=model_name,
           model_path=effective_model_path,  # ← BUG
       )
   ```

6. **`update_model()`** then calls `_apply_model_update_to_cfg()`, which sets:
   - Workers: `--model /opt/model-cache` (or whatever `effective_model_path` is)
   - Frontend (if `patch_frontend=True`, i.e. path starts with `/`): `--model-path /opt/model-cache`

7. **The doubling**: AIC or the operator subsequently applies the PVC mount path logic again on top of this already-PVC-based model path, resulting in `/opt/model-cache/opt/model-cache` or `/home/dynamo/.cache/huggingface/home/dynamo/.cache/huggingface`.

## Proposed fix

In the `else` branch of `build_dgd_config()`, when `pvc_path` is empty and the model name is an HF ID (not an absolute path), the code should:

1. Mount the PVC (for HF cache purposes) — this part is correct
2. Pass `model_name` as the model identifier (NOT `effective_model_path` / `pvc_mount_path`)
3. NOT set `--model-path` on the frontend (since the model is an HF ID, not a local path)

```python
else:
    # Model is an HF ID — mount PVC as HF cache but keep model name as HF ID
    cfg_dict = cfg.model_dump()
    cfg2 = Config.model_validate(cfg_dict)
    cls._ensure_spec_pvc(cfg2, pvc_name)
    for svc_name, svc in cfg2.spec.services.items():
        cls._ensure_service_volume_mount(svc, pvc_name, pvc_mount_path)
    result = cls.update_model(
        cfg2.model_dump(),
        model_name=model_name,
        model_path=model_name,  # ← FIX: use HF ID, not mount path
    )
```

This ensures workers get `--model Qwen/Qwen3-32B` and `patch_frontend` is `False` (since `model_name` doesn't start with `/`), so the frontend doesn't get a bad `--model-path`.

Additionally, `HF_HOME` should be set as an environment variable on worker containers pointing to the PVC mount path, so HuggingFace's cache mechanism can discover the pre-downloaded model.

## Workaround

Set `pvcModelPath` explicitly in the DGDR to the HF cache snapshot path within the PVC. This takes the `if pvc_path:` branch which works correctly:

```yaml
modelCache:
  pvcName: model-cache
  pvcMountPath: /home/dynamo/.cache/huggingface
  pvcModelPath: hub/models--Qwen--Qwen3-32B/snapshots/9216db5781bf21249d130ec9da846c4624c16137
```

This produces the correct `--model /home/dynamo/.cache/huggingface/hub/models--Qwen--Qwen3-32B/snapshots/9216db5...` via `_normalize_model_path()`.

## Affected files

- `components/src/dynamo/profiler/utils/config_modifiers/protocol.py` — `build_dgd_config()` method, line ~559
- `components/src/dynamo/profiler/utils/dgdr_v1beta1_types.py` — `ModelCacheSpec.pvcMountPath` default value

## Affected versions

Observed on Dynamo 1.0.1 (`nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.0.1`).

## Additional context

The recipe at `recipes/qwen3-32b/` manually constructs DGDs that mount the PVC at `/home/dynamo/.cache/huggingface` and use `--model Qwen/Qwen3-32B` — this works correctly because it bypasses the profiler's `build_dgd_config()` entirely. The bug only manifests when using the DGDR → profiler → auto-generated DGD flow with a PVC-backed model cache.
