"""因子看板 API 路由"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from web.services import factor_svc
from core.universe import get_tickers

router = APIRouter(prefix='/api/factors', tags=['factors'])


@router.get('/universes')
def universes():
    return ['sp500', 'nasdaq100', 'russell2000']


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


class FactorUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    params: Optional[dict] = None


@router.put('/registry/{factor_key}')
def update_factor(factor_key: str, body: FactorUpdateRequest):
    """更新因子开关或参数"""
    ok = factor_svc.update_factor_config(
        key=factor_key,
        enabled=body.enabled,
        params=body.params,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=f"更新因子 {factor_key} 失败，请检查 key 是否合法")
    return {'status': 'ok', 'key': factor_key}


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


@router.delete('/cache')
def clear_cache(universe: str = Query(None)):
    """手动清除因子缓存"""
    factor_svc.invalidate_cache(universe)
    return {'status': 'ok'}
