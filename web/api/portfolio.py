"""持仓总览 API 路由"""
from fastapi import APIRouter, HTTPException, Query
from web.services import portfolio_svc

router = APIRouter(prefix='/api/portfolio', tags=['portfolio'])


@router.get('/ib-status')
def ib_status():
    return portfolio_svc.get_ib_status()


@router.get('/ib-debug')
def ib_debug():
    """返回详细连接诊断信息，方便排查账号切换问题"""
    import config
    info = {
        'config_port': config.IB_PORT,
        'config_host': config.IB_HOST,
        'config_client_id': config.IB_CLIENT_ID,
        'is_live_port': config.IB_PORT == 4001,
    }
    # 主动尝试连接，并捕获具体错误
    try:
        portfolio_svc._ensure_connected()
    except Exception as e:
        info['connect_error'] = str(e)
    try:
        svc = portfolio_svc
        ib = svc._ib
        conn = svc._conn
        info['conn_exists'] = conn is not None
        info['ib_exists'] = ib is not None
        info['last_connect_error'] = getattr(svc, '_last_connect_error', None)
        if ib:
            info['is_connected'] = ib.isConnected()
            try:
                info['managed_accounts'] = list(ib.managedAccounts())
            except Exception as e:
                info['managed_accounts_error'] = str(e)
            try:
                items = portfolio_svc._run_ib_sync(ib.portfolio)
                info['portfolio_count'] = len(items)
                info['portfolio_items'] = [
                    {'symbol': i.contract.symbol, 'qty': i.position, 'value': i.marketValue}
                    for i in items
                ]
            except Exception as e:
                info['portfolio_error'] = str(e)
            try:
                pos_items = portfolio_svc._run_ib_sync(ib.positions)
                info['positions_count'] = len(pos_items)
                info['positions_items'] = [
                    {'account': p.account, 'symbol': p.contract.symbol, 'qty': p.position, 'avg_cost': p.avgCost}
                    for p in pos_items
                ]
            except Exception as e:
                info['positions_error'] = str(e)
        else:
            info['is_connected'] = False
    except Exception as e:
        info['error'] = str(e)
    return info


@router.get('/balance')
def balance():
    try:
        return portfolio_svc.get_balance()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get('/positions')
def positions(refresh: bool = Query(False), account: str = Query(None)):
    try:
        return portfolio_svc.get_positions(force_refresh=refresh, account=account or None)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get('/orders')
def orders(symbol: str = Query(None), limit: int = Query(50, le=500)):
    return portfolio_svc.get_orders(symbol=symbol, limit=limit)


@router.get('/account-history')
def account_history(limit: int = Query(90, le=500)):
    return portfolio_svc.get_account_history(limit=limit)


@router.get('/performance')
def performance(days: int = Query(30, ge=7, le=730)):
    from web.services import performance_svc
    try:
        return performance_svc.get_performance(days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/signals')
def signals(universe: str = Query('sp500')):
    try:
        return portfolio_svc.get_signals(universe=universe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
