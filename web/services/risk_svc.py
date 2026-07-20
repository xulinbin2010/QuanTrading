"""风险温度计：市场环境与组合结构的风险观察信号。

当前包含四个低成本、偏领先的信号：
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
from datetime import date, datetime, timedelta, timezone

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


def _ai_universe_symbols() -> list[str]:
    """AI 关注池活跃成员（groups.<组>.symbols 扁平化），作为半导体/AI 硬件板块代理。"""
    try:
        raw = json.load(open(os.path.join('data', 'ai_universe.json')))
    except Exception:
        return []
    out: list[str] = []
    for g in (raw.get('groups') or {}).values():
        for s in (g.get('symbols') or []):
            if isinstance(s, str):
                out.append(s.upper())
    return sorted(set(out))


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
    syms = _ai_universe_symbols()
    if syms:
        return syms, {s: 1 / len(syms) for s in syms}, 'ai_universe'
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


# ── 信号 3：板块广度 ────────────────────────────────────────
def _breadth() -> dict:
    syms = _ai_universe_symbols()
    if len(syms) < 10:
        return {'available': False, 'error': 'AI 池成员不足'}
    ds = DataStore()
    start = (date.today() - timedelta(days=160)).strftime('%Y-%m-%d')
    a50, a20 = {}, {}
    for s in syms:
        try:
            df = ds.get([s], start=start, auto_update=False).get(s)
        except Exception:
            df = None
        if df is None or len(df) < 55:
            continue
        c = df['close']
        a50[s] = (c > c.rolling(50).mean()).astype(float)
        a20[s] = (c > c.rolling(20).mean()).astype(float)
    if len(a50) < 10:
        return {'available': False, 'error': '可用数据不足'}

    b50 = pd.DataFrame(a50).mean(axis=1)
    b20 = pd.DataFrame(a20).mean(axis=1)
    now50 = float(b50.iloc[-1]); prev50 = float(b50.iloc[-6]) if len(b50) > 6 else now50
    now20 = float(b20.iloc[-1]); prev20 = float(b20.iloc[-6]) if len(b20) > 6 else now20
    drop5 = now50 - prev50

    if now50 < 0.40:
        score, label = 2, '内部走弱'
    elif now50 < 0.60 or drop5 <= -0.15:
        score, label = 1, '内部转弱'
    else:
        score, label = 0, '健康'

    hist = [{'date': i.strftime('%Y-%m-%d'), 'ma50': round(float(v), 3)}
            for i, v in b50.tail(90).items()]
    return {
        'available': True, 'n': len(a50),
        'above_ma50': round(now50, 3), 'above_ma50_prev5': round(prev50, 3),
        'above_ma20': round(now20, 3), 'above_ma20_prev5': round(prev20, 3),
        'score': score, 'label': label, 'history': hist,
    }


# ── 信号 4：龙头 RS 掉头 ────────────────────────────────────
def _leadership_rs() -> dict:
    syms = _ai_universe_symbols()
    if len(syms) < 10:
        return {'available': False, 'error': 'AI 池成员不足'}
    ds = DataStore()
    start = (date.today() - timedelta(days=160)).strftime('%Y-%m-%d')
    try:
        spy = ds.get(['SPY'], start=start, auto_update=True).get('SPY')
    except Exception:
        spy = None
    if spy is None or len(spy) < 70:
        return {'available': False, 'error': '无 SPY 数据'}
    spc = spy['close']

    rows = []
    for s in syms:
        try:
            df = ds.get([s], start=start, auto_update=False).get(s)
        except Exception:
            df = None
        if df is None:
            continue
        m = pd.DataFrame({'c': df['close'], 'spy': spc}).dropna()
        if len(m) < 70:
            continue
        rs63 = (m['c'].iloc[-1] / m['c'].iloc[-64]) / (m['spy'].iloc[-1] / m['spy'].iloc[-64])
        rel10 = (m['c'].iloc[-1] / m['c'].iloc[-11]) / (m['spy'].iloc[-1] / m['spy'].iloc[-11]) - 1
        rows.append((s, float(rs63), float(rel10)))
    if len(rows) < 10:
        return {'available': False, 'error': '可用数据不足'}

    R = pd.DataFrame(rows, columns=['sym', 'rs63', 'rel10']).set_index('sym')
    thr = R['rs63'].quantile(2 / 3)          # 63 日 RS 前 1/3 = 龙头
    leaders = R[R['rs63'] >= thr]
    rolled = leaders[leaders['rel10'] < 0].sort_values('rel10')   # 近 10 日跑输 SPY = 掉头
    frac = len(rolled) / len(leaders) if len(leaders) else 0.0

    if frac >= 0.60:
        score, label = 2, '龙头集体掉头'
    elif frac >= 0.35:
        score, label = 1, '龙头转弱'
    else:
        score, label = 0, '龙头健康'
    return {
        'available': True,
        'n_leaders': int(len(leaders)),
        'rolled_over': int(len(rolled)),
        'frac': round(frac, 3),
        'rolled_symbols': list(rolled.index[:12]),
        'score': score, 'label': label,
    }


# ── 合成温度 ────────────────────────────────────────────────
def get_thermometer(force: bool = False) -> dict:
    def _build():
        signals = {
            'vix_term':    _vix_term_structure(),
            'correlation': _portfolio_correlation(),
            'breadth':     _breadth(),
            'leadership':  _leadership_rs(),
        }
        avail = [
            signal for key, signal in signals.items()
            if signal.get('available')
            and not (key == 'correlation' and signal.get('source') != 'ib')
        ]
        total = sum(s.get('score', 0) for s in avail)
        max_total = (len(avail) * 2) or 1          # 每信号 0-2，随启用数量动态变化
        ratio = total / max_total

        if ratio >= 0.5:
            level, color, advice = 'high', 'red', '多信号共振 → 暂停新增波段仓，复核核心持仓 thesis 与失效条件'
        elif ratio >= 0.25:
            level, color, advice = 'mid', 'yellow', '风险信号升温 → 控制新增仓位，按半自动出场纪律观察'
        else:
            level, color, advice = 'low', 'green', '风险信号未触发 → 维持既定仓位与半自动出场纪律'

        return {
            'level': level, 'color': color, 'score': total, 'max_score': max_total,
            'advice': advice,
            **signals,
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
    return _cached('thermometer', _build, force=force)


def _pillar(score: float | None, level: str | None, label: str,
            available: bool = True, detail: str = '') -> dict:
    return {
        'label': label,
        'available': available,
        'score': round(float(score), 1) if score is not None else None,
        'level': level if available else 'unknown',
        'detail': detail,
    }


def get_dashboard(force: bool = False) -> dict:
    """统一风险驾驶舱。

    Market / Portfolio / Leverage 保持独立口径，综合灯号只做共振提示，
    不直接连接自动交易或修改仓位参数。
    """
    from web.services import leverage_monitor_svc

    thermometer = get_thermometer(force=force)
    leverage = leverage_monitor_svc.get_dashboard(force=force)

    market_parts = [
        thermometer.get('vix_term') or {},
        thermometer.get('breadth') or {},
        thermometer.get('leadership') or {},
    ]
    market_available = [part for part in market_parts if part.get('available')]
    market_points = sum(float(part.get('score') or 0) for part in market_available)
    market_max = len(market_available) * 2
    market_score = round(market_points / market_max * 100, 1) if market_max else None
    market_level = (
        'high' if market_score is not None and market_score >= 50
        else 'mid' if market_score is not None and market_score >= 25
        else 'low'
    )

    corr = thermometer.get('correlation') or {}
    # AI 关注池只是 IB 离线时的板块代理，不能代表真实组合，也不得升级综合灯号。
    portfolio_available = bool(corr.get('available')) and corr.get('source') == 'ib'
    portfolio_score = float(corr.get('score') or 0) * 50 if portfolio_available else None
    portfolio_level = (
        'high' if portfolio_score is not None and portfolio_score >= 100
        else 'mid' if portfolio_score is not None and portfolio_score >= 50
        else 'low'
    )

    lev_summary = leverage.get('summary') or {}
    leverage_score = lev_summary.get('unwind_score')
    leverage_available = leverage_score is not None
    leverage_level = lev_summary.get('unwind_level', 'low')

    pillars = {
        'market': _pillar(
            market_score, market_level, '市场环境',
            available=bool(market_available),
            detail=' · '.join(
                str(part.get('label')) for part in market_available if part.get('label')
            ),
        ),
        'portfolio': _pillar(
            portfolio_score, portfolio_level, '组合结构',
            available=portfolio_available,
            detail=(
                f"平均相关 {corr.get('avg_corr')} · 有效持仓 {corr.get('enb_corr')}/{corr.get('n')}"
                if portfolio_available
                else (
                    f"IB 离线；AI 关注池代理 {corr.get('n')} 只（不计入综合灯号）"
                    if corr.get('source') == 'ai_universe' and corr.get('available')
                    else str(corr.get('error') or '组合数据不可用')
                )
            ),
        ),
        'leverage': _pillar(
            leverage_score, leverage_level, '杠杆压力',
            available=leverage_available,
            detail=(
                f"主导市场 {lev_summary.get('dominant_market') or '—'}"
                if leverage_available else '杠杆行情不可用'
            ),
        ),
    }

    available_levels = [
        item['level'] for item in pillars.values() if item['available']
    ]
    high_count = available_levels.count('high')
    mid_count = available_levels.count('mid')
    if high_count or mid_count >= 2:
        overall_level = 'high'
    elif mid_count:
        overall_level = 'mid'
    else:
        overall_level = 'low'

    reasons: list[str] = []
    for key, item in pillars.items():
        if item['available'] and item['level'] != 'low':
            reasons.append(f"{item['label']}：{item['detail']}")
    if not reasons:
        reasons.append('当前可用指标尚未形成明显风险共振')

    market_high = pillars['market']['level'] == 'high'
    portfolio_high = pillars['portfolio']['level'] == 'high'
    leverage_high = pillars['leverage']['level'] == 'high'
    if market_high or leverage_high:
        core_advice = '复核核心持仓 thesis 与失效条件；不因单一技术信号机械清仓'
    else:
        core_advice = '核心中长期仓维持既定 thesis 与半自动出场纪律'
    if market_high:
        tactical_advice = '暂停追高和新增短线仓，优先压缩弱势、高 beta 波段仓'
    elif portfolio_high:
        tactical_advice = '暂停新增同主题仓位，避免继续放大相关性暴露'
    else:
        tactical_advice = '短线仓按既有仓位上限执行，继续观察风险是否共振'
    if leverage_high:
        leveraged_advice = '优先降低 2X/3X、margin 与高 beta 隔夜暴露'
    elif pillars['leverage']['level'] == 'mid':
        leveraged_advice = '停止增加杠杆，关注 inverse ETF 放量与 tracking dislocation'
    else:
        leveraged_advice = '未见明显 forced deleveraging；仍遵守既定杠杆上限'

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'overall': {
            'level': overall_level,
            'label': {'low': '低风险', 'mid': '警惕', 'high': '高风险'}[overall_level],
            'method': '任一维度高风险，或至少两个维度同时警惕，则升级为高风险',
        },
        'pillars': pillars,
        'reasons': reasons,
        'advice': {
            'core': core_advice,
            'tactical': tactical_advice,
            'leveraged': leveraged_advice,
        },
        'data_quality': {
            'thermometer_updated_at': thermometer.get('updated_at'),
            'leverage_generated_at': leverage.get('generated_at'),
            'leverage_is_stale': bool(leverage.get('is_stale')),
            'market_data_quality': leverage.get('market_data_quality'),
            'portfolio_source': corr.get('source', 'none'),
        },
        'thermometer': thermometer,
        'leverage': leverage,
        'automated_action': False,
        'methodology_version': '1.0',
    }
