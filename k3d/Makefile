CLUSTER_CONFIG  := cluster.yaml
CLUSTER_NAME    := dev
GITOPS_REPO     := git@github.com:okccl/platform-gitops.git
SSH_KEY         := ~/.ssh/id_ed25519
ARGOCD_NS       := argocd

.PHONY: cluster-create cluster-delete cluster-status bootstrap bootstrap-argocd bootstrap-sync

cluster-create:
	k3d cluster create --config $(CLUSTER_CONFIG)

cluster-delete:
	k3d cluster delete --config $(CLUSTER_CONFIG)

cluster-status:
	kubectl get nodes -o wide

# クラスタ再作成からsyncまでを一括実行
bootstrap: cluster-create bootstrap-argocd bootstrap-sync

# ArgoCDインストール・Root App適用・リポジトリ登録
bootstrap-argocd:
	# ArgoCDインストール
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm install argocd argocd/argo-cd \
		-n $(ARGOCD_NS) \
		--create-namespace \
		-f ~/platform-gitops/platform/argocd/values.yaml \
		--wait
	# Root App適用
	kubectl apply -f ~/platform-gitops/bootstrap/root.yaml
	kubectl apply -f ~/platform-gitops/bootstrap/apps-root.yaml
	# ArgoCD CLIログイン
	@ARGOCD_PASS=$$(argocd admin initial-password -n $(ARGOCD_NS) | head -1) && \
	argocd login argocd.localhost \
		--username admin \
		--password $$ARGOCD_PASS \
		--grpc-web
	# リポジトリ登録
	argocd repo add $(GITOPS_REPO) --ssh-private-key-path $(SSH_KEY)

# コンポーネントの段階的sync（CRDの依存順序に従う）
bootstrap-sync:
	# cert-manager
	argocd app sync root --resource argoproj.io:Application:cert-manager --async
	argocd app sync cert-manager --async
	kubectl wait --for=condition=established \
		crd/clusterissuers.cert-manager.io --timeout=120s
	# ingress-nginx
	argocd app sync root --resource argoproj.io:Application:ingress-nginx --async
	argocd app sync ingress-nginx --async
	# external-secrets
	argocd app sync root --resource argoproj.io:Application:external-secrets --async
	argocd app sync external-secrets --server-side --async
	kubectl wait --for=condition=established \
		crd/clustersecretstores.external-secrets.io --timeout=120s
	# argocd自己管理・残りのコンポーネント
	argocd app sync root --server-side --async
	@echo "bootstrap-sync 完了。'argocd app list' で状態を確認してください。"
