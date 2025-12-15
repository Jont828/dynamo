export NAMESPACE=dynamo-system
kubectl create namespace ${NAMESPACE}

# to pull model from HF
export HF_TOKEN="[token]"
kubectl create secret generic hf-token-secret \
  --from-literal=HF_TOKEN="$HF_TOKEN" \
  -n ${NAMESPACE};

# Deploy any example (this uses vLLM with Qwen model using aggregated serving)
kubectl apply -f examples/backends/vllm/deploy/agg.yaml -n ${NAMESPACE}

# Check status
kubectl get dynamoGraphDeployment -n ${NAMESPACE}

# Test it
# kubectl port-forward svc/vllm-agg-frontend 8000:8000 -n ${NAMESPACE}
# curl http://localhost:8000/v1/models