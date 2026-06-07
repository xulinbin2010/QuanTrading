"""风险温度计：板块/组合「减仓预警」信号（区别于只'停买'的 SPY/VIX brake）。

v1 含两个最便宜、最有领先性的信号：
  1. VIX 期限结构 (VIX / VIX3M)：≥1 倒挂 = 近月恐慌 > 远月 → risk-off
  2. 组合相关性 / 有效持仓数：揭示「假分散」（多只票其实是一个仓）

两者合成一个温度（low/mid/high = 绿/黄/红），多个独立信号共振才升级，避免单点误报。
数据全走 yfinance（DataStore），零额外成本。
"""
from __future__ import annotations
import time
import json
import os
import numpy as np
import pandas as pd
from datetime import date, timedelta

from core.data_store import DataStore

CASH_EQUIV = {'SGOV', 'BIL', 'USFR'}
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 1800  # 30 分钟


def _cached(key: str, fn, force: bool = False):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and not force and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


# ── 信号 1：VIX 期限结构 ────────────────────────────────────
def _vix_term_structure() -> dict:
    ds = DataStore()
    start = (date.today() - timedelta(days=200)).strftime('%Y-%m-%d')
    try:
        d = ds.get(['^VIX', '^VIX3M'], start=start, auto_update=True)
    except Exception as e:
        return {'available': False, 'error': str(e)}
    vix, vix3m = d.get('^VIX'), d.get('^VIX3M')
    if vix is None or vix3m is None or len(vix) == 0 or len(vix3m) == 0:
        return {'available': False, 'error': '无 VIX 数据'}

    df = pd.DataFrame({'vix': vix['close'], 'vix3m': vix3m['close']}).dropna()
    df['ratio'] = df['vix'] / df['vix3m']
    last = df.iloc[-1]
    ratio = float(last['ratio'])

    if ratio >= 1.0:
        score, label = 2, '倒挂 · risk-off'
    elif ratio >= 0.95:
        score, label = 1, '走平 · 警惕'
    else:
        score, label = 0, '正常 · contango'

    history = [
        {'date': idx.strftime('%Y-%m-%d'), 'vix': round(float(r.vix), 2),
         'vix3m': round(float(r.vix3m), 2), 'ratio': round(float(r.ratio), 4)}
        for idx, r in df.tail(90).iterrows()
    ]
    return {
        'available': True,
        'vix': round(float(last['vix']), 2),
        'vix3m': round(float(last['vix3m']), 2),
        'ratio': round(ratio, 4),
        'score': score, 'label': label,
        'history': history,
    }


# ── 信号 2：组合相关性 / 有效持仓数 ─────────────────────────
def _get_holdings() -> tuple[list[str], dict[str, float], str]:
    """返回 (symbols, weights_by_mv, source)。
    优先真实 IB 持仓（按市值权重）；IB 不可用时退回 AI 关注池（等权）。"""
    try:
        from web.services import portfolio_svc
        pos = portfolio_svc.get_positions()
        syms, weights = [], {}
        for p in pos:
            s = (p.get('symbol') or '').upper()
            if (not s.isalpha()) or s in CASH_EQUIV:   # 排除期权（含空格）与现金等价
                continue
            mv = abs(float(p.get('market_value') or 0))
            if mv <= 0:
                continue
            syms.append(s)
            weights[s] = mv
        if syms:
            tot = sum(weights.values())
            weights = {s: weights[s] / tot for s in syms}
            return syms, weights, 'ib'
    except Exception:
        pass
    # 兜底：AI 关注池等权
    path = os.path.join('data', 'ai_universe.json')
    try:
        raw = json.load(open(path))
        flat = []
        if isinstance(raw, dict):
            for v in raw.values():
                if isinstance(v, list):
                    flat += [str(x).upper() for x in v]
        elif isinstance(raw, list):
            flat = [str(x).upper() for x in raw]
        syms = sorted(set(flat))[:30]
        return syms, {s: 1 / len(syms) for s in syms} if syms else {}, 'ai_universe'
    except Exception:
        return [], {}, 'none'


def _portfolio_correlation(lookback: int = 60) -> dict:
    syms, weights, source = _get_holdings()
    if len(syms) < 2:
        return {'available': False, 'error': '持仓不足 2 只', 'source': source}

    ds = DataStore()
    start = (date.today() - timedelta(days=lookback * 2 + 40)).strftime('%Y-%m-%d')
    rets = {}
    for s in syms:
        try:
            df = ds.get([s], start=start, auto_update=False).get(s)
            if df is not None and len(df) > 5:
                rets[s] = df['close'].pct_change()
        except Exception:
            continue
    if len(rets) < 2:
        return {'available': False, 'error': '可用价格数据不足', 'source': source}

    R = pd.DataFrame(rets).dropna().tail(lookback)
    used = list(R.columns)
    C = R.corr()

    tri = C.values[np.triu_indices_from(C.values, 1)]
    avg_corr = float(np.nanmean(tri))
    max_corr = float(np.nanmax(tri))

    # 有效持仓数(相关性视角)：相关矩阵特征值的熵。完全独立=N，完全同步=1
    eig = np.clip(np.linalg.eigvalsh(C.values), 1e-9, None)
    p = eig / eig.sum()
    enb_corr = float(np.exp(-(p * np.log(p)).sum()))

    # 资金集中度(权重视角)：1/Σw²（HHI 倒数）
    w = np.array([weights.get(s, 1 / len(used)) for s in used])
    w = w / w.sum()
    enb_weight = float(1.0 / np.square(w).sum())

    if avg_corr >= 0.7:
        score, label = 2, '高度同步 · 假分散'
    elif avg_corr >= 0.5:
        score, label = 1, '偏同步'
    else:
        score, label = 0, '尚可'

    return {
        'available': True,
        'source': source,
        'symbols': used,
        'n': len(used),
        'avg_corr': round(avg_corr, 3),
        'max_corr': round(max_corr, 3),
        'enb_corr': round(enb_corr, 2),
        'enb_weight': round(enb_weight, 2),
        'lookback': lookback,
        'matrix': [[round(float(C.iloc[i, j]), 2) for j in range(len(used))]
                   for i in range(len(used))],
        'score': score, 'label': label,
    }


# ── 合成温度 ────────────────────────────────────────────────
def get_thermometer(force: bool = False) -> dict:
    def _build():
        vix = _vix_term_structure()
        corr = _portfolio_correlation()
        total = (vix.get('score', 0) if vix.get('available') else 0) \
              + (corr.get('score', 0) if corr.get('available') else 0)
        if total >= 3:
            level, color, advice = 'high', 'red', '多信号共振 → 建议主动减仓至目标仓 50% 或更低'
        elif total >= 1:
            level, color, advice = 'mid', 'yellow', '出现单一风险信号 → 收紧止损，考虑减至 70%'
        else:
            level, color, advice = 'low', 'green', '风险信号未触发 → 维持，按既有止损纪律'
        return {
            'level': level, 'color': color, 'score': total, 'max_score': 4,
            'advice': advice,
            'vix_term': vix,
            'correlation': corr,
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    return _cached('thermometer', _build, force=force)
