"""
测试夹具：硬编码的历史订单样本和账户初始状态。

不依赖数据库或网络连接，直接在测试代码中导入使用。
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────
#  6 个月历史订单样本
#  字段说明：
#    symbol      股票代码
#    action      'BUY' 或 'SELL'
#    quantity    委托数量（股）
#    price       委托价格 / 参考收盘价
#    fill_ratio  成交比例（1.0=完全成交, 0.5=部分成交, 0.0=零成交）
#    date        下单日期（T日信号 → T+1 OPG 成交）
# ──────────────────────────────────────────────────────────

SAMPLE_ORDERS_6M: list[dict] = [
    {'symbol': 'NVDA', 'action': 'BUY',  'quantity': 50,  'price': 480.0,  'fill_ratio': 1.0, 'date': '2024-10-01'},
    {'symbol': 'AAPL', 'action': 'BUY',  'quantity': 60,  'price': 225.0,  'fill_ratio': 1.0, 'date': '2024-10-03'},
    {'symbol': 'MSFT', 'action': 'BUY',  'quantity': 30,  'price': 420.0,  'fill_ratio': 0.5, 'date': '2024-10-07'},
    {'symbol': 'GOOGL','action': 'BUY',  'quantity': 40,  'price': 175.0,  'fill_ratio': 1.0, 'date': '2024-10-10'},
    {'symbol': 'META', 'action': 'BUY',  'quantity': 25,  'price': 580.0,  'fill_ratio': 0.0, 'date': '2024-10-15'},
    {'symbol': 'NVDA', 'action': 'SELL', 'quantity': 50,  'price': 510.0,  'fill_ratio': 1.0, 'date': '2024-11-01'},
    {'symbol': 'TSM',  'action': 'BUY',  'quantity': 80,  'price': 190.0,  'fill_ratio': 1.0, 'date': '2024-11-05'},
    {'symbol': 'AAPL', 'action': 'SELL', 'quantity': 60,  'price': 230.0,  'fill_ratio': 1.0, 'date': '2024-11-10'},
    {'symbol': 'AMAT', 'action': 'BUY',  'quantity': 100, 'price': 200.0,  'fill_ratio': 1.0, 'date': '2024-11-15'},
    {'symbol': 'KLAC', 'action': 'BUY',  'quantity': 20,  'price': 850.0,  'fill_ratio': 1.0, 'date': '2024-11-20'},
    {'symbol': 'LRCX', 'action': 'BUY',  'quantity': 15,  'price': 990.0,  'fill_ratio': 1.0, 'date': '2024-11-25'},
]

# ──────────────────────────────────────────────────────────
#  行业映射（GICS Sector，用于 MAX_PER_SECTOR 测试）
# ──────────────────────────────────────────────────────────

SECTOR_MAP: dict[str, str] = {
    'NVDA':  'Semiconductors',
    'AMAT':  'Semiconductors',
    'KLAC':  'Semiconductors',
    'LRCX':  'Semiconductors',
    'TSM':   'Semiconductors',
    'AAPL':  'Technology',
    'MSFT':  'Technology',
    'GOOGL': 'Technology',
    'META':  'Communication Services',
}

# ──────────────────────────────────────────────────────────
#  账户初始状态（对应 $60K 资金配置）
# ──────────────────────────────────────────────────────────

ACCOUNT_STATE: dict[str, float] = {
    'net_liq':      60_000.0,
    'cash':         45_000.0,
    'buying_power': 45_000.0,
}

# ──────────────────────────────────────────────────────────
#  便捷工厂：从 SAMPLE_ORDERS_6M 构建 MockIB 所需的信号 dict
# ──────────────────────────────────────────────────────────

def make_buy_signals(symbols: list[str] | None = None) -> list[dict]:
    """
    从 SAMPLE_ORDERS_6M 提取 BUY 记录，构建 auto_trader 风格的 buy_signals 列表。

    返回格式与 auto_trader.scan_signals() 返回的 buy 列表一致：
      [{'symbol', 'rs_score', 'close', 'vol_ratio', 'drawdown_from_high',
        'market_cap_b', 'industry', 'sector', 'insider_score'}, ...]
    """
    buys = [o for o in SAMPLE_ORDERS_6M if o['action'] == 'BUY' and o['fill_ratio'] > 0]
    if symbols:
        sym_set = {s.upper() for s in symbols}
        buys = [o for o in buys if o['symbol'] in sym_set]

    result = []
    for o in buys:
        sym = o['symbol']
        result.append({
            'symbol':            sym,
            'rs_score':          0.05,          # 虚拟 RS 得分（>0 表示跑赢 SPY）
            'close':             o['price'],
            'vol_ratio':         1.8,            # 虚拟量比（>1.5 满足放量条件）
            'drawdown_from_high': -0.10,         # 距高点 -10%（满足崩跌过滤）
            'market_cap_b':      50.0,
            'industry':          SECTOR_MAP.get(sym, 'Unknown'),
            'sector':            SECTOR_MAP.get(sym, 'Unknown'),
            'insider_score':     0,
        })
    return result


def make_signals_dict(
    buy_symbols: list[str] | None = None,
    sell_symbols: list[str] | None = None,
    prices: dict[str, float] | None = None,
) -> dict:
    """
    构建完整的 signals dict，格式与 auto_trader.scan_signals() 返回值一致。

    参数：
      buy_symbols   需要包含的买入信号 symbol 列表（None = 用所有 BUY 样本）
      sell_symbols  需要包含的卖出报警 symbol 列表
      prices        价格覆盖（不传时用 SAMPLE_ORDERS_6M 的 price 字段）
    """
    buy_signals = make_buy_signals(buy_symbols)

    # 构建价格 map
    price_map: dict[str, float] = {}
    for o in SAMPLE_ORDERS_6M:
        price_map[o['symbol']] = o['price']
    if prices:
        price_map.update(prices)

    # 卖出报警
    sell_alerts = []
    for sym in (sell_symbols or []):
        sell_alerts.append({
            'symbol': sym.upper(),
            'close':  price_map.get(sym.upper(), 100.0),
            'reason': '量价背离（新高缩量）',
        })

    return {
        'buy':          buy_signals,
        'sell':         sell_alerts,
        '_prices':      price_map,
        '_atr':         {sym: price_map.get(sym, 100.0) * 0.02 for sym in price_map},
        '_signal_map':  {},
        'spy_brake':    False,
        '_vix':         18.0,
        '_vix_brake':   False,
        '_breadth':     0.65,
        '_breadth_cap': False,
        '_mss':         0.6,
    }
