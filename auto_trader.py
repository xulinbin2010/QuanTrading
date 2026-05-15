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

def _load_ai_boost_map() -> dict[str, float]:
    """
    返回 {symbol: ai_boost} 供 _entry_score 使用。

    boost 计算规则：
      - 在 ai_universe.json 中且有 AI 追踪器评分（0–15）：boost = (score/15) * 0.20
      - 在 ai_universe.json 中但无评分缓存：                boost = 0.10（基础加成）
      - 不在 AI 股票池：                                     boost = 0.0

    最终对 rs_score 乘数项加成，最高 +20%。
    """
    import json
    from pathlib import Path
    root = Path(__file__).parent
    boost_map: dict[str, float] = {}

    # 读取 AI 股票池（确定成员资格）
    universe_file = root / 'data' / 'ai_universe.json'
    if not universe_file.exists():
        return boost_map
    try:
        u = json.loads(universe_file.read_text(encoding='utf-8'))
        for gv in u.get('groups', {}).values():
            for s in gv.get('symbols', []):
                boost_map[s.upper()] = 0.10   # 基础加成
    except Exception:
        return boost_map

    if not boost_map:
        return boost_map

    # 叠加 AI 追踪器评分（有缓存则用，无则保持基础值）
    cache_file = root / 'data' / 'ai_tracker_cache.json'
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding='utf-8'))
            score_max = 15
            for row in cached.get('rows', []):
                sym   = row.get('symbol', '').upper()
                score = row.get('score')
                if sym in boost_map and score is not None:
                    boost_map[sym] = min((score / score_max) * 0.20, 0.20)
        except Exception:
            pass

    return boost_map


def scan_signals(
    held_symbols:     list[str],
    extra:            list[str] = None,
    universe:         str       = 'sp500+ndx',
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

    all_syms = list(set(tickers + ['SPY', '^VIX']))
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
    strategy = RSMomentum(vol_shrink_ratio=config.VOL_SHRINK_RATIO)
    strategy.set_spy(spy_close)

    buy_signals = []
    sell_alerts = []
    prices      = {}
    atr_map     = {}   # symbol → ATR14 绝对值（用于自适应止损计算）
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

    # ── AI 关联度加成（一次性加载，后续 _entry_score 直接读 sig['ai_boost']）──
    ai_boost_map = _load_ai_boost_map()
    if ai_boost_map:
        ai_cnt = sum(1 for s in buy_signals if s['symbol'] in ai_boost_map)
        if ai_cnt:
            print(f"  [AI关联] {ai_cnt} 只候选在 AI 股票池，入场优先级加成最高 +20%")
    for sig in buy_signals:
        sig['ai_boost'] = ai_boost_map.get(sig['symbol'], 0.0)

    # ── 复合入场得分排序（过滤后执行，insider_score / sector 已就位）────────────
    def _entry_score(sig: dict) -> float:
        rs       = sig.get('rs_score', 0)
        vol      = sig.get('vol_ratio', 1.0)
        drawdown = sig.get('drawdown_from_high', -0.15)   # 负数，越接近 0 越强
        insider  = sig.get('insider_score', 0)
        ai       = sig.get('ai_boost', 0.0)
        # 量比加成：最高 +15%，3x 以上不再加分（避免一次性暴量失真）
        vol_boost       = min(vol / 3.0, 1.0) * 0.15
        # 近高点加成：drawdown=0% → +10%；drawdown=-30% → +0%
        proximity_boost = max(0.0, (drawdown + 0.30) / 0.30) * 0.10
        # 内幕加成：有内幕买入 → +10%（上限）
        insider_boost   = min(insider / 10.0, 1.0) * 0.10
        # AI 关联加成：在 AI 池中基础 +10%，有追踪器评分最高 +20%
        return rs * (1 + vol_boost + proximity_boost + insider_boost + ai)

    buy_signals.sort(key=_entry_score, reverse=True)

    # ── 财报日期预取（批量缓存，execute 阶段直接用缓存）────────────────────────
    if buy_signals and config.EARNINGS_AVOID_DAYS > 0:
        prefetch_earnings([s['symbol'] for s in buy_signals])

    # ── 市场强度评分（MSS）────────────────────────────────────────────────────
    mss = compute_mss(spy_close, vix_close, breadth_pct)
    print(f"  MSS={mss:.2f} ({mss_label(mss)})  "
          f"[SPY趋势/市场宽度{'' if breadth_pct is None else f'={breadth_pct:.0%}'}/VIX{'' if vix_close is None else f'={vix_close:.1f}'}]")

    return {'buy': buy_signals, 'sell': sell_alerts, '_prices': prices, '_atr': atr_map,
            '_signal_map': signal_map, 'spy_brake': spy_brake,
            '_vix': vix_close, '_vix_brake': vix_brake,
            '_breadth': breadth_pct, '_breadth_cap': breadth_cap,
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


def execute(signals: dict, dry_run: bool = True):
    """连接 IB Gateway，根据信号执行买卖单"""
    db   = Database()
    db.connect()
    conn = IBConnection()
    ib   = conn.connect()
    try:
        _execute_inner(signals, dry_run, db, conn, ib)
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
        db.close()


def _execute_inner(signals: dict, dry_run: bool, db, conn, ib):

    account   = Account(ib, db=db)
    trader    = Trading(ib, db=db)
    tif       = get_order_tif(ib)
    tif_label = {'DAY': '盘中市价单', 'OPG': '开盘集合竞价单(OPG)'}[tif]
    print(f"  订单类型：{tif_label}")

    # ── MSS 自适应：根据市场强度动态覆盖止损/仓位参数 ────────────────────────
    # MSS_BULL/BEAR_MAX_POS 默认与 MAX_POSITIONS 一致，防止静默扩/缩仓位
    mss = signals.get('_mss', 0.0)
    bull_thr = getattr(config, 'MSS_BULL_THRESHOLD',      0.5)
    bear_thr = getattr(config, 'MSS_BEAR_THRESHOLD',      0.0)
    if mss >= bull_thr:
        eff_max_pos        = getattr(config, 'MSS_BULL_MAX_POS',        config.MAX_POSITIONS)
        eff_trail_activate = getattr(config, 'MSS_BULL_TRAIL_ACTIVATE', 0.15)
        eff_trail_pct      = getattr(config, 'MSS_BULL_TRAIL_PCT',      -0.10)
    elif mss < bear_thr:
        eff_max_pos        = getattr(config, 'MSS_BEAR_MAX_POS',        config.MAX_POSITIONS)
        eff_trail_activate = getattr(config, 'MSS_BEAR_TRAIL_ACTIVATE', 0.06)
        eff_trail_pct      = getattr(config, 'MSS_BEAR_TRAIL_PCT',      -0.06)
    else:
        eff_max_pos        = config.MAX_POSITIONS
        eff_trail_activate = config.TRAIL_STOP_ACTIVATE_PCT
        eff_trail_pct      = config.TRAIL_STOP_PCT
    print(f"  MSS={mss:.2f} ({mss_label(mss)}) → 仓位上限={eff_max_pos}  "
          f"移动止损激活={eff_trail_activate:.0%} 触发={eff_trail_pct:.0%}")

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

    # ── 预加载持仓历史数据（用于移动止损峰值计算，与因子看板逻辑一致）──
    from core.data_store import DataStore
    held_syms   = [s for s in stock_positions if s not in CASH_EQUIV]
    dl_start    = (date.today() - timedelta(days=400)).strftime('%Y-%m-%d')
    held_data   = DataStore().get(held_syms, start=dl_start, end=date.today().strftime('%Y-%m-%d'),
                                  auto_update=False) if held_syms else {}

    # ── 止损检查 ──────────────────────────────────────────────
    stop_loss_list = []
    for sym, pos in stock_positions.items():
        avg_cost  = pos.avgCost
        qty       = int(pos.position)
        cur_price = signals.get('_prices', {}).get(sym)
        if avg_cost <= 0 or qty <= 0 or cur_price is None:
            continue
        ret = (cur_price - avg_cost) / avg_cost
        # ATR 自适应止损：止损价 = 入场价 - N×ATR14，最大亏损不超过 ATR_STOP_FLOOR
        atr14 = signals.get('_atr', {}).get(sym)
        if atr14 is not None and atr14 > 0 and avg_cost > 0:
            atr_stop_pct = max(config.ATR_STOP_FLOOR, -(config.ATR_STOP_MULTIPLIER * atr14 / avg_cost))
        else:
            atr_stop_pct = STOP_LOSS_PCT  # 无ATR数据时回退固定止损
        if ret <= atr_stop_pct:
            stop_loss_list.append({
                'symbol': sym, 'qty': qty,
                'avg_cost': avg_cost, 'cur_price': cur_price, 'return': ret,
                'stop_pct': atr_stop_pct,
            })

    print(f"\n{'='*54}")
    if stop_loss_list:
        print(f"  止损触发（ATR自适应止损）：{len(stop_loss_list)} 只")
        print(f"{'='*54}")
        for s in stop_loss_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f} → 现价 ${s['cur_price']:.2f}  "
                  f"浮亏 {s['return']:.1%}（止损线 {s['stop_pct']:.1%}）")
            _place_sell(s['symbol'], s['qty'], s['cur_price'], '止损单', 0.95)
    else:
        print(f"  无止损触发")
        print(f"{'='*54}")
    exit_orders.extend(stop_loss_list)

    # ── EMA 破位止损检查 ─────────────────────────────────────
    # 强牛市利器：收盘跌破短期 EMA 立即出场，比移动止损更早触发
    already_hard_stop = {o['symbol'] for o in exit_orders}
    ema_period       = int(getattr(config, 'EMA_STOP_PERIOD', 8) or 0)
    ema_stop_list    = []
    if ema_period > 0:
        for sym, pos in stock_positions.items():
            if sym in already_hard_stop or sym in CASH_EQUIV:
                continue
            qty       = int(pos.position)
            cur_price = signals.get('_prices', {}).get(sym)
            if qty <= 0 or cur_price is None:
                continue
            df = held_data.get(sym)
            if df is None or len(df) < ema_period + 1:
                continue
            try:
                ema_today = float(df['close'].ewm(span=ema_period, adjust=False).mean().iloc[-1])
            except Exception:
                continue
            # 现价已跌破今日 EMA → 触发
            if cur_price < ema_today:
                ema_stop_list.append({
                    'symbol': sym, 'qty': qty,
                    'avg_cost': pos.avgCost, 'cur_price': cur_price,
                    'ema':      ema_today,
                    'return':   (cur_price - pos.avgCost) / pos.avgCost if pos.avgCost > 0 else 0,
                })

    print(f"\n{'='*54}")
    if ema_stop_list:
        print(f"  EMA{ema_period}破位止损触发：{len(ema_stop_list)} 只")
        print(f"{'='*54}")
        for s in ema_stop_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f}  现价 ${s['cur_price']:.2f}  "
                  f"EMA{ema_period} ${s['ema']:.2f}  {s['return']:+.1%}")
            _place_sell(s['symbol'], s['qty'], s['cur_price'], f'EMA{ema_period}止损单', 0.95)
    elif ema_period > 0:
        print(f"  无 EMA{ema_period} 破位止损触发")
        print(f"{'='*54}")
    exit_orders.extend(ema_stop_list)

    # ── 移动止损检查 ──────────────────────────────────────────
    already_hard_stop = {o['symbol'] for o in exit_orders}
    trail_stop_list   = []
    for sym, pos in stock_positions.items():
        if sym in already_hard_stop or sym in CASH_EQUIV:
            continue
        avg_cost  = pos.avgCost
        qty       = int(pos.position)
        cur_price = signals.get('_prices', {}).get(sym)
        if avg_cost <= 0 or qty <= 0 or cur_price is None:
            continue
        ed        = _entry_date(sym, db)   # 只看入场日之后的收盘，避免旧历史高点误触发
        peak      = _peak_price(sym, avg_cost, held_data.get(sym), entry_date=ed)
        peak_ret  = (peak - avg_cost) / avg_cost
        trail_ret = (cur_price - peak) / peak
        if peak_ret >= eff_trail_activate and trail_ret <= eff_trail_pct:
            trail_stop_list.append({
                'symbol': sym, 'qty': qty,
                'avg_cost': avg_cost, 'cur_price': cur_price,
                'peak': peak, 'peak_ret': peak_ret, 'trail_ret': trail_ret,
            })

    print(f"\n{'='*54}")
    if trail_stop_list:
        print(f"  移动止损触发（浮盈>{eff_trail_activate:.0%}后从峰值回撤>{abs(eff_trail_pct):.0%}）：{len(trail_stop_list)} 只")
        print(f"{'='*54}")
        for s in trail_stop_list:
            print(f"  [{s['symbol']}]  均价 ${s['avg_cost']:.2f}  峰值 ${s['peak']:.2f}"
                  f"（+{s['peak_ret']:.1%}）→ 现价 ${s['cur_price']:.2f}  回撤 {s['trail_ret']:.1%}")
            _place_sell(s['symbol'], s['qty'], s['cur_price'], '移动止损单', 0.95)
    else:
        print(f"  无移动止损触发")
        print(f"{'='*54}")
    exit_orders.extend(trail_stop_list)

    # ── 时间止损检查（Time Stop）─────────────────────────────
    already_stopped = {o['symbol'] for o in exit_orders}
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
            _place_sell(s['symbol'], s['qty'], s['cur_price'], '时间止损单', 0.97)
    else:
        print(f"  无时间止损触发")
        print(f"{'='*54}")
    exit_orders.extend(time_stop_list)

    # ── 卖出报警（量价背离）──────────────────────────────────
    # 用真实 IB 持仓补充：--held 未传入的股票也能被检测到
    sell_list      = list(signals.get('sell', []))
    signal_map     = signals.get('_signal_map', {})
    already_alerted = {s['symbol'] for s in sell_list} | {o['symbol'] for o in exit_orders}
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
                _place_sell(s['symbol'], qty, s['close'], '卖出报警单', 0.97)
    else:
        print(f"  持仓无卖出报警")
        print(f"{'='*54}")

    # ── 重新计算可用仓位和资金（含本次止损/卖出释放的仓位和回笼资金）──
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
        # 统计当前持仓的行业分布
        sector_counts: dict[str, int] = {}
        if stock_positions:
            pos_info = get_stock_info(list(stock_positions.keys()))
            for sym in stock_positions:
                if sym in exiting_syms:
                    continue  # 本次已触发止损/卖出，不计入行业占用
                sec = (pos_info.get(sym, {}).get('sector') or 'Unknown')
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

        executed = 0
        opg_buy_trades: list = []   # [(symbol, orig_qty, limit_price, trade)] 供补单监控用
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
                ai_tag = f"  🤖AI+{sig.get('ai_boost', 0)*100:.0f}%" if sig.get('ai_boost') else ''
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
                ai_tag = f"  🤖AI+{sig.get('ai_boost', 0)*100:.0f}%" if sig.get('ai_boost') else ''
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
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            executed += 1

        # 在线路径：等待开盘集合竞价完成，检测并补充部分成交订单
        if opg_buy_trades:
            _handle_opg_partial_fills(ib, trader, opg_buy_trades)


def main():
    parser = argparse.ArgumentParser(description='RS 动量策略自动交易')
    parser.add_argument('--run',      action='store_true', help='正式下单（默认 dry-run）')
    parser.add_argument('--dry-run',  action='store_true', help='只显示信号，不下单')
    parser.add_argument('--held',     nargs='+', default=[], help='当前持仓（用于卖出报警）')
    parser.add_argument('--extra',    nargs='+', default=[], help='追加股票（如 SNDK TSM）')
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
        held, extra=extra, universe='sp500+ndx',
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
