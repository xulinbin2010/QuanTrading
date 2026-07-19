"""韩国 / 美国杠杆压力监控 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from web.services import leverage_monitor_svc

router = APIRouter(prefix="/api/leverage", tags=["leverage-monitor"])


@router.get("/dashboard")
def dashboard(force: bool = Query(False)):
    """杠杆产品行情、跟踪偏差和官方融资余额（默认 15 分钟缓存）。"""
    try:
        return leverage_monitor_svc.get_dashboard(force=force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
