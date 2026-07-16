"""
股票池管理。优先从 Wikipedia 拉最新成分股，失败则用内置列表。
支持：S&P 500 / NASDAQ-100 / S&P500+NDX合并池
"""
import io
import os
import pickle
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 内置 S&P 500 列表 ─────────────────────────────────────────────
_BUILTIN_SP500 = [
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','GOOG','TSLA','AVGO','ORCL',
    'CRM','ADBE','NOW','INTU','PANW','SNPS','CDNS','ANET','ROP','FTNT',
    'CTSH','EPAM','GDDY','AKAM','CDW','PAYC','LDOS',
    'AMD','INTC','QCOM','TXN','ADI','LRCX','KLAC','AMAT','MRVL','ON',
    'MPWR','MU','SNDK',
    'PLTR','DDOG','MDB','SNOW','ZS','CRWD','NET','HUBS',
    'COST','WMT','HD','TGT','LOW','NKE','SBUX','MCD','YUM',
    'BKNG','ABNB','MAR','HLT','CCL','RCL',
    'JPM','BAC','WFC','GS','MS','BLK','SPGI','ICE','CME','AXP',
    'V','MA','PYPL','USB','TFC','PNC','COF','DFS',
    'UNH','JNJ','LLY','ABBV','MRK','BMY','AMGN','GILD','REGN','VRTX',
    'TMO','ABT','MDT','ISRG','IDXX','ZTS','DXCM','BIIB','MRNA','PFE',
    'CI','HUM','CVS','MCK','ELV',
    'XOM','CVX','COP','EOG','SLB','PSX','VLO','MPC','OXY','HAL',
    'CAT','DE','GE','HON','RTX','LMT','BA','MMM','UPS','FDX',
    'ITW','EMR','ETN','PH','ROK','XYL',
    'PG','KO','PEP','PM','MO','CL','KMB','GIS','K','CPB',
    'NEE','DUK','SO','AEP','EXC','SPG','PLD','AMT','CCI','EQIX',
    'NFLX','DIS','CMCSA','T','VZ','TMUS','CHTR','WBD',
    'LIN','APD','SHW','ECL','NEM','FCX','NUE','VMC','MLM',
    'BRK-B','MCO','MSCI','VRSK','CARR','OTIS','TT','IR',
]

# ── 内置 NASDAQ-100 列表 ──────────────────────────────────────────
_BUILTIN_NDX = [
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','GOOG','TSLA','AVGO','COST',
    'NFLX','AMD','INTU','CSCO','AMGN','ISRG','TXN','QCOM','AMAT','HON',
    'BKNG','SBUX','VRTX','GILD','ADP','PANW','INTC','LRCX','MU','ADI',
    'REGN','MDLZ','MELI','ASML','KLAC','SNPS','CDNS','FTNT','CEG','MAR',
    'PYPL','CRWD','ABNB','CTAS','ORLY','MNST','PCAR','MRVL','WDAY','MRNA',
    'ADSK','DXCM','PAYX','KDP','FAST','IDXX','VRSK','ODFL','EXC','BIIB',
    'ROST','CPRT','FANG','CTSH','DLTR','ON','GEHC','TTD','SGEN','WBD',
    'ZS','DDOG','TEAM','OKTA','ALGN','ILMN','LCID','RIVN','ZM',
]

_BUILTIN_SP500 = sorted(set(_BUILTIN_SP500))
_BUILTIN_NDX   = sorted(set(_BUILTIN_NDX))


# ── 公共获取函数 ──────────────────────────────────────────────────

def get_sp500_tickers(extra: list[str] = None) -> list[str]:
    # Wikipedia 优先：iShares 官网 2026-07 起对 CSV 端点上了 JS bot 防护，
    # requests 直接抓取只会拿到 HTML 挑战页，IVV 降级为兜底。
    tickers = (_try_wikipedia(
                   url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
                   table_id='constituents',
                   col='Symbol',
                   label='S&P 500',
               )
               or _try_ivv_holdings()
               or list(_BUILTIN_SP500))
    return _append_extra(tickers, extra)


def get_nasdaq100_tickers(extra: list[str] = None) -> list[str]:
    tickers = _try_wikipedia(
        url='https://en.wikipedia.org/wiki/Nasdaq-100',
        table_id='constituents',
        col='Ticker',
        label='NASDAQ-100',
    ) or list(_BUILTIN_NDX)
    return _append_extra(tickers, extra)



_STOCK_INFO_CACHE    = os.path.join(os.path.dirname(__file__), '..', '.stock_info_cache.pkl')
_STOCK_INFO_TTL      = timedelta(days=1)   # 市值每日刷新
_STOCK_INFO_FIELDS   = {                   # 完整字段集，缺字段的旧条目视为需要重查
    'market_cap_b', 'industry', 'sector',
    'revenue_growth', 'earnings_growth', 'roe', 'debt_to_equity',
    'free_cashflow', 'gross_margins', 'pe_ratio', 'pb_ratio', 'ps_ratio',
    'shares_out', 'eps_ttm', 'book_value_ps', 'rev_ps',
}


_INFO_EMPTY = {
    'market_cap_b': None, 'industry': None, 'sector': None,
    'revenue_growth': None, 'earnings_growth': None,
    'roe': None, 'debt_to_equity': None, 'free_cashflow': None,
    'gross_margins': None,
    'pe_ratio': None, 'pb_ratio': None, 'ps_ratio': None,
    'shares_out': None, 'eps_ttm': None, 'book_value_ps': None, 'rev_ps': None,
}


def _fetch_one_info(sym: str) -> tuple[str, dict]:
    """单只股票 info 请求，供并发调用。

    价格衍生字段（市值/PE/PB/PS）这里只存 yfinance 原值作兜底；真正返回时
    会用本地 parquet 最新价 × 每股慢变量重算（见 _reprice_with_local），
    保证与选股/回测的 parquet 价格同源，不再出现两套价格串台。
    """
    try:
        info = yf.Ticker(sym).info
        mc   = info.get('marketCap')
        de   = info.get('debtToEquity')
        return sym, {
            'industry':        info.get('industry'),
            'sector':          info.get('sector'),
            'revenue_growth':  info.get('revenueGrowth'),
            'earnings_growth': info.get('earningsGrowth'),
            'roe':             info.get('returnOnEquity'),
            'debt_to_equity':  round(de / 100, 4) if de is not None else None,
            'free_cashflow':   info.get('freeCashflow'),
            'gross_margins':   info.get('grossMargins'),
            # 每股慢变量：用于与本地价同源重算市值/PE/PB/PS
            'shares_out':      info.get('sharesOutstanding'),
            'eps_ttm':         info.get('trailingEps'),
            'book_value_ps':   info.get('bookValue'),
            'rev_ps':          info.get('revenuePerShare'),
            # yfinance 原值：本地无 parquet 时兜底
            'market_cap_b':    round(mc / 1e9, 1) if mc else None,
            'pe_ratio':        info.get('trailingPE'),
            'pb_ratio':        info.get('priceToBook'),
            'ps_ratio':        info.get('priceToSalesTrailing12Months'),
        }
    except Exception:
        return sym, dict(_INFO_EMPTY)


def _local_last_price(sym: str) -> float | None:
    """读本地 parquet 最新收盘价（与选股/回测同源）。无文件/读失败返回 None。"""
    f = os.path.normpath(os.path.join(
        os.path.dirname(__file__), '..', 'data', 'stocks', f'{sym}.parquet'))
    try:
        df = pd.read_parquet(f, columns=['close'])
        px = float(df['close'].iloc[-1])
        return px if px > 0 else None
    except Exception:
        return None


def _reprice_with_local(sym: str, d: dict) -> dict:
    """用本地最新价重算市值/PE/PB/PS，使其与 parquet 价格同源。

    本地无价时保留 yfinance 原值兜底；EPS≤0（亏损）时 PE 置 None。
    """
    px = _local_last_price(sym)
    if px is None:
        return d
    out = dict(d)
    sh  = d.get('shares_out')
    eps = d.get('eps_ttm')
    bv  = d.get('book_value_ps')
    rps = d.get('rev_ps')
    if sh:
        out['market_cap_b'] = round(px * sh / 1e9, 1)
    if eps is not None:
        out['pe_ratio'] = round(px / eps, 1) if eps > 0 else None
    if bv and bv > 0:
        out['pb_ratio'] = round(px / bv, 2)
    if rps and rps > 0:
        out['ps_ratio'] = round(px / rps, 2)
    return out


def get_stock_info(symbols: list[str]) -> dict[str, dict]:
    """
    返回 {symbol: {'market_cap_b': float, 'industry': str, 'sector': str, ...}}
    market_cap_b 单位：十亿美元（Billion USD）

    结果本地缓存 1 天（.stock_info_cache.pkl），字段不完整的旧条目自动补刷。
    并发请求（max_workers=20），500 只约 30s，比串行快 20x。
    """
    cache = {}
    cache_path = os.path.normpath(_STOCK_INFO_CACHE)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                stored = pickle.load(f)
            if datetime.now() - stored.get('_time', datetime.min) < _STOCK_INFO_TTL:
                cache = stored.get('data', {})
        except Exception:
            pass

    need = [s for s in symbols
            if s not in cache
            or not _STOCK_INFO_FIELDS.issubset(cache[s].keys())
            or (cache[s].get('industry') is None and cache[s].get('sector') is None)]
    if need:
        print(f"  [信息] 并发查询 {len(need)} 只股票市值/行业（max_workers=20）...")
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(_fetch_one_info, s): s for s in need}
            for fut in as_completed(futs):
                sym, data = fut.result()
                cache[sym] = data
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump({'_time': datetime.now(), 'data': cache}, f)
        except Exception:
            pass

    # 市值/PE/PB/PS 用本地最新价重算，确保与 parquet 价格同源（缓存只存慢变量）
    return {s: _reprice_with_local(s, cache[s]) for s in symbols if s in cache}


def get_sp500_ndx_tickers(extra: list[str] = None) -> list[str]:
    """S&P 500 + NASDAQ 100 合并去重，保留大盘流动性好的股票池。"""
    sp5 = get_sp500_tickers()
    ndx = get_nasdaq100_tickers()
    combined = list(dict.fromkeys(sp5 + [t for t in ndx if t not in set(sp5)]))
    ndx_only = [t for t in ndx if t not in set(sp5)]
    print(f"  合并股票池：S&P500({len(sp5)}) + NDX独有({len(ndx_only)}) = {len(combined)} 只")
    if extra:
        for t in extra:
            if t.upper() not in combined:
                combined.append(t.upper())
    return combined


def get_russell2000_tickers(extra: list[str] = None) -> list[str]:
    """Russell 2000 成分股。主源 Vanguard VTWO 持仓 API（iShares IWM 已被 bot 防护挡住，降级兜底），
    成功后写 data/universe_cache/russell2000.json，全部失败时回退本地缓存。无内置列表。"""
    tickers = _try_vtwo_holdings() or _try_iwm_holdings()
    if tickers:
        _save_universe_cache('russell2000', tickers)
    else:
        tickers = _load_universe_cache('russell2000')
    if not tickers:
        raise ValueError(
            "Russell 2000 股票池获取失败（Vanguard VTWO / iShares IWM 均失败，且无本地缓存）"
        )
    return _append_extra(tickers, extra)


_EXTRA_TICKERS_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'extra_tickers.txt')
)


def load_extra_tickers() -> list[str]:
    """从 data/extra_tickers.txt 读取用户自定义扩展股票，# 注释和空行忽略。"""
    if not os.path.exists(_EXTRA_TICKERS_FILE):
        return []
    tickers = []
    with open(_EXTRA_TICKERS_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if line:
                tickers.append(line.upper())
    return tickers


def _rewrite_extra_file(clean_tickers: list[str]) -> None:
    """回写 extra_tickers.txt，保留注释行和空行，只保留 clean_tickers 中的 ticker（每个只留首次出现）。"""
    if not os.path.exists(_EXTRA_TICKERS_FILE):
        return
    remaining = {t.upper() for t in clean_tickers}
    with open(_EXTRA_TICKERS_FILE, encoding='utf-8') as f:
        lines = f.readlines()
    new_lines = []
    for line in lines:
        stripped = line.split('#', 1)[0].strip()
        if stripped:
            sym = stripped.upper()
            if sym in remaining:
                new_lines.append(line)
                remaining.discard(sym)  # 只保留首次出现，后续重复跳过
        else:
            new_lines.append(line)
    with open(_EXTRA_TICKERS_FILE, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)


def get_ai_tickers() -> list[str]:
    """从 data/ai_universe.json 读取 AI 产业链全股票池（去重，保留分组顺序）。"""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'data' / 'ai_universe.json'
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    seen: set[str] = set()
    result: list[str] = []
    for gv in data.get('groups', {}).values():
        for s in gv.get('symbols', []):
            if s not in seen:
                seen.add(s)
                result.append(s)
    return result


def get_tickers(universe: str = 'sp500', extra: list[str] = None) -> list[str]:
    """统一入口，按名称选择股票池。自动合并 data/extra_tickers.txt 中的自定义股票。"""
    # AI 产业链股票池自管理，不走 extra_tickers.txt
    if universe.lower() == 'ai':
        tickers = get_ai_tickers()
        if extra:
            seen = set(tickers)
            for t in extra:
                if t not in seen:
                    tickers.append(t)
                    seen.add(t)
        return tickers

    mapping = {
        'sp500+ndx':   get_sp500_ndx_tickers,
        'sp500':       get_sp500_tickers,
        'nasdaq100':   get_nasdaq100_tickers,
        'ndx':         get_nasdaq100_tickers,
        'russell2000': get_russell2000_tickers,
        'iwm':         get_russell2000_tickers,
    }
    fn = mapping.get(universe.lower())
    if fn is None:
        raise ValueError(f"未知股票池：{universe}，可选：sp500+ndx / sp500 / nasdaq100 / russell2000 / ai")

    # 先获取基础股票池（不含 file extra）
    base = fn(extra=extra)
    base_set = set(base)

    # 加载并去重 extra_tickers.txt
    file_extra = load_extra_tickers()
    seen: set[str] = set()
    clean: list[str] = []
    dups_internal: list[str] = []
    dups_base: list[str] = []
    for t in file_extra:
        if t in seen:
            dups_internal.append(t)
        elif t in base_set:
            dups_base.append(t)
            seen.add(t)
        else:
            seen.add(t)
            clean.append(t)

    if dups_internal or dups_base:
        parts = []
        if dups_internal:
            parts.append(f"文件内重复 {dups_internal}")
        if dups_base:
            parts.append(f"与 {universe} 重复 {dups_base}")
        print(f"  extra_tickers.txt 自动移除 {len(dups_internal)+len(dups_base)} 只：{' | '.join(parts)}")
        _rewrite_extra_file(clean)

    # 将剩余 clean extras 追加到结果
    added = []
    for t in clean:
        if t not in base_set:
            base.append(t)
            base_set.add(t)
            added.append(t)
    if added:
        print(f"  extra_tickers.txt 追加 {len(added)} 只：{added}，股票池合计 {len(base)} 只")

    return base


# ── 内部工具函数 ──────────────────────────────────────────────────

_UNIVERSE_CACHE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'universe_cache')
)


def _save_universe_cache(name: str, tickers: list[str]) -> None:
    """成功抓取后落盘，供数据源全部失效时兜底。"""
    import json
    try:
        os.makedirs(_UNIVERSE_CACHE_DIR, exist_ok=True)
        path = os.path.join(_UNIVERSE_CACHE_DIR, f'{name}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'asof': datetime.now().isoformat(timespec='seconds'),
                       'tickers': tickers}, f)
    except Exception:
        pass


def _load_universe_cache(name: str) -> list[str]:
    import json
    path = os.path.join(_UNIVERSE_CACHE_DIR, f'{name}.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        tickers = data.get('tickers') or []
        if tickers:
            print(f"  [警告] {name} 在线获取失败，使用本地缓存（{data.get('asof', '未知时间')}，{len(tickers)} 只）")
        return tickers
    except Exception:
        return []


def _reject_bot_challenge(resp) -> None:
    """iShares/BlackRock 对 CSV 端点返回 HTTP 200 的 JS bot 防护页，需按内容识别。"""
    head = resp.text[:200].lstrip().lower()
    if head.startswith('<!doctype') or head.startswith('<html'):
        raise ValueError('返回 HTML bot 防护页而非 CSV（iShares 已屏蔽程序化抓取）')


def _try_vtwo_holdings() -> list[str]:
    """从 Vanguard VTWO（Russell 2000 ETF）持仓 API 获取成分股，500 只/页分页拉全。"""
    base = ('https://investor.vanguard.com/investment-products/etfs'
            '/profile/api/VTWO/portfolio-holding/stock')
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    try:
        tickers: list[str] = []
        seen: set[str] = set()
        start, total = 1, None
        while total is None or start <= total:
            resp = requests.get(base, params={'start': start, 'count': 500},
                                headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            total = int(data.get('size', 0))
            entities = (data.get('fund') or {}).get('entity') or []
            if not entities:
                break
            for e in entities:
                t = (e.get('ticker') or '').strip().upper().replace('.', '-')
                if t and t[0].isalpha() and t not in seen:
                    seen.add(t)
                    tickers.append(t)
            start += len(entities)
        if len(tickers) < 1000:   # Russell 2000 正常 ~1900+，过少视为数据异常
            raise ValueError(f'仅解析到 {len(tickers)} 只，疑似接口异常')
        print(f"  Vanguard VTWO 获取成功：{len(tickers)} 只 Russell 2000 成分股")
        return tickers
    except Exception as e:
        print(f"  Vanguard VTWO 请求失败：{e}")
        return []


def _try_ivv_holdings() -> list[str]:
    """从 iShares IVV ETF 官方持仓 CSV 获取 S&P 500 成分股（每日更新，比 Wikipedia 更准确）。"""
    url = (
        'https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf'
        '/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund'
    )
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    # IVV ticker 与 yfinance 格式差异映射
    _TICKER_FIX = {'BRKB': 'BRK-B', 'BFB': 'BF-B'}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        _reject_bot_challenge(resp)
        df = pd.read_csv(io.StringIO(resp.text), skiprows=9)
        stocks = df[df['Asset Class'] == 'Equity']
        tickers = (
            stocks['Ticker']
            .dropna()
            .str.strip()
            .loc[lambda s: s.str.match(r'^[A-Z]')]   # 过滤 '-' 等非ticker占位符
            .map(lambda t: _TICKER_FIX.get(t, t))
            .tolist()
        )
        print(f"  iShares IVV 获取成功：{len(tickers)} 只 S&P 500 成分股")
        return tickers
    except Exception as e:
        print(f"  iShares IVV 请求失败，回退 Wikipedia：{e}")
        return []


def _try_iwm_holdings() -> list[str]:
    """从 iShares IWM ETF 官方持仓 CSV 获取 Russell 2000 成分股。"""
    url = (
        'https://www.ishares.com/us/products/239710/ishares-russell-2000-etf'
        '/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund'
    )
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        _reject_bot_challenge(resp)
        df = pd.read_csv(io.StringIO(resp.text), skiprows=9)
        stocks = df[df['Asset Class'] == 'Equity']
        tickers = (
            stocks['Ticker']
            .dropna()
            .str.strip()
            .loc[lambda s: s.str.match(r'^[A-Z]')]
            .tolist()
        )
        print(f"  iShares IWM 获取成功：{len(tickers)} 只 Russell 2000 成分股")
        return tickers
    except Exception as e:
        print(f"  iShares IWM 请求失败：{e}")
        return []


def _try_wikipedia(url: str, table_id: str, col: str, label: str) -> list[str]:
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        resp  = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        table = pd.read_html(io.StringIO(resp.text), attrs={'id': table_id})[0]
        tickers = table[col].str.replace('.', '-', regex=False).tolist()
        print(f"  Wikipedia 获取成功：{len(tickers)} 只 {label} 成分股")
        return tickers
    except Exception as e:
        print(f"  Wikipedia 请求失败（{label}）：{e}")
        return []


def get_sector_map(universe: str = 'sp500') -> dict[str, str]:
    """返回 {symbol: sector} 行业映射，优先从 Wikipedia 获取，失败则用内置表"""
    if universe.lower() in ('sp500',):
        result = _try_wikipedia_sectors()
        if result:
            return result
    return dict(_BUILTIN_SECTOR_MAP)


def _try_wikipedia_sectors() -> dict[str, str]:
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }
    try:
        url  = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        table = pd.read_html(io.StringIO(resp.text), attrs={'id': 'constituents'})[0]
        result = {
            str(r['Symbol']).replace('.', '-'): str(r['GICS Sector'])
            for _, r in table.iterrows()
        }
        print(f"  行业分类（Wikipedia）：{len(result)} 只")
        return result
    except Exception as e:
        print(f"  行业分类获取失败，使用内置表：{e}")
        return {}


# ── 内置行业分类（S&P 500 主要成分股）────────────────────────
_BUILTIN_SECTOR_MAP = {
    # Information Technology
    'AAPL':'Information Technology', 'MSFT':'Information Technology',
    'NVDA':'Information Technology', 'AVGO':'Information Technology',
    'AMD': 'Information Technology', 'INTC':'Information Technology',
    'QCOM':'Information Technology', 'TXN': 'Information Technology',
    'ADI': 'Information Technology', 'LRCX':'Information Technology',
    'KLAC':'Information Technology', 'AMAT':'Information Technology',
    'MU':  'Information Technology', 'MRVL':'Information Technology',
    'ORCL':'Information Technology', 'CRM': 'Information Technology',
    'ADBE':'Information Technology', 'NOW': 'Information Technology',
    'INTU':'Information Technology', 'CSCO':'Information Technology',
    'PANW':'Information Technology', 'SNPS':'Information Technology',
    'CDNS':'Information Technology', 'ANET':'Information Technology',
    'PLTR':'Information Technology', 'DDOG':'Information Technology',
    'MDB': 'Information Technology', 'SNOW':'Information Technology',
    'ZS':  'Information Technology', 'CRWD':'Information Technology',
    'NET': 'Information Technology', 'HUBS':'Information Technology',
    'FTNT':'Information Technology', 'ROP': 'Information Technology',
    'GDDY':'Information Technology', 'CDW': 'Information Technology',
    'SNDK':'Information Technology',
    # Communication Services
    'META':'Communication Services', 'GOOGL':'Communication Services',
    'GOOG':'Communication Services', 'NFLX': 'Communication Services',
    'DIS': 'Communication Services', 'CMCSA':'Communication Services',
    'T':   'Communication Services', 'VZ':   'Communication Services',
    'TMUS':'Communication Services', 'CHTR': 'Communication Services',
    'WBD': 'Communication Services',
    # Consumer Discretionary
    'AMZN':'Consumer Discretionary', 'TSLA':'Consumer Discretionary',
    'HD':  'Consumer Discretionary', 'MCD': 'Consumer Discretionary',
    'NKE': 'Consumer Discretionary', 'SBUX':'Consumer Discretionary',
    'LOW': 'Consumer Discretionary', 'TGT': 'Consumer Discretionary',
    'BKNG':'Consumer Discretionary', 'ABNB':'Consumer Discretionary',
    'MAR': 'Consumer Discretionary', 'HLT': 'Consumer Discretionary',
    'CCL': 'Consumer Discretionary', 'RCL': 'Consumer Discretionary',
    'YUM': 'Consumer Discretionary',
    # Consumer Staples
    'COST':'Consumer Staples', 'WMT':'Consumer Staples',
    'PG':  'Consumer Staples', 'KO': 'Consumer Staples',
    'PEP': 'Consumer Staples', 'PM': 'Consumer Staples',
    'MO':  'Consumer Staples', 'CL': 'Consumer Staples',
    'KMB': 'Consumer Staples', 'GIS':'Consumer Staples',
    'K':   'Consumer Staples', 'CPB':'Consumer Staples',
    # Financials
    'JPM': 'Financials', 'BAC':'Financials', 'WFC':'Financials',
    'GS':  'Financials', 'MS': 'Financials', 'BLK':'Financials',
    'SPGI':'Financials', 'ICE':'Financials', 'CME':'Financials',
    'AXP': 'Financials', 'V':  'Financials', 'MA': 'Financials',
    'PYPL':'Financials', 'USB':'Financials', 'TFC':'Financials',
    'PNC': 'Financials', 'COF':'Financials', 'DFS':'Financials',
    'MCO': 'Financials', 'MSCI':'Financials','VRSK':'Financials',
    'BRK-B':'Financials',
    # Health Care
    'UNH': 'Health Care', 'JNJ': 'Health Care', 'LLY': 'Health Care',
    'ABBV':'Health Care', 'MRK': 'Health Care', 'BMY': 'Health Care',
    'AMGN':'Health Care', 'GILD':'Health Care', 'REGN':'Health Care',
    'VRTX':'Health Care', 'TMO': 'Health Care', 'ABT': 'Health Care',
    'MDT': 'Health Care', 'ISRG':'Health Care', 'IDXX':'Health Care',
    'ZTS': 'Health Care', 'DXCM':'Health Care', 'BIIB':'Health Care',
    'MRNA':'Health Care', 'PFE': 'Health Care', 'CI':  'Health Care',
    'HUM': 'Health Care', 'CVS': 'Health Care', 'ELV': 'Health Care',
    'MCK': 'Health Care',
    # Industrials
    'CAT': 'Industrials', 'DE':   'Industrials', 'GE':  'Industrials',
    'HON': 'Industrials', 'RTX':  'Industrials', 'LMT': 'Industrials',
    'BA':  'Industrials', 'UPS':  'Industrials', 'FDX': 'Industrials',
    'ITW': 'Industrials', 'EMR':  'Industrials', 'ETN': 'Industrials',
    'PH':  'Industrials', 'ROK':  'Industrials', 'XYL': 'Industrials',
    'CARR':'Industrials', 'OTIS': 'Industrials', 'TT':  'Industrials',
    'IR':  'Industrials', 'LDOS': 'Industrials', 'MMM': 'Industrials',
    # Energy
    'XOM': 'Energy', 'CVX':'Energy', 'COP':'Energy', 'EOG':'Energy',
    'SLB': 'Energy', 'PSX':'Energy', 'VLO':'Energy', 'MPC':'Energy',
    'OXY': 'Energy', 'HAL':'Energy',
    # Materials
    'LIN': 'Materials', 'APD':'Materials', 'SHW':'Materials',
    'ECL': 'Materials', 'NEM':'Materials', 'FCX':'Materials',
    'NUE': 'Materials', 'VMC':'Materials', 'MLM':'Materials',
    # Real Estate
    'SPG': 'Real Estate', 'PLD': 'Real Estate', 'AMT':  'Real Estate',
    'CCI': 'Real Estate', 'EQIX':'Real Estate',
    # Utilities
    'NEE': 'Utilities', 'DUK':'Utilities', 'SO': 'Utilities',
    'AEP': 'Utilities', 'EXC':'Utilities',
}


def _append_extra(tickers: list[str], extra: list[str] = None) -> list[str]:
    if extra:
        for s in extra:
            s = s.upper()
            if s not in tickers:
                tickers.append(s)
                print(f"  追加自选股：{s}")
    print(f"  股票池共 {len(tickers)} 只")
    return tickers
