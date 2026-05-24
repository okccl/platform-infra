#!/usr/bin/env bash
# backup-to-gcs.sh - MinIO の cnpg-backup バケットを GCS に同期する
# 使用方法: make backup-to-gcs
set -euo pipefail

SOPS_AGE_KEY_FILE="${HOME}/.config/sops/age/keys.txt"
MINIO_SECRET_SOPS="${HOME}/platform-gitops/platform/secrets/sources/minio-backup-secret-source.yaml"
GCP_SA_KEY_SOPS="${HOME}/platform-infra/secrets/gcp-backup-sa-key.enc.json"

MINIO_ENDPOINT="http://localhost:9000"
MINIO_BUCKET="cnpg-backup"
GCS_BUCKET="ccl-platform-cnpg-backup"

# 一時ディレクトリ（終了時に削除）
WORK_DIR=$(mktemp -d)
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "=== MinIO → GCS バックアップ開始: $(date '+%Y-%m-%d %H:%M:%S') ==="

# MinIO 認証情報を SOPS から復号
echo "[1/4] MinIO 認証情報を復号中..."
MINIO_SECRET_YAML=$(SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE}" sops decrypt "${MINIO_SECRET_SOPS}")
ACCESS_KEY_ID=$(echo "${MINIO_SECRET_YAML}" | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); print(d['stringData']['ACCESS_KEY_ID'])")
ACCESS_SECRET_KEY=$(echo "${MINIO_SECRET_YAML}" | python3 -c "import sys, yaml; d=yaml.safe_load(sys.stdin); print(d['stringData']['ACCESS_SECRET_KEY'])")

# GCP SA キーを SOPS から復号
echo "[2/4] GCP サービスアカウントキーを復号中..."
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE}" sops decrypt "${GCP_SA_KEY_SOPS}" > "${WORK_DIR}/gcp-sa-key.json"

# rclone 設定ファイルを一時生成
echo "[3/4] rclone 設定を生成中..."
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

# rclone sync で同期
echo "[4/4] rclone sync 実行中..."
rclone sync \
    --config "${WORK_DIR}/rclone.conf" \
    --transfers 4 \
    --progress \
    "minio:${MINIO_BUCKET}" \
    "gcs:${GCS_BUCKET}"

echo "=== バックアップ完了: $(date '+%Y-%m-%d %H:%M:%S') ==="
