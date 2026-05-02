CLUSTER_CONFIG  := cluster.yaml
CLUSTER_NAME    := dev
GITOPS_REPO     := git@github.com:okccl/platform-gitops.git
CHARTS_REPO     := git@github.com:okccl/platform-charts.git
SSH_KEY         := ~/.ssh/id_ed25519
ARGOCD_NS       := argocd
SECRETS_DIR     := ~/platform-gitops/secrets/encrypted
SOPS_AGE_KEY    := $(HOME)/.config/sops/age/keys.txt
CILIUM_VERSION  := 1.19.3

.PHONY: cluster-create cluster-delete cluster-status bootstrap \
        bootstrap-argocd bootstrap-apps bootstrap-sync fix-coredns install-cilium

cluster-create:
	# 既存クラスタが残っている場合は削除してから作成
	k3d cluster delete $(CLUSTER_NAME) 2>/dev/null || true
	k3d cluster create --config $(CLUSTER_CONFIG)

cluster-delete:
	k3d cluster delete --config $(CLUSTER_CONFIG)

cluster-status:
	kubectl get nodes -o wide

# Cilium を先にインストールしてから CoreDNS を設定する
bootstrap: cluster-create install-cilium fix-coredns bootstrap-argocd bootstrap-sync

# CoreDNS に host.k3d.internal を追加（MinIO 疎通に必要）
fix-coredns:
	$(eval K3D_GW := $(shell docker network inspect k3d-$(CLUSTER_NAME) \
		--format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'))
	kubectl get configmap coredns -n kube-system -o json \
		| python3 -c "import sys,json; \
		  d=json.load(sys.stdin); \
		  nh=d['data']['NodeHosts']; \
		  entry='$(K3D_GW) host.k3d.internal\n'; \
		  d['data']['NodeHosts']=nh if 'host.k3d.internal' in nh else nh+entry; \
		  print(json.dumps(d))" \
		| kubectl apply -f -
	kubectl rollout restart deployment coredns -n kube-system
	kubectl rollout status deployment coredns -n kube-system

# Cilium を Helm で直接インストール（ArgoCD 起動前に CNI が必要なため）
install-cilium:
	$(eval API_SERVER_IP := $(shell kubectl get endpoints kubernetes \
		-o jsonpath='{.subsets[0].addresses[0].ip}'))
	helm repo add cilium https://helm.cilium.io/ 2>/dev/null || true
	helm repo update cilium
	helm upgrade --install cilium cilium/cilium \
		--version $(CILIUM_VERSION) \
		--namespace kube-system \
		--set kubeProxyReplacement=false \
		--set hostServices.enabled=false \
		--set externalIPs.enabled=true \
		--set nodePort.enabled=true \
		--set hostPort.enabled=true \
		--set bpf.masquerade=false \
		--set image.pullPolicy=IfNotPresent \
		--set ipam.mode=kubernetes \
		--wait --timeout=300s
	# Cilium が全ノードで Ready になるまで待機
	kubectl rollout status daemonset/cilium -n kube-system --timeout=300s

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
	kubectl apply -f ~/platform-gitops/platform/argocd/root-app.yaml
	# ArgoCD Server の起動を待機
	kubectl wait --for=condition=available deployment/argocd-server \
		-n $(ARGOCD_NS) --timeout=300s


# アプリケーション層の展開（プラットフォーム基盤が安定してから手動実行）
bootstrap-apps:
	kubectl apply -f ~/platform-gitops/bootstrap/apps-root.yaml
	@echo "apps-root を適用しました。argocd app list でアプリの sync 状態を確認してください。"

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
	@until kubectl get deployment envoy-gateway -n envoy-gateway-system 2>/dev/null; do echo "envoy-gateway待機..."; sleep 5; done
	kubectl wait --for=condition=available deployment/envoy-gateway \
		-n envoy-gateway-system --timeout=600s
	# envoy proxyデータプレーンの起動を待機（これが完了するとArgoCD GUIにアクセス可能）
	kubectl wait --for=condition=ready pod \
		-l gateway.envoyproxy.io/owning-gateway-name=eg \
		-n envoy-gateway-system --timeout=300s
	# CoreDNS に *.platform.local の rewrite を追加（Envoy Gateway 起動後
	$(eval EG_SVC := $(shell kubectl get svc -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=eg -o jsonpath='{.items[0].metadata.name}'))
	kubectl get configmap coredns -n kube-system -o json \
		| python3 -c "import sys,json; \
		  d=json.load(sys.stdin); \
		  cf=d['data']['Corefile']; \
		  rule='    rewrite name keycloak.platform.local $(EG_SVC).envoy-gateway-system.svc.cluster.local\n    rewrite name argocd.platform.local $(EG_SVC).envoy-gateway-system.svc.cluster.local\n    rewrite name backstage.platform.local $(EG_SVC).envoy-gateway-system.svc.cluster.local\n'; \
		  d['data']['Corefile']=cf.replace('ready\n', 'ready\n'+rule) if 'rewrite name keycloak.platform.local' not in cf else cf; \
		  print(json.dumps(d))" \
		| kubectl apply -f -
	kubectl rollout restart deployment coredns -n kube-system
	kubectl rollout status deployment coredns -n kube-system
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/minio-backup-secret.yaml \
		| kubectl apply -f -
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/keycloak-db-credentials.yaml \
		| kubectl apply -f -
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/keycloak-admin-credentials.yaml \
		| kubectl apply -f -
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/argocd-keycloak-secret.yaml \
		| kubectl apply -f -
	@echo ""
	@echo "=== bootstrap完了 ==="
	@echo "ArgoCD URL: http://argocd.platform.local"
	@echo "ArgoCD 初期パスワード: $$(argocd admin initial-password -n $(ARGOCD_NS) | head -1)"
	@echo "残りのAppは自動syncで適用されます。argocd app list で確認してください。"
