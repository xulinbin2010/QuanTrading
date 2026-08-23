"""A 股动能扫描 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, Body

router = APIRouter(prefix='/api/astock', tags=['astock'])


@router.post('/backtest')
def backtest_submit(data: dict = Body(...)):
    """提交 A 股动能轮动回测(每周一 rebalance,持有 composite 前 N)。
    body: { start_date, end_date, initial_cash?, top_n?, groups? }
    return: { task_id }
    """
    try:
        from web.services.astock_backtest_svc import submit_backtest
        params = {
            'start_date': data['start_date'],
            'end_date':   data['end_date'],
            'initial_cash': float(data.get('initial_cash', 100_000)),
            'top_n':      int(data.get('top_n', 4)),
            'groups':     data.get('groups'),
            'strategy':   data.get('strategy', 'momentum'),
            'rebalance_freq': data.get('rebalance_freq', 'weekly'),
            'apply_costs':    bool(data.get('apply_costs', False)),
            'stop_loss':      data.get('stop_loss', 'none'),
        }
        task_id = submit_backtest(params)
        return {'task_id': task_id, 'status': 'running'}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f'缺少字段: {e}')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/backtest/{task_id}')
def backtest_status(task_id: str):
    """查询回测状态/结果。status: running / completed / failed。"""
    from web.services.astock_backtest_svc import get_task
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f'task {task_id} 不存在')
    return task


@router.get('/momentum')
def momentum(mode: str = Query('theme'), force: bool = Query(False),
             include_watchlist: bool = Query(False)):
    """A 股板块强度 + 个股动能；默认只扫 core，可选纳入 watchlist。"""
    try:
        from web.services.astock_momentum_svc import scan_momentum
        return scan_momentum(mode=mode, force=force,
                             include_watchlist=include_watchlist)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/fundamentals')
def fundamentals(force: bool = Query(False)):
    """当前 A 股主题股票池基本面摘要（估值快照 + 最近报告期）。"""
    try:
        from web.services.astock_fundamental_svc import get_fundamentals
        return get_fundamentals(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/fundamentals/{code}')
def fundamental_detail(
    code: str,
    force: bool = Query(False),
    pe_period: str = Query('3y'),
):
    """单只主题 A 股基本面详情（报告期序列 + 历史 PE）。"""
    try:
        from web.services.astock_fundamental_svc import get_fundamental_detail
        return get_fundamental_detail(code, force=force, pe_period=pe_period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/universe')
def universe():
    """返回完整主题板块定义及 core/watchlist/exclude 元数据。"""
    try:
        from core import astock_universe as au
        data = au.load_themes()
        meta = data.get('stock_meta', {})
        counts = {'core': 0, 'watchlist': 0, 'exclude': 0}
        for code in {str(s).zfill(6) for g in data.get('groups', {}).values()
                     for s in g.get('symbols', [])}:
            role = au.get_stock_meta(code, data)['pool_role']
            counts[role] += 1
        return {
            'themes': data.get('groups', {}),
            'stock_meta': meta,
            'pool_summary': counts,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/stock/{code}')
def stock_detail(code: str, days: int = Query(120, le=500)):
    """单只 A 股 K 线详情（K线+MA10/20+RS），结构对齐美股 /factors/stock。"""
    try:
        from web.services.astock_momentum_svc import get_astock_detail
        return get_astock_detail(code, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/themes')
def update_themes(data: dict = Body(...)):
    """覆盖保存主题板块定义（{groups: {...}}）。"""
    try:
        from core import astock_universe as au
        # 兼容旧客户端只提交 groups 的请求，不能因此丢掉 stock_meta。
        if 'stock_meta' not in data:
            current = au.load_themes()
            if current.get('stock_meta'):
                data = {**data, 'stock_meta': current['stock_meta']}
        au.save_themes(data)
        return {'ok': True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/classify')
def classify(code: str = Query(...)):
    """识别单只 A 股并推荐所属板块（申万三级反查 + 名称关键词）。"""
    try:
        from core import astock_universe as au
        return au.classify_stock(code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/themes/add')
def add_stock(data: dict = Body(...)):
    """把股票加入指定主题板块。body: {code, group}。"""
    try:
        from core import astock_universe as au
        code = str(data.get('code', '')).strip()
        group = str(data.get('group', '')).strip()
        if not code or not group:
            raise HTTPException(status_code=400, detail='缺少 code 或 group')
        res = au.add_theme_stock(code, group)
        if not res.get('ok'):
            raise HTTPException(status_code=400, detail=res.get('error', '添加失败'))
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/themes/remove')
def remove_stock(data: dict = Body(...)):
    """从所有主题板块移除股票。body: {code}。"""
    try:
        from core import astock_universe as au
        code = str(data.get('code', '')).strip()
        if not code:
            raise HTTPException(status_code=400, detail='缺少 code')
        return au.remove_theme_stock(code)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
