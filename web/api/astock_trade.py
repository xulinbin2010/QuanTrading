"""A 股半自动交易 API（信号 + 人工下单，不接券商）。

独立于美股 portfolio 路由。前缀 /api/astock/trade。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter(prefix='/api/astock/trade', tags=['astock_trade'])


@router.get('/settings')
def get_settings():
    from web.services import astock_trade_svc as svc
    return svc.get_settings()


@router.put('/settings')
def put_settings(data: dict = Body(...)):
    from web.services import astock_trade_svc as svc
    try:
        return svc.update_settings(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/holdings')
def holdings():
    from web.services import astock_trade_svc as svc
    try:
        return svc.get_holdings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/position')
def set_position(data: dict = Body(...)):
    """手动录入/修改持仓。body: {code, qty, avg_cost, name?}"""
    from web.services import astock_trade_svc as svc
    try:
        return svc.set_position(
            code=data['code'], qty=int(data['qty']),
            avg_cost=float(data['avg_cost']), name=data.get('name'),
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f'缺少字段: {e}')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/position/{code}')
def delete_position(code: str):
    from web.services import astock_trade_svc as svc
    return svc.delete_position(code)


@router.post('/plan')
def gen_plan(data: dict = Body(default={})):
    """生成本次调仓清单（与本地台账 diff）。body: {force_scan?}"""
    from web.services import astock_trade_svc as svc
    try:
        return svc.generate_plan(force_scan=bool(data.get('force_scan', False)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/plan')
def get_plan(plan_date: str | None = Query(None)):
    from web.services import astock_trade_svc as svc
    return svc.get_plan(plan_date)


@router.post('/fill')
def confirm_fill(data: dict = Body(...)):
    """回填成交。body: {order_id, filled_qty, filled_price}"""
    from web.services import astock_trade_svc as svc
    try:
        return svc.confirm_fill(
            order_id=int(data['order_id']),
            filled_qty=int(data['filled_qty']),
            filled_price=float(data['filled_price']),
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f'缺少字段: {e}')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/order/{order_id}/status')
def set_order_status(order_id: int, data: dict = Body(...)):
    """改订单状态：skipped(放弃) / canceled(取消) / pending(恢复)。"""
    from web.services import astock_trade_svc as svc
    try:
        return svc.update_order_status(order_id, str(data.get('status', '')))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
