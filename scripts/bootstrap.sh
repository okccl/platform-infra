#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------
# Helpers
# -----------------------------------------------
info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
skip()    { echo "[SKIP]  $*"; }

AQUA_BIN="${XDG_DATA_HOME:-$HOME/.local/share}/aquaproj-aqua/bin"
AQUA_GLOBAL_CONFIG="$HOME/platform-infra/aqua.yaml"

# -----------------------------------------------
# 1. System packages
# -----------------------------------------------
info "Updating apt packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq git curl make unzip ca-certificates direnv iptables

# WSL2 では iptables-legacy を使用する（Docker daemon の要件）
if command -v update-alternatives &>/dev/null && [ -f /usr/sbin/iptables-legacy ]; then
  sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
  sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
fi

# -----------------------------------------------
# 2. Homebrew
# -----------------------------------------------
if command -v brew &>/dev/null; then
  skip "Homebrew already installed"
else
  info "Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
  success "Homebrew installed"
fi

# -----------------------------------------------
# 3. aqua
# -----------------------------------------------
if command -v aqua &>/dev/null; then
  skip "aqua already installed"
else
  info "Installing aqua..."
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
  brew install aqua
  success "aqua installed"
fi

# -----------------------------------------------
# 4. aqua-managed tools（Docker CLI/daemon 含む）
#    バージョンの source of truth は aqua.yaml
# -----------------------------------------------
info "Installing aqua-managed tools (kubectl, helm, k3d, docker, ...)..."
export PATH="${AQUA_BIN}:$PATH"
# aqua install は aqua.yaml が存在するディレクトリで実行する必要がある
# （AQUA_GLOBAL_CONFIG 環境変数は aqua v2.x では bin シンボリックリンク作成に使われない）
(cd "$(dirname "${AQUA_GLOBAL_CONFIG}")" && aqua install)
success "Tools installed via aqua"

# -----------------------------------------------
# 5. Docker daemon setup
#    aqua が提供する dockerd を systemd サービスとして起動する。
#    containerd.io（dockerd の依存）は Docker 公式 APT リポジトリから取得する。
#    docker-ce 本体は aqua 管理のため APT からはインストールしない。
# -----------------------------------------------
if systemctl is-active --quiet docker 2>/dev/null; then
  skip "Docker daemon already running"
else
  info "Setting up Docker daemon..."

  # containerd.io（dockerd の依存。aqua 管理外のためここだけ APT）
  if ! dpkg -l containerd.io &>/dev/null 2>&1; then
    info "Installing containerd.io..."
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
      https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq containerd.io
  fi

  # docker グループ
  sudo groupadd -f docker
  sudo usermod -aG docker "$USER"

  # aqua の dockerd は遅延インストールのため、service ファイルに埋め込む前に
  # docker CLI を一度実行して実体バイナリをダウンロードさせる
  "${AQUA_BIN}/docker" version >/dev/null 2>&1 || true
  DOCKERD_PATH="$(aqua which dockerd)"
  DOCKER_PROXY_PATH="$(aqua which docker-proxy)"

  # systemd ユニット（aqua の dockerd を使用）
  sudo tee /etc/systemd/system/docker.service > /dev/null <<EOF
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target containerd.service
Requires=docker.socket containerd.service

[Service]
Type=notify
ExecStart=${DOCKERD_PATH} -H fd:// --containerd=/run/containerd/containerd.sock --userland-proxy-path=${DOCKER_PROXY_PATH}
ExecReload=/bin/kill -s HUP \$MAINPID
TimeoutStartSec=0
RestartSec=2
Restart=always
StartLimitIntervalSec=60
StartLimitBurst=3

[Install]
WantedBy=multi-user.target
EOF

  sudo tee /etc/systemd/system/docker.socket > /dev/null <<EOF
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/var/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now docker
  success "Docker daemon started (re-login required for group to take effect)"
fi

# -----------------------------------------------
# 6. .bashrc integrations
# -----------------------------------------------
BASHRC="$HOME/.bashrc"

if ! grep -q "linuxbrew" "$BASHRC"; then
  echo 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"' >> "$BASHRC"
  success "Added Homebrew to .bashrc"
fi

if ! grep -q "AQUA_GLOBAL_CONFIG" "$BASHRC"; then
  printf '\n# aqua\nexport PATH="${AQUA_ROOT_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/aquaproj-aqua}/bin:$PATH"\nexport AQUA_GLOBAL_CONFIG="$HOME/platform-infra/aqua.yaml"\n' >> "$BASHRC"
  success "Added aqua to .bashrc"
fi

if ! grep -q "direnv hook" "$BASHRC"; then
  echo 'eval "$(direnv hook bash)"' >> "$BASHRC"
  success "Added direnv to .bashrc"
fi

if ! grep -q "BASH_SOURCED" "$BASHRC"; then
  echo '[ -z "$BASH_SOURCED" ] && export BASH_SOURCED=1 && cd ~' >> "$BASHRC"
  success "Added default cd to .bashrc"
fi

# -----------------------------------------------
# 7. リポジトリのクローン
#    対象リポジトリは scripts/repos.txt で管理。
#    実行前にリポジトリの追加・削除・名前変更がないか確認すること。
# -----------------------------------------------
REPOS_FILE="$(dirname "$0")/repos.txt"
mapfile -t repos < <(grep -v '^\s*#' "$REPOS_FILE" | grep -v '^\s*$')
for repo in "${repos[@]}"; do
  if [ -d "$HOME/$repo" ]; then
    skip "$repo は既存"
  else
    git clone "git@github.com:okccl/$repo.git" "$HOME/$repo"
    success "$repo クローン完了"
  fi
done

# -----------------------------------------------
# 8. Age 秘密鍵の配置
# -----------------------------------------------
AGE_KEY_FILE="${HOME}/.config/sops/age/keys.txt"
if [ -f "$AGE_KEY_FILE" ]; then
  skip "Age 秘密鍵は既存"
else
  echo ""
  echo "[INPUT] Age 秘密鍵をパスワードマネージャーからコピーして貼り付け、Ctrl+D で確定してください。"
  mkdir -p "$(dirname "$AGE_KEY_FILE")"
  cat > "$AGE_KEY_FILE"
  chmod 600 "$AGE_KEY_FILE"
  success "Age 秘密鍵を配置しました"
fi

# -----------------------------------------------
# 9. minio-external コンテナの作成
#    Docker グループはスクリプト内では有効にならないため sudo -E を使用
# -----------------------------------------------
# sudo は secure_path で PATH を上書きするため AQUA_BIN が見えない。
# 実体バイナリの絶対パスを使って sudo を呼ぶ。
DOCKER_REAL="$(aqua which docker)"

if sudo "${DOCKER_REAL}" ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^minio-external$'; then
  skip "minio-external は既存"
else
  info "minio-external コンテナを作成中..."
  mkdir -p "$HOME/minio-data"
  sudo "${DOCKER_REAL}" run -d \
    --name minio-external \
    --restart unless-stopped \
    -p 9000:9000 -p 9001:9001 \
    -v "$HOME/minio-data:/data" \
    -e MINIO_ROOT_USER=minioadmin \
    -e MINIO_ROOT_PASSWORD=minioadmin123 \
    quay.io/minio/minio:latest \
    server /data --console-address ":9001"
  sleep 3
  sudo "${DOCKER_REAL}" exec minio-external /usr/bin/mc alias set local \
    http://localhost:9000 minioadmin minioadmin123
  sudo "${DOCKER_REAL}" exec minio-external /usr/bin/mc mb local/cnpg-backup
  success "minio-external 作成完了"
fi

# -----------------------------------------------
# 10. Done
# -----------------------------------------------
echo ""
echo "Bootstrap complete. Next steps:"
echo ""
echo "  1. source ~/.bashrc"
echo "  2. WSL 全損リストアの場合: ~/platform-docs/docs/runbook/dr-restore.md シナリオ C の続きを実行"
echo "     通常セットアップの場合: cd ~/platform-infra/k3d && make bootstrap"
