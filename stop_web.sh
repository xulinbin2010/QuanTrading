#!/bin/bash
# 停止 QuanTrading Web UI

cd "$(dirname "$0")"

if [ ! -f .web.pid ]; then
    echo "未找到 .web.pid 文件，尝试通过进程名搜索..."
    PIDS=$(pgrep -f "python -m web.server")
    if [ -z "$PIDS" ]; then
        echo "没有发现运行中的 web.server 进程。"
    else
        echo "发现匹配进程: $PIDS，正在终止..."
        pkill -f "python -m web.server"
    fi
else
    PID=$(cat .web.pid)
    if ps -p $PID > /dev/null; then
        echo "正在停止进程 $PID..."
        kill $PID
        # 等待进程彻底退出
        sleep 1
        rm .web.pid
        echo "服务已停止。"
    else
        echo "进程 $PID 已不存在，清理残留 PID 文件。"
        rm .web.pid
    fi
fi

# 清理可能残留的 Vite 进程 (仅开发模式)
pkill -f "node.*/bin/vite" 2>/dev/null