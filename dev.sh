#!/usr/bin/env bash
# 一键启动 Open WebUI 本地 dev（前端 + 后端）
set -euo pipefail

cd "$(dirname "$0")"

echo "=============================================="
echo "  Open WebUI 本地开发模式启动"
echo "=============================================="

# 启动后端（8080）
echo "[1/2] 启动后端 uvicorn @ http://localhost:8080 ..."
bash dev-backend.sh &
BACKEND_PID=$!

# 启动前端（5173）
echo "[2/2] 启动前端 vite @ http://localhost:5173 ..."
bash dev-frontend.sh &
FRONTEND_PID=$!

# 退出时清理两个子进程
trap 'echo; echo "正在停止服务..."; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; wait 2>/dev/null' EXIT INT TERM

echo ""
echo "两个服务已在后台启动。"
echo "  前端界面: http://localhost:5173"
echo "  后端 API:  http://localhost:8080"
echo ""
echo "按 Ctrl+C 停止两个服务。"
echo "=============================================="

# 等待任一子进程退出（否则脚本立即结束会触发 trap 杀掉它们）
wait
