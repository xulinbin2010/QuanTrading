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
from core.earnings import prefetch_earnings, has_upcoming_earnings
from strategies.rs_momentum import RSMomentum
from strategies.factors.atr import compute_atr
from core.market_regime import compute_mss, mss_label
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
ATR_STOP_MULTIPLIER     = config.ATR_STOP_MULTIPLIER
ATR_STOP_FLOOR          = config.ATR_STOP_FLOOR
TARGET_RISK_PER_POS     = config.TARGET_RISK_PER_POS
TIME_STOP_DAYS          = config.TIME_STOP_DAYS
TIME_STOP_MIN_RETURN    = config.TIME_STOP_MIN_RETURN

MAX_ENTRY_SLIPPAGE = config.MAX_ENTRY_SLIPPAGE

# 仓位计算：5 × 15% = 75% 持仓 + 25% 现金保留
SELL_ON_ALERT    = False  # 已废弃：量价背离出场已下线，恒 False（出场仅 -15%硬止损 + EMA21两日破位）


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


def _peak_price(sym: str, avg_cost: float, df, entry_date=None) -> float:
    """计算持仓峰值收盘价：只看入场日之后的数据，避免把买入前旧高误认为峰值。"""
    if df is None or df.empty:
        return avg_cost
    if entry_date is not None:
        import pandas as pd
        df = df[df.index >= pd.Timestamp(entry_date)]
    if df.empty:
        return avg_cost
    above = df['close'][df['close'] >= avg_cost]
    return float(above.max()) if not above.empty else avg_cost

# 现金等价 ETF：占仓时计入现金、不占槽位、跳过止损和信号扫描
CASH_EQUIV = {'SGOV', 'BIL', 'USFR'}

# OPG 限价保护：从 config.py 统一读取（默认 1%），支持 Web UI 修改

# ══════════════════════════════════════════════════════

# AI 优先池成员加载 + 入场得分 + 槽位分配统一收敛到 core/pool_policy。
# 此处重新导出 _load_ai_priority_set，保持 production_signal_svc / backtest_rs 旧 import 不破。
from core.pool_policy import (
    load_ai_priority_set as _load_ai_priority_set,
    load_ai_tracker_boost_map,
    build_pool_policies,
    classify as _classify_pool,
    sort_key as _pool_sort_key,
    ai_theme_brake_now,
    PoolAllocator,
)


def scan_signals(
    held_symbols:     list[str],
    extra:            list[str] = None,
    universe:         str       = 'ai',
    min_cap_b:        float     = None,    # 最小市值（十亿 USD），如 10
    max_cap_b:        float     = None,    # 最大市值（十亿 USD），如 500
    deny_industries:  list[str] = None,    # 拒绝行业，如 ['Software—Application']
    force_refresh_recent_days: int = 0,    # 强制重拉最近 N 个交易日(覆盖 yfinance 校正过的数据)
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

    all_syms = list(set(tickers + ['SPY', '^VIX', 'SMH']))
    store    = DataStore()
    all_data = store.get(all_syms, start=dl_start, end=dl_end,
                         force_refresh_recent_days=force_refresh_recent_days)
    if force_refresh_recent_days > 0:
        print(f"  [数据校准] 已强制重拉最近 {force_refresh_recent_days} 个交易日(覆盖 yfinance 校正过的 K 线)")

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

    # ── VIX 熔断检查 ──────────────────────────────────────────
    vix_brake = False
    vix_close = None
    vix_df = all_data.get('^VIX')
    if vix_df is not None and len(vix_df) > 0:
        vix_close = float(vix_df['close'].iloc[-1])
        if vix_close >= config.VIX_BRAKE_LEVEL:
            vix_brake = True
            print(f"\n  [VIX熔断] VIX={vix_close:.1f}（阈值 {config.VIX_BRAKE_LEVEL:.0f}），"
                  f"今日暂停新仓买入！")
        else:
            print(f"  VIX={vix_close:.1f}（安全线 <{config.VIX_BRAKE_LEVEL:.0f}）")

    # ── 运行策略 ──────────────────────────────────────────────
    # 双路扫描:SP500 普通票走严格(默认 5 条件) / AI 优先池走宽松(近高点-15%, 放量条件取消)
    from web.services.factor_svc import get_factor_params_from_db
    rs_params = get_factor_params_from_db('rs_score')
    common_kw = dict(
        rs_period=int(rs_params.get('period', 63)),
        rs_weights=str(rs_params.get('weights', '') or ''),
        vol_shrink_ratio=config.VOL_SHRINK_RATIO,
    )
    # AI 主题熔断:SMH/SPY 相对强度跌破 MA → AI 池降级(回退严格扫描 + 取消行业豁免,不清空信号)
    _smh_df = all_data.get('SMH')
    _smh_close = _smh_df['close'] if _smh_df is not None else None
    ai_theme_brake = ai_theme_brake_now(_smh_close, spy_close)
    if ai_theme_brake:
        print(f"  [AI主题熔断] SMH/SPY 相对强度跌破 MA{getattr(config,'AI_THEME_BRAKE_MA',50)}"
              f" → AI 优先池降级(严格扫描+取消行业豁免,已有信号不清空)")

    # PoolPolicy 驱动:每池一套扫描参数,候选按所属池打 pool/rank_tier 标签。
    policies = build_pool_policies(ai_theme_brake=ai_theme_brake)
    ai_set   = next((p.members for p in policies if p.name == 'ai_priority'), set()) or set()
    pool_strategies: dict[str, RSMomentum] = {}
    for p in policies:
        strat = RSMomentum(**common_kw, **p.signal_params)
        strat.set_spy(spy_close)
        pool_strategies[p.name] = strat
    if ai_set:
        print(f"  [AI优先池] 加载 {len(ai_set)} 只,走宽松扫描(近高点-15%、不要求放量)")

    buy_signals = []
    sell_alerts = []
    prices      = {}
    rs_scores   = {}   # symbol → 当日 RS score（用于 RS 衰退止盈）
    atr_map     = {}   # symbol → ATR14 绝对值（用于自适应止损计算）
    signal_map  = {}   # symbol → signal 值，供 execute() 对真实 IB 持仓补充卖出报警

    for symbol, df in all_data.items():
        if symbol == 'SPY' or symbol in CASH_EQUIV:
            continue
        try:
            if len(df) < 80:
                continue
            pol            = _classify_pool(symbol, policies)
            is_ai_priority = pol.name == 'ai_priority'
            strat          = pool_strategies[pol.name]
            result         = strat.generate_signals(df)
            latest    = result.iloc[-1]
            sig       = latest['signal']
            rs        = latest['rs_score']
            vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 0
            prices[symbol]     = latest['close']
            rs_scores[symbol]  = float(rs)
            signal_map[symbol] = int(sig)
            # ATR14 用于自适应止损
            atr_df = compute_atr(df.tail(30).copy())
            atr14  = float(atr_df['atr14'].iloc[-1])
            if pd.notna(atr14) and atr14 > 0:
                atr_map[symbol] = atr14

            if sig == 1:
                buy_signals.append({
                    'symbol':           symbol,
                    'rs_score':         rs,
                    'close':            latest['close'],
                    'vol_ratio':        vol_ratio,
                    'drawdown_from_high': float(latest.get('drawdown_from_high', -0.15)),
                    'pool':             pol.name,
                    'rank_tier':        pol.rank_tier,
                    'ai_priority':      is_ai_priority,   # = (rank_tier==0)，保留供 execute 豁免/前端用
                })
            if symbol in held_symbols and sig == -1:
                sell_alerts.append({
                    'symbol': symbol,
                    'close':  latest['close'],
                    'reason': '量价背离（新高缩量）',
                })
        except Exception:
            pass

    # ── 市场宽度：S&P500 中站上 MA200 的比例 ─────────────────
    breadth_cap = False
    breadth_pct = None
    n_above = n_total = 0
    for sym, df in all_data.items():
        if sym in ('SPY', '^VIX') or sym in CASH_EQUIV or len(df) < 201:
            continue
        ma200 = df['close'].rolling(200).mean().iloc[-1]
        if pd.notna(ma200):
            n_total += 1
            if df['close'].iloc[-1] > ma200:
                n_above += 1
    if n_total > 0:
        breadth_pct = n_above / n_total
        if breadth_pct < config.BREADTH_MIN_PCT:
            breadth_cap = True
            print(f"\n  [市场宽度] 仅 {breadth_pct:.0%} 股票站上MA200"
                  f"（阈值 {config.BREADTH_MIN_PCT:.0%}），"
                  f"最多开 {config.BREADTH_MAX_POS} 仓！")
        else:
            print(f"  市场宽度={breadth_pct:.0%} 股票站上MA200（健康线 >{config.BREADTH_MIN_PCT:.0%}）")

    # 熔断期间清空买入信号（卖出报警照常输出）
    if spy_brake or vix_brake:
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
        import re as _re
        def _norm_ind(s: str) -> str:
            # 统一各种破折号/连字符为空格，折叠多余空格，小写
            s = _re.sub(r'[—–‒\-_/]', ' ', s.lower())
            return _re.sub(r'\s+', ' ', s).strip()
        deny_set = {_norm_ind(d) for d in (deny_industries or [])}
        for sig in buy_signals:
            info = info_map.get(sig['symbol'], {})
            cap  = info.get('market_cap_b')
            ind  = _norm_ind(info.get('industry') or '')
            # 市值过滤（无法获取市值的股票放行）
            if cap is not None:
                if min_cap_b is not None and cap < min_cap_b:
                    continue
                if max_cap_b is not None and cap > max_cap_b:
                    continue
            # 行业过滤：deny 关键词只要是行业名的子串即可匹配
            # 例：'software' 匹配 'Software - Application' / 'Enterprise Software' 等
            if deny_set and any(d in ind for d in deny_set):
                continue
            # 基本面硬门槛（数据缺失时放行）
            if config.FUND_FILTER_ENABLED:
                roe = info.get('roe')
                de  = info.get('debt_to_equity')
                rev = info.get('revenue_growth')
                if roe is not None and roe < config.FUND_MIN_ROE:
                    continue
                if de is not None and de > config.FUND_MAX_DE:
                    continue
                if rev is not None and rev < config.FUND_MIN_REV_GROWTH:
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

    # ── AI 追踪器加成（保留为 config 可调项，仅影响 AI 池内次序）──────────────────
    ai_boost_map = load_ai_tracker_boost_map()
    if ai_boost_map:
        ai_cnt = sum(1 for s in buy_signals if s['symbol'] in ai_boost_map)
        if ai_cnt:
            _bmax = getattr(config, 'ENTRY_AI_TRACKER_BOOST_MAX', 0.20)
            print(f"  [AI关联] {ai_cnt} 只候选在 AI 股票池，AI 池内排序加成最高 +{_bmax*100:.0f}%")
    for sig in buy_signals:
        sig['ai_tracker_boost'] = ai_boost_map.get(sig['symbol'], 0.0)

    # ── 复合入场得分排序（过滤后执行，insider_score / sector 已就位）────────────
    # 排序 key = (rank_tier 升序, entry_score 降序):
    #   - 跨池次序由 rank_tier 承载(AI 池 tier=0 绝对置顶,替代旧 ai_priority_bonus +0.5)
    #   - 同池内按 entry_score = rs × (1+量比+近高点+内幕+AI追踪器加成) 排(权重从 config 读)
    buy_signals.sort(key=_pool_sort_key)

    # ── 财报日期预取（批量缓存，execute 阶段直接用缓存）────────────────────────
    if buy_signals and config.EARNINGS_AVOID_DAYS > 0:
        prefetch_earnings([s['symbol'] for s in buy_signals])

    # ── 市场强度评分（MSS）────────────────────────────────────────────────────
    mss = compute_mss(spy_close, vix_close, breadth_pct)
    print(f"  MSS={mss:.2f} ({mss_label(mss)})  "
          f"[SPY趋势/市场宽度{'' if breadth_pct is None else f'={breadth_pct:.0%}'}/VIX{'' if vix_close is None else f'={vix_close:.1f}'}]")

    return {'buy': buy_signals, 'sell': sell_alerts, '_prices': prices, '_atr': atr_map,
            '_signal_map': signal_map, '_rs_scores': rs_scores, 'spy_brake': spy_brake,
            '_vix': vix_close, '_vix_brake': vix_brake,
            '_breadth': breadth_pct, '_breadth_cap': breadth_cap,
            '_ai_theme_brake': ai_theme_brake,
            '_mss': mss}


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


def _handle_opg_partial_fills(ib, trader, opg_buy_trades: list):
    """
    【在线路径】等待 OPG 集合竞价完成，检测部分成交并立即补挂 DAY 限价单。

    opg_buy_trades: [(symbol, orig_qty, limit_price, trade), ...]
    逻辑：
      - 完全成交 → 无需处理
      - 零成交   → 开盘价超限，跳过（不追高）
      - 部分成交 → 补挂 DAY 限价单，限价 = 实际成交均价 × (1 + MAX_ENTRY_SLIPPAGE)
    """
    import time as _time

    et = datetime.now(ZoneInfo('America/New_York'))
    # 等到 9:32 ET，给集合竞价留足时间
    target = et.replace(hour=9, minute=32, second=0, microsecond=0)
    wait_sec = (target - et).total_seconds()

    if wait_sec > 0:
        import sys
        if not sys.stdin.isatty():
            # 调度器/子进程模式：无法交互，直接退出，由 confirm_fills.py 在 9:35 处理
            print(f"\n[补单监控] 非交互模式，跳过等待（OPG 单已提交，由 confirm_fills.py 在 9:35 处理成交确认）")
            return
        mins = wait_sec / 60
        print(f"\n[补单监控] OPG 单已提交，等待开盘集合竞价完成（约 {mins:.0f} 分钟后检查）...")
        print(f"  可按 Ctrl+C 跳过等待（已提交的 OPG 单将继续在交易所执行）")
        try:
            _time.sleep(wait_sec)
        except KeyboardInterrupt:
            print(f"\n[补单监控] 跳过等待，改由 confirm_fills.py 在 9:35 处理部分成交")
            return

    ib.sleep(2)   # pump ib_insync event loop，刷新订单状态

    print(f"\n[补单监控] 检查 OPG 买入单成交情况...")
    补单_count = 0
    for symbol, orig_qty, limit_price, trade in opg_buy_trades:
        if trade is None:
            continue
        filled   = float(trade.orderStatus.filled)
        remainder = orig_qty - int(filled)

        if remainder <= 0:
            print(f"  [{symbol}] 完全成交 {orig_qty} 股 ✓")
            continue

        if filled < 0.5:
            print(f"  [{symbol}] OPG 单零成交（开盘价超限价 ${limit_price:.2f}），不追高，跳过")
            continue

        # 部分成交 — 补一笔 DAY 限价单
        avg_fill  = float(trade.orderStatus.avgFillPrice) or limit_price
        day_limit = round(avg_fill * (1 + MAX_ENTRY_SLIPPAGE), 2)
        print(f"  [{symbol}] 部分成交 {int(filled)}/{orig_qty} 股，"
              f"补挂 DAY 限价单 {remainder} 股 @ ${day_limit:.2f}")
        trader.limit_buy(symbol, remainder, price=day_limit, tif='DAY')
        补单_count += 1

    if 补单_count == 0:
        print(f"  所有 OPG 单均已完整成交或零成交，无需补单")
    else:
        print(f"[补单监控] 已补挂 {补单_count} 笔 DAY 限价单")


def execute(signals: dict, dry_run: bool = True, exits_only: bool = False):
    """连接 IB Gateway，根据信号执行买卖单"""
    db   = Database()
    db.connect()
    conn = IBConnection()
    ib   = conn.connect()
    try:
        _execute_inner(signals, dry_run, db, conn, ib, exits_only=exits_only)
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
        db.close()


def _execute_inner(signals: dict, dry_run: bool, db, conn, ib, exits_only: bool = False):

    account   = Account(ib, db=db)
    trader    = Trading(ib, db=db)
    tif       = get_order_tif(ib)
    tif_label = {'DAY': '盘中市价单', 'OPG': '开盘集合竞价单(OPG)'}[tif]
    if exits_only:
        print(f"  [仅出场模式] 只执行止损/卖出，不买入"
              f"{'（盘中 DAY 单，开盘后运行可即时成交）' if tif == 'DAY' else '（当前非盘中，出场单仍走 OPG，建议开盘后运行）'}")
    print(f"  订单类型：{tif_label}")

    # ── MSS 自适应：根据市场强度动态覆盖仓位上限 ────────────────────────
    # MSS_BULL/BEAR_MAX_POS 默认与 MAX_POSITIONS 一致，防止静默扩/缩仓位
    # 注：移动止损已下线（出场仅 -15% 硬止损 + EMA21 两日破位），MSS 不再调止损参数
    mss = signals.get('_mss', 0.0)
    bull_thr = getattr(config, 'MSS_BULL_THRESHOLD', 0.5)
    bear_thr = getattr(config, 'MSS_BEAR_THRESHOLD', 0.0)
    if mss >= bull_thr:
        eff_max_pos = getattr(config, 'MSS_BULL_MAX_POS', config.MAX_POSITIONS)
    elif mss < bear_thr:
        eff_max_pos = getattr(config, 'MSS_BEAR_MAX_POS', config.MAX_POSITIONS)
    else:
        eff_max_pos = config.MAX_POSITIONS
    print(f"  MSS={mss:.2f} ({mss_label(mss)}) → 仓位上限={eff_max_pos}")

    net_liq   = account._get_value('NetLiquidation')
    cash      = account._get_value('TotalCashValue')
    # 过滤 qty=0 的幽灵仓位（IB 平仓后可能残留）和非股票合约（期权等不占槽位）
    positions = {
        p.contract.symbol: p
        for p in ib.positions()
        if float(p.position) != 0 and getattr(p.contract, 'secType', 'STK') == 'STK'
    }

    # 分离现金等价 ETF（SGOV 等）：市值计入现金，不占槽位
    cash_equiv_pos  = {s: p for s, p in positions.items() if s in CASH_EQUIV}
    stock_positions = {s: p for s, p in positions.items() if s not in CASH_EQUIV}
    # ── 实仓基线（买入端硬闸用）──────────────────────────────────
    # IB 真实持有的非现金等价股票数，未被 pending_sell_exits / exiting_syms 乐观扣减。
    # 下方槽位计算会按"预期卖出"提前腾槽，但 OPG 出场单是 LMT、可能不成交（如 DELL 连日挂
    # 全平单却始终未成交），导致仓位没真退却已补新仓 → 超过 MAX_POSITIONS。买入循环以本基线
    # 做硬上限：真实持仓 + 本轮已买 ≥ 上限即停，不信任"预期会卖出"，宁可补仓延后一天。
    real_held_count = len(stock_positions)
    if cash_equiv_pos:
        sgov_value = sum(abs(p.position) * p.avgCost for p in cash_equiv_pos.values())
        cash += sgov_value
        print(f"  现金等价持仓：{list(cash_equiv_pos)} 合计 ${sgov_value:,.0f}（已计入可用现金）")

    # ── 待成交市价卖单感知（含 Web UI 手动卖出）：对应持仓视为退出，腾槽位 + 预计回笼资金 ──
    # Web UI 用不同 clientId 下单，ib.openTrades() 看不到，故从 DB 读今日非终态卖单。
    # 仅认市价单(MKT/MOC)——开盘集合竞价必成交，腾槽安全；限价卖单可能不成交，不腾以防过投。
    # 仅当卖出股数 ≥ 持仓股数(全平)才腾槽，部分卖出不腾。处理同 CASH_EQUIV：从 stock_positions
    # 移出 → 不占槽、不被止损/报警重复处理。自校正：卖单若已成交，持仓不在 stock_positions，不会误腾。
    _px = signals.get('_prices', {})
    pending_sell_exits = {}
    try:
        _sell_qty: dict[str, float] = {}
        for _row in db.get_pending_orders(date.today().strftime('%Y-%m-%d')):
            # (id, symbol, action, order_type, quantity, price, order_id)
            if _row[2] == 'SELL' and _row[3] in ('MKT', 'MOC'):
                _sell_qty[_row[1]] = _sell_qty.get(_row[1], 0.0) + (_row[4] or 0.0)
        for _sym, _q in _sell_qty.items():
            if (_sym in stock_positions and _sym not in CASH_EQUIV
                    and _q >= int(stock_positions[_sym].position)):
                pending_sell_exits[_sym] = stock_positions[_sym]
    except Exception as _e:
        print(f"  [warn] 待成交卖单检测失败，按常规持仓处理：{_e}")
    if pending_sell_exits:
        _proceeds = sum(abs(p.position) * (_px.get(s) or p.avgCost)
                        for s, p in pending_sell_exits.items())
        cash += _proceeds
        for s in pending_sell_exits:
            del stock_positions[s]
        print(f"  待成交市价卖单：{list(pending_sell_exits)} → 视为退出，腾出 {len(pending_sell_exits)} 槽，"
              f"预计回笼 ${_proceeds:,.0f}（计入可用资金）")

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
        """撤销同方向已有挂单，为新单让路；无挂单则静默返回。
        SELL 跨 tif 全撤：开盘后下 DAY 出场单前，必须清掉盘前残留、未成交的 OPG 卖单，
        否则两张卖单并存会重复卖出（OPG 若在实盘开盘已成交则本就不在挂单里，不受影响）。"""
        if action == 'SELL':
            cancelled = 0
            for _t in ib.openTrades():
                if _t.contract.symbol == sym and _t.order.action == 'SELL':
                    ib.cancelOrder(_t.order)
                    cancelled += 1
            if cancelled:
                ib.sleep(1)
                print(f"  [{sym}] 已撤销 {cancelled} 笔旧 SELL 挂单（跨 OPG/DAY）")
            return
        entry = existing_orders.get(sym, {}).get(action)
        if entry is None:
            return
        old_price = getattr(entry.order, 'lmtPrice', '市价')
        ib.cancelOrder(entry.order)
        ib.sleep(1)
        print(f"  [{sym}] 已撤销旧 {tif} {action} 挂单（原限价 {old_price}）")

    def _place_sell(sym: str, qty: int, cur_price: float, verb: str, floor_pct: float = 0.95):
        """提交一笔出场卖单；dry-run 时只打印。"""
        if not dry_run:
            _cancel_existing(sym, 'SELL')
            if tif == 'OPG':
                floor = round(cur_price * floor_pct, 2)
                trade = trader.limit_sell(sym, qty, price=floor, tif='OPG')
                order_label = f"限价 OPG {verb}（下限 ${floor:.2f}）"
            else:
                trade = trader.market_sell(sym, qty, tif=tif)
                order_label = tif_label
            if trade is None:
                print(f"  → [ERROR] {sym} {verb}提交失败，请手动处理！")
            else:
                print(f"  → 已下{verb} [{order_label}] 卖出 {qty} 股")
        else:
            floor_str = f"（限价下限 ${cur_price * floor_pct:.2f}）" if tif == 'OPG' else ""
            print(f"  → [dry-run] 将下{verb} {tif_label}{floor_str} 卖出 {qty} 股")

    # 统一出场登记表：所有止损类型注册至此，槽位重算从此派生，新增止损类型只需 extend
    exit_orders: list[dict] = []

    # ── 资金计算（与 backtest_rs.py 逻辑对齐）────────────────
    min_cash       = net_liq * CASH_RESERVE_PCT
    deployable     = max(0.0, cash - min_cash)
    slots          = eff_max_pos - n_held
    budget_per_pos = min(
        deployable / max(1, slots),
        net_liq * POSITION_PCT,
    )

    print(f"\n{'─'*54}")
    print(f"  账户净值    ${net_liq:>12,.0f}")
    print(f"  当前现金    ${cash:>12,.0f}")
    print(f"  保留现金    ${min_cash:>12,.0f}  ({CASH_RESERVE_PCT:.0%} × 净值)")
    print(f"  可用资金    ${deployable:>12,.0f}")
    print(f"  当前持仓    {n_held} 只 / 上限 {eff_max_pos} 只  → 剩余槽位 {slots} 个")
    print(f"  单仓预算    ${budget_per_pos:>12,.0f}  "
          f"(min( ${deployable:,.0f}/{max(1,slots)} , {POSITION_PCT:.0%}×净值${net_liq*POSITION_PCT:,.0f} ))")
    print(f"{'─'*54}")
    account.print_balance()   # 顺带存快照到 DB，便于事后审计
    account.print_positions()

    # ── 预加载持仓历史数据（用于 EMA21 两日破位判定）──
    from core.data_store import DataStore
    held_syms   = [s for s in stock_positions if s not in CASH_EQUIV]
    dl_start    = (date.today() - timedelta(days=400)).strftime('%Y-%m-%d')
    held_data   = DataStore().get(held_syms, start=dl_start, end=date.today().strftime('%Y-%m-%d'),
                                  auto_update=False) if held_syms else {}

    # ── 出场体系（精简版：-15% 硬止损 + EMA21 两日破位）──────────────
    # 设计依据：单边趋势票上频繁止损/高抛是负 alpha（见 CLAUDE.md 单股回测教训），
    # 故只保留两条出场、给趋势充分空间：
    #   规则1 灾难硬止损：浮亏 ≤ STOP_LOSS_PCT（默认 -15%，按现价/盘前价算）
    #   规则2 趋势破位：最近两根【已收盘】日线（T-1、T-2）收盘均 < 各自当日 EMA21
    #         盘前(9:00 ET)运行时当日 bar 未生成，只用已确认收盘，不把盘前现价算作一天
    EMA_EXIT_PERIOD = 21
    exit_list = []
    for sym, pos in stock_positions.items():
        if sym in CASH_EQUIV:
            continue
        avg_cost  = pos.avgCost
        qty       = int(pos.position)
        cur_price = signals.get('_prices', {}).get(sym)
        if avg_cost <= 0 or qty <= 0 or cur_price is None:
            continue
        ret = (cur_price - avg_cost) / avg_cost

        # 规则1：硬止损
        if ret <= STOP_LOSS_PCT:
            exit_list.append({
                'symbol': sym, 'qty': qty, 'avg_cost': avg_cost,
                'cur_price': cur_price, 'return': ret,
                'reason': f'硬止损（浮亏 {ret:+.1%} ≤ {STOP_LOSS_PCT:.0%}）',
            })
            continue

        # 规则2：EMA21 连续两日破位（已收盘 T-1、T-2）
        df = held_data.get(sym)
        if df is None or len(df) < EMA_EXIT_PERIOD + 2:
            continue
        try:
            ema21 = df['close'].ewm(span=EMA_EXIT_PERIOD, adjust=False).mean()
            c_t1, c_t2 = float(df['close'].iloc[-1]), float(df['close'].iloc[-2])
            e_t1, e_t2 = float(ema21.iloc[-1]),       float(ema21.iloc[-2])
        except Exception:
            continue
        if c_t1 < e_t1 and c_t2 < e_t2:
            exit_list.append({
                'symbol': sym, 'qty': qty, 'avg_cost': avg_cost,
                'cur_price': cur_price, 'return': ret,
                'reason': (f'跌破EMA{EMA_EXIT_PERIOD}两日'
                           f'（收 {c_t2:.2f}/{c_t1:.2f} < EMA {e_t2:.2f}/{e_t1:.2f}）'),
            })

    print(f"\n{'='*54}")
    if exit_list:
        print(f"  出场触发（-15%硬止损 / EMA{EMA_EXIT_PERIOD}两日破位）：{len(exit_list)} 只")
        print(f"{'='*54}")
        for s in exit_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f} → 现价 ${s['cur_price']:.2f}  "
                  f"{s['return']:+.1%}  | {s['reason']}")
            _place_sell(s['symbol'], s['qty'], s['cur_price'], '出场单', 0.95)
    else:
        print(f"  无出场触发")
        print(f"{'='*54}")
    exit_orders.extend(exit_list)

    # 移动止损/时间止损/量价背离/RS衰退止盈已下线：出场仅由上面两条规则驱动
    sell_list: list[dict] = []

    # ── 重新计算可用仓位和资金（含本次出场释放的仓位和回笼资金）──
    exiting_syms = (
        {o['symbol'] for o in exit_orders} |
        {s['symbol'] for s in sell_list
         if SELL_ON_ALERT and s['symbol'] in stock_positions}
    )
    freed_cash   = sum(
        int(stock_positions[sym].position) * (signals.get('_prices', {}).get(sym) or 0)
        for sym in exiting_syms if sym in stock_positions
    )
    effective_held = n_held - len(exiting_syms)
    slots          = eff_max_pos - effective_held
    deployable     = max(0.0, cash + freed_cash - min_cash)
    budget_per_pos = min(
        deployable / max(1, slots),
        net_liq * POSITION_PCT,
    )
    if exiting_syms:
        print(f"\n  [仓位更新] 本次卖出 {len(exiting_syms)} 只 → 有效持仓 {effective_held} 只"
              f"  回笼资金 ${freed_cash:,.0f}  可用槽位 {slots} 个")

    if exits_only:
        print(f"\n  [仅出场模式] 已处理全部止损/卖出共 {len(exiting_syms)} 只，跳过买入段。")
        return

    # ── 买入信号 ──────────────────────────────────────────────
    buy_list = signals.get('buy', [])

    # 市场环境过滤：VIX熔断 / 市场宽度压仓
    vix_brake_active   = signals.get('_vix_brake', False)
    breadth_cap_active = signals.get('_breadth_cap', False)
    if vix_brake_active:
        buy_list = []
    elif breadth_cap_active:
        effective_max_pos = min(eff_max_pos, config.BREADTH_MAX_POS)
        slots = max(0, effective_max_pos - effective_held)

    print(f"\n{'='*54}")
    print(f"  买入信号：{len(buy_list)} 只  可用仓位：{slots} 个")
    print(f"{'='*54}")

    if vix_brake_active:
        print(f"  [VIX熔断] 暂停买入")
    elif deployable < budget_per_pos * 0.5:
        print(f"  可用资金 ${deployable:,.0f} 不足（保留现金规则：须保留 ${min_cash:,.0f}）")
    elif slots <= 0:
        print(f"  仓位已满（{effective_held}/{MAX_POSITIONS}），今日不开新仓")
    elif not buy_list:
        print(f"  今日无买入信号")
    else:
        # ── PoolAllocator 统一槽位分配 ──────────────────────────────
        # 结构性决策(全局上限/实仓硬闸/行业上限/池配额)交给 allocator;执行特定检查
        # (已持仓/待卖/财报/qty)留在本循环——后者跳过的候选不消耗行业/池配额,故仅
        # 真正下单后才 commit。MAX_NON_AI_POS 语义已迁移为 sp500_base 池配额(在 policy 内)。
        # AI 主题熔断状态由 scan_signals 计算并随 signals 传入,execute 用同一状态构池(行业豁免一致)
        policies = build_pool_policies(ai_theme_brake=signals.get('_ai_theme_brake', False))
        ai_set   = next((p.members for p in policies if p.name == 'ai_priority'), set()) or set()
        # 预播：当前持仓的行业分布(含 AI,与历史口径一致) + 非 AI 池存量
        sector_counts: dict[str, int] = {}
        non_ai_held = 0
        if stock_positions:
            pos_info = get_stock_info(list(stock_positions.keys()))
            for sym in stock_positions:
                if sym in exiting_syms:
                    continue  # 本次已触发止损/卖出，不计入行业占用
                sec = (pos_info.get(sym, {}).get('sector') or 'Unknown')
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                if sym.upper() not in ai_set:
                    non_ai_held += 1

        alloc = PoolAllocator(
            policies,
            global_slots=slots,
            global_cap=eff_max_pos,
            real_held_count=real_held_count,
            sector_cap=MAX_PER_SECTOR,
            sector_counts=sector_counts,
            pool_counts={'sp500_base': non_ai_held},
        )

        opg_buy_trades: list = []   # [(symbol, orig_qty, limit_price, trade)] 供补单监控用
        for sig in buy_list:
            if alloc.executed >= alloc.global_slots:
                break
            # 实仓硬闸：以 IB 实际持仓为准，不信任"预期卖出会成交"（OPG LMT 出场可能不成交）。
            # 即便 slots 因预期卖出腾出，真实持仓 + 本轮已买达上限就停，从根上杜绝超仓。
            if alloc.real_held + alloc.executed >= alloc.global_cap:
                print(f"  {alloc.stop_reason()}")
                break
            symbol = sig['symbol']
            if symbol in stock_positions:
                print(f"  [{symbol}] 已持仓，跳过")
                continue
            if symbol in pending_sell_exits:
                print(f"  [{symbol}] 正在卖出（待成交市价单），不回补，跳过")
                continue
            # 行业上限(AI 池豁免) + 非 AI 池配额,统一在 allocator 判定
            sec = sig.get('sector') or 'Unknown'
            _skip = alloc.structural_skip(sig)
            if _skip:
                print(f"  [{symbol}] {_skip}")
                continue
            # 财报回避
            if config.EARNINGS_AVOID_DAYS > 0 and has_upcoming_earnings(symbol, config.EARNINGS_AVOID_DAYS):
                print(f"  [{symbol}] 财报将在 {config.EARNINGS_AVOID_DAYS} 日内，跳过")
                continue
            # 波动率仓位：每仓风险 = TARGET_RISK_PER_POS × 净值，止损距离 = ATR_STOP_MULTIPLIER × ATR14
            atr14 = signals.get('_atr', {}).get(symbol)
            if atr14 is not None and atr14 > 0:
                target_risk_dollars = net_liq * config.TARGET_RISK_PER_POS
                stop_distance = config.ATR_STOP_MULTIPLIER * atr14
                qty_by_risk = int(target_risk_dollars / stop_distance)
                qty_by_pct  = int(net_liq * POSITION_PCT / sig['close'])
                qty = min(qty_by_risk, qty_by_pct) if qty_by_risk > 0 else qty_by_pct
                sizing_note = (f"风险法 ${target_risk_dollars:,.0f}÷ATR止损${stop_distance:.2f}={qty_by_risk}股"
                               f"  上限{POSITION_PCT:.0%}={qty_by_pct}股  → {qty}股")
            else:
                qty = int(budget_per_pos / sig['close'])
                sizing_note = f"固定比例 ${budget_per_pos:,.0f}÷${sig['close']:.2f}={qty}股"
            if qty <= 0:
                print(f"  [{symbol}] 单价 ${sig['close']:.2f} 超出预算，跳过")
                continue
            cost = qty * sig['close']
            # OPG 时改用限价单，防止开盘跳空过高追入
            if tif == 'OPG':
                limit_price = round(sig['close'] * (1 + MAX_ENTRY_SLIPPAGE), 2)
                ind_display = sig.get('industry') or sec
                ai_tag = '  ⭐AI优先' if sig.get('ai_priority') else ''
                print(f"  [{symbol}]  RS={sig['rs_score']:+.3f}  量比={sig['vol_ratio']:.1f}x  行业={ind_display}{ai_tag}")
                print(f"    {sizing_note}  限价 ${limit_price:.2f}  预计成本 ${qty*limit_price:,.0f}")
                if not dry_run:
                    _cancel_existing(symbol, 'BUY')
                    trade = trader.limit_buy(symbol, qty, price=limit_price, tif=tif)
                    if trade is None:
                        print(f"  → [ERROR] {symbol} 买入单提交失败，请手动处理！")
                    else:
                        print(f"  → 已下限价 OPG 单（开盘超过 ${limit_price:.2f} 则自动放弃）")
                        opg_buy_trades.append((symbol, qty, limit_price, trade))
                else:
                    print(f"  → [dry-run] 将下限价 OPG 单 ${limit_price:.2f}")
            else:
                ind_display = sig.get('industry') or sec
                ai_tag = '  ⭐AI优先' if sig.get('ai_priority') else ''
                print(f"  [{symbol}]  RS={sig['rs_score']:+.3f}  量比={sig['vol_ratio']:.1f}x  行业={ind_display}{ai_tag}")
                print(f"    {sizing_note}  拟成本 ${cost:,.0f}")
                if not dry_run:
                    _cancel_existing(symbol, 'BUY')
                    trade = trader.market_buy(symbol, qty, tif=tif)
                    if trade is None:
                        print(f"  → [ERROR] {symbol} 买入单提交失败，请手动处理！")
                    else:
                        print(f"  → 已下单 [{tif_label}]")
                else:
                    print(f"  → [dry-run] 将下 {tif_label}")
            # 占用一个全局槽 + 按池规则占用行业/池配额(AI 池豁免行业计数)
            alloc.commit(sig)

        # 在线路径：等待开盘集合竞价完成，检测并补充部分成交订单
        if opg_buy_trades:
            _handle_opg_partial_fills(ib, trader, opg_buy_trades)


def main():
    parser = argparse.ArgumentParser(description='RS 动量策略自动交易')
    parser.add_argument('--run',      action='store_true', help='正式下单（默认 dry-run）')
    parser.add_argument('--dry-run',  action='store_true', help='只显示信号，不下单')
    parser.add_argument('--exits-only', action='store_true',
                        help='只执行止损/卖出出场（不买入）。开盘后(9:35 ET)运行以 DAY 单可即时成交，替代不成交的盘前 OPG 出场')
    parser.add_argument('--held',     nargs='+', default=[], help='当前持仓（用于卖出报警）')
    parser.add_argument('--extra',    nargs='+', default=[], help='追加股票（如 SNDK TSM）')
    parser.add_argument('--min-cap',  type=float, default=config.MIN_CAP_B,
                        help=f'最小市值（十亿USD），默认 {config.MIN_CAP_B}B')
    parser.add_argument('--max-cap',  type=float, default=config.MAX_CAP_B,
                        help=f'最大市值（十亿USD），默认 {config.MAX_CAP_B}B')
    parser.add_argument('--deny-industry', nargs='+', default=config.DENY_INDUSTRIES,
                        help='拒绝行业关键词（模糊匹配）')
    parser.add_argument('--universe', default='ai',
                        help='股票池：ai(默认，data/ai_universe.json) / sp500+ndx / sp500 / nasdaq100')
    args = parser.parse_args()

    dry_run         = not args.run
    held            = [s.upper() for s in args.held]
    extra           = [s.upper() for s in args.extra]
    deny_industries = args.deny_industry or []
    now_bj          = datetime.now().strftime('%Y-%m-%d %H:%M')
    mode            = "dry-run（不下单）" if dry_run else "正式执行（将下单）"
    if args.exits_only:
        mode += " · 仅出场"

    print(f"\n{'='*54}")
    print(f"  RS 动量自动交易  [{mode}]")
    print(f"  北京时间：{now_bj}")
    print(f"  股票池：sp500+ndx  |  "
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
    execute(signals, dry_run=dry_run, exits_only=args.exits_only)

    # 发送信号通知（若已配置 NOTIFY_EMAIL_TO）
    try:
        from core.notifier import send_signal_summary
        buy_list  = [{'symbol': s, 'rs_score': signals.get('_scores', {}).get(s, 0)}
                     for s in signals.get('buy', [])]
        sell_list = [{'symbol': s, 'reason': signals.get('_reasons', {}).get(s, '')}
                     for s in signals.get('sell', [])]
        send_signal_summary(buy_list, sell_list, dry_run=dry_run)
    except Exception:
        pass


if __name__ == '__main__':
    main()
