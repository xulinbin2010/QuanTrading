"""社区热度 API（Reddit / StockTwits 异动榜）"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/social', tags=['social'])


@router.get('/buzz')
def buzz(refresh: bool = Query(False)):
    """社区热度榜。默认读缓存（调度任务每日更新）；refresh=true 现场采集一轮（约 40-90 秒）。"""
    try:
        from web.services.social_svc import collect_now, get_cached_board, build_board
        if refresh:
            collect_now()
            return build_board()
        cached = get_cached_board()
        if cached is not None:
            return cached
        # 无缓存（首次使用且调度未开）：现场采一轮
        collect_now()
        return build_board()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
