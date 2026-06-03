"""生产信号服务:复用 auto_trader.scan_signals 的真实买入候选(含 AI 双路扫描 + 过滤 + 排名),
取 top10 写缓存,供「因子看板/生产信号」面板展示。无 IB 依赖,纯 yfinance + DataStore。"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / 'data' / '.production_signals_cache.json'
TOP_N = 10

_logger = logging.getLogger(__name__)
_scan_lock = threading.Lock()
_scan_running = False


def _entry_score(sig: dict) -> float:
    """与 auto_trader._entry_score 保持同步:rs × 综合 boost + ai_priority_bonus。"""
    rs       = sig.get('rs_score', 0) or 0
    vol      = sig.get('vol_ratio', 1.0) or 1.0
    drawdown = sig.get('drawdown_from_high', -0.15) or -0.15
    insider  = sig.get('insider_score', 0) or 0
    ai       = sig.get('ai_boost', 0.0) or 0.0
    vol_boost       = min(vol / 3.0, 1.0) * 0.15
    proximity_boost = max(0.0, (drawdown + 0.30) / 0.30) * 0.10
    insider_boost   = min(insider / 10.0, 1.0) * 0.10
    base = rs * (1 + vol_boost + proximity_boost + insider_boost + ai)
    if sig.get('ai_priority'):
        base += 0.5
    return base


def _clean(v):
    """NaN/inf → None,递归;datetime → iso。供 json.dumps 用。"""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    return v


def _serialize_signals(raw: dict, universe: str, ai_pool_size: int) -> dict:
    """scan_signals 返回值 → 前端可消费的精简结构。"""
    buy = list(raw.get('buy', []))
    atr_map = raw.get('_atr', {})
    rows = []
    for i, sig in enumerate(buy[:TOP_N]):
        sym   = sig['symbol']
        close = float(sig.get('close') or 0)
        atr14 = atr_map.get(sym)
        # 默认 ATR 止损位:close - 2.5×ATR(与 auto_trader 同公式;前端按账户净值算建议股数)
        stop_price = (close - 2.5 * float(atr14)) if atr14 else None
        rows.append({
            'rank':              i + 1,
            'symbol':            sym,
            'entry_score':       round(_entry_score(sig), 4),
            'rs_score':          round(float(sig.get('rs_score') or 0), 4),
            'vol_ratio':         round(float(sig.get('vol_ratio') or 0), 2),
            'close':             round(close, 2),
            'sector':            sig.get('sector'),
            'industry':          sig.get('industry'),
            'market_cap_b':      sig.get('market_cap_b'),
            'ai_priority':       bool(sig.get('ai_priority')),
            'ai_boost':          round(float(sig.get('ai_boost') or 0), 3),
            'insider_score':     sig.get('insider_score', 0),
            'drawdown_from_high': round(float(sig.get('drawdown_from_high') or 0), 4),
            'atr14':             round(float(atr14), 2) if atr14 else None,
            'stop_price':        round(stop_price, 2) if stop_price else None,
        })
    return _clean({
        'rows':           rows,
        'top_n':          TOP_N,
        'total_buy':      len(buy),
        'sell_alerts':    len(raw.get('sell', [])),
        'spy_brake':      bool(raw.get('spy_brake')),
        'vix_brake':      bool(raw.get('_vix_brake')),
        'vix_close':      raw.get('_vix'),
        'breadth_pct':    raw.get('_breadth'),
        'breadth_cap':    bool(raw.get('_breadth_cap')),
        'universe':       universe,
        'ai_pool_size':   ai_pool_size,
        'last_updated':   datetime.now().isoformat(timespec='seconds'),
    })


def read_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        with open(CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _do_scan(universe: str = 'ai') -> dict:
    """同步跑一次扫描 + 写缓存。"""
    from auto_trader import scan_signals, _load_ai_priority_set
    import config
    _logger.info(f'[ProductionSignals] 扫描中(universe={universe})...')
    raw = scan_signals(
        held_symbols=[],
        extra=[],
        universe=universe,
        min_cap_b=config.MIN_CAP_B,
        max_cap_b=config.MAX_CAP_B,
        deny_industries=config.DENY_INDUSTRIES,
        force_refresh_recent_days=2,   # 强制重拉最近 2 个交易日,根治 stale-but-corrupted 数据
    )
    ai_pool_size = len(_load_ai_priority_set())
    result = _serialize_signals(raw, universe, ai_pool_size)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    _logger.info(f'[ProductionSignals] 完成: {len(result["rows"])} 只 (total_buy={result["total_buy"]})')
    return result


def trigger_scan_background(universe: str = 'ai') -> dict:
    """触发后台扫描,立即返回当前缓存(可能为旧或 None)。"""
    global _scan_running
    with _scan_lock:
        if _scan_running:
            cached = read_cache() or {}
            return {**cached, 'scanning': True}
        _scan_running = True

    def _bg():
        global _scan_running
        try:
            _do_scan(universe)
        except Exception as e:
            _logger.error(f'[ProductionSignals] 扫描失败: {e}', exc_info=True)
        finally:
            with _scan_lock:
                _scan_running = False

    threading.Thread(target=_bg, daemon=True).start()
    cached = read_cache() or {}
    return {**cached, 'scanning': True}


def get_signals() -> dict:
    """读缓存;无缓存自动触发后台首次扫描。"""
    cached = read_cache()
    if cached is None:
        return trigger_scan_background()
    return {**cached, 'scanning': _scan_running}


# CLI 入口(供 scheduler 调用)
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    _do_scan()
