#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$PROJECT_DIR/tools/searxng"
if [[ ! -x "$RUNTIME/.venv/bin/python" || ! -f "$RUNTIME/searxng-master/searx/webapp.py" ]]; then
  echo "SearXNG尚未安装，请按docs/validation中的社媒自动发现说明安装独立运行环境。" >&2
  exit 1
fi
export SEARXNG_SETTINGS_PATH="$RUNTIME/settings.yml"
export PYTHONPATH="$RUNTIME/searxng-master"
exec "$RUNTIME/.venv/bin/python" -m searx.webapp
