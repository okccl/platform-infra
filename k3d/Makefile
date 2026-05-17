CLUSTER_CONFIG  := cluster.yaml
CLUSTER_NAME    := dev
ARGOCD_NS       := argocd
SECRETS_DIR     := ~/platform-gitops/secrets/encrypted
SOPS_AGE_KEY    := $(HOME)/.config/sops/age/keys.txt
CILIUM_VERSION  := 1.19.3

.PHONY: cluster-create cluster-delete cluster-status bootstrap \
        bootstrap-argocd bootstrap-apps bootstrap-sync fix-coredns install-cilium \
        generate-dr-manifests

generate-dr-manifests: ## GitOps ソースから DR マニフェストを生成（DR 手順の最初のステップ）
	@python3 scripts/generate-dr-manifests.py

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

# CoreDNS に host.k3d.internal と *.platform.local rewrite を追加
# *.platform.local は固定 SVC 名 envoy-eg へのリライトルール（bootstrap 前に書き込み可能）
fix-coredns:
	$(eval K3D_GW := $(shell docker network inspect k3d-$(CLUSTER_NAME) \
		--format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'))
	kubectl get configmap coredns -n kube-system -o json \
		| python3 -c "import sys,json; \
		  d=json.load(sys.stdin); \
		  nh=d['data']['NodeHosts']; \
		  entry='$(K3D_GW) host.k3d.internal\n'; \
		  d['data']['NodeHosts']=nh if 'host.k3d.internal' in nh else nh+entry; \
		  cf=d['data']['Corefile']; \
		  rule='    rewrite name keycloak.platform.local envoy-eg.envoy-gateway-system.svc.cluster.local\n    rewrite name argocd.platform.local envoy-eg.envoy-gateway-system.svc.cluster.local\n    rewrite name backstage.platform.local envoy-eg.envoy-gateway-system.svc.cluster.local\n'; \
		  d['data']['Corefile']=cf.replace('ready\n', 'ready\n'+rule) if 'rewrite name keycloak.platform.local' not in cf else cf; \
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

# ArgoCDインストール
bootstrap-argocd:
	@if [ ! -f "$(SOPS_AGE_KEY)" ]; then \
		echo "エラー: Age秘密鍵が見つかりません: $(SOPS_AGE_KEY)"; exit 1; \
	fi
	# cert-manager namespace を作成して local-ca-secret を事前投入（cert-manager が新規CA生成しないよう）
	kubectl create namespace cert-manager --dry-run=client -o yaml | kubectl apply -f -
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/local-ca-secret.yaml \
		| kubectl apply -f -
	# argocd namespace を作成して argocd-local-ca Secret を事前投入
	kubectl create namespace $(ARGOCD_NS) --dry-run=client -o yaml | kubectl apply -f -
	kubectl get secret local-ca-secret -n cert-manager \
		-o jsonpath='{.data.tls\.crt}' | base64 -d \
		| kubectl create secret generic argocd-local-ca \
			-n $(ARGOCD_NS) \
			--from-file=ca.crt=/dev/stdin \
			--dry-run=client -o yaml \
		| kubectl label --local -f - app.kubernetes.io/part-of=argocd -o yaml \
		| kubectl apply -f -
	# ArgoCDインストール
	# sops Age秘密鍵をargocd namespaceに投入（ksops用）
	kubectl create secret generic sops-age \
		--from-file=keys.txt=$(SOPS_AGE_KEY) \
		-n $(ARGOCD_NS) \
		--dry-run=client -o yaml | kubectl apply -f -
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		-n $(ARGOCD_NS) \
		--create-namespace \
		-f ~/platform-gitops/platform/argocd/values.yaml \
		--wait
	# ArgoCD Server の起動を待機
	kubectl wait --for=condition=available deployment/argocd-server \
		-n $(ARGOCD_NS) --timeout=300s
	# GitHub App 認証でリポジトリ接続（credential template を適用）
	SOPS_AGE_KEY_FILE=$(SOPS_AGE_KEY) sops decrypt $(SECRETS_DIR)/argocd-repo-creds.yaml \
		| kubectl apply -f -


# アプリケーション層の展開（プラットフォーム基盤が安定してから手動実行）
bootstrap-apps:
	kubectl apply -f ~/platform-gitops/bootstrap/apps-root.yaml
	# goldilocks用ラベル付与
	kubectl label namespace sample-app goldilocks.fairwinds.com/enabled=true --overwrite
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
	# root-1-gateway を sync して完了待機
	kubectl apply -f ~/platform-gitops/platform/group-roots/root-1-gateway.yaml
	@until argocd app get root-1-gateway &>/dev/null; do echo "root-1-gateway 作成待機中..."; sleep 5; done
	argocd app sync root-1-gateway --server-side || true
	# root-1-gateway の子 App が起動するまで待機（ArgoCD GUI HTTPS アクセス可能になるまで）
	@until kubectl get deployment envoy-gateway -n envoy-gateway-system 2>/dev/null; do echo "envoy-gateway 待機中..."; sleep 5; done
	kubectl wait --for=condition=available deployment/envoy-gateway \
		-n envoy-gateway-system --timeout=600s
	@until kubectl get pod -l gateway.envoyproxy.io/owning-gateway-name=eg -n envoy-gateway-system 2>/dev/null | grep -q eg; do echo "Envoy Gateway Pod 作成待機中..."; sleep 5; done
	kubectl wait --for=condition=ready pod \
		-l gateway.envoyproxy.io/owning-gateway-name=eg \
		-n envoy-gateway-system --timeout=300s
	# port-forward を切って argocd.platform.local 経由に切り替え
	lsof -ti:8080 | xargs kill -9 2>/dev/null || true
	@until curl -sk https://argocd.platform.local/healthz | grep -q ok; do echo "argocd.platform.local 疎通待機中..."; sleep 5; done
	@ARGOCD_PASS=$$(argocd admin initial-password -n $(ARGOCD_NS) | head -1) && \
	argocd login argocd.platform.local \
		--username admin \
		--password $$ARGOCD_PASS \
		--insecure \
		--name argocd.platform.local
	# root-2-auth を sync して完了待機（CNPG webhook 含む全コンポーネントが Ready になるまで）
	kubectl apply -f ~/platform-gitops/platform/group-roots/root-2-auth.yaml
	@until argocd app get root-2-auth &>/dev/null; do echo "root-2-auth 作成待機中..."; sleep 5; done
	argocd app sync root-2-auth --server-side || true
	argocd app wait root-2-auth --sync --health --timeout 900
	# keycloak Pod が実際に起動するまで待機
	argocd app wait keycloak --health --timeout 600
	argocd app wait keycloak-config-cli --health --timeout 600
	# root-3-others を sync（root-2-auth 完了後なので CNPG webhook は確実に Ready）
	kubectl apply -f ~/platform-gitops/platform/group-roots/root-3-others.yaml
	@until argocd app get root-3-others &>/dev/null; do echo "root-3-others 作成待機中..."; sleep 5; done
	argocd app sync root-3-others --server-side || true
	@echo "=== bootstrap完了 ==="
	@echo "ArgoCD URL: https://argocd.platform.local"
