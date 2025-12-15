export RESOURCE_GROUP="dynamo-a100"
export CLUSTER_NAME="dynamo-a100"
export LOCATION="westeurope"
az group create --name $RESOURCE_GROUP --location $LOCATION
az aks create --resource-group $RESOURCE_GROUP --name $CLUSTER_NAME --enable-oidc-issuer --enable-workload-identity --enable-managed-identity --generate-ssh-keys

az aks nodepool add -g $RESOURCE_GROUP -n a100pool --cluster-name $CLUSTER_NAME -s Standard_NC24ads_A100_v4 -c 1
# az aks nodepool scale -g $RESOURCE_GROUP -n a10pool --cluster-name $CLUSTER_NAME -c 2