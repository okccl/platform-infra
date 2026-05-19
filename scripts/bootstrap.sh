#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------
# Helpers
# -----------------------------------------------
info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
skip()    { echo "[SKIP]  $*"; }

# -----------------------------------------------
# 1. System packages
# -----------------------------------------------
info "Updating apt packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq git curl make unzip ca-certificates direnv

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
# 4. Docker Engine
# -----------------------------------------------
if command -v docker &>/dev/null; then
  skip "Docker already installed"
else
  info "Installing Docker Engine..."
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
  sudo apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin
  sudo usermod -aG docker "$USER"
  success "Docker Engine installed (re-login required for group to take effect)"
fi

# -----------------------------------------------
# 5. .bashrc integrations
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
# 6. Done
# -----------------------------------------------
echo ""
echo "Bootstrap complete. Next steps:"
echo ""
echo "  0. Set git identity (if not yet configured):"
echo '     git config --global user.email "your@email.com"'
echo '     git config --global user.name "Your Name"'
echo ""
echo "  1. source ~/.bashrc"
echo "  2. cd ~/platform-infra"
echo "  3. make init"
echo "  4. make check"
