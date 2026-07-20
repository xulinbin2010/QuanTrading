"""风险温度计 API：减仓预警信号（VIX 期限结构 + 组合相关性）"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from web.services import risk_svc

router = APIRouter(prefix='/api/risk', tags=['risk'])


@router.get('/thermometer')
def thermometer(force: bool = Query(False)):
    """风险温度计：合成 VIX 期限结构 + 组合相关性，返回温度等级与子信号（默认 30 分钟缓存）"""
    try:
        return risk_svc.get_thermometer(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/dashboard')
def dashboard(force: bool = Query(False)):
    """统一风险驾驶舱：市场、组合与杠杆三层风险及来源化行动建议。"""
    try:
        return risk_svc.get_dashboard(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
