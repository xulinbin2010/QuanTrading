"""AI 基建追踪服务

三大子主题：GPU/算力芯片 | 数据中心/网络 | 电力/冷却/能源

核心指标（6 维，满分 12）：
  1. AI 营收占比   (3pt)  >50%=3, >25%=2, >10%=1
  2. Capex YoY 增速 (2pt) >50%=2, >20%=1
  3. NVDA 价格相关性 (2pt) >0.8=2, >0.6=1
  4. RS vs SPY 60日 (2pt) >10%=2, >0%=1
  5. 营收增速 YoY  (2pt)  >30%=2, >10%=1
  6. 新闻/叙事评分  (1pt) 近30天 AI 关键词命中 ≥3 条
"""
from __future__ import annotations
import os
import sys
import json
import math
import time
import pickle
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path


def _clean_floats(obj):
    """递归将 nan/inf/-inf 替换为 None，确保结果可 JSON 序列化。"""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_floats(v) for v in obj]
    return obj

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# 主线程预加载：ib_insync 在后台线程初始化 asyncio.event_loop 会失败（Py3.12）
from core import data_store as _data_store           # noqa: F401
from core import universe as _universe               # noqa: F401
from strategies import rs_momentum as _rs_momentum   # noqa: F401

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

_AI_UNIVERSE_FILE    = ROOT / 'data' / 'ai_universe.json'
_AI_REVENUE_FILE     = ROOT / 'data' / 'ai_revenue_share.json'
_AI_CACHE_FILE       = ROOT / 'data' / 'ai_tracker_cache.json'
_AI_CAPEX_CACHE      = ROOT / 'data' / '.ai_capex_cache.pkl'
_AI_CACHE_TTL_HOURS  = 4   # 4 小时缓存
_AI_CAPEX_TTL_DAYS   = 7   # capex 缓存 7 天（财报数据，更新慢）

# 后台扫描状态
_ai_scan_running: dict[str, bool] = {}
_ai_scan_lock = threading.Lock()

_AI_KEYWORDS = {'ai', 'artificial intelligence', 'machine learning', 'deep learning',
                'gpu', 'inference', 'training', 'data center', 'accelerat', 'nvidia',
                'generative', 'large language', 'llm', 'neural network'}


# ── 加载配置文件 ──────────────────────────────────────────────

def load_universe() -> dict:
    with open(_AI_UNIVERSE_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_universe(data: dict):
    _AI_UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_AI_UNIVERSE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_ai_revenue() -> dict:
    if not _AI_REVENUE_FILE.exists():
        return {}
    with open(_AI_REVENUE_FILE, encoding='utf-8') as f:
        return json.load(f).get('data', {})


def save_ai_revenue(data: dict):
    raw = {}
    if _AI_REVENUE_FILE.exists():
        with open(_AI_REVENUE_FILE, encoding='utf-8') as f:
            raw = json.load(f)
    raw['data'] = data
    raw['_updated'] = datetime.now().strftime('%Y-%m-%d')
    with open(_AI_REVENUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


# ── 数据计算 ──────────────────────────────────────────────────

def _calc_capex_growth(sym: str) -> float | None:
    """Capex 同比增速（最近财年 vs 上一财年）"""
    try:
        import yfinance as yf
        cf = yf.Ticker(sym).cashflow
        if cf is None or cf.empty:
            return None
        for label in ['Capital Expenditure', 'Capital Expenditures', 'Purchase Of Property Plant And Equipment']:
            if label in cf.index:
                row = cf.loc[label].dropna()
                if len(row) >= 2:
                    cur  = abs(float(row.iloc[0]))
                    prev = abs(float(row.iloc[1]))
                    return (cur - prev) / prev if prev > 0 else None
    except Exception:
        pass
    return None


def _load_capex_cache() -> dict:
    if _AI_CAPEX_CACHE.exists():
        try:
            with open(_AI_CAPEX_CACHE, 'rb') as f:
                stored = pickle.load(f)
            if datetime.now() - stored.get('_time', datetime.min) < timedelta(days=_AI_CAPEX_TTL_DAYS):
                return stored.get('data', {})
        except Exception:
            pass
    return {}


def _save_capex_cache(data: dict):
    _AI_CAPEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_AI_CAPEX_CACHE, 'wb') as f:
            pickle.dump({'_time': datetime.now(), 'data': data}, f)
    except Exception:
        pass


def _batch_capex_growth(symbols: list[str]) -> dict[str, float | None]:
    """并发查询 capex 同比，带 7 天缓存。"""
    cache = _load_capex_cache()
    need  = [s for s in symbols if s not in cache]
    if need:
        _logger.info(f'[AITracker] 并发查询 {len(need)} 只 capex（max_workers=10）...')
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(_calc_capex_growth, s): s for s in need}
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    cache[sym] = fut.result()
                except Exception:
                    cache[sym] = None
        _save_capex_cache(cache)
    return {s: cache.get(s) for s in symbols}


def _calc_nvda_correlation(sym: str, days: int = 60, price_map: dict | None = None) -> float | None:
    """计算与 NVDA 的价格相关性（日收益率）。
    price_map 为预加载的 {sym: df}，避免逐股调 DataStore。
    """
    if sym == 'NVDA':
        return 1.0
    try:
        if price_map is None:
            from core.data_store import DataStore
            from datetime import date, timedelta
            store = DataStore()
            start = str(date.today() - timedelta(days=days + 10))
            price_map = store.get([sym, 'NVDA'], start=start, auto_update=False)

        if sym not in price_map or 'NVDA' not in price_map:
            return None
        s_close = price_map[sym]['close'].pct_change().dropna()
        n_close = price_map['NVDA']['close'].pct_change().dropna()
        common = s_close.index.intersection(n_close.index)
        if len(common) < 20:
            return None
        # 限制窗口
        common = common[-days:]
        corr = float(s_close.loc[common].corr(n_close.loc[common]))
        return round(corr, 3)
    except Exception:
        return None


def _calc_rs(sym: str) -> float | None:
    """63 日 RS vs SPY"""
    try:
        import yfinance as yf
        df = yf.download([sym, 'SPY'], period='100d', progress=False, auto_adjust=True)['Close']
        if df.empty or sym not in df.columns:
            return None
        ret = df.pct_change(63).iloc[-1]
        spy_ret = float(ret.get('SPY', 0))
        sym_ret = float(ret.get(sym, 0))
        return round(sym_ret - spy_ret, 4)
    except Exception:
        return None


def _news_score(sym: str) -> int:
    """近 30 天 AI 关键词新闻命中数（用现有 stock_news_cache）"""
    try:
        cache_path = ROOT / '.stock_news_cache.pkl'
        if not cache_path.exists():
            return 0
        with open(cache_path, 'rb') as f:
            cache = pickle.load(f)
        news_list = cache.get('data', {}).get(sym, [])
        cutoff = datetime.now() - timedelta(days=30)
        count = 0
        for item in news_list:
            try:
                pub = item.get('providerPublishTime', 0)
                if pub and datetime.fromtimestamp(pub) < cutoff:
                    continue
                title = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
                if any(kw in title for kw in _AI_KEYWORDS):
                    count += 1
            except Exception:
                pass
        return min(count, 5)
    except Exception:
        return 0


def _calc_tech_signals(sym: str, price_map: dict | None = None) -> dict:
    """从预加载 price_map（或 DataStore）拉最新价格，调用 RSMomentum 计算技术信号。"""
    try:
        from strategies.rs_momentum import RSMomentum
        if price_map is None:
            from core.data_store import DataStore
            from datetime import date, timedelta
            store = DataStore()
            start = str(date.today() - timedelta(days=130))
            price_map = store.get([sym], start=start, auto_update=False)
        df = price_map.get(sym)
        if df is None or len(df) < 60:
            return {}
        sig = RSMomentum().generate_signals(df).iloc[-1]
        return {
            'breakout':  bool(sig.get('breakout', False)),
            'vol_surge': bool(sig.get('vol_surge', False)),
            'uptrend':   bool(sig.get('uptrend', False)),
            'signal':    int(sig.get('signal', 0)),  # 1=买入信号, -1=卖出预警
        }
    except Exception:
        return {}


def _score_ai(ai_pct: float | None, capex_growth: float | None,
              nvda_corr: float | None, rs: float | None,
              rev_growth: float | None, news: int,
              tech: dict | None = None) -> tuple[int, dict]:
    score = 0
    bd = {}

    # 1. AI 营收占比
    s = 3 if (ai_pct or 0) > 0.50 else (2 if (ai_pct or 0) > 0.25 else (1 if (ai_pct or 0) > 0.10 else 0))
    score += s; bd['ai_revenue'] = s

    # 2. Capex 增速
    s = 2 if (capex_growth or 0) > 0.50 else (1 if (capex_growth or 0) > 0.20 else 0)
    score += s; bd['capex_growth'] = s

    # 3. NVDA 相关性
    c = nvda_corr or 0
    s = 2 if c > 0.80 else (1 if c > 0.60 else 0)
    score += s; bd['nvda_corr'] = s

    # 4. RS vs SPY
    s = 2 if (rs or 0) > 0.10 else (1 if (rs or 0) > 0 else 0)
    score += s; bd['rs'] = s

    # 5. 营收增速
    s = 2 if (rev_growth or 0) > 0.30 else (1 if (rev_growth or 0) > 0.10 else 0)
    score += s; bd['rev_growth'] = s

    # 6. 新闻评分
    s = 1 if news >= 3 else 0
    score += s; bd['news'] = s

    # 7. 技术信号（满分 3）：突破 + 量能 + 趋势
    if tech:
        s = (1 if tech.get('breakout') else 0) + \
            (1 if tech.get('vol_surge') else 0) + \
            (1 if tech.get('uptrend') else 0)
        score += s; bd['tech'] = s
    else:
        bd['tech'] = 0

    return score, bd


# ── 主扫描函数 ────────────────────────────────────────────────

def _read_disk_cache() -> dict | None:
    """读盘上 AI 扫描结果，TTL 内返回，否则返回 None。"""
    if not _AI_CACHE_FILE.exists():
        return None
    try:
        with open(_AI_CACHE_FILE, encoding='utf-8') as f:
            cached = json.load(f)
        age = datetime.now() - datetime.fromisoformat(cached['last_updated'])
        if age < timedelta(hours=_AI_CACHE_TTL_HOURS):
            return cached
        return cached   # 也返回过期缓存供 stale 回退使用
    except Exception:
        return None


def scan_ai_tracker(force: bool = False) -> dict:
    """扫描 AI 基建全股票池。

    缓存策略（与 factor_svc 一致）：
    - 缓存有效 → 立即返回
    - 缓存过期但存在 → 立即返回 stale + 后台刷新
    - 无缓存（首次）→ 同步等待
    """
    cached = _read_disk_cache()
    if not force and cached is not None:
        age = datetime.now() - datetime.fromisoformat(cached['last_updated'])
        if age < timedelta(hours=_AI_CACHE_TTL_HOURS):
            return cached

    key = 'ai'
    thread_to_wait = None
    with _ai_scan_lock:
        if not _ai_scan_running.get(key, False):
            _ai_scan_running[key] = True
            t = threading.Thread(target=_run_ai_scan_bg, daemon=True)
            t.start()
            if cached is None:
                thread_to_wait = t   # 首次运行，无任何缓存可返回

    if thread_to_wait is not None:
        thread_to_wait.join()
        cached = _read_disk_cache()

    if cached is not None:
        is_bg = _ai_scan_running.get(key, False)
        return {**cached, 'scanning': is_bg}
    return {'rows': [], 'total': 0, 'scanning': True}


def _run_ai_scan_bg() -> None:
    """后台线程入口，捕获异常并清理状态。"""
    try:
        _do_ai_scan()
    except Exception as e:
        _logger.error(f'[AITracker] 扫描失败：{e}', exc_info=True)
    finally:
        with _ai_scan_lock:
            _ai_scan_running.pop('ai', None)


def _do_ai_scan() -> dict:
    """实际扫描逻辑，返回结果 dict 并写盘缓存。"""
    from core.universe import get_stock_info
    from core.insider import get_insider_buys
    from core.data_store import DataStore
    from datetime import date, timedelta as _td

    universe   = load_universe()
    ai_revenue = load_ai_revenue()
    groups     = universe.get('groups', {})

    all_syms: list[str] = []
    sym_to_group: dict[str, str] = {}
    for gk, gv in groups.items():
        for s in gv.get('symbols', []):
            if s not in sym_to_group:
                all_syms.append(s)
                sym_to_group[s] = gk

    _logger.info(f'[AITracker] 扫描 {len(all_syms)} 只 (后台线程)...')

    # ── 一次性预加载 ────────────────────────────────────────
    info_map    = get_stock_info(all_syms)
    insider_map = get_insider_buys(days=90, min_value_k=50)
    rs_map      = _batch_rs(all_syms)
    capex_map   = _batch_capex_growth(all_syms)

    # 批量价格预加载（130 天，覆盖 NVDA 相关性 + RSMomentum 信号）
    store     = DataStore()
    start_d   = str(date.today() - _td(days=130))
    price_map = store.get(list(set(all_syms + ['NVDA'])), start=start_d, auto_update=False)

    rows = []
    for sym in all_syms:
        info     = info_map.get(sym, {})
        ins      = insider_map.get(sym, {})
        gk       = sym_to_group[sym]
        gv       = groups[gk]
        ai_data  = ai_revenue.get(sym, {})
        ai_pct   = ai_data.get('ai_pct')

        capex_g  = capex_map.get(sym)
        nvda_c   = _calc_nvda_correlation(sym, price_map=price_map)
        rs       = rs_map.get(sym)
        news     = _news_score(sym)
        rev_g    = info.get('revenue_growth')
        tech     = _calc_tech_signals(sym, price_map=price_map)

        total, bd = _score_ai(ai_pct, capex_g, nvda_c, rs, rev_g, news, tech)

        rows.append({
            'symbol':          sym,
            'group':           gk,
            'group_label':     gv['label'],
            'group_color':     gv.get('color', '#94a3b8'),
            'score':           total,
            'breakdown':       bd,
            'ai_revenue_pct':  ai_pct,
            'ai_revenue_note': ai_data.get('note', ''),
            'capex_growth':    round(capex_g, 3) if capex_g is not None else None,
            'nvda_corr':       nvda_c,
            'rs_score':        rs,
            'news_score':      news,
            'revenue_growth':  rev_g,
            'market_cap_b':    info.get('market_cap_b'),
            'sector':          info.get('sector', ''),
            'industry':        info.get('industry', ''),
            'insider_score':   ins.get('score', 0) if ins else 0,
            'insider_count':   ins.get('count', 0) if ins else 0,
            'breakout':        tech.get('breakout', False),
            'vol_surge':       tech.get('vol_surge', False),
            'uptrend':         tech.get('uptrend', False),
            'signal':          tech.get('signal', 0),
        })

    rows.sort(key=lambda x: x['score'], reverse=True)

    result = _clean_floats({
        'rows':          rows,
        'last_updated':  datetime.now().isoformat(timespec='seconds'),
        'total':         len(rows),
        'groups':        {gk: gv['label'] for gk, gv in groups.items()},
        'group_colors':  {gk: gv.get('color', '#94a3b8') for gk, gv in groups.items()},
    })

    _AI_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_AI_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return result


def _batch_rs(symbols: list[str]) -> dict[str, float]:
    try:
        import yfinance as yf
        df = yf.download(list(set(symbols + ['SPY'])), period='100d',
                         progress=False, auto_adjust=True)['Close']
        if df.empty:
            return {}
        ret = df.pct_change(63).iloc[-1]
        spy_val = ret.get('SPY', 0)
        spy = float(spy_val) if spy_val == spy_val else 0.0  # NaN guard
        result = {}
        for s in symbols:
            if s not in ret.index:
                continue
            v = ret.get(s)
            if v is None or v != v:  # None or NaN
                continue
            result[s] = round(float(v) - spy, 4)
        return result
    except Exception:
        return {}


# ── 股票池管理 ────────────────────────────────────────────────

def add_symbol_to_universe(symbol: str, group: str) -> dict:
    u = load_universe()
    if group not in u['groups']:
        raise ValueError(f'group "{group}" 不存在')
    syms = u['groups'][group]['symbols']
    sym = symbol.upper().strip()
    if sym not in syms:
        syms.append(sym)
    save_universe(u)
    # 使下次扫描强制刷新
    if _AI_CACHE_FILE.exists():
        _AI_CACHE_FILE.unlink()
    return u


def remove_symbol_from_universe(symbol: str) -> dict:
    u = load_universe()
    sym = symbol.upper().strip()
    for gv in u['groups'].values():
        if sym in gv['symbols']:
            gv['symbols'].remove(sym)
    # 从待审核移除
    u['pending_review'] = [p for p in u.get('pending_review', []) if p.get('symbol') != sym]
    save_universe(u)
    if _AI_CACHE_FILE.exists():
        _AI_CACHE_FILE.unlink()
    return u


def approve_pending(symbol: str, group: str) -> dict:
    """将待审核股票移入正式列表"""
    u = load_universe()
    u['pending_review'] = [p for p in u.get('pending_review', []) if p.get('symbol') != symbol.upper()]
    save_universe(u)
    return add_symbol_to_universe(symbol, group)


def reject_pending(symbol: str) -> dict:
    u = load_universe()
    u['pending_review'] = [p for p in u.get('pending_review', []) if p.get('symbol') != symbol.upper()]
    save_universe(u)
    return u


# ── 自动发现 ──────────────────────────────────────────────────

_AI_SECTOR_KEYWORDS = {
    'semiconductors', 'semiconductor equipment', 'information technology',
    'electronic equipment', 'data center', 'it services', 'cloud', 'software',
    'utilities', 'electrical', 'diversified utilities',
}

_AI_INDUSTRY_KEYWORDS = {
    'semiconductor', 'data center', 'computer hardware', 'cloud computing',
    'application software', 'information technology services', 'electronic components',
    'electrical equipment', 'independent power', 'renewable electricity',
}

_AI_COMPANY_KEYWORDS = {
    'ai', 'artificial intelligence', 'gpu', 'data center', 'accelerat',
    'machine learning', 'inference', 'cloud', 'hyperscale', 'nvidia',
}


def auto_discover(limit: int = 20) -> list[dict]:
    """扫描 sp500+ndx+russell2000，发现潜在 AI 相关标的（$10B–$500B），加入 pending_review"""
    from core.universe import get_sp500_tickers, get_nasdaq100_tickers, get_russell2000_tickers
    from core.universe import get_stock_info

    u = load_universe()
    existing = set()
    for gv in u['groups'].values():
        existing.update(s.upper() for s in gv['symbols'])
    existing.update(p['symbol'] for p in u.get('pending_review', []))

    sp500   = set(get_sp500_tickers())
    ndx     = set(get_nasdaq100_tickers())
    try:
        r2000 = set(get_russell2000_tickers())
    except Exception:
        r2000 = set()
    candidates = list((sp500 | ndx | r2000) - existing)

    _logger.info(f'[AITracker] 自动发现扫描 {len(candidates)} 只...')
    info_map = get_stock_info(candidates)

    suggestions = []
    for sym, info in info_map.items():
        if sym in existing:
            continue
        sector   = (info.get('sector') or '').lower()
        industry = (info.get('industry') or '').lower()

        hit_industry = any(kw in industry for kw in _AI_INDUSTRY_KEYWORDS)
        hit_sector   = any(kw in sector for kw in _AI_SECTOR_KEYWORDS)
        if not (hit_industry or hit_sector):
            continue

        # 市值过滤：只发现 $10B–$500B 的新标的（超大市值已在追踪器内手动管理）
        cap = info.get('market_cap_b') or 0
        if cap < 10.0 or cap > 500.0:
            continue

        # 推测子主题
        if any(kw in industry for kw in ('semiconductor', 'electronic component')):
            suggest_group = 'gpu_chips'
        elif any(kw in industry for kw in ('data center', 'cloud', 'computer hardware', 'application software', 'information technology')):
            suggest_group = 'datacenter_network'
        elif any(kw in industry for kw in ('power', 'utilities', 'electrical', 'renewable')):
            suggest_group = 'power_cooling'
        else:
            suggest_group = 'datacenter_network'

        suggestions.append({
            'symbol':         sym,
            'suggest_group':  suggest_group,
            'sector':         info.get('sector', ''),
            'industry':       info.get('industry', ''),
            'market_cap_b':   info.get('market_cap_b'),
            'revenue_growth': info.get('revenue_growth'),
            'discovered_at':  datetime.now().isoformat(timespec='seconds'),
        })

    # 按市值排序，取前 limit 只
    suggestions.sort(key=lambda x: x.get('market_cap_b') or 0, reverse=True)
    suggestions = suggestions[:limit]

    # 追加到 pending_review（去重）
    pending_syms = {p['symbol'] for p in u.get('pending_review', [])}
    for s in suggestions:
        if s['symbol'] not in pending_syms:
            u.setdefault('pending_review', []).append(s)

    save_universe(u)
    return suggestions


def update_ai_revenue(symbol: str, ai_pct: float, note: str = '') -> dict:
    """更新单只股票的 AI 营收占比"""
    data = load_ai_revenue()
    data[symbol.upper()] = {
        'ai_pct': ai_pct,
        'note': note,
        'updated': datetime.now().strftime('%Y-%m'),
    }
    save_ai_revenue(data)
    if _AI_CACHE_FILE.exists():
        _AI_CACHE_FILE.unlink()
    return data
