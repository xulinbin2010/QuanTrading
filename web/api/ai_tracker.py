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


@router.get('/earnings-compare')
def earnings_compare(symbols: str = Query(..., description='逗号分隔,最多3只,如 MU,LITE,MRVL'),
                     force: bool = Query(False)):
    """最多 3 只 AI 标的财报横向对比:快照(YoY增速/估值/市值)+ 最近5季营收/净利/EPS(24h缓存)"""
    try:
        from web.services.ai_momentum_svc import get_earnings_compare
        syms = [s for s in symbols.split(',') if s.strip()]
        return get_earnings_compare(syms, force=force)
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


import threading
import time


_INDEX_CACHE: tuple[float, dict] | None = None
_INDEX_CACHE_TTL_SECONDS = 6 * 60 * 60
_INDEX_LOCK = threading.Lock()


def _index_sets(force: bool = False) -> dict:
    """S&P500 / Nasdaq100 成分；6 小时 TTL，避免一次 fallback 被锁到进程重启。"""
    global _INDEX_CACHE
    now = time.time()
    if not force and _INDEX_CACHE and now - _INDEX_CACHE[0] < _INDEX_CACHE_TTL_SECONDS:
        return _INDEX_CACHE[1]
    with _INDEX_LOCK:
        now = time.time()
        if not force and _INDEX_CACHE and now - _INDEX_CACHE[0] < _INDEX_CACHE_TTL_SECONDS:
            return _INDEX_CACHE[1]
        from core.universe import (
            get_sp500_tickers,
            get_nasdaq100_tickers,
            get_nasdaq100_source_meta,
        )
        result = {
            'sp500': sorted(set(get_sp500_tickers())),
            'ndx': sorted(set(get_nasdaq100_tickers())),
            'ndx_meta': get_nasdaq100_source_meta(),
        }
        _INDEX_CACHE = (now, result)
        return result


@router.get('/index-membership')
def index_membership(force: bool = Query(False)):
    """产业图谱用：标记每只 AI 股是否属于 S&P500 / Nasdaq100 成分。"""
    return _index_sets(force=force)


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


# 注意：路径不能用 /universe/{symbol}/...，否则会被 /universe/{group}/{symbol} 抢匹配
@router.post('/trade-priority/{symbol}')
def set_symbol_trade_priority(symbol: str, enabled: bool = Body(..., embed=True)):
    """切换某只是否纳入实盘 AI 优先池（True=实盘优先 / False=仅研究观察）。"""
    from web.services.ai_tracker_svc import set_trade_priority
    return set_trade_priority(symbol, enabled)


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
    """扫描 sp500+ndx+russell2000（$2B–$500B），自动发现 AI 相关标的，加入待审核队列"""
    try:
        from web.services.ai_tracker_svc import auto_discover
        return {'suggestions': auto_discover(limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/retire-suggestions')
def retire_suggestions(force: bool = Query(False), cached_only: bool = Query(False)):
    """汰旧建议：近 2 个月持续弱势的池内成员（缓存 24h，force=true 现算）。
    cached_only=true 只读缓存不现算（供页面自动加载，无缓存时返回 no_cache 标记）。
    只出建议，移出走 DELETE /universe/{symbol}（人工确认），「保留」60 天内不再提醒。"""
    try:
        from web.services.ai_tracker_svc import get_cached_retire, suggest_retire
        if not force:
            cached = get_cached_retire()
            if cached is not None:
                return cached
            if cached_only:
                return {'as_of': None, 'suggestions': [], 'no_cache': True}
        return suggest_retire()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/retire-keep')
def retire_keep(symbol: str = Body(..., embed=True)):
    """「保留」某只汰旧建议：60 天内不再提醒"""
    try:
        from web.services.ai_tracker_svc import keep_retire_suggestion
        return keep_retire_suggestion(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

