"""因子看板 API 路由"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from web.services import factor_svc
from core.universe import get_tickers

router = APIRouter(prefix='/api/factors', tags=['factors'])


@router.get('/universes')
def universes():
    return ['sp500+ndx', 'ai', 'sp500', 'nasdaq100', 'russell2000']


@router.get('/tickers')
def tickers(universe: str = Query('sp500')):
    try:
        return get_tickers(universe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/registry')
def factor_registry():
    """返回所有注册因子的元数据 + 当前启用状态"""
    try:
        return factor_svc.get_factor_registry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/scan')
def scan(
    universe: str = Query('sp500'),
    top: int = Query(50, le=600),
    force: bool = Query(False),
):
    """全股票池因子扫描（缓存 1 小时）"""
    try:
        return factor_svc.scan_factors(universe=universe, top=top, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/stock/{symbol}')
def stock_detail(symbol: str, days: int = Query(120, le=500)):
    """单股因子详情"""
    try:
        return factor_svc.get_stock_factors(symbol=symbol.upper(), days=days)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/stock/{symbol}/news')
def stock_news(symbol: str):
    """单股新闻 & SEC 公告（8-K/10-K/10-Q），按需加载，2 小时缓存"""
    try:
        from core.stock_news import get_stock_news
        return get_stock_news(symbol.upper())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/insider')
def insider(days: int = Query(None), min_value_k: int = Query(None)):
    """内部人净买入扫描（OpenInsider，带 20 小时缓存）"""
    try:
        return factor_svc.get_insider_data(days=days, min_value_k=min_value_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/earnings')
def earnings(symbols: str = Query(..., description='逗号分隔的股票代码，如 AAPL,NVDA')):
    """批量查询下次财报日期（缓存 12 小时）"""
    try:
        from core.earnings import prefetch_earnings
        sym_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
        result = prefetch_earnings(sym_list)
        return {k: (v.isoformat() if v else None) for k, v in result.items()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/cache')
def clear_cache(universe: str = Query(None)):
    """手动清除因子缓存"""
    factor_svc.invalidate_cache(universe)
    return {'status': 'ok'}
