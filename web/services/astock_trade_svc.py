"""A 股半自动交易服务：系统出目标持仓 → diff 出调仓清单 → 人工下单 → 回填成交。

不接券商、不自动下单。核心是把回测里「每周一按 composite 选前 N、整百股」的
rebalance 逻辑搬到实盘当天：用最新一次动能扫描结果选股，与本地台账 diff 出买卖单。

复用：
  - astock_momentum_svc.scan_momentum  当日 composite 扫描（带 close/group/name）
  - astock_backtest_svc._STRATEGIES     选股函数（sector_rotation 等，与回测同源）
台账：core.astock_trade_db
"""
from __future__ import annotations

from datetime import date

from core import astock_trade_db as db

# A 股按手交易：1 手 = 100 股
LOT = 100


def _scan_rows(mode: str, force: bool = False) -> tuple[list[dict], dict]:
    """取一次动能扫描，返回 (rows, meta)。rows 每行含 symbol/name/close/group/composite。"""
    from web.services.astock_momentum_svc import scan_momentum
    res = scan_momentum(mode=mode, force=force)
    rows = res.get('rows') or []
    meta = {
        'scanning': res.get('scanning', False),
        'last_updated': res.get('last_updated'),
        'mode': res.get('mode', mode),
    }
    return rows, meta


def _row_map(rows: list[dict]) -> dict[str, dict]:
    return {r['symbol']: r for r in rows}


def get_settings() -> dict:
    return db.get_settings()


def update_settings(patch: dict) -> dict:
    return db.update_settings(patch)


# ── 持仓台账（手动录入 / 浮盈）──────────────────────────

def set_position(code: str, qty: int, avg_cost: float, name: str | None = None) -> dict:
    """手动录入/修改一只持仓（初始化台账用：把现有持仓告诉系统）。"""
    code = str(code).strip()
    if not code:
        raise ValueError('缺少股票代码')
    if name is None:
        name = _lookup_name(code)
    db.upsert_position(code, name, int(qty), float(avg_cost))
    return {'ok': True}


def delete_position(code: str) -> dict:
    db.remove_position(str(code).strip())
    return {'ok': True}


def get_holdings() -> dict:
    """当前持仓 + 最新价（用扫描缓存，不强制刷新）+ 浮盈。"""
    settings = db.get_settings()
    rows, meta = _scan_rows(settings['mode'], force=False)
    rmap = _row_map(rows)
    positions = db.get_positions()
    out = []
    total_cost = 0.0
    total_mv = 0.0
    have_all_px = True
    for p in positions:
        cur = rmap.get(p['code'], {}).get('close')
        cost = p['avg_cost'] * p['qty']
        total_cost += cost
        if cur is not None:
            mv = cur * p['qty']
            total_mv += mv
            pnl = mv - cost
            pnl_pct = (cur / p['avg_cost'] - 1) if p['avg_cost'] else None
        else:
            have_all_px = False
            mv = pnl = pnl_pct = None
        out.append({
            'code': p['code'], 'name': p['name'], 'qty': p['qty'],
            'avg_cost': round(p['avg_cost'], 3),
            'cur_price': cur, 'market_value': round(mv, 2) if mv is not None else None,
            'cost': round(cost, 2),
            'pnl': round(pnl, 2) if pnl is not None else None,
            'pnl_pct': round(pnl_pct, 4) if pnl_pct is not None else None,
            'open_date': p['open_date'],
        })
    total_pnl = (total_mv - total_cost) if have_all_px else None
    return {
        'positions': out,
        'capital': settings['capital'],
        'total_cost': round(total_cost, 2),
        'total_market_value': round(total_mv, 2) if have_all_px else None,
        'total_pnl': round(total_pnl, 2) if total_pnl is not None else None,
        'last_updated': meta['last_updated'],
    }


# ── 生成调仓清单 ──────────────────────────────────────

def generate_plan(force_scan: bool = False) -> dict:
    """按设置里的策略/资金/持仓数，生成本次调仓清单并写入台账（替换当日 pending）。

    返回 { plan_date, targets, orders, holdings_unchanged, scanning, ... }
    """
    from web.services.astock_backtest_svc import _STRATEGIES

    settings = db.get_settings()
    strategy = settings['strategy']
    top_n = settings['top_n']
    capital = settings['capital']
    mode = settings['mode']

    selector = _STRATEGIES.get(strategy)
    if selector is None:
        raise ValueError(f'未知策略: {strategy}')

    rows, meta = _scan_rows(mode, force=force_scan)
    if not rows:
        # 扫描还在后台跑（首次预热），让前端轮询重试
        return {'scanning': True, 'plan_date': date.today().isoformat(),
                'targets': [], 'orders': [], 'holdings_unchanged': [],
                'message': '动能扫描进行中，请稍后重试', 'last_updated': meta['last_updated']}

    rmap = _row_map(rows)
    positions = {p['code']: p for p in db.get_positions()}
    per_pos_budget = capital / max(top_n, 1)

    def _affordable(code: str) -> tuple[int, float | None]:
        """按单仓预算能买的整百股数 + 参考价；买不起或无报价则股数 <= 0。"""
        r = rmap.get(code)
        price = r.get('close') if r else None
        if not price or price <= 0:
            return 0, price
        return int(per_pos_budget / price) // LOT * LOT, price

    # 策略理想目标（与回测同源；sector_rotation 默认 top_sectors=2）
    primary = selector(rows, top_n)
    primary_set = set(primary)
    # 候选序列：理想目标 → 策略更长候选(如 sector_rotation 同板块次龙头) → 全市场 composite 兜底
    extended = selector(rows, max(top_n * 4, top_n + 20))
    allrank = [r['symbol'] for r in sorted(
        rows, key=lambda x: x['composite'] if x.get('composite') is not None else -999, reverse=True)]
    seen: set = set()
    candidates: list[str] = []
    for lst in (primary, extended, allrank):
        for s in lst:
            if s not in seen:
                seen.add(s); candidates.append(s)

    # 贪心凑满 top_n 个「可成交」目标位：已持有的占位；未持有的须买得起 1 手，
    # 买不起/无报价就跳过、用下一个候选替补，避免仓位空着、资金闲置
    chosen: list[str] = []
    chosen_set: set = set()
    for code in candidates:
        if len(chosen) >= top_n:
            break
        if code in positions:
            chosen.append(code); chosen_set.add(code); continue
        qty, _ = _affordable(code)
        if qty <= 0:
            continue
        chosen.append(code); chosen_set.add(code)

    orders: list[dict] = []
    # SELL：持仓中不在最终目标的，全平
    for code, pos in positions.items():
        if code in chosen_set:
            continue
        ref = rmap.get(code, {}).get('close')
        orders.append({
            'code': code, 'name': pos['name'] or rmap.get(code, {}).get('name') or code,
            'side': 'SELL', 'target_qty': pos['qty'], 'ref_price': ref,
            'budget': round(ref * pos['qty'], 2) if ref else None,
            'reason': '掉出目标', 'status': 'pending',
        })
    # BUY：最终目标中尚未持有的（理想目标 + 替补）
    for code in chosen:
        if code in positions:
            continue
        qty, price = _affordable(code)
        orders.append({
            'code': code, 'name': rmap.get(code, {}).get('name') or code,
            'side': 'BUY', 'target_qty': qty,
            'ref_price': round(price, 3), 'budget': round(qty * price, 2),
            'reason': '新进目标' if code in primary_set else '替补入选', 'status': 'pending',
        })
    # 信息行：理想目标里买不起、已被替补掉的（让用户知道为何换了票，不静默）
    for code in primary:
        if code in chosen_set:
            continue
        qty, price = _affordable(code)
        note = (f'预算不足1手(¥{price:.0f}/股)，已替补' if price and price > 0
                else '无有效报价，已替补')
        orders.append({
            'code': code, 'name': rmap.get(code, {}).get('name') or code,
            'side': 'BUY', 'target_qty': 0,
            'ref_price': round(price, 3) if price else None, 'budget': None,
            'reason': note, 'status': 'skipped',
        })

    plan_date = date.today().isoformat()
    db.replace_plan(plan_date, orders)

    # 维持不动的（最终目标 ∩ 持仓）
    unchanged = [{'code': c, 'name': positions[c]['name'], 'qty': positions[c]['qty']}
                 for c in chosen if c in positions]

    targets = [{'code': c, 'name': rmap.get(c, {}).get('name', c),
                'composite': rmap.get(c, {}).get('composite'),
                'group_label': rmap.get(c, {}).get('group_label'),       # 板块(17)
                'subcat_label': rmap.get(c, {}).get('subcat_label'),     # 细分(50+),股票标签
                'close': rmap.get(c, {}).get('close'),
                'substitute': c not in primary_set}
               for c in chosen]

    return {
        'scanning': False,
        'plan_date': plan_date,
        'strategy': strategy, 'top_n': top_n, 'capital': capital,
        'per_pos_budget': round(per_pos_budget, 2),
        'targets': targets,
        'orders': db.get_orders(plan_date=plan_date),
        'holdings_unchanged': unchanged,
        'last_updated': meta['last_updated'],
    }


def get_plan(plan_date: str | None = None) -> dict:
    """读取某次（默认最近一次）调仓清单。"""
    pd_ = plan_date or db.latest_plan_date()
    if not pd_:
        return {'plan_date': None, 'orders': []}
    return {'plan_date': pd_, 'orders': db.get_orders(plan_date=pd_)}


# ── 回填成交 ──────────────────────────────────────────

def confirm_fill(order_id: int, filled_qty: int, filled_price: float) -> dict:
    """回填一条调仓单的实际成交，并据此更新持仓台账。"""
    o = db.get_order(int(order_id))
    if o is None:
        raise ValueError(f'订单 {order_id} 不存在')
    filled_qty = int(filled_qty)
    filled_price = float(filled_price)
    if filled_qty <= 0 or filled_price <= 0:
        raise ValueError('成交数量/价格必须为正')

    code = o['code']
    pos = db.get_position(code)
    if o['side'] == 'BUY':
        if pos:
            new_qty = pos['qty'] + filled_qty
            new_cost = (pos['qty'] * pos['avg_cost'] + filled_qty * filled_price) / new_qty
            db.upsert_position(code, pos['name'] or o['name'], new_qty, new_cost, pos['open_date'])
        else:
            db.upsert_position(code, o['name'], filled_qty, filled_price)
    else:  # SELL
        if pos:
            new_qty = pos['qty'] - filled_qty
            db.upsert_position(code, pos['name'], new_qty, pos['avg_cost'], pos['open_date'])
        # 台账里没有该持仓的 SELL 回填：忽略持仓更新，仅记录订单

    db.update_order(int(order_id), filled_qty, filled_price, 'filled')
    return {'ok': True, 'order': db.get_order(int(order_id))}


def update_order_status(order_id: int, status: str) -> dict:
    """手动改订单状态（skipped=放弃执行 / canceled=取消 / pending=恢复）。"""
    if status not in ('pending', 'skipped', 'canceled'):
        raise ValueError(f'非法状态: {status}')
    o = db.get_order(int(order_id))
    if o is None:
        raise ValueError(f'订单 {order_id} 不存在')
    db.update_order(int(order_id), o['filled_qty'], o['filled_price'], status)
    return {'ok': True}


def _lookup_name(code: str) -> str:
    """补全股票名称：先查最近扫描缓存，再退回代码本身。"""
    try:
        rows, _ = _scan_rows(db.get_settings()['mode'], force=False)
        for r in rows:
            if r['symbol'] == code:
                return r.get('name') or code
    except Exception:
        pass
    return code
