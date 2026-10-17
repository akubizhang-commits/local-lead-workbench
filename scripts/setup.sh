#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
# Fail early with a clear message instead of an opaque pip/npm error.
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3：需要 Python 3.11 或更高版本" >&2; exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "需要 Python 3.11 或更高版本（当前：$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')）" >&2; exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "未找到 node：需要 Node.js 22.6 或更高版本" >&2; exit 1
fi
NODE_VERSION="$(node -v | sed 's/^v//')"
IFS=. read -r NODE_MAJOR NODE_MINOR _ <<< "$NODE_VERSION"
if (( NODE_MAJOR < 22 || (NODE_MAJOR == 22 && NODE_MINOR < 6) )); then
  echo "需要 Node.js 22.6 或更高版本（当前：${NODE_VERSION}）" >&2; exit 1
fi
python3 -m venv .venv
if [[ -f requirements.lock ]]; then
  .venv/bin/python -m pip install -r requirements.lock
  .venv/bin/python -m pip install --no-deps -e .
else
  .venv/bin/python -m pip install -e '.[dev]'
fi
cd frontend
npm ci
npm run build
echo "安装完成。返回项目目录后运行 scripts/start.sh"
