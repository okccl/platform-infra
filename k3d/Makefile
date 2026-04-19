CLUSTER_CONFIG  := cluster.yaml
CLUSTER_NAME    := dev
GITOPS_REPO     := git@github.com:okccl/platform-gitops.git
CHARTS_REPO     := git@github.com:okccl/platform-charts.git
SSH_KEY         := ~/.ssh/id_ed25519
ARGOCD_NS       := argocd
SECRETS_DIR     := ~/platform-gitops/secrets/encrypted
SOPS_AGE_KEY    := ~/.config/sops/age/keys.txt

.PHONY: cluster-create cluster-delete cluster-status bootstrap bootstrap-argocd bootstrap-sync

cluster-create:
	# 既存クラスタが残っている場合は削除してから作成
	k3d cluster delete $(CLUSTER_NAME) 2>/dev/null || true
	k3d cluster create --config $(CLUSTER_CONFIG)

cluster-delete:
	k3d cluster delete --config $(CLUSTER_CONFIG)

cluster-status:
	kubectl get nodes -o wide

# クラスタ再作成からsyncまでを一括実行
bootstrap: cluster-create bootstrap-argocd bootstrap-sync

# ArgoCDインストール・Root App適用
bootstrap-argocd:
	@if [ ! -f "$(SOPS_AGE_KEY)" ]; then \
		echo "エラー: Age秘密鍵が見つかりません: $(SOPS_AGE_KEY)"; exit 1; \
	fi
	# ArgoCDインストール
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		-n $(ARGOCD_NS) \
		--create-namespace \
		-f ~/platform-gitops/platform/argocd/values.yaml \
		--wait
	# ghcr-pat をSOPSで復号して投入
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/ghcr-pat.yaml \
		| kubectl apply -f -
	# Root App適用
	kubectl apply -f ~/platform-gitops/bootstrap/root.yaml
	kubectl apply -f ~/platform-gitops/bootstrap/apps-root.yaml
	# ArgoCD Server の起動を待機
	kubectl wait --for=condition=available deployment/argocd-server \
		-n $(ARGOCD_NS) --timeout=120s

# コンポーネントの段階的sync（CRDの依存順序に従う）
bootstrap-sync:
	# port-forward起動（8080を使用中のプロセスがあれば先に終了）
	lsof -ti:8080 | xargs kill -9 2>/dev/null || true
	sleep 2
	kubectl port-forward svc/argocd-server -n $(ARGOCD_NS) 8080:80 &
	sleep 5
	# ArgoCD CLIログイン（コンテキスト名を明示して上書き）
	@ARGOCD_PASS=$$(argocd admin initial-password -n $(ARGOCD_NS) | head -1) && \
	argocd login localhost:8080 \
		--username admin \
		--password $$ARGOCD_PASS \
		--plaintext \
		--name localhost
	argocd context localhost
	# リポジトリ登録（既存の場合はスキップ）
	argocd repo add $(GITOPS_REPO) --ssh-private-key-path $(SSH_KEY) 2>/dev/null || true
	argocd repo add $(CHARTS_REPO) --ssh-private-key-path $(SSH_KEY) 2>/dev/null || true
	# Root AppからNamespaceと子Applicationのみ先にsync（CRD未存在エラーを回避）
	argocd app sync root --server-side \
		--resource :Namespace:* \
		--resource argoproj.io:Application:*
	# cert-manager（CRD基盤）
	argocd app sync cert-manager --async
	kubectl wait --for=condition=established \
		crd/clusterissuers.cert-manager.io --timeout=180s
	# ingress-nginx
	argocd app sync ingress-nginx --async
	kubectl wait --for=condition=available deployment/ingress-nginx-controller \
		-n ingress-nginx --timeout=300s
	# external-secrets（CRD基盤）
	argocd app sync external-secrets --server-side --async
	kubectl wait --for=condition=established \
		crd/clustersecretstores.external-secrets.io --timeout=180s
	# webhookの起動を待機（ClusterExternalSecretの処理に必要）
	kubectl wait --for=condition=available deployment/external-secrets-webhook \
		-n external-secrets --timeout=180s
	# kyverno（CRD基盤）
	argocd app sync kyverno --async
	kubectl wait --for=condition=established \
		crd/clusterpolicies.kyverno.io --timeout=180s
	# 残りのコンポーネントを個別sync
	argocd app sync kyverno-policies --async
	argocd app sync cnpg --async
	argocd app sync kube-prometheus-stack --async
	argocd app sync loki --async
	argocd app sync alloy --async
	argocd app sync tempo --async
	argocd app sync minio --async
	argocd app sync apps-root --server-side --async
	# minio namespaceが作成されるまで待機してからSecret投入
	kubectl wait --for=jsonpath='{.status.phase}'=Active \
		namespace/minio --timeout=300s
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/minio-auth.yaml \
		| kubectl apply -f -
	# sample-app namespaceが作成されるまで待機してからSecret投入
	kubectl wait --for=jsonpath='{.status.phase}'=Active \
		namespace/sample-app --timeout=300s
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/minio-backup-secret.yaml \
		| kubectl apply -f -
	# goldilocks用namespaceラベル付与
	kubectl label namespace sample-app goldilocks.fairwinds.com/enabled=true --overwrite
	@echo "bootstrap-sync 完了。'argocd app list' で状態を確認してください。"
