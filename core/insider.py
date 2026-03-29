"""
内部人士净买入因子（SEC Form 4 P类交易）。

数据来源：OpenInsider.com（聚合 SEC EDGAR Form 4，免费无需 Key）
缓存：.insider_cache.pkl，TTL 20小时（每天刷新一次）

评分规则（insider_score 0-3）：
  0 — 无买入记录
  1 — 1位内部人买入，金额 ≤ $200K
  2 — 2位内部人，或单人金额 > $200K
  3 — ≥3位内部人，或合计金额 > $500K（群体买入，信号最强）

用法：
  from core.insider import get_insider_buys
  data = get_insider_buys(days=30, min_value_k=100)
  # → {'AAPL': {'count': 2, 'total_value': 350000, 'last_date': '2026-03-25', 'score': 2}, ...}
"""

import io
import pickle
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

_CACHE_FILE = Path('.insider_cache.pkl')
_CACHE_TTL  = timedelta(hours=20)

# OpenInsider screener URL 模板
_URL = (
    'http://openinsider.com/screener'
    '?s=&o=&pl=&ph=&ll=&lh='
    '&fd={days}&td=0'
    '&xp=1'          # 排除 10b5-1 计划自动交易
    '&xs=1'          # 仅买入（排除卖出）
    '&vl={min_value_k}'  # 单笔最小金额（千美元）
    '&cnt=500&sortcol=0&page=1'
)


def _fetch_raw(days: int, min_value_k: int) -> pd.DataFrame | None:
    """从 OpenInsider 拉取原始数据，返回 DataFrame 或 None（失败时）。"""
    url = _URL.format(days=days, min_value_k=min_value_k)
    try:
        resp = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (personal research)'},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f'  [Insider] 网络请求失败：{e}')
        return None

    try:
        # 用 StringIO 包装避免 pandas 将 HTML 字符串误判为文件路径
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:
        print(f'  [Insider] HTML 解析失败：{e}')
        return None

    # 找含 'Ticker' 列的表格
    for tbl in tables:
        cols = [str(c).strip() for c in tbl.columns]
        tbl.columns = cols
        if 'Ticker' in cols and 'Value' in cols:
            return tbl

    print('  [Insider] 未找到数据表格')
    return None


def _parse(df: pd.DataFrame) -> dict[str, dict]:
    """解析原始 DataFrame → {symbol: {count, total_value, last_date, score}}"""
    result: dict[str, dict] = {}

    # 统一列名（OpenInsider 列名含 \xa0 非断行空格）
    col_map = {}
    for c in df.columns:
        lc = str(c).lower().replace('\xa0', ' ').strip()
        if lc == 'ticker':
            col_map[c] = 'ticker'
        elif lc == 'value':
            col_map[c] = 'value'
        elif 'filing' in lc and 'date' in lc:
            col_map[c] = 'filing_date'
        elif 'insider' in lc or 'owner' in lc:
            col_map[c] = 'insider'
        elif 'trade' in lc and 'type' in lc:
            col_map[c] = 'trade_type'
    df = df.rename(columns=col_map)

    if 'ticker' not in df.columns or 'value' not in df.columns:
        return result

    # 清洗：去除非股票行
    df = df[df['ticker'].astype(str).str.match(r'^[A-Z]{1,5}$', na=False)].copy()

    # 只保留买入记录（xs=1 参数不稳定，在解析层二次过滤）
    if 'trade_type' in df.columns:
        df = df[df['trade_type'].astype(str).str.contains('P -', na=False)]

    if df.empty:
        return result

    # Value 列格式如 '+$958,650' 或 '-$7,711,150'，转为数值（美元）
    def _to_float(v) -> float:
        try:
            return float(str(v).replace('+', '').replace('$', '').replace(',', '').strip())
        except Exception:
            return 0.0

    df['_val'] = df['value'].apply(_to_float)
    # 确保只统计正值（买入金额 > 0）
    df = df[df['_val'] > 0]

    # 按 ticker 聚合
    for sym, grp in df.groupby('ticker'):
        sym = str(sym).upper()
        count       = int(grp['insider'].nunique()) if 'insider' in grp.columns else len(grp)
        total_value = float(grp['_val'].sum())
        last_date   = ''
        if 'filing_date' in grp.columns:
            try:
                last_date = str(grp['filing_date'].iloc[0])
            except Exception:
                pass

        # 评分
        if total_value >= 500_000 or count >= 3:
            score = 3
        elif total_value >= 200_000 or count >= 2:
            score = 2
        elif count >= 1:
            score = 1
        else:
            score = 0

        result[sym] = {
            'count':       count,
            'total_value': total_value,
            'last_date':   last_date,
            'score':       score,
        }

    return result


def _load_cache(days: int, min_value_k: int) -> dict[str, dict] | None:
    """读缓存，过期或参数不匹配时返回 None。"""
    if not _CACHE_FILE.exists():
        return None
    try:
        with open(_CACHE_FILE, 'rb') as f:
            stored = pickle.load(f)
        if datetime.now() - stored.get('_time', datetime.min) >= _CACHE_TTL:
            return None
        if stored.get('_days') != days or stored.get('_min_value_k') != min_value_k:
            return None
        return stored.get('data', {})
    except Exception:
        return None


def _save_cache(data: dict, days: int, min_value_k: int) -> None:
    try:
        with open(_CACHE_FILE, 'wb') as f:
            pickle.dump({
                '_time':        datetime.now(),
                '_days':        days,
                '_min_value_k': min_value_k,
                'data':         data,
            }, f)
    except Exception:
        pass


def get_insider_buys(
    symbols:     list[str] | None = None,
    days:        int = 30,
    min_value_k: int = 100,
) -> dict[str, dict]:
    """
    返回最近 days 天内有内部人买入的股票信息。

    参数：
      symbols      过滤到指定股票列表，None 表示返回全部
      days         观察窗口（天），默认 30
      min_value_k  单笔最小金额（千美元），默认 $100K

    返回：
      {symbol: {'count': int, 'total_value': float, 'last_date': str, 'score': int}}
      网络/解析失败时返回空字典（降级，不影响主流程）
    """
    cached = _load_cache(days, min_value_k)
    if cached is not None:
        data = cached
    else:
        raw = _fetch_raw(days, min_value_k)
        if raw is None:
            return {}
        data = _parse(raw)
        _save_cache(data, days, min_value_k)
        print(f'  [Insider] 已加载 {len(data)} 只股票的内部人买入记录')

    if symbols is not None:
        sym_set = {s.upper() for s in symbols}
        return {k: v for k, v in data.items() if k in sym_set}
    return data


def score_to_stars(score: int) -> str:
    """将 score 转为显示字符串：0→'-', 1→'★', 2→'★★', 3→'★★★'"""
    return ['-', '★', '★★', '★★★'][max(0, min(score, 3))]
