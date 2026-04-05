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
    tickers = (_try_ivv_holdings()
               or _try_wikipedia(
                   url='https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
                   table_id='constituents',
                   col='Symbol',
                   label='S&P 500',
               )
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



_STOCK_INFO_CACHE = os.path.join(os.path.dirname(__file__), '..', '.stock_info_cache.pkl')
_STOCK_INFO_TTL   = timedelta(days=7)


def get_stock_info(symbols: list[str]) -> dict[str, dict]:
    """
    返回 {symbol: {'market_cap_b': float, 'industry': str, 'sector': str}}
    market_cap_b 单位：十亿美元（Billion USD）

    结果本地缓存 7 天（.stock_info_cache.pkl），只查询缓存中没有的 symbol。
    仅对 buy signal 候选（通常 10-50 只）调用，速度可接受。
    """
    cache = {}
    cache_path = os.path.normpath(_STOCK_INFO_CACHE)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                stored = pickle.load(f)
            # 过期则清空
            if datetime.now() - stored.get('_time', datetime.min) < _STOCK_INFO_TTL:
                cache = stored.get('data', {})
        except Exception:
            pass

    need = [s for s in symbols if s not in cache]
    if need:
        print(f"  [信息] 查询 {len(need)} 只股票市值/行业（首次约需 {len(need)//5+1}s）...")
        for sym in need:
            try:
                info = yf.Ticker(sym).info
                mc   = info.get('marketCap')
                de   = info.get('debtToEquity')
                cache[sym] = {
                    'market_cap_b':    round(mc / 1e9, 1) if mc else None,
                    'industry':        info.get('industry'),
                    'sector':          info.get('sector'),
                    # 成长因子
                    'revenue_growth':  info.get('revenueGrowth'),
                    'earnings_growth': info.get('earningsGrowth'),
                    # 质量因子
                    'roe':             info.get('returnOnEquity'),
                    'debt_to_equity':  round(de / 100, 4) if de is not None else None,
                    'free_cashflow':   info.get('freeCashflow'),
                    # 估值因子
                    'pe_ratio':        info.get('trailingPE'),
                    'pb_ratio':        info.get('priceToBook'),
                }
            except Exception:
                cache[sym] = {
                    'market_cap_b': None, 'industry': None, 'sector': None,
                    'revenue_growth': None, 'earnings_growth': None,
                    'roe': None, 'debt_to_equity': None, 'free_cashflow': None,
                    'pe_ratio': None, 'pb_ratio': None,
                }
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump({'_time': datetime.now(), 'data': cache}, f)
        except Exception:
            pass

    return {s: cache[s] for s in symbols if s in cache}


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


def get_tickers(universe: str = 'sp500', extra: list[str] = None) -> list[str]:
    """统一入口，按名称选择股票池"""
    mapping = {
        'sp500+ndx':  get_sp500_ndx_tickers,
        'sp500':      get_sp500_tickers,
        'nasdaq100':  get_nasdaq100_tickers,
        'ndx':        get_nasdaq100_tickers,
    }
    fn = mapping.get(universe.lower())
    if fn is None:
        raise ValueError(f"未知股票池：{universe}，可选：sp500+ndx / sp500 / nasdaq100")
    return fn(extra=extra)


# ── 内部工具函数 ──────────────────────────────────────────────────

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
        df = pd.read_csv(io.StringIO(resp.text), skiprows=9)
        stocks = df[df['Asset Class'] == 'Equity']
        tickers = (
            stocks['Ticker']
            .dropna()
            .str.strip()
            .map(lambda t: _TICKER_FIX.get(t, t))
            .tolist()
        )
        print(f"  iShares IVV 获取成功：{len(tickers)} 只 S&P 500 成分股")
        return tickers
    except Exception as e:
        print(f"  iShares IVV 请求失败，回退 Wikipedia：{e}")
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
