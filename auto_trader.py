"""
自动交易模块：扫描信号 → 自动下单（模拟盘）

执行时机（日线策略）：
  每天盘前跑一次（北京时间晚 9:00 = 美东早 9:00，开盘前 30 分钟）：
      python auto_trader.py --run

  订单类型自动选择：
    - 盘前（美东 9:30 前）→ OPG 开盘集合竞价单，9:30 自动成交
    - 盘中（美东 9:30-16:00）→ DAY 市价单，立即成交

  可选：提前预览信号（不下单）：
      python auto_trader.py --dry-run

用法：
  python auto_trader.py --dry-run                          # 扫描信号，不下单
  python auto_trader.py --run                              # 正式下单
  python auto_trader.py --run --held NVDA AMD              # 同时监控持仓报警
  python auto_trader.py --run --extra SNDK TSM             # 追加非 S&P500 股票
  python auto_trader.py --dry-run --universe nasdaq100     # 切换股票池
"""
import argparse
import warnings
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

import pandas as pd

from core.connection import IBConnection
from core.account import Account
from core.trading import Trading
from core.database import Database
from core.data_store import DataStore
from core.universe import get_tickers, get_stock_info
from core.insider import get_insider_buys
from strategies.rs_momentum import RSMomentum
import config

warnings.filterwarnings('ignore')

# ══════════════════════════════════════════════════════
#  风控参数（统一定义在 config.py）
# ══════════════════════════════════════════════════════

MAX_POSITIONS           = config.MAX_POSITIONS
POSITION_PCT            = config.POSITION_PCT
CASH_RESERVE_PCT        = config.CASH_RESERVE_PCT
STOP_LOSS_PCT           = config.STOP_LOSS_PCT
MAX_PER_SECTOR          = config.MAX_PER_SECTOR
TRAIL_STOP_ACTIVATE_PCT = config.TRAIL_STOP_ACTIVATE_PCT
TRAIL_STOP_PCT          = config.TRAIL_STOP_PCT
TIME_STOP_DAYS          = config.TIME_STOP_DAYS
TIME_STOP_MIN_RETURN    = config.TIME_STOP_MIN_RETURN

# 仓位计算：5 × 15% = 75% 持仓 + 25% 现金保留
SELL_ON_ALERT    = True   # 量价背离时是否自动卖出（False = 只报警）


def _entry_date(sym: str, db) -> date | None:
    """从 orders 表查该持仓最近一笔已成交 BUY 单的日期。"""
    rows = db.get_orders(symbol=sym, limit=50)
    for r in rows:
        if r[2] == 'BUY' and r[6] is not None:   # action=BUY, filled_price 有值
            dt = r[9]
            if dt is None:
                continue
            return dt.date() if hasattr(dt, 'date') else dt
    return None


def _peak_price(sym: str, avg_cost: float, db) -> float:
    """从 DataStore 计算该持仓自建仓以来的峰值收盘价。
    入场日期从 orders 表查最近一笔 BUY 成交记录；若无记录则以 avg_cost 为峰值。
    """
    from core.data_store import DataStore
    # orders 列：id, symbol, action, order_type, qty, price, filled_price, status, order_id, created_at
    rows = db.get_orders(symbol=sym, limit=50)
    entry_date = None
    for r in rows:
        if r[2] == 'BUY' and r[6] is not None:   # action=BUY, filled_price 有值
            entry_date = r[9]                      # created_at (datetime)
            break
    if entry_date is None:
        return avg_cost

    start = (entry_date.date() if hasattr(entry_date, 'date') else entry_date).strftime('%Y-%m-%d')
    end   = date.today().strftime('%Y-%m-%d')
    data  = DataStore().get([sym], start=start, end=end, auto_update=False)
    df    = data.get(sym)
    if df is None or df.empty:
        return avg_cost
    return max(float(df['close'].max()), avg_cost)

# 现金等价 ETF：占仓时计入现金、不占槽位、跳过止损和信号扫描
CASH_EQUIV = {'SGOV', 'BIL', 'USFR'}

# OPG 限价保护：最多接受高于昨收 1% 的开盘价，超过则放弃该笔
MAX_ENTRY_SLIPPAGE = 0.01

# ══════════════════════════════════════════════════════


def scan_signals(
    held_symbols:     list[str],
    extra:            list[str] = None,
    universe:         str       = 'sp500',
    min_cap_b:        float     = None,    # 最小市值（十亿 USD），如 10
    max_cap_b:        float     = None,    # 最大市值（十亿 USD），如 500
    deny_industries:  list[str] = None,    # 拒绝行业，如 ['Software—Application']
) -> dict:
    """用昨日收盘数据跑 RS 策略，返回买卖信号（可按市值/行业过滤）"""

    # ── 获取股票池 ────────────────────────────────────────────
    print(f"获取股票池（{universe}）...")
    tickers = get_tickers(universe, extra=extra)
    for s in held_symbols:
        s = s.upper()
        if s not in tickers:
            tickers.append(s)

    # ── 通过本地缓存获取数据 ──────────────────────────────────
    dl_start = (date.today() - timedelta(days=400)).strftime('%Y-%m-%d')
    dl_end   = date.today().strftime('%Y-%m-%d')

    all_syms = list(set(tickers + ['SPY']))
    store    = DataStore()
    all_data = store.get(all_syms, start=dl_start, end=dl_end)

    spy_df = all_data.get('SPY')
    if spy_df is None:
        print("SPY 数据获取失败")
        return {'buy': [], 'sell': [], '_prices': {}}
    spy_close = spy_df['close']

    # ── SPY 熔断检查 ──────────────────────────────────────────
    spy_brake = False
    if len(spy_close) > config.SPY_BRAKE_PERIOD:
        spy_20d_ret = spy_close.iloc[-1] / spy_close.iloc[-1 - config.SPY_BRAKE_PERIOD] - 1
        if spy_20d_ret <= config.SPY_BRAKE_PCT:
            spy_brake = True
            print(f"\n  [熔断] SPY 近 {config.SPY_BRAKE_PERIOD} 日跌幅 {spy_20d_ret:.1%}"
                  f"（阈值 {config.SPY_BRAKE_PCT:.0%}），今日暂停新仓买入！")

    # ── 运行策略 ──────────────────────────────────────────────
    strategy = RSMomentum(vol_shrink_ratio=config.VOL_SHRINK_RATIO)
    strategy.set_spy(spy_close)

    buy_signals = []
    sell_alerts = []
    prices      = {}
    signal_map  = {}   # symbol → signal 值，供 execute() 对真实 IB 持仓补充卖出报警

    for symbol, df in all_data.items():
        if symbol == 'SPY' or symbol in CASH_EQUIV:
            continue
        try:
            if len(df) < 80:
                continue
            result    = strategy.generate_signals(df)
            latest    = result.iloc[-1]
            sig       = latest['signal']
            rs        = latest['rs_score']
            vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 0
            prices[symbol]     = latest['close']
            signal_map[symbol] = int(sig)

            if sig == 1:
                buy_signals.append({
                    'symbol':    symbol,
                    'rs_score':  rs,
                    'close':     latest['close'],
                    'vol_ratio': vol_ratio,
                })
            if symbol in held_symbols and sig == -1:
                sell_alerts.append({
                    'symbol': symbol,
                    'close':  latest['close'],
                    'reason': '量价背离（新高缩量）',
                })
        except Exception:
            pass

    buy_signals.sort(key=lambda x: x['rs_score'], reverse=True)

    # 熔断期间清空买入信号（卖出报警照常输出）
    if spy_brake:
        buy_signals = []

    # ── 内部人士买入数据（一次性拉取，用于买入候选排序参考）──────────────────
    insider_map = get_insider_buys(
        days        = config.INSIDER_DAYS,
        min_value_k = config.INSIDER_MIN_VALUE_K,
    )

    # ── 市值 / 行业过滤 + 附加 sector（只对买入候选查询，通常 < 50 只）────────
    if buy_signals:
        candidates = [s['symbol'] for s in buy_signals]
        info_map   = get_stock_info(candidates)
        filtered   = []
        deny_set   = {d.lower() for d in (deny_industries or [])}
        for sig in buy_signals:
            info = info_map.get(sig['symbol'], {})
            cap  = info.get('market_cap_b')
            ind  = (info.get('industry') or '').lower()
            # 市值过滤（无法获取市值的股票放行）
            if cap is not None:
                if min_cap_b is not None and cap < min_cap_b:
                    continue
                if max_cap_b is not None and cap > max_cap_b:
                    continue
            # 行业过滤
            if deny_set and any(d in ind for d in deny_set):
                continue
            sig['market_cap_b']  = cap
            sig['industry']      = info.get('industry')
            sig['sector']        = info.get('sector')
            sig['insider_score'] = insider_map.get(sig['symbol'], {}).get('score', 0)
            filtered.append(sig)
        n_removed = len(buy_signals) - len(filtered)
        if n_removed:
            print(f"  [过滤] 市值/行业过滤移除 {n_removed} 只，剩余 {len(filtered)} 只买入候选")
        buy_signals = filtered

    return {'buy': buy_signals, 'sell': sell_alerts, '_prices': prices, '_signal_map': signal_map,
            'spy_brake': spy_brake}


def market_is_open(ib) -> bool:
    """判断美股当前是否在正常交易时段（美东 9:30-16:00 工作日）"""
    et = datetime.now(ZoneInfo('America/New_York'))   # 自动处理 EDT/EST
    if et.weekday() >= 5:
        return False
    open_t  = et.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return open_t <= et <= close_t


def get_order_tif(ib) -> str:
    """
    根据当前时间选择订单有效期：
      盘中 (9:30-16:00 ET)  → DAY  （当天市价成交）
      盘前/盘后/周末        → OPG  （下一个开盘集合竞价成交）
    """
    return 'DAY' if market_is_open(ib) else 'OPG'


def execute(signals: dict, dry_run: bool = True):
    """连接 IB Gateway，根据信号执行买卖单"""
    db   = Database()
    db.connect()
    conn = IBConnection()
    ib   = conn.connect()

    account   = Account(ib, db=db)
    trader    = Trading(ib, db=db)
    tif       = get_order_tif(ib)
    tif_label = {'DAY': '盘中市价单', 'OPG': '开盘集合竞价单(OPG)'}[tif]
    print(f"  订单类型：{tif_label}")

    net_liq   = account._get_value('NetLiquidation')
    cash      = account._get_value('TotalCashValue')
    positions = {p.contract.symbol: p for p in ib.positions()}

    # 分离现金等价 ETF（SGOV 等）：市值计入现金，不占槽位
    cash_equiv_pos  = {s: p for s, p in positions.items() if s in CASH_EQUIV}
    stock_positions = {s: p for s, p in positions.items() if s not in CASH_EQUIV}
    if cash_equiv_pos:
        sgov_value = sum(abs(p.position) * p.avgCost for p in cash_equiv_pos.values())
        cash += sgov_value
        print(f"  现金等价持仓：{list(cash_equiv_pos)} 合计 ${sgov_value:,.0f}（已计入可用现金）")

    n_held    = len(stock_positions)

    # ── 查询已有挂单，重复运行时撤旧换新 ─────────────────────────
    # 只处理与当前模式相同的挂单（OPG/DAY），避免误撤手动单
    existing_orders: dict[str, dict[str, object]] = {}
    for _t in ib.openTrades():
        if _t.order.tif != tif:
            continue
        _sym = _t.contract.symbol
        _act = _t.order.action          # 'BUY' or 'SELL'
        existing_orders.setdefault(_sym, {})[_act] = _t

    if existing_orders:
        summary = {s: list(v.keys()) for s, v in existing_orders.items()}
        print(f"  检测到今日 {tif} 挂单：{summary}（重复信号将撤旧换新）")

    def _cancel_existing(sym: str, action: str):
        """撤销同方向已有挂单，为新单让路；无挂单则静默返回"""
        entry = existing_orders.get(sym, {}).get(action)
        if entry is None:
            return
        old_price = getattr(entry.order, 'lmtPrice', '市价')
        ib.cancelOrder(entry.order)
        ib.sleep(1)
        print(f"  [{sym}] 已撤销旧 {tif} {action} 挂单（原限价 {old_price}）")

    # ── 资金计算（与 backtest_rs.py 逻辑对齐）────────────────
    min_cash       = net_liq * CASH_RESERVE_PCT
    deployable     = max(0.0, cash - min_cash)
    slots          = MAX_POSITIONS - n_held
    budget_per_pos = min(
        deployable / max(1, slots),
        net_liq * POSITION_PCT,
    )

    print(f"\n{'─'*54}")
    print(f"  账户净值    ${net_liq:>12,.0f}")
    print(f"  当前现金    ${cash:>12,.0f}")
    print(f"  保留现金    ${min_cash:>12,.0f}  ({CASH_RESERVE_PCT:.0%} × 净值)")
    print(f"  可用资金    ${deployable:>12,.0f}")
    print(f"  当前持仓    {n_held} 只 / 上限 {MAX_POSITIONS} 只  → 剩余槽位 {slots} 个")
    print(f"  单仓预算    ${budget_per_pos:>12,.0f}  "
          f"(min( ${deployable:,.0f}/{max(1,slots)} , {POSITION_PCT:.0%}×净值${net_liq*POSITION_PCT:,.0f} ))")
    print(f"{'─'*54}")
    account.print_balance()   # 顺带存快照到 DB，便于事后审计
    account.print_positions()

    # ── 止损检查 ──────────────────────────────────────────────
    stop_loss_list = []
    for sym, pos in stock_positions.items():
        avg_cost  = pos.avgCost
        qty       = int(pos.position)
        cur_price = signals.get('_prices', {}).get(sym)
        if avg_cost <= 0 or qty <= 0 or cur_price is None:
            continue
        ret = (cur_price - avg_cost) / avg_cost
        if ret <= STOP_LOSS_PCT:
            stop_loss_list.append({
                'symbol': sym, 'qty': qty,
                'avg_cost': avg_cost, 'cur_price': cur_price, 'return': ret,
            })

    print(f"\n{'='*54}")
    if stop_loss_list:
        print(f"  止损触发（跌破入场价 {STOP_LOSS_PCT:.0%}）：{len(stop_loss_list)} 只")
        print(f"{'='*54}")
        for s in stop_loss_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f} → 现价 ${s['cur_price']:.2f}  "
                  f"浮亏 {s['return']:.1%}")
            if not dry_run:
                _cancel_existing(s['symbol'], 'SELL')
                if tif == 'OPG':
                    floor = round(s['cur_price'] * 0.95, 2)
                    trade = trader.limit_sell(s['symbol'], s['qty'], price=floor, tif='OPG')
                    label = f"限价 OPG 止损（下限 ${floor:.2f}）"
                else:
                    trade = trader.market_sell(s['symbol'], s['qty'], tif=tif)
                    label = tif_label
                if trade is None:
                    print(f"  → [ERROR] {s['symbol']} 止损单提交失败，请手动处理！")
                else:
                    print(f"  → 已下止损单 [{label}] 卖出 {s['qty']} 股")
            else:
                floor_str = f"（限价下限 ${s['cur_price']*0.95:.2f}）" if tif == 'OPG' else ""
                print(f"  → [dry-run] 将下止损单 {tif_label}{floor_str} 卖出 {s['qty']} 股")
    else:
        print(f"  无止损触发")
        print(f"{'='*54}")

    # ── 移动止损检查 ──────────────────────────────────────────
    already_hard_stop = {s['symbol'] for s in stop_loss_list}
    trail_stop_list   = []
    for sym, pos in stock_positions.items():
        if sym in already_hard_stop or sym in CASH_EQUIV:
            continue
        avg_cost  = pos.avgCost
        qty       = int(pos.position)
        cur_price = signals.get('_prices', {}).get(sym)
        if avg_cost <= 0 or qty <= 0 or cur_price is None:
            continue
        peak      = _peak_price(sym, avg_cost, db)
        peak_ret  = (peak - avg_cost) / avg_cost
        trail_ret = (cur_price - peak) / peak
        if peak_ret >= TRAIL_STOP_ACTIVATE_PCT and trail_ret <= TRAIL_STOP_PCT:
            trail_stop_list.append({
                'symbol': sym, 'qty': qty,
                'avg_cost': avg_cost, 'cur_price': cur_price,
                'peak': peak, 'peak_ret': peak_ret, 'trail_ret': trail_ret,
            })

    print(f"\n{'='*54}")
    if trail_stop_list:
        print(f"  移动止损触发（浮盈>{TRAIL_STOP_ACTIVATE_PCT:.0%}后从峰值回撤>{abs(TRAIL_STOP_PCT):.0%}）：{len(trail_stop_list)} 只")
        print(f"{'='*54}")
        for s in trail_stop_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f}  峰值 ${s['peak']:.2f}"
                  f"（+{s['peak_ret']:.1%}）→ 现价 ${s['cur_price']:.2f}  回撤 {s['trail_ret']:.1%}")
            if not dry_run:
                _cancel_existing(s['symbol'], 'SELL')
                if tif == 'OPG':
                    floor = round(s['cur_price'] * 0.95, 2)
                    trade = trader.limit_sell(s['symbol'], s['qty'], price=floor, tif='OPG')
                    label = f"限价 OPG 移动止损（下限 ${floor:.2f}）"
                else:
                    trade = trader.market_sell(s['symbol'], s['qty'], tif=tif)
                    label = tif_label
                if trade is None:
                    print(f"  → [ERROR] {s['symbol']} 移动止损单提交失败，请手动处理！")
                else:
                    print(f"  → 已下移动止损单 [{label}] 卖出 {s['qty']} 股")
            else:
                floor_str = f"（限价下限 ${s['cur_price']*0.95:.2f}）" if tif == 'OPG' else ""
                print(f"  → [dry-run] 将下移动止损单 {tif_label}{floor_str} 卖出 {s['qty']} 股")
    else:
        print(f"  无移动止损触发")
        print(f"{'='*54}")

    # ── 时间止损检查（Time Stop）─────────────────────────────
    already_stopped = {s['symbol'] for s in stop_loss_list} | {s['symbol'] for s in trail_stop_list}
    time_stop_list  = []
    if TIME_STOP_DAYS > 0:
        today = date.today()
        for sym, pos in stock_positions.items():
            if sym in already_stopped or sym in CASH_EQUIV:
                continue
            avg_cost  = pos.avgCost
            qty       = int(pos.position)
            cur_price = signals.get('_prices', {}).get(sym)
            if avg_cost <= 0 or qty <= 0 or cur_price is None:
                continue
            ret = (cur_price - avg_cost) / avg_cost
            if ret >= TIME_STOP_MIN_RETURN:
                continue  # 已达最低盈利，不触发
            ed = _entry_date(sym, db)
            if ed is None:
                continue
            days_held = (today - ed).days
            if days_held >= TIME_STOP_DAYS:
                time_stop_list.append({
                    'symbol': sym, 'qty': qty,
                    'avg_cost': avg_cost, 'cur_price': cur_price,
                    'return': ret, 'days_held': days_held,
                })

    print(f"\n{'='*54}")
    if time_stop_list:
        print(f"  时间止损触发（持仓>{TIME_STOP_DAYS}天未达{TIME_STOP_MIN_RETURN:.0%}）：{len(time_stop_list)} 只")
        print(f"{'='*54}")
        for s in time_stop_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f} → 现价 ${s['cur_price']:.2f}"
                  f"  {s['return']:+.1%}  持仓 {s['days_held']} 天")
            if not dry_run:
                _cancel_existing(s['symbol'], 'SELL')
                if tif == 'OPG':
                    trade = trader.limit_sell(s['symbol'], s['qty'],
                                              price=round(s['cur_price'] * 0.97, 2), tif='OPG')
                    label = f"限价 OPG 时间止损（下限 ${s['cur_price']*0.97:.2f}）"
                else:
                    trade = trader.market_sell(s['symbol'], s['qty'], tif=tif)
                    label = tif_label
                if trade is None:
                    print(f"  → [ERROR] {s['symbol']} 时间止损单提交失败，请手动处理！")
                else:
                    print(f"  → 已下时间止损单 [{label}] 卖出 {s['qty']} 股")
            else:
                floor_str = f"（限价下限 ${s['cur_price']*0.97:.2f}）" if tif == 'OPG' else ""
                print(f"  → [dry-run] 将下时间止损单 {tif_label}{floor_str} 卖出 {s['qty']} 股")
    else:
        print(f"  无时间止损触发")
        print(f"{'='*54}")

    # ── 卖出报警（量价背离）──────────────────────────────────
    # 用真实 IB 持仓补充：--held 未传入的股票也能被检测到
    sell_list      = list(signals.get('sell', []))
    signal_map     = signals.get('_signal_map', {})
    already_alerted = {s['symbol'] for s in sell_list} | {s['symbol'] for s in time_stop_list}
    for sym in stock_positions:
        if sym in already_alerted or sym in CASH_EQUIV:
            continue
        if signal_map.get(sym) == -1:
            cur_price = signals.get('_prices', {}).get(sym)
            if cur_price is None:
                continue
            sell_list.append({'symbol': sym, 'close': cur_price, 'reason': '量价背离（新高缩量）'})
    print(f"\n{'='*54}")
    if sell_list:
        print(f"  持仓卖出报警（量价背离）：{len(sell_list)} 只")
        print(f"{'='*54}")
        for s in sell_list:
            print(f"  [{s['symbol']}]  {s['reason']}  收盘 ${s['close']:.2f}")
            if SELL_ON_ALERT and s['symbol'] in stock_positions:
                qty = int(stock_positions[s['symbol']].position)
                if not dry_run:
                    _cancel_existing(s['symbol'], 'SELL')
                    if tif == 'OPG':
                        floor = round(s['close'] * 0.97, 2)
                        trade = trader.limit_sell(s['symbol'], qty, price=floor, tif='OPG')
                        label = f"限价 OPG（下限 ${floor:.2f}）"
                    else:
                        trade = trader.market_sell(s['symbol'], qty, tif=tif)
                        label = tif_label
                    if trade is None:
                        print(f"  → [ERROR] {s['symbol']} 卖出报警单提交失败，请手动处理！")
                    else:
                        print(f"  → 已下单 [{label}] 卖出 {qty} 股")
                else:
                    floor_str = f"（限价下限 ${s['close']*0.97:.2f}）" if tif == 'OPG' else ""
                    print(f"  → [dry-run] 将下 {tif_label}{floor_str} 卖出 {qty} 股")
    else:
        print(f"  持仓无卖出报警")
        print(f"{'='*54}")

    # ── 买入信号 ──────────────────────────────────────────────
    buy_list = signals.get('buy', [])
    print(f"\n{'='*54}")
    print(f"  买入信号：{len(buy_list)} 只  可用仓位：{slots} 个")
    print(f"{'='*54}")

    if deployable < budget_per_pos * 0.5:
        print(f"  可用资金 ${deployable:,.0f} 不足（保留现金规则：须保留 ${min_cash:,.0f}）")
    elif slots <= 0:
        print(f"  仓位已满（{n_held}/{MAX_POSITIONS}），今日不开新仓")
    elif not buy_list:
        print(f"  今日无买入信号")
    else:
        # 统计当前持仓的行业分布
        sector_counts: dict[str, int] = {}
        if stock_positions:
            pos_info = get_stock_info(list(stock_positions.keys()))
            for sym in stock_positions:
                sec = (pos_info.get(sym, {}).get('sector') or 'Unknown')
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

        executed = 0
        for sig in buy_list:
            if executed >= slots:
                break
            symbol = sig['symbol']
            if symbol in stock_positions:
                print(f"  [{symbol}] 已持仓，跳过")
                continue
            # 行业集中度限制
            sec = sig.get('sector') or 'Unknown'
            if sector_counts.get(sec, 0) >= MAX_PER_SECTOR:
                print(f"  [{symbol}] 行业[{sec}]已持有 {sector_counts[sec]} 只（上限 {MAX_PER_SECTOR}），跳过")
                continue
            qty = int(budget_per_pos / sig['close'])
            if qty <= 0:
                print(f"  [{symbol}] 单价 ${sig['close']:.2f} 超出预算，跳过")
                continue
            cost = qty * sig['close']
            # OPG 时改用限价单，防止开盘跳空过高追入
            if tif == 'OPG':
                limit_price = round(sig['close'] * (1 + MAX_ENTRY_SLIPPAGE), 2)
                print(f"  [{symbol}]  RS={sig['rs_score']:+.3f}  量比={sig['vol_ratio']:.1f}x  行业={sec}")
                print(f"    预算 ${budget_per_pos:,.0f} ÷ 昨收 ${sig['close']:.2f} = {qty} 股  "
                      f"限价 ${limit_price:.2f}  预计成本 ${qty*limit_price:,.0f}")
                if not dry_run:
                    _cancel_existing(symbol, 'BUY')
                    trade = trader.limit_buy(symbol, qty, price=limit_price, tif=tif)
                    if trade is None:
                        print(f"  → [ERROR] {symbol} 买入单提交失败，请手动处理！")
                    else:
                        print(f"  → 已下限价 OPG 单（开盘超过 ${limit_price:.2f} 则自动放弃）")
                else:
                    print(f"  → [dry-run] 将下限价 OPG 单 ${limit_price:.2f}")
            else:
                print(f"  [{symbol}]  RS={sig['rs_score']:+.3f}  量比={sig['vol_ratio']:.1f}x  "
                      f"行业={sec}  "
                      f"拟买 {qty} 股 × ${sig['close']:.2f} = ${cost:,.0f}")
                if not dry_run:
                    _cancel_existing(symbol, 'BUY')
                    trade = trader.market_buy(symbol, qty, tif=tif)
                    if trade is None:
                        print(f"  → [ERROR] {symbol} 买入单提交失败，请手动处理！")
                    else:
                        print(f"  → 已下单 [{tif_label}]")
                else:
                    print(f"  → [dry-run] 将下 {tif_label}")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            executed += 1

    conn.disconnect()
    db.close()


def main():
    parser = argparse.ArgumentParser(description='RS 动量策略自动交易')
    parser.add_argument('--run',      action='store_true', help='正式下单（默认 dry-run）')
    parser.add_argument('--dry-run',  action='store_true', help='只显示信号，不下单')
    parser.add_argument('--held',     nargs='+', default=[], help='当前持仓（用于卖出报警）')
    parser.add_argument('--extra',    nargs='+', default=[], help='追加股票（如 SNDK TSM）')
    parser.add_argument('--universe', default='sp500',
                        help='股票池：sp500 / nasdaq100 / russell2000')
    parser.add_argument('--min-cap',  type=float, default=config.MIN_CAP_B,
                        help=f'最小市值（十亿USD），默认 {config.MIN_CAP_B}B')
    parser.add_argument('--max-cap',  type=float, default=config.MAX_CAP_B,
                        help=f'最大市值（十亿USD），默认 {config.MAX_CAP_B}B')
    parser.add_argument('--deny-industry', nargs='+', default=config.DENY_INDUSTRIES,
                        help='拒绝行业关键词（模糊匹配）')
    args = parser.parse_args()

    dry_run         = not args.run
    held            = [s.upper() for s in args.held]
    extra           = [s.upper() for s in args.extra]
    deny_industries = args.deny_industry or []
    now_bj          = datetime.now().strftime('%Y-%m-%d %H:%M')
    mode            = "dry-run（不下单）" if dry_run else "正式执行（将下单）"

    print(f"\n{'='*54}")
    print(f"  RS 动量自动交易  [{mode}]")
    print(f"  北京时间：{now_bj}")
    print(f"  股票池：{args.universe}  |  "
          f"最多{MAX_POSITIONS}仓 | 单仓{POSITION_PCT:.0%} | 保留现金{CASH_RESERVE_PCT:.0%}")
    if args.min_cap or args.max_cap:
        cap_str = f"市值过滤：${args.min_cap or 0:.0f}B ~ ${args.max_cap or '∞'}B"
        print(f"  {cap_str}")
    if deny_industries:
        print(f"  拒绝行业：{deny_industries}")
    if held:
        print(f"  监控持仓：{held}")
    if extra:
        print(f"  追加股票：{extra}")
    print(f"{'='*54}\n")

    if dry_run:
        print("提示：盘前扫描（北京时间晚8点）用此模式预览信号")
        print("      开盘后执行用 --run（北京时间晚10:40）\n")

    print("第一步：扫描信号（基于昨日收盘数据）")
    signals = scan_signals(
        held, extra=extra, universe=args.universe,
        min_cap_b=args.min_cap, max_cap_b=args.max_cap,
        deny_industries=deny_industries,
    )

    # 信号存库（审计用，dry-run 也存）
    _db = Database()
    _db.connect()
    _db.save_signals(date.today(), signals)
    _db.close()

    print("\n第二步：执行交易")
    execute(signals, dry_run=dry_run)


if __name__ == '__main__':
    main()
