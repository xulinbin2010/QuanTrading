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


@router.get('/momentum')
def momentum(force: bool = Query(False)):
    """AI 篮子短期动能 + 资金流扫描（30 分钟缓存，force=true 强制刷新）

    返回：
      rows[]     个股动能 + 资金流复合分（按 composite 降序）
      groups[]   子组热力（按 5 日中位 RS 降序）
      basket{}   篮子层面 A/D 线 + 金额加权 OBV（最近 10 天序列）
      top4[]     推荐持仓（默认 Top-4）
      spy{}      SPY 3/5/10 日基准收益
    """
    try:
        from web.services.ai_momentum_svc import scan_momentum
        return scan_momentum(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/universe')
def get_universe():
    from web.services.ai_tracker_svc import load_universe
    return load_universe()


# NOTE: pending 字面路由必须在 /universe/{group}/{symbol} 之前注册，
# 否则会被误匹配为 group="pending"。
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


@router.get('/analyze')
def analyze(symbol: str = Query(...)):
    """分析单只股票，返回推荐分组 + 决策依据（供管理股票池手动加入用）"""
    try:
        from web.services.ai_tracker_svc import analyze_symbol
        return analyze_symbol(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/discover')
def discover(limit: int = Query(20)):
    """扫描 sp500+ndx，自动发现 AI 相关标的，加入待审核队列"""
    try:
        from web.services.ai_tracker_svc import auto_discover
        return {'suggestions': auto_discover(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


