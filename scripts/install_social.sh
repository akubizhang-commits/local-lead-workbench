#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install 'playwright>=1.55,<2' 'instaloader>=4.14,<5'
if [[ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
  .venv/bin/python -m playwright install chromium
fi
echo "社媒读取依赖已安装。到设置页选择读取方式，再连接专用浏览器。"
