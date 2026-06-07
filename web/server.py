"""
FastAPI 服务器入口
- 开发模式：前端 Vite 跑在 :5178，API 在 :3001
- 生产模式：FastAPI 同时服务 API + 前端静态文件（dist/）

启动：
  cd /path/to/QuanTrading
  python -m web.server              # 默认端口 3001
  python -m web.server --port 3001  # 指定端口
"""
import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler

# 确保项目根目录在 sys.path
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── 全局文件日志（server.log）────────────────────────────────
# 在 import 任何业务模块之前配置，确保所有 logger 都能落盘。
_LOG_DIR  = os.path.join(ROOT, 'logs')
_LOG_FILE = os.path.join(_LOG_DIR, 'server.log')
os.makedirs(_LOG_DIR, exist_ok=True)

_file_handler = TimedRotatingFileHandler(
    _LOG_FILE, when='midnight', backupCount=30, encoding='utf-8'
)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)-5s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
_file_handler.setLevel(logging.DEBUG)

# root logger 兜底：propagate=True 的模块都会写入 server.log
logging.root.setLevel(logging.DEBUG)
logging.root.addHandler(_file_handler)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from web.api.portfolio  import router as portfolio_router
from web.api.factors    import router as factors_router
from web.api.backtest   import router as backtest_router
from web.api.scheduler  import router as scheduler_router
from web.api.config     import router as config_router
from web.api.optimizer  import router as optimizer_router
from web.api.watchlist  import router as watchlist_router
from web.api.screener   import router as screener_router
from web.api.comparison  import router as comparison_router
from web.api.ai_tracker  import router as ai_tracker_router
from web.api.single_backtest import router as single_backtest_router
from web.api.astock import router as astock_router
from web.api.risk import router as risk_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 从 DB 加载最新配置
    try:
        import config
        config.reload()
        print("  [配置] 已从 DB 加载")
    except Exception as e:
        print(f"  [配置] 加载失败，使用默认值：{e}")

    # 启动调度器
    from web.services.scheduler_svc import get_scheduler
    svc = get_scheduler()
    try:
        svc.start()
        print("  [调度器] 已启动")
    except Exception as e:
        print(f"  [调度器] 启动失败（数据库未连接？）：{e}")
    yield
    svc.stop()
    print("  [调度器] 已停止")


app = FastAPI(
    title='QuanTrading Web API',
    description='量化交易平台 Web 接口',
    version='1.0.0',
    lifespan=lifespan,
)

# CORS（开发时允许 Vite 前端跨域调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5178', 'http://localhost:3001'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# API 路由
app.include_router(portfolio_router)
app.include_router(factors_router)
app.include_router(backtest_router)
app.include_router(scheduler_router)
app.include_router(config_router)
app.include_router(optimizer_router)
app.include_router(watchlist_router)
app.include_router(screener_router)
app.include_router(comparison_router)
app.include_router(ai_tracker_router)
app.include_router(single_backtest_router)
app.include_router(astock_router)
app.include_router(risk_router)


@app.get('/api/health')
def health():
    return {'status': 'ok'}


# 生产环境：挂载前端静态文件
_dist = os.path.join(os.path.dirname(__file__), 'frontend', 'dist')
if os.path.isdir(_dist):
    app.mount('/assets', StaticFiles(directory=os.path.join(_dist, 'assets')), name='assets')

    @app.get('/{full_path:path}', include_in_schema=False)
    def spa_fallback(full_path: str):
        """dist 根目录下的静态文件直接返回，其余返回 index.html（SPA 路由）"""
        candidate = os.path.join(_dist, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_dist, 'index.html'))


if __name__ == '__main__':
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=3001)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()

    print(f"\n  QuanTrading Web UI")
    print(f"  API 文档：http://{args.host}:{args.port}/docs")
    print(f"  前端地址：http://{args.host}:{args.port}\n")

    log_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                'format': '%(asctime)s %(levelname)s %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
            'access': {
                'format': '%(asctime)s %(levelname)s %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
        },
        'handlers': {
            'default': {'class': 'logging.StreamHandler',               'formatter': 'default', 'stream': 'ext://sys.stderr'},
            'access':  {'class': 'logging.StreamHandler',               'formatter': 'access',  'stream': 'ext://sys.stdout'},
            'file':    {'class': 'logging.handlers.TimedRotatingFileHandler',
                        'formatter': 'default', 'filename': _LOG_FILE,
                        'when': 'midnight', 'backupCount': 30, 'encoding': 'utf-8'},
        },
        'loggers': {
            'uvicorn':        {'handlers': ['default', 'file'], 'level': 'INFO', 'propagate': False},
            'uvicorn.error':  {'handlers': ['default', 'file'], 'level': 'INFO', 'propagate': False},
            'uvicorn.access': {'handlers': ['access',  'file'], 'level': 'INFO', 'propagate': False},
        },
    }

    uvicorn.run(
        'web.server:app',
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=log_config,
    )
