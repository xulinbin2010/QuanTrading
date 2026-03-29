"""策略回测 API 路由"""
from fastapi import APIRouter, HTTPException
from web.models import BacktestRequest
from web.services import backtest_svc

router = APIRouter(prefix='/api/backtest', tags=['backtest'])


@router.post('/run')
def run(req: BacktestRequest):
    """提交回测任务，立即返回 task_id，后台异步执行"""
    params = {
        'period':          req.period,
        'top':             req.top_n,
        'start':           req.start,
        'end':             req.end,
        'universe':        req.universe,
        'daily':           req.daily,
        'min_cap_b':       req.min_cap_b,
        'max_cap_b':       req.max_cap_b,
        'deny_industries': req.deny_industries,
        'factors':         req.factors,
        'factor_params':   req.factor_params,
    }
    task_id = backtest_svc.submit_backtest(params)
    return {'task_id': task_id}


@router.get('/status/{task_id}')
def status(task_id: str):
    s = backtest_svc.get_status(task_id)
    if s is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return s


@router.get('/result/{task_id}')
def result(task_id: str):
    r = backtest_svc.get_result(task_id)
    if r is None:
        s = backtest_svc.get_status(task_id)
        if s is None:
            raise HTTPException(status_code=404, detail='任务不存在')
        if s['status'] == 'failed':
            raise HTTPException(status_code=500, detail=s.get('error', '回测失败'))
        raise HTTPException(status_code=202, detail='回测尚未完成')
    return r


@router.get('/history')
def history():
    return backtest_svc.get_history()
