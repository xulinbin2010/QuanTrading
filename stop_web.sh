#!/bin/bash
# 停止 QuanTrading Web UI

cd "$(dirname "$0")"

if [ -f .web.pid ]; then
    PID=$(cat .web.pid)
    if kill -0 "${PID}" 2>/dev/null; then
        echo "正在停止进程 ${PID}..."
        kill "${PID}" 2>/dev/null || true
        sleep 1
    else
        echo "进程 ${PID} 已不存在，清理残留 PID 文件。"
    fi
    rm -f .web.pid
else
    echo "未找到 .web.pid 文件，改按项目端口清理..."
fi

# 按项目端口精准清理，避免依赖具体的 Python 命令行文本，也不误杀其他项目。
for PORT in 3001 5178; do
    PIDS=$(lsof -ti:$PORT 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "释放端口 $PORT (PID: $PIDS)..."
        kill $PIDS 2>/dev/null || true
        sleep 1
        REMAINING=$(lsof -ti:$PORT 2>/dev/null)
        if [ -n "$REMAINING" ]; then
            kill -9 $REMAINING 2>/dev/null || true
        fi
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
