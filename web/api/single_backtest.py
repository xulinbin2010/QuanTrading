"""单股回测 API（EMA21 补仓策略）"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Body, Query

router = APIRouter(prefix='/api/single-bt', tags=['single-backtest'])


@router.post('/run')
def run(params: dict = Body(...)):
    """提交一次单股回测，返回 task_id。

    必填：symbol / start / end
    选填：initial_cash / base_pct / add_size_mult / max_adds / touch_tol /
         sell_atr_mult / stop_ema_period / ema_fast
    """
    required = ('symbol', 'start', 'end')
    for k in required:
        if not params.get(k):
            raise HTTPException(status_code=400, detail=f'缺少必填参数: {k}')
    from web.services.single_backtest_svc import submit
    task_id = submit(params)
    return {'task_id': task_id}


@router.get('/status/{task_id}')
def status(task_id: str):
    from web.services.single_backtest_svc import get_status
    s = get_status(task_id)
    if s is None:
        raise HTTPException(status_code=404, detail='task not found')
    return s


@router.get('/result/{task_id}')
def result(task_id: str):
    from web.services.single_backtest_svc import get_result, get_status
    r = get_result(task_id)
    if r is None:
        st = get_status(task_id)
        if st is None:
            raise HTTPException(status_code=404, detail='task not found')
        raise HTTPException(status_code=425, detail=f'task not ready: status={st["status"]}')
    return r


@router.get('/history')
def history(limit: int = Query(30)):
    from web.services.single_backtest_svc import get_history
    return {'items': get_history(limit=limit)}
