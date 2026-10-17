#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
export LEAD_DATA_DIR="${LEAD_DATA_DIR:-$PROJECT_DIR/data}"
PORT="${LEAD_PORT:-18765}"
if [[ ! -x .venv/bin/python ]]; then
  echo "Python 环境不存在。请先运行 scripts/setup.sh" >&2
  exit 1
fi
if [[ ! -f frontend/dist/index.html ]]; then
  echo "界面尚未构建。请先运行：cd frontend && npm ci && npm run build" >&2
  exit 1
fi
mkdir -p "$LEAD_DATA_DIR"
API_PID=""
WORKER_PID=""
SEARCH_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$WORKER_PID" ]]; then kill "$WORKER_PID" 2>/dev/null || true; fi
  if [[ -n "$SEARCH_PID" ]]; then kill "$SEARCH_PID" 2>/dev/null || true; fi
  if [[ -n "$API_PID" ]]; then kill "$API_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
if [[ "${1:-}" != "--api-only" && -x tools/searxng/.venv/bin/python ]]; then
  if ! .venv/bin/python -c 'import socket; s=socket.create_connection(("127.0.0.1",18888),timeout=1); s.close()' 2>/dev/null; then
    bash scripts/start-searxng.sh > "$LEAD_DATA_DIR/searxng.log" 2>&1 &
    SEARCH_PID=$!
  fi
fi
.venv/bin/python -m uvicorn leadworkbench.api:create_app --factory --host 127.0.0.1 --port "$PORT" &
API_PID=$!
if [[ "${1:-}" != "--api-only" ]]; then
  .venv/bin/python -m leadworkbench.worker --data-dir "$LEAD_DATA_DIR" &
  WORKER_PID=$!
fi
echo "本地工作台：http://127.0.0.1:$PORT"
echo "数据目录：$LEAD_DATA_DIR"
echo "保持此终端运行，Ctrl+C 停止。"
wait "$API_PID"
