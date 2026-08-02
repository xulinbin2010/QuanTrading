#!/bin/bash
# 启动 QuanTrading Web UI
# 用法：./start_web.sh [--dev]

set -u
cd "$(dirname "$0")"

UV_BIN="${UV_BIN:-uv}"
if ! command -v "${UV_BIN}" >/dev/null 2>&1; then
    echo "未找到 uv，请先安装 uv 或设置 UV_BIN"
    exit 1
fi

is_running() {
    kill -0 "$1" 2>/dev/null
}

port_pids() {
    lsof -ti:"$1" 2>/dev/null || true
}

# 检查是否已经在运行。使用 kill -0，不依赖受限环境中的 ps。
if [ -f .web.pid ]; then
    OLD_PID=$(cat .web.pid)
    if [ -n "${OLD_PID}" ] && is_running "${OLD_PID}"; then
        echo "服务已在运行中 (PID: ${OLD_PID})，请先执行 ./stop_web.sh"
        exit 1
    fi
    rm -f .web.pid
fi

# PID 文件丢失时也不要重复占用项目自己的端口。
for PORT in 3001; do
    PIDS=$(port_pids "${PORT}")
    if [ -n "${PIDS}" ]; then
        echo "端口 ${PORT} 已被占用 (PID: ${PIDS})，请先执行 ./stop_web.sh 或检查占用进程"
        exit 1
    fi
done
if [ "${1:-}" = "--dev" ]; then
    PIDS=$(port_pids 5178)
    if [ -n "${PIDS}" ]; then
        echo "端口 5178 已被占用 (PID: ${PIDS})，请先执行 ./stop_web.sh 或检查占用进程"
        exit 1
    fi
fi

# 自我脱离终端：首次调用时用 nohup 重启自身后退出，使服务真正后台常驻。
if [ -z "${__WEB_DAEMONIZED:-}" ]; then
    export __WEB_DAEMONIZED=1
    nohup "$0" "$@" > web_session.log 2>&1 < /dev/null &
    DAEMON_PID=$!
    disown

    READY=0
    for _ in $(seq 1 20); do
        if curl -sf http://127.0.0.1:3001/api/health >/dev/null 2>&1; then
            READY=1
            break
        fi
        if ! is_running "${DAEMON_PID}"; then
            break
        fi
        sleep 1
    done

    if [ "${READY}" -eq 1 ]; then
        echo "服务已后台启动 (wrapper PID: ${DAEMON_PID})，日志：web_session.log"
        if [ "${1:-}" = "--dev" ]; then
            echo "  前端 UI:  http://localhost:5178  (代理 /api → 3001)"
            echo "  后端 API: http://127.0.0.1:3001"
        else
            echo "  地址：http://127.0.0.1:3001"
        fi
        echo "停止：./stop_web.sh"
        exit 0
    fi

    echo "服务启动失败，健康检查未通过；请查看 web_session.log（开发模式另看 server.log）"
    exit 1
fi

RUNNER=("${UV_BIN}" run --locked --no-dev python)

if [ "${1:-}" = "--dev" ]; then
  # 开发模式：FastAPI 热重载 + Vite dev server（两个进程）
  echo "开发模式启动..."
  echo "  前端 UI:  http://localhost:5178  (代理 /api → 3001)"
  echo ""
  "${RUNNER[@]}" -m web.server --reload > server.log 2>&1 &
  BACKEND_PID=$!
  echo "${BACKEND_PID}" > .web.pid
  echo "  后端 API: http://127.0.0.1:3001 (PID: ${BACKEND_PID})"

  echo -n "  等待后端就绪..."
  BACKEND_READY=0
  for _ in $(seq 1 20); do
    sleep 1
    if curl -sf http://127.0.0.1:3001/api/health >/dev/null 2>&1; then
      BACKEND_READY=1
      echo " 就绪 ✓"
      break
    fi
    echo -n "."
  done
  echo ""

  if [ "${BACKEND_READY}" -ne 1 ]; then
    echo "后端未就绪，停止本次启动；请查看 server.log"
    kill "${BACKEND_PID}" 2>/dev/null || true
    rm -f .web.pid
    exit 1
  fi

  cd web/frontend && npm run dev
else
  echo "生产模式启动..."
  "${RUNNER[@]}" -m web.server &
  BACKEND_PID=$!
  echo "${BACKEND_PID}" > .web.pid
  echo "  地址：http://127.0.0.1:3001 (PID: ${BACKEND_PID})"
  wait "${BACKEND_PID}"
fi
