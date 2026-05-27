"""AI 基建追踪服务

三大子主题：GPU/算力芯片 | 数据中心/网络 | 电力/冷却/能源

核心指标（4 维 + 技术信号，满分 10）：
  1. Capex YoY 增速 (2pt) >50%=2, >20%=1
  2. RS vs SPY 60日 (2pt) >10%=2, >0%=1
  3. 营收增速 YoY  (2pt)  >30%=2, >10%=1
  4. 新闻/叙事评分  (1pt) 近30天 AI 关键词命中 ≥3 条
  5. 技术信号       (3pt) 突破(1) + 量能(1) + 趋势(1)
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

import pandas as pd


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
_AI_CACHE_FILE       = ROOT / 'data' / 'ai_tracker_cache.json'
_AI_CAPEX_CACHE      = ROOT / 'data' / '.ai_capex_cache.pkl'
_AI_NEWS_CACHE       = ROOT / 'data' / '.ai_news_cache.pkl'
_AI_CACHE_TTL_HOURS  = 1   # 1 小时缓存（短期动量列要求更新及时）
_AI_CAPEX_TTL_DAYS   = 7   # capex 缓存 7 天（财报数据，更新慢）
_AI_NEWS_TTL_HOURS   = 12  # news 缓存 12 小时（新闻日更，半天足够）

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


def _load_news_cache() -> dict:
    """加载 AI 新闻命中数缓存，12 小时 TTL。"""
    if not _AI_NEWS_CACHE.exists():
        return {}
    try:
        with open(_AI_NEWS_CACHE, 'rb') as f:
            stored = pickle.load(f)
        if datetime.now() - stored.get('_time', datetime.min) < timedelta(hours=_AI_NEWS_TTL_HOURS):
            return stored.get('data', {})
    except Exception:
        pass
    return {}


def _save_news_cache(data: dict):
    _AI_NEWS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(_AI_NEWS_CACHE, 'wb') as f:
        pickle.dump({'_time': datetime.now(), 'data': data}, f)


def _fetch_news_count_one(sym: str) -> tuple[str, int]:
    """单只股票：从 yfinance 拉近期新闻，统计 30 天内 AI 关键词命中数（上限 5）。"""
    try:
        import yfinance as yf
        news_list = yf.Ticker(sym).news or []
        cutoff = datetime.now() - timedelta(days=30)
        count = 0
        for item in news_list:
            try:
                # yfinance v0.2+ 返回 {'content': {...}}，旧版直接平铺
                content = item.get('content', item)
                pub_ts  = content.get('pubDate') or item.get('providerPublishTime')
                if isinstance(pub_ts, str):
                    pub_dt = datetime.fromisoformat(pub_ts.replace('Z', '+00:00')).replace(tzinfo=None)
                elif isinstance(pub_ts, (int, float)) and pub_ts > 0:
                    pub_dt = datetime.fromtimestamp(pub_ts)
                else:
                    continue
                if pub_dt < cutoff:
                    continue
                title   = (content.get('title') or item.get('title') or '').lower()
                summary = (content.get('summary') or item.get('summary') or '').lower()
                if any(kw in (title + ' ' + summary) for kw in _AI_KEYWORDS):
                    count += 1
            except Exception:
                pass
        return sym, min(count, 5)
    except Exception:
        return sym, 0


def _batch_news_scores(symbols: list[str]) -> dict[str, int]:
    """并发拉新闻命中数，带 12 小时缓存。"""
    cache = _load_news_cache()
    need  = [s for s in symbols if s not in cache]
    if need:
        _logger.info(f'[AITracker] 拉新闻 {len(need)} 只（缓存命中 {len(symbols)-len(need)}）...')
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(_fetch_news_count_one, s) for s in need]
            for fut in as_completed(futs):
                try:
                    sym, count = fut.result()
                    cache[sym] = count
                except Exception:
                    pass
        _save_news_cache(cache)
    return {s: cache.get(s, 0) for s in symbols}


def _news_score(sym: str) -> int:
    """兼容接口：单股新闻分（不走缓存，仅作为 fallback）。
    生产路径优先用 _batch_news_scores 在扫描前预拉一次。
    """
    _, count = _fetch_news_count_one(sym)
    return count


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


def _score_ai(capex_growth: float | None,
              rs: float | None,
              rev_growth: float | None, news: int,
              tech: dict | None = None) -> tuple[int, dict]:
    score = 0
    bd = {}

    # 1. Capex 增速
    s = 2 if (capex_growth or 0) > 0.50 else (1 if (capex_growth or 0) > 0.20 else 0)
    score += s; bd['capex_growth'] = s

    # 2. RS vs SPY
    s = 2 if (rs or 0) > 0.10 else (1 if (rs or 0) > 0 else 0)
    score += s; bd['rs'] = s

    # 3. 营收增速
    s = 2 if (rev_growth or 0) > 0.30 else (1 if (rev_growth or 0) > 0.10 else 0)
    score += s; bd['rev_growth'] = s

    # 4. 新闻评分
    s = 1 if news >= 3 else 0
    score += s; bd['news'] = s

    # 5. 技术信号（满分 3）：突破 + 量能 + 趋势
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
    news_map    = _batch_news_scores(all_syms)

    # 批量价格预加载（130 天，覆盖 RSMomentum 信号 + 短期动量计算）
    # auto_update=True：确保拉到最新收盘（解决"指标延迟"问题）
    store     = DataStore()
    start_d   = str(date.today() - _td(days=130))
    price_map = store.get(all_syms, start=start_d, auto_update=True)

    def _mom(closes, lookback: int):
        """计算 close[-1] vs close[-1-lookback] 的涨跌幅；样本不足返回 None。"""
        if closes is None or len(closes) <= lookback:
            return None
        cur = closes.iloc[-1]
        ref = closes.iloc[-1 - lookback]
        if ref == 0 or pd.isna(ref) or pd.isna(cur):
            return None
        return float(cur / ref - 1.0)

    rows = []
    for sym in all_syms:
        info     = info_map.get(sym, {})
        ins      = insider_map.get(sym, {})
        gk       = sym_to_group[sym]
        gv       = groups[gk]

        capex_g  = capex_map.get(sym)
        rs       = rs_map.get(sym)
        news     = news_map.get(sym, 0)
        rev_g    = info.get('revenue_growth')
        tech     = _calc_tech_signals(sym, price_map=price_map)

        # 价格 / 短期动量（解决"看不到 MU 单日 19% 异动"问题）
        df_sym   = price_map.get(sym)
        closes   = df_sym['close'] if df_sym is not None and not df_sym.empty else None
        price    = float(closes.iloc[-1]) if closes is not None and len(closes) else None
        mom_1d   = _mom(closes, 1)
        mom_5d   = _mom(closes, 5)
        mom_20d  = _mom(closes, 20)

        total, bd = _score_ai(capex_g, rs, rev_g, news, tech)

        rows.append({
            'symbol':          sym,
            'group':           gk,
            'group_label':     gv['label'],
            'group_color':     gv.get('color', '#94a3b8'),
            'score':           total,
            'breakdown':       bd,
            'price':           price,
            'mom_1d':          mom_1d,
            'mom_5d':          mom_5d,
            'mom_20d':         mom_20d,
            'capex_growth':    round(capex_g, 3) if capex_g is not None else None,
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
    # 用户显式加入 → 从 rejected 黑名单移除（恢复其候选资格）
    if 'rejected' in u and sym in u['rejected']:
        u['rejected'] = [s for s in u['rejected'] if s != sym]
    save_universe(u)
    # 使下次扫描强制刷新
    if _AI_CACHE_FILE.exists():
        _AI_CACHE_FILE.unlink()
    return u


def remove_symbol_from_universe(symbol: str) -> dict:
    """从池中移除某只，并自动加入 rejected 黑名单
    （下次 auto_discover 不会再推荐它；如需恢复，用 add_symbol_to_universe）。
    """
    u = load_universe()
    sym = symbol.upper().strip()
    for gv in u['groups'].values():
        if sym in gv['symbols']:
            gv['symbols'].remove(sym)
    # 从待审核移除
    u['pending_review'] = [p for p in u.get('pending_review', []) if p.get('symbol') != sym]
    # 加入黑名单
    rejected = u.setdefault('rejected', [])
    if sym not in rejected:
        rejected.append(sym)
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
    """忽略待审核 → 从 pending_review 移除并加入 rejected 黑名单"""
    u = load_universe()
    sym = symbol.upper().strip()
    u['pending_review'] = [p for p in u.get('pending_review', []) if p.get('symbol') != sym]
    rejected = u.setdefault('rejected', [])
    if sym not in rejected:
        rejected.append(sym)
    save_universe(u)
    return u


# ── 自动发现 ──────────────────────────────────────────────────

# 候选发现关键词（用户已剔除软件股，所以 software / IT services / 通信运营商 等不纳入）
_AI_SECTOR_KEYWORDS = {
    'semiconductors', 'semiconductor equipment',
    'electronic equipment',
}

_AI_INDUSTRY_KEYWORDS = {
    'semiconductor', 'semiconductor equipment', 'semiconductor materials',
    'computer hardware', 'electronic components', 'communication equipment',
    'electrical equipment', 'independent power', 'renewable electricity', 'uranium',
}

# 明确排除的行业（即使前述关键词命中，命中这些也直接跳过）
_DENY_INDUSTRY_KEYWORDS = {
    'software', 'information technology services', 'telecom',
    'entertainment', 'advertising', 'internet retail',
    'utilities - regulated',  # 通用电网/水/气，与 AI 用电关联弱
    'oil & gas', 'gas distribution',
    'capital markets',        # 投行/券商
}


def _suggest_group(industry: str, sector: str) -> str | None:
    """根据 industry/sector 推测最匹配的子组 key（对齐当前 universe 的 8 个子组）。

    返回 None 表示该公司不适合任何 AI 子组（auto_discover 应跳过）。
    """
    ind = (industry or '').lower()
    sec = (sector or '').lower()

    # 1. 半导体设备/材料/封测 — 优先级最高，避免被 semiconductor 抢走
    if 'semiconductor equipment' in ind or 'semiconductor materials' in ind:
        return 'semicon_equip'

    # 2. 半导体/芯片 — 默认归 gpu_compute，DRAM/NAND 需要用户手动调到 memory_storage
    if 'semiconductor' in ind:
        return 'gpu_compute'

    # 3. 通信设备 / 电子组件 — 归 ai_networking
    if 'communication equipment' in ind or 'electronic components' in ind:
        return 'ai_networking'

    # 4. 计算机硬件 — 归 datacenter_infra
    if 'computer hardware' in ind:
        return 'datacenter_infra'

    # 5. 数据中心 REIT
    if 'reit' in ind and ('specialty' in ind or 'industrial' in ind or 'data' in ind):
        return 'datacenter_infra'

    # 6. 互联网平台 / 云服务（用户保留 AMZN/GOOGL 这类）
    if 'internet content' in ind or 'internet retail' in ind:
        return 'hyperscalers'

    # 7. 电力 / 能源 / 数据中心承包商
    if any(kw in ind for kw in [
        'independent power', 'utilities - renewable', 'renewable',
        'electrical equipment', 'fuel cell', 'uranium',
    ]):
        return 'power_cooling'
    if any(kw in ind for kw in [
        'specialty industrial', 'engineering & construction',
    ]):
        return 'power_cooling'

    return None


def analyze_symbol(symbol: str) -> dict:
    """分析单只股票，返回推荐分组及决策依据。供「管理股票池」手动加入时使用。

    返回字段：
      symbol / sector / industry / market_cap_b / revenue_growth
      suggest_group     推荐分组 key（None 表示不适合任何子组）
      suggest_label     推荐分组中文名
      already_in_group  若已存在于某分组，返回 group key，否则 None
      denied            命中 DENY 行业关键词时为 True
      reason            人类可读的决策说明
    """
    from core.universe import get_stock_info

    sym = symbol.upper().strip()
    info_map = get_stock_info([sym])
    info = info_map.get(sym, {})

    sector   = (info.get('sector') or '').strip()
    industry = (info.get('industry') or '').strip()
    cap      = info.get('market_cap_b')
    rev_g    = info.get('revenue_growth')

    u = load_universe()
    already_in = None
    for gk, gv in u['groups'].items():
        if sym in [s.upper() for s in gv['symbols']]:
            already_in = gk
            break

    ind_lc = industry.lower()
    sec_lc = sector.lower()
    denied = any(kw in ind_lc for kw in _DENY_INDUSTRY_KEYWORDS)
    suggest_group = None if denied else _suggest_group(ind_lc, sec_lc)
    suggest_label = u['groups'][suggest_group]['label'] if suggest_group and suggest_group in u['groups'] else None

    if not info or (not industry and not sector):
        reason = f'未获取到 {sym} 的行业信息（yfinance 可能查不到，可能是新股/退市/拼写错误）'
    elif already_in:
        reason = f'{sym} 已在分组「{u["groups"][already_in]["label"]}」'
    elif denied:
        reason = f'行业 "{industry}" 命中排除关键词（软件/通信运营/油气/投行/通用公用事业等），不建议纳入'
    elif suggest_group is None:
        reason = f'行业 "{industry}" 与 8 个 AI 子组都不匹配，可手动选择或不加入'
    else:
        reason = f'根据行业 "{industry}" 推荐分组「{suggest_label}」'

    return {
        'symbol':           sym,
        'sector':           sector or None,
        'industry':         industry or None,
        'market_cap_b':     cap,
        'revenue_growth':   rev_g,
        'suggest_group':    suggest_group,
        'suggest_label':    suggest_label,
        'already_in_group': already_in,
        'denied':           denied,
        'reason':           reason,
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
    # 用户已 reject / remove 过的，不再纳入候选
    existing.update(s.upper() for s in u.get('rejected', []))

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

        # Deny 优先：明确排除软件/IT 服务/通用电力/油气/投行等
        if any(kw in industry for kw in _DENY_INDUSTRY_KEYWORDS):
            continue

        hit_industry = any(kw in industry for kw in _AI_INDUSTRY_KEYWORDS)
        hit_sector   = any(kw in sector for kw in _AI_SECTOR_KEYWORDS)
        if not (hit_industry or hit_sector):
            continue

        # 市值过滤：只发现 $10B–$500B 的新标的（超大市值已在追踪器内手动管理）
        cap = info.get('market_cap_b') or 0
        if cap < 10.0 or cap > 500.0:
            continue

        suggest_group = _suggest_group(industry, sector)
        if suggest_group is None:
            continue   # 无法归到 8 个子组任何一个，跳过

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


