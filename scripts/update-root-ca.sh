#!/usr/bin/env bash
set -euo pipefail

# local-ca-secret が存在するまで待機
echo "local-ca-secret を待機中..."
until kubectl get secret local-ca-secret -n cert-manager &>/dev/null; do
  sleep 5
done

# CA 証明書を取得して argocd namespace に Secret として作成
echo "argocd-local-ca Secret を作成中..."
kubectl get secret local-ca-secret -n cert-manager \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > /tmp/local-ca.crt

kubectl create secret generic argocd-local-ca \
  -n argocd \
  --from-file=ca.crt=/tmp/local-ca.crt \
  --dry-run=client -o yaml | kubectl apply -f -

echo "argocd-local-ca Secret を作成しました"
