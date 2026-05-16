.DEFAULT_GOAL := help

CLUSTER_NAME := dev

.PHONY: help bootstrap init check cluster-start cluster-stop cluster-restart

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Set up WSL from scratch (Homebrew, mise, Docker, direnv)
	@bash scripts/bootstrap.sh

init: ## Install all tools via mise
	mise install

check: ## Show versions of all tools
	@echo "kubectl : $$(kubectl version --client -o json | grep gitVersion | head -1 | tr -d '\" ,')"
	@echo "helm    : $$(helm version --short)"
	@echo "k3d     : $$(k3d version | head -1)"
	@echo "argocd  : $$(argocd version --client --short 2>/dev/null | head -1)"

cluster-stop: ## k3d クラスターを停止
	k3d cluster stop $(CLUSTER_NAME)

cluster-start: ## k3d クラスターを起動し Cilium eBPF マップを再初期化
	k3d cluster start $(CLUSTER_NAME)
	@echo ">>> ノードの準備を待機中..."
	kubectl wait node --all --for=condition=Ready --timeout=120s
	@echo ">>> CiliumNode キャッシュをクリア中..."
	kubectl delete ciliumnodes --all
	@echo ">>> Cilium eBPF マップを再初期化中..."
	kubectl rollout restart daemonset cilium -n kube-system
	kubectl rollout status daemonset cilium -n kube-system --timeout=120s
	@echo ">>> Cilium の全ノード疎通を確認中..."
	kubectl exec -n kube-system ds/cilium -- cilium status
	@echo ">>> クラスター起動完了"

cluster-restart: cluster-stop cluster-start ## k3d クラスターを安全に再起動（Cilium eBPF 再初期化含む）
