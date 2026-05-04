"""AI 基建追踪器 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Body

router = APIRouter(prefix='/api/ai', tags=['ai-tracker'])


@router.get('/scan')
def scan(force: bool = Query(False)):
    """扫描 AI 基建全股票池，返回评分结果（4 小时缓存，force=true 强制刷新）"""
    try:
        from web.services.ai_tracker_svc import scan_ai_tracker
        return scan_ai_tracker(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/universe')
def get_universe():
    from web.services.ai_tracker_svc import load_universe
    return load_universe()


@router.post('/universe/{group}/{symbol}')
def add_symbol(group: str, symbol: str):
    try:
        from web.services.ai_tracker_svc import add_symbol_to_universe
        return add_symbol_to_universe(symbol, group)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/universe/{symbol}')
def remove_symbol(symbol: str):
    from web.services.ai_tracker_svc import remove_symbol_from_universe
    return remove_symbol_from_universe(symbol)


@router.post('/universe/pending/approve')
def approve(symbol: str = Body(..., embed=True), group: str = Body(..., embed=True)):
    try:
        from web.services.ai_tracker_svc import approve_pending
        return approve_pending(symbol, group)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/universe/pending/reject')
def reject(symbol: str = Body(..., embed=True)):
    from web.services.ai_tracker_svc import reject_pending
    return reject_pending(symbol)


@router.post('/discover')
def discover(limit: int = Query(20)):
    """扫描 sp500+ndx，自动发现 AI 相关标的，加入待审核队列"""
    try:
        from web.services.ai_tracker_svc import auto_discover
        return {'suggestions': auto_discover(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/revenue/{symbol}')
def update_revenue(
    symbol: str,
    ai_pct: float = Body(..., embed=True),
    note: str = Body('', embed=True),
):
    """更新某只股票的 AI 营收占比（手动维护）"""
    try:
        from web.services.ai_tracker_svc import update_ai_revenue
        return update_ai_revenue(symbol, ai_pct, note)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
