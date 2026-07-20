"""情报中心路由：持仓新闻事件卡（读缓存秒回 + 后台异步刷新）。"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix='/api/intel', tags=['intel'])


@router.get('/events')
def events_cached():
    """事件卡缓存 + 刷新状态（无 LLM，秒回；未生成过 events 为空）。"""
    from web.services import intel_svc
    data = intel_svc.get_cached_news_events() or {}
    return {**data, **intel_svc.news_events_status()}


@router.post('/events/refresh')
def events_refresh():
    """后台线程刷新事件卡（拉新闻 + 一次 Claude 调用，约 1-3 分钟）。前端轮询 GET /events。"""
    from web.services import intel_svc
    started = intel_svc.refresh_news_events_async()
    if not started:
        raise HTTPException(status_code=409, detail='已有刷新任务在跑，请稍候')
    return {'running': True}
