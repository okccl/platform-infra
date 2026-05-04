#!/usr/bin/env bash
set -euo pipefail

GITOPS_DIR="${HOME}/platform-gitops"
VALUES_FILE="${GITOPS_DIR}/platform/argocd/values.yaml"

# local-ca-secret が存在するまで待機
echo "local-ca-secret を待機中..."
until kubectl get secret local-ca-secret -n cert-manager &>/dev/null; do
  sleep 5
done

# CA証明書を一時ファイルに保存
kubectl get secret local-ca-secret -n cert-manager \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > /tmp/local-ca.crt

# values.yaml の rootCA ブロックを更新
echo "rootCA を更新中..."
python3 << PYEOF
import re

with open("/tmp/local-ca.crt") as f:
    ca_cert = f.read().strip()

with open("${VALUES_FILE}") as f:
    content = f.read()

# 8スペースインデントで証明書を整形
indented = "\n".join("        " + line for line in ca_cert.splitlines())
new_block = "      rootCA: |\n" + indented

# rootCA ブロックを次のキーまで置換
new_content = re.sub(
    r"      rootCA: \|.*?(?=\n  [a-z]|\Z)",
    new_block,
    content,
    flags=re.DOTALL
)

with open("${VALUES_FILE}", "w") as f:
    f.write(new_content)
print("rootCA を更新しました")
PYEOF

# git push
cd "${GITOPS_DIR}"
git add platform/argocd/values.yaml
git diff --cached --quiet || git commit -m "chore: rootCA を自動更新"
git push
