#!/usr/bin/env bash
# Start the API, wait until it is ready, then start the worker and watch both
# (P1-5, design §4.4). Compatible with macOS bash 3.2: no `wait -n`, no arrays.
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
mkdir -p "$LEAD_DATA_DIR" 2>/dev/null || true
mkdir -p "$LEAD_DATA_DIR/logs" 2>/dev/null || true
if [[ ! -w "$LEAD_DATA_DIR" || ! -w "$LEAD_DATA_DIR/logs" ]]; then
  echo "数据目录不可写：${LEAD_DATA_DIR}，请检查权限" >&2
  exit 1
fi
API_PID=""
WORKER_PID=""
SEARCH_PID=""
stop_pid() {
  # TERM, then wait up to $2 seconds before KILL.
  local pid="$1" limit="$2" n=0
  kill -TERM "$pid" 2>/dev/null || return 0
  while kill -0 "$pid" 2>/dev/null && [[ $n -lt $((limit * 2)) ]]; do
    sleep 0.5
    n=$((n + 1))
  done
  kill -KILL "$pid" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  # Worker first: its Maps guard needs time to stop the scraper group.
  if [[ -n "$WORKER_PID" ]]; then stop_pid "$WORKER_PID" 10; fi
  if [[ -n "$SEARCH_PID" ]]; then kill "$SEARCH_PID" 2>/dev/null || true; fi
  if [[ -n "$API_PID" ]]; then stop_pid "$API_PID" 15; fi
  wait 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT
if [[ "${1:-}" != "--api-only" && -x tools/searxng/.venv/bin/python ]]; then
  if ! .venv/bin/python -c 'import socket; s=socket.create_connection(("127.0.0.1",18888),timeout=1); s.close()' 2>/dev/null; then
    bash scripts/start-searxng.sh > "$LEAD_DATA_DIR/searxng.log" 2>&1 &
    SEARCH_PID=$!
  fi
fi
.venv/bin/python -m uvicorn leadworkbench.api:create_app --factory --host 127.0.0.1 --port "$PORT" --no-access-log &
API_PID=$!

# Readiness: no fixed timeout, a large legacy database may migrate for minutes.
ticks=0
UNAVAILABLE="-"
while true; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    code=0
    wait "$API_PID" || code=$?
    API_PID=""
    echo "本地服务启动失败（退出码 ${code}）。请查看上方信息或日志：$LEAD_DATA_DIR/logs/api.log" >&2
    exit "$code"
  fi
  probe="$(.venv/bin/python scripts/_probe_session.py "$PORT" 2>/dev/null || echo down)"
  case "$probe" in
    "200 "*) UNAVAILABLE="${probe#200 }"; break ;;
  esac
  sleep 0.5
  ticks=$((ticks + 1))
  if [[ $ticks -eq 30 ]]; then
    echo "本地服务正在初始化数据库，可能需要几分钟，请勿关闭此窗口…"
  elif [[ $ticks -gt 30 && $(((ticks - 30) % 60)) -eq 0 ]]; then
    echo "仍在初始化数据库（已等待 $((ticks / 2)) 秒）…"
  fi
done

start_worker() {
  .venv/bin/python -m leadworkbench.worker --data-dir "$LEAD_DATA_DIR" &
  WORKER_PID=$!
}
if [[ "$UNAVAILABLE" != "-" ]]; then
  case "$UNAVAILABLE" in
    readonly_storage) reason="数据目录不可写" ;;
    disk_full) reason="磁盘空间不足" ;;
    *) reason="数据库初始化失败" ;;
  esac
  echo "本地服务处于受限模式：${reason}。后台采集未启动；打开界面可以查看原因。处理后请重新运行 start.sh。"
elif [[ "${1:-}" != "--api-only" ]]; then
  start_worker
fi
echo "本地工作台：http://127.0.0.1:$PORT"
echo "数据目录：$LEAD_DATA_DIR"
echo "保持此终端运行，Ctrl+C 停止。"

# Watch both processes. Worker restarts (D2): within 300 s of the first exit,
# exits 1-3 restart after 5/15/45 s; the 4th only prints a notice.
EXITS=""
while true; do
  if [[ -n "$API_PID" ]] && ! kill -0 "$API_PID" 2>/dev/null; then
    code=0
    wait "$API_PID" || code=$?
    API_PID=""
    echo "本地服务已退出（退出码 ${code}），正在停止后台进程。"
    cleanup
    exit "$code"
  fi
  if [[ -n "$WORKER_PID" ]] && ! kill -0 "$WORKER_PID" 2>/dev/null; then
    code=0
    wait "$WORKER_PID" || code=$?
    WORKER_PID=""
    if [[ $code -ne 0 ]]; then
      now="$(date +%s)"
      recent=""
      count=0
      for stamp in $EXITS $now; do
        if [[ $((now - stamp)) -lt 300 ]]; then
          recent="$recent $stamp"
          count=$((count + 1))
        fi
      done
      EXITS="$recent"
      if [[ $count -le 3 ]]; then
        case $count in
          1) delay=5 ;;
          2) delay=15 ;;
          *) delay=45 ;;
        esac
        echo "后台采集进程已退出（退出码 ${code}），$delay 秒后第 $count 次自动重启…"
        i=0
        while [[ $i -lt $delay ]]; do
          sleep 1
          i=$((i + 1))
        done
        start_worker
      else
        echo "后台采集进程反复退出，已停止自动重启。界面仍可使用，但不会执行检索。请查看 $LEAD_DATA_DIR/logs/worker.log，或在设置页导出诊断包。"
      fi
    fi
  fi
  sleep 2
done
