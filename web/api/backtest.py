"""策略回测 API 路由"""
from fastapi import APIRouter, HTTPException, Query
from web.models import BacktestRequest, WalkForwardRequest, FactorComboSaveRequest
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
        'strategy':        req.strategy,
        'hard_stop':       req.hard_stop,
        'pos_pct':         req.pos_pct,
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


@router.post('/walk-forward')
def walk_forward(req: WalkForwardRequest):
    """提交 Walk-Forward 验证任务，立即返回 task_id，后台异步执行"""
    params = {
        'train_months': req.train_months,
        'test_months':  req.test_months,
        'total_start':  req.total_start,
        'total_end':    req.total_end,
        'universe':     req.universe,
        'top':          req.top_n,
    }
    task_id = backtest_svc.submit_walk_forward(params)
    return {'task_id': task_id}


@router.get('/combos')
def list_combos():
    """列出所有因子组合（内置预设 + 用户保存的）"""
    return backtest_svc.list_combos()


@router.post('/combos')
def save_combo(req: FactorComboSaveRequest):
    """保存当前因子选择为命名组合"""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail='组合名称不能为空')
    if not req.factors:
        raise HTTPException(status_code=400, detail='至少选择一个因子')
    combo = backtest_svc.save_combo(req.name, req.factors, req.factor_params)
    return combo


@router.delete('/combos/{combo_id}')
def delete_combo(combo_id: str):
    """删除用户组合（内置组合不可删除）"""
    ok = backtest_svc.delete_combo(combo_id)
    if not ok:
        raise HTTPException(status_code=400, detail='内置组合不可删除，或组合不存在')
    return {'status': 'deleted', 'id': combo_id}


@router.get('/vix')
def vix_analysis(
    threshold: float = Query(30, description='VIX触发阈值'),
    start: str = Query('2010-01-01', description='起始日期'),
    end: str = Query(None, description='结束日期，默认今天'),
    symbol: str = Query('SPY', description='目标标的，如SPY/QQQ'),
    mode: str = Query('spike', description='spike=当日触发 / peak=峰值回落触发'),
):
    try:
        return backtest_svc.run_vix_analysis(threshold, start, end, symbol, mode)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
