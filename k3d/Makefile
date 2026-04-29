CLUSTER_CONFIG  := cluster.yaml
CLUSTER_NAME    := dev
GITOPS_REPO     := git@github.com:okccl/platform-gitops.git
CHARTS_REPO     := git@github.com:okccl/platform-charts.git
SSH_KEY         := ~/.ssh/id_ed25519
ARGOCD_NS       := argocd
SECRETS_DIR     := ~/platform-gitops/secrets/encrypted
SOPS_AGE_KEY    := $(HOME)/.config/sops/age/keys.txt

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

bootstrap-sync:
	# port-forward起動
	lsof -ti:8080 | xargs kill -9 2>/dev/null || true
	sleep 2
	kubectl port-forward svc/argocd-server -n $(ARGOCD_NS) 8080:80 &
	sleep 5
	# ArgoCD CLIログイン
	@ARGOCD_PASS=$$(argocd admin initial-password -n $(ARGOCD_NS) | head -1) && \
	argocd login localhost:8080 \
		--username admin \
		--password $$ARGOCD_PASS \
		--plaintext \
		--name localhost
	# SSHエージェント起動・鍵登録
	eval $$(ssh-agent -s) && ssh-add $(SSH_KEY)
	# リポジトリ登録
	argocd repo add $(GITOPS_REPO) --ssh-private-key-path $(SSH_KEY) 2>/dev/null || true
	argocd repo add $(CHARTS_REPO) --ssh-private-key-path $(SSH_KEY) 2>/dev/null || true
	# root appのsyncを開始（sync-waveで順序制御）
	argocd app get root --refresh > /dev/null
	argocd app sync root --server-side --async || true
	# ingress-nginxの起動を待機（wave1,2が完了した証拠）
	@until kubectl get deployment ingress-nginx-controller -n ingress-nginx 2>/dev/null; do echo "ingress-nginx待機..."; sleep 5; done
	kubectl wait --for=condition=available deployment/ingress-nginx-controller \
		-n ingress-nginx --timeout=600s
	# minio Secret投入
	kubectl wait --for=jsonpath='{.status.phase}'=Active \
		namespace/minio --timeout=300s
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/minio-auth.yaml \
		| kubectl apply -f -
	# sample-app Secret投入
	kubectl wait --for=jsonpath='{.status.phase}'=Active \
		namespace/sample-app --timeout=300s
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/minio-backup-secret.yaml \
		| kubectl apply -f -
	# goldilocks用ラベル付与
	kubectl label namespace sample-app goldilocks.fairwinds.com/enabled=true --overwrite
	@echo "bootstrap完了。残りは自動syncで適用されます。argocd app list で確認してください。"
