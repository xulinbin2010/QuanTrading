"""社区热度服务层：监控集组装 + z-score 异动检测 + 榜单缓存。

信号定义：**异动**（相对自身 7 日基线的偏离），不是热度绝对值——
NVDA 天天霸榜没有信息量，冷门票提及数从 5 跳到 80 才是信号。

第一期为纯观察层：不进 entry_score、不碰交易信号（WSB 看多比例在顶部往往最高）。
"""
from __future__ import annotations

import os
import sys
import json
import math
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_BUZZ_CACHE = ROOT / 'data' / '.social_buzz_cache.json'

SPIKE_Z = 2.0          # 异动阈值（先用 2 跑两周看体感再调）
BASELINE_MIN_DAYS = 3  # 基线最少历史天数，不足则 z 记 None（积累期）


# ── 监控集组装（AI 池 + 持仓）────────────────────────────────────────────

def _monitored() -> dict[str, str]:
    """返回 {symbol: 标签}。持仓标签优先于 AI 池（异动提醒只看持仓票）。"""
    tags: dict[str, str] = {}
    try:
        from web.services.ai_tracker_svc import load_universe
        for gv in load_universe().get('groups', {}).values():
            for s in gv.get('symbols', []):
                tags[str(s).upper()] = 'AI池'
    except Exception as e:
        _logger.warning(f'[social] 加载 ai_universe 失败：{e}')
    try:
        from web.services.intel_svc import _holdings_for_intel, _news_symbols
        holds = _holdings_for_intel()
        for s in _news_symbols(holds):    # 杠杆 ETF 展开到底层个股
            tags[str(s).upper()] = '持仓'
    except Exception as e:
        _logger.warning(f'[social] 加载持仓失败（不影响 AI 池采集）：{e}')
    return tags


def collect_now() -> dict:
    """采集一轮（三源→DB）并重建榜单缓存。全程约 40-90 秒（StockTwits 逐票限速）。"""
    from core.social_buzz import collect
    tags = _monitored()
    if not tags:
        raise ValueError('监控集为空：ai_universe.json 不可读且无持仓记录')
    # StockTwits 配额有限：持仓最优先，其余按池内顺序；单轮 cap 在采集器内
    st_priority = ([s for s, t in tags.items() if t == '持仓']
                   + [s for s, t in tags.items() if t != '持仓'])
    summary = collect(set(tags), st_priority)
    board = build_board()
    summary['spikes'] = len([r for r in board['rows'] if r.get('spike')])
    return summary


# ── z-score 异动检测 ─────────────────────────────────────────────────────

def _zscore_map(daily_rows: list, value_idx: int = 2) -> dict[str, dict]:
    """从日度聚合行算每只票的今日值 vs 前 7 日基线 z-score。

    daily_rows: get_social_daily 输出（symbol, trade_date, mentions, ...），按日期升序。
    缺日按 0 补（不在榜 = 热度低于截断线）；历史不足 BASELINE_MIN_DAYS 天 → z=None。
    """
    by_sym: dict[str, dict[str, float]] = {}
    all_dates: set[str] = set()
    for row in daily_rows:
        sym, td = row[0], row[1]
        v = row[value_idx] or 0
        by_sym.setdefault(sym, {})[td] = float(v)
        all_dates.add(td)
    if not all_dates:
        return {}
    dates = sorted(all_dates)
    today = dates[-1]
    base_dates = dates[:-1][-7:]   # 今日之前最多 7 个采样日

    out: dict[str, dict] = {}
    for sym, series in by_sym.items():
        cur = series.get(today, 0.0)
        base = [series.get(d, 0.0) for d in base_dates]
        if len(base) < BASELINE_MIN_DAYS:
            out[sym] = {'today': cur, 'avg7': None, 'z': None}
            continue
        mean = sum(base) / len(base)
        var = sum((x - mean) ** 2 for x in base) / len(base)
        std = math.sqrt(var)
        std = max(std, max(mean * 0.25, 3.0))   # 下限防低基数噪音（5→15 不该算异动爆表）
        out[sym] = {'today': cur, 'avg7': round(mean, 1),
                    'z': round((cur - mean) / std, 2)}
    return out


def build_board() -> dict:
    """从 DB 组装热度榜并写缓存：apewisdom 主信号 + reddit 标题样本 + stocktwits 情绪。"""
    from core.database import Database
    db = Database()
    db.connect()
    ape_daily = db.get_social_daily('apewisdom', days=14)
    red_daily = db.get_social_daily('reddit_posts', days=14)
    st_daily  = db.get_social_daily('stocktwits', days=2)
    # 最近一轮 reddit 标题样本（extra 里，仅展示用）
    titles: dict[str, list] = {}
    if db.conn:
        db.cursor.execute("""
            SELECT symbol, extra FROM social_mentions
             WHERE source = 'reddit_posts' AND trade_date = (
                   SELECT MAX(trade_date) FROM social_mentions WHERE source = 'reddit_posts')
             ORDER BY id
        """)
        for sym, extra in db.cursor.fetchall():
            try:
                titles[sym] = (json.loads(extra) or {}).get('titles', [])
            except (TypeError, ValueError):
                pass
    db.close()

    tags = _monitored()
    ape_z = _zscore_map(ape_daily)
    red_z = _zscore_map(red_daily)
    # 最新采样日的全站排名 / StockTwits 情绪
    ape_today = max((r[1] for r in ape_daily), default='')
    ranks = {r[0]: r[3] for r in ape_daily if r[1] == ape_today}
    st_today = max((r[1] for r in st_daily), default='')
    st_map = {r[0]: {'msgs_24h': r[2], 'bull': r[5] or 0, 'bear': r[6] or 0}
              for r in st_daily if r[1] == st_today}

    rows = []
    for sym in sorted(set(ape_z) | set(red_z) | set(st_map)):
        if sym not in tags:
            continue
        a = ape_z.get(sym, {})
        rd = red_z.get(sym, {})
        st = st_map.get(sym, {})
        z = a.get('z')
        labeled = (st.get('bull', 0) + st.get('bear', 0))
        rows.append({
            'symbol':      sym,
            'tag':         tags[sym],
            'mentions':    a.get('today'),
            'avg7':        a.get('avg7'),
            'z':           z,
            'rank':        ranks.get(sym),
            'reddit_posts': rd.get('today'),
            'reddit_z':    rd.get('z'),
            'st_msgs':     st.get('msgs_24h'),
            'bull_pct':    round(st['bull'] / labeled, 2) if labeled >= 5 else None,
            'titles':      titles.get(sym, []),
            'spike':       bool(z is not None and z >= SPIKE_Z),
        })
    # 异动在前，其余按今日提及降序
    rows.sort(key=lambda r: (-(r['z'] if r['z'] is not None else -99),
                             -(r['mentions'] or 0)))

    # 基线积累进度：有多少个历史采样日
    ape_days = len({r[1] for r in ape_daily})
    result = {
        'as_of': datetime.now().isoformat(timespec='seconds'),
        'baseline_days': max(0, ape_days - 1),
        'spike_z': SPIKE_Z,
        'alerts': [r['symbol'] for r in rows if r['spike'] and r['tag'] == '持仓'],
        'rows': rows,
    }
    try:
        _BUZZ_CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=1), 'utf-8')
    except Exception:
        pass
    return result


def get_cached_board() -> dict | None:
    if _BUZZ_CACHE.exists():
        try:
            return json.loads(_BUZZ_CACHE.read_text('utf-8'))
        except Exception:
            return None
    return None


# ── CLI（供调度任务）─────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='社区热度采集（Reddit/StockTwits）')
    ap.add_argument('--collect', action='store_true', help='采集一轮并重建榜单缓存')
    args = ap.parse_args()
    if args.collect:
        try:
            s = collect_now()
            print(f"[social] 完成：apewisdom {s['apewisdom']} / reddit {s['reddit_posts']} / "
                  f"stocktwits {s['stocktwits']}，异动 {s['spikes']} 只")
        except Exception as e:
            print(f'[social] 采集失败：{e}')
            sys.exit(1)
    else:
        ap.print_help()
