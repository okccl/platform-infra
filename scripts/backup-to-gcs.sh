#!/usr/bin/env bash
# backup-to-gcs.sh - MinIO の cnpg-backup バケットを GCS に同期する
# 使用方法: make backup-to-gcs
set -euo pipefail

SOPS_AGE_KEY_FILE="${HOME}/.config/sops/age/keys.txt"
MINIO_SECRET_SOPS="${HOME}/platform-gitops/platform/secrets/sources/minio-backup-secret-source.yaml"
GCP_SA_KEY_SOPS="${HOME}/platform-infra/secrets/gcp-backup-sa-key.enc.json"
DISCORD_SECRET_SOPS="${HOME}/platform-gitops/platform/secrets/sources/discord-webhook-secret-source.yaml"

MINIO_ENDPOINT="http://localhost:9000"
MINIO_BUCKET="cnpg-backup"
GCS_BUCKET="ccl-platform-cnpg-backup"

# 一時ディレクトリ（終了時に削除）
WORK_DIR=$(mktemp -d)

# BACKUP_STATUS はデフォルト "failed"。全ステップ完了後に "success" に更新する
BACKUP_STATUS="failed"
DISCORD_WEBHOOK_URL=""

cleanup() {
    # 失敗時は Discord へ通知
    if [ "${BACKUP_STATUS}" != "success" ] && [ -n "${DISCORD_WEBHOOK_URL}" ]; then
        curl -s -X POST "${DISCORD_WEBHOOK_URL}" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"🔴 **MinIO → GCS バックアップ失敗**\\n実行日時: $(date '+%Y-%m-%d %H:%M:%S')\"}" \
            > /dev/null || true
    fi
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

echo "=== MinIO → GCS バックアップ開始: $(date '+%Y-%m-%d %H:%M:%S') ==="

# MinIO 認証情報を SOPS から復号
echo "[1/5] MinIO 認証情報を復号中..."
MINIO_SECRET_YAML=$(SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE}" sops decrypt "${MINIO_SECRET_SOPS}")
ACCESS_KEY_ID=$(echo "${MINIO_SECRET_YAML}" | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); print(d['stringData']['ACCESS_KEY_ID'])")
ACCESS_SECRET_KEY=$(echo "${MINIO_SECRET_YAML}" | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); print(d['stringData']['ACCESS_SECRET_KEY'])")

# Discord Webhook URL を SOPS から復号
echo "[2/5] Discord Webhook URL を復号中..."
DISCORD_SECRET_YAML=$(SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE}" sops decrypt "${DISCORD_SECRET_SOPS}")
DISCORD_WEBHOOK_URL=$(echo "${DISCORD_SECRET_YAML}" | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); print(d['stringData']['url'])")

# GCP SA キーを SOPS から復号
echo "[3/5] GCP サービスアカウントキーを復号中..."
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE}" sops decrypt "${GCP_SA_KEY_SOPS}" > "${WORK_DIR}/gcp-sa-key.json"

# rclone 設定ファイルを一時生成
echo "[4/5] rclone 設定を生成中..."
cat > "${WORK_DIR}/rclone.conf" <<EOF
[minio]
type = s3
provider = Minio
endpoint = ${MINIO_ENDPOINT}
access_key_id = ${ACCESS_KEY_ID}
secret_access_key = ${ACCESS_SECRET_KEY}
no_check_bucket = true

[gcs]
type = google cloud storage
service_account_file = ${WORK_DIR}/gcp-sa-key.json
bucket_policy_only = true
EOF

# rclone copy で同期（GCS 側は MinIO の削除に追随しない。30日 Lifecycle で独立管理）
echo "[5/5] rclone copy 実行中..."
rclone copy \
    --config "${WORK_DIR}/rclone.conf" \
    --transfers 4 \
    --progress \
    "minio:${MINIO_BUCKET}" \
    "gcs:${GCS_BUCKET}"

BACKUP_STATUS="success"
echo "=== バックアップ完了: $(date '+%Y-%m-%d %H:%M:%S') ==="
