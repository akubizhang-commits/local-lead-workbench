#!/bin/sh
# First-time server setup on Amazon Linux 2023 (AWS trial, docs/deploy/aws.md step 4).
# Installs Docker, git and pinned Docker Compose / Buildx CLI plugins (sha256-verified),
# adds the invoking user to the docker group and optionally creates a 2 GiB swap file.
# Safe to run again. Contains no secrets and never calls AWS APIs.
#   sudo sh deploy/install.sh [--swap]
set -eu

COMPOSE_VERSION=v5.6.0
BUILDX_VERSION=v0.38.0
SWAP=0
case "${1:-}" in
  "") ;;
  --swap) SWAP=1 ;;
  *) echo "用法：sudo sh deploy/install.sh [--swap]" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行：sudo sh deploy/install.sh" >&2; exit 2; }

case "$(uname -m)" in
  aarch64|arm64)
    COMPOSE_ASSET=docker-compose-linux-aarch64
    COMPOSE_SHA256=733ec76717ceb59052a9609b9dadfb523b2df8eab57a54212872d10a58078ea2
    BUILDX_ASSET=buildx-$BUILDX_VERSION.linux-arm64
    BUILDX_SHA256=38e890e1a162bfdbf32fbe91404991dc4fc28e55b0de901b34374a061bab4d2c ;;
  x86_64|amd64)
    COMPOSE_ASSET=docker-compose-linux-x86_64
    COMPOSE_SHA256=40343e21ca777173e69cff5dbafeb37c6f81f3b0d57d9e597f036e95eb63e76a
    BUILDX_ASSET=buildx-$BUILDX_VERSION.linux-amd64
    BUILDX_SHA256=4fe4cc38adf48169132749b6ca22a990928db0118e3407584ee553723115d287 ;;
  *) echo "不支持的架构：$(uname -m)" >&2; exit 2 ;;
esac

dnf install -y docker git
systemctl enable --now docker

PLUGINS=/usr/local/lib/docker/cli-plugins
mkdir -p "$PLUGINS"
install_plugin() {  # name url sha256
  target="$PLUGINS/$1"
  if [ -x "$target" ] && echo "$3  $target" | sha256sum -c --status; then
    echo "$1 已是固定版本，跳过"
    return
  fi
  tmp=$(mktemp)
  curl -fsSL -o "$tmp" "$2"
  echo "$3  $tmp" | sha256sum -c - || { rm -f "$tmp"; echo "$1 校验失败，未安装" >&2; exit 1; }
  install -m 0755 "$tmp" "$target"
  rm -f "$tmp"
}
install_plugin docker-compose \
  "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/$COMPOSE_ASSET" "$COMPOSE_SHA256"
install_plugin docker-buildx \
  "https://github.com/docker/buildx/releases/download/$BUILDX_VERSION/$BUILDX_ASSET" "$BUILDX_SHA256"

USER_NAME=${SUDO_USER:-ec2-user}
if id "$USER_NAME" >/dev/null 2>&1; then
  usermod -aG docker "$USER_NAME"
fi

if [ "$SWAP" = 1 ] && [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

docker compose version
docker buildx version
echo "完成。请退出并重新登录，让 docker 组对 $USER_NAME 生效。"
