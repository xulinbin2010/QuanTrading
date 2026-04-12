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
                info['portfolio_count'] = len(list(ib.portfolio()))
                info['portfolio_items'] = [
                    {'symbol': i.contract.symbol, 'qty': i.position, 'value': i.marketValue}
                    for i in ib.portfolio()
                ]
            except Exception as e:
                info['portfolio_error'] = str(e)
            try:
                info['positions_count'] = len(list(ib.positions()))
                info['positions_items'] = [
                    {'account': p.account, 'symbol': p.contract.symbol, 'qty': p.position, 'avg_cost': p.avgCost}
                    for p in ib.positions()
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


@router.get('/price-test')
def price_test():
    """诊断：直接调用 reqTickers 看 IB 返回的原始价格"""
    import math
    from web.services.portfolio_svc import _ensure_connected, _ib
    _ensure_connected()
    result = {
        'portfolio_path': False,
        'positions_path': False,
        'portfolio_items': [],
        'tickers': [],
        'error': None,
    }
    try:
        svc = __import__('web.services.portfolio_svc', fromlist=['_ib'])
        ib = svc._ib
        if not ib or not ib.isConnected():
            result['error'] = 'IB not connected'
            return result

        portfolio_items = list(ib.portfolio())
        if portfolio_items:
            result['portfolio_path'] = True
            for item in portfolio_items:
                if float(item.position) == 0:
                    continue
                result['portfolio_items'].append({
                    'symbol': item.contract.symbol,
                    'sec_type': item.contract.secType,
                    'qty': item.position,
                    'avg_cost': item.averageCost,
                    'market_price': item.marketPrice,
                    'market_value': item.marketValue,
                    'unrealized_pnl': item.unrealizedPNL,
                })
        else:
            result['positions_path'] = True
            raw_list = [p for p in ib.positions() if float(p.position) != 0]
            contracts = [p.contract for p in raw_list]
            from web.services.portfolio_svc import _run_ib_sync
            try:
                qualified = _run_ib_sync(ib.qualifyContracts, *contracts)
                result['qualified_count'] = len(qualified)
            except Exception as e:
                qualified = contracts
                result['qualify_error'] = str(e)

            # 股票和期权统一用 reqHistoricalData
            import math as _m
            for c in qualified:
                sec_type = getattr(c, 'secType', 'STK')
                show_modes = ['TRADES', 'MIDPOINT', 'BID_ASK'] if sec_type == 'OPT' else ['TRADES']
                found = False
                for what in show_modes:
                    try:
                        bars = _run_ib_sync(
                            ib.reqHistoricalData, c,
                            endDateTime='', durationStr='5 D',
                            barSizeSetting='1 day', whatToShow=what,
                            useRTH=True, formatDate=1,
                        )
                        if bars:
                            raw = bars[-1].close
                            if raw is not None and not _m.isnan(float(raw)) and float(raw) > 0:
                                result['tickers'].append({
                                    'symbol': c.symbol, 'conId': c.conId,
                                    'sec_type': sec_type,
                                    'source': f'reqHistoricalData/{what}',
                                    'close': float(raw),
                                })
                                found = True
                                break
                    except Exception as e:
                        pass
                if not found:
                    result['tickers'].append({'symbol': c.symbol, 'sec_type': sec_type, 'no_price': True})
    except Exception as e:
        result['error'] = str(e)
    return result


@router.get('/signals')
def signals(universe: str = Query('sp500')):
    try:
        return portfolio_svc.get_signals(universe=universe)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
