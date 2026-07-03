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

# 注意：不要用 pkill -f "node.*/bin/vite"，会误杀其他项目（如 opticx）的 vite。
# Vite(5178) 由下面的「按端口精准强杀」处理，只动 QuanTrading 自己的端口。

# 兜底：按端口强杀（防止 PID 文件丢失或进程名匹配失败后端口仍被占用）
for PORT in 3001 5178; do
    PIDS=$(lsof -ti:$PORT 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "端口 $PORT 仍被占用 (PID: $PIDS)，强制释放..."
        kill -9 $PIDS 2>/dev/null
    fi
done

# 兜底2：清理仍持有「调度锁」的残留进程。
# --reload 残留的孤儿 worker 会丢掉 3001 监听(躲过端口清理)、命令行也不含 "web.server"
# (是 multiprocessing.spawn 形态，pkill 匹配不到)，但只要它还在跑调度器，就必然 flock 着
# data/.scheduler.lock —— 按这个精准揪，且不会误伤手动跑的 auto_trader.py 等(它们不持此锁)。
LOCKF="$(pwd)/data/.scheduler.lock"
LOCKPIDS=$(lsof -t "$LOCKF" 2>/dev/null)
if [ -n "$LOCKPIDS" ]; then
    echo "清理仍持有调度锁的残留进程 (PID: $LOCKPIDS)..."
    kill -9 $LOCKPIDS 2>/dev/null
fi