#!/usr/bin/env bash
# 本地 dev 启动脚本（前端 vite）
set -euo pipefail

cd "$(dirname "$0")"

# The launcher detects Node 22 without relying on one machine's Homebrew path.
exec "${ORCHESTRA_LAUNCHER_PYTHON:-python3}" scripts/orchestra.py frontend "$@"
