#!/bin/bash
# 启动 QuanTrading Web UI
# 用法：./start_web.sh [--dev]

cd "$(dirname "$0")"

# 检查是否已经在运行
if [ -f .web.pid ]; then
    OLD_PID=$(cat .web.pid)
    if ps -p $OLD_PID > /dev/null; then
        echo "服务已在运行中 (PID: $OLD_PID)，请先执行 ./stop_web.sh"
        exit 1
    fi
    rm .web.pid
fi

source .venv/bin/activate

if [ "$1" = "--dev" ]; then
  # 开发模式：FastAPI 热重载 + Vite dev server（两个进程）
  echo "开发模式启动..."
  echo "  前端 UI:  http://localhost:5173  (代理 /api → 3001)"
  echo ""
  # 后台启动后端FastAPI并记录 PID
  python -m web.server --reload > server.log 2>&1 &
  echo $! > .web.pid

  # 前台启动 Vite
  echo "  后端 API: http://127.0.0.1:3001 (PID: $!)"
  cd web/frontend && npm run dev
else
  echo "生产模式启动..."
  # 生产模式通常前台运行，但在脚本中我们记录它以便外部停止
  python -m web.server &
  echo $! > .web.pid
  echo "  地址：http://127.0.0.1:3001 (PID: $(cat .web.pid))"
  wait $(cat .web.pid)
fi