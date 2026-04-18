CLUSTER_CONFIG := cluster.yaml

.PHONY: cluster-create cluster-delete cluster-status

cluster-create:
	k3d cluster create --config $(CLUSTER_CONFIG)

cluster-delete:
	k3d cluster delete --config $(CLUSTER_CONFIG)

cluster-status:
	kubectl get nodes -o wide
