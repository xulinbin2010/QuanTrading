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
import statistics
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_BUZZ_CACHE = ROOT / 'data' / '.social_buzz_cache.json'
BOARD_SCHEMA_VERSION = 3

SPIKE_Z = 2.0          # 异动阈值（先用 2 跑两周看体感再调）
BASELINE_MIN_DAYS = 3  # 基线最少历史天数，不足则 z 记 None（积累期）
SENTIMENT_MIN_LABELS = 5       # 达到后展示 raw bullish percentage
SENTIMENT_RELIABLE_LABELS = 10 # 达到后才判断相对基线的情绪偏移
SENTIMENT_SHIFT_PP = 15.0      # 相对自身基线偏移阈值（百分点）
_CN = ZoneInfo('Asia/Shanghai')
_ET = ZoneInfo('America/New_York')
_UTC = ZoneInfo('UTC')


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
        # 调度器/CLI 进程不得连接 IB Gateway：Web 常驻进程才持有 master clientId。
        # 社区热度采集使用最近一次本地实盘诊断持仓；没有本地台账时仍可采 AI 池。
        holds = _holdings_for_intel(include_ib=False)
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

def _is_weekday(value: str) -> bool:
    try:
        return date.fromisoformat(value).weekday() < 5
    except ValueError:
        return False


def _zscore_map(daily_rows: list, value_idx: int = 2,
                current_complete: bool = True,
                current_date: str | None = None) -> dict[str, dict]:
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
    today = current_date or dates[-1]
    base_dates = [d for d in dates if d < today and _is_weekday(d)][-7:]
    is_trading_day = _is_weekday(today)

    out: dict[str, dict] = {}
    for sym, series in by_sym.items():
        cur = series.get(today)
        if cur is None and current_complete:
            cur = 0.0
        base = [series.get(d, 0.0) for d in base_dates]
        if not is_trading_day:
            out[sym] = {
                'today': cur, 'avg7': round(sum(base) / len(base), 1) if base else None,
                'z': None, 'status': 'non_trading_day',
            }
            continue
        if cur is None:
            out[sym] = {'today': None, 'avg7': None, 'z': None, 'status': 'unavailable'}
            continue
        if len(base) < BASELINE_MIN_DAYS:
            out[sym] = {
                'today': cur, 'avg7': None, 'z': None,
                'status': 'baseline_accumulating',
            }
            continue
        mean = sum(base) / len(base)
        var = sum((x - mean) ** 2 for x in base) / len(base)
        std = math.sqrt(var)
        std = max(std, max(mean * 0.25, 3.0))   # 下限防低基数噪音（5→15 不该算异动爆表）
        out[sym] = {
            'today': cur, 'avg7': round(mean, 1),
            'z': round((cur - mean) / std, 2), 'status': 'ready',
        }
    return out


def _extra(row: tuple) -> dict:
    if len(row) <= 8 or not row[8]:
        return {}
    try:
        return json.loads(row[8]) or {}
    except (TypeError, ValueError):
        return {}


def _db_time_et(value) -> str:
    """SQLite CURRENT_TIMESTAMP 是 UTC；统一转成带 ET 标签的展示时间。"""
    if not isinstance(value, datetime):
        return str(value or '')
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value.astimezone(_ET).strftime('%Y-%m-%d %H:%M ET')


def _sentiment_map(daily_rows: list, current_date: str | None = None) -> dict[str, dict]:
    """严格 24h StockTwits 情绪 + 相对自身历史中位数的偏移。

    旧样本没有 window_hours=24，不混入新基线，避免口径切换后产生伪变化。
    """
    if not daily_rows:
        return {}
    today = current_date or max(r[1] for r in daily_rows)
    by_sym: dict[str, list] = {}
    for row in daily_rows:
        by_sym.setdefault(row[0], []).append(row)

    out: dict[str, dict] = {}
    for sym, rows in by_sym.items():
        current = next((r for r in reversed(rows) if r[1] == today), None)
        if current is None or _extra(current).get('window_hours') != 24:
            continue
        bull, bear = current[5] or 0, current[6] or 0
        labeled = bull + bear
        pct = bull / labeled if labeled else None
        histories = []
        for row in rows:
            if row[1] >= today or _extra(row).get('window_hours') != 24:
                continue
            b, br = row[5] or 0, row[6] or 0
            if b + br >= SENTIMENT_RELIABLE_LABELS:
                histories.append(b / (b + br))
        histories = histories[-20:]
        baseline = statistics.median(histories) if len(histories) >= BASELINE_MIN_DAYS else None
        reliable = labeled >= SENTIMENT_RELIABLE_LABELS
        delta_pp = round((pct - baseline) * 100, 1) if reliable and baseline is not None else None
        if not _is_weekday(today):
            status, shift = 'non_trading_day', 'unknown'
        elif labeled < SENTIMENT_MIN_LABELS:
            status, shift = 'insufficient', 'unknown'
        elif not reliable:
            status, shift = 'low_sample', 'unknown'
        elif baseline is None:
            status, shift = 'baseline_accumulating', 'unknown'
        elif delta_pp <= -SENTIMENT_SHIFT_PP:
            status, shift = 'ready', 'deteriorating'
        elif delta_pp >= SENTIMENT_SHIFT_PP:
            status, shift = 'ready', 'improving'
        else:
            status, shift = 'ready', 'normal'
        out[sym] = {
            'msgs_24h': current[2],
            'bull': bull,
            'bear': bear,
            'labeled': labeled,
            'sampled': _extra(current).get('sampled_24h', current[2]),
            'bull_pct': round(pct, 2) if pct is not None and labeled >= SENTIMENT_MIN_LABELS else None,
            'bull_baseline': round(baseline, 2) if baseline is not None else None,
            'bull_delta_pp': delta_pp,
            'sentiment_status': status,
            'sentiment_shift': shift,
            'sentiment_reliable': reliable and baseline is not None and _is_weekday(today),
            'sample_at': _db_time_et(current[7]),
        }
    return out


def build_board() -> dict:
    """从 DB 组装热度榜并写缓存：apewisdom 主信号 + reddit 标题样本 + stocktwits 情绪。"""
    from core.database import Database
    db = Database()
    db.connect()
    ape_daily = db.get_social_daily('apewisdom', days=14)
    red_daily = db.get_social_daily('reddit_posts', days=14)
    st_daily  = db.get_social_daily('stocktwits', days=45)
    run_rows = db.get_latest_social_collection_runs()
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
    source_status = {}
    for source, td, status, requested, covered, row_count, detail, created_at in run_rows:
        source_status[source] = {
            'status': status,
            'trade_date': td,
            'requested': requested,
            'covered': covered,
            'rows': row_count,
            'detail': detail or '',
            'as_of': _db_time_et(created_at),
        }
    # 兼容升级前已有 DB：旧样本可继续展示热度，但没有覆盖审计；StockTwits
    # 旧情绪口径不会进入 _sentiment_map，必须重新采集后才显示。
    latest_by_source = {
        'apewisdom': max((r[1] for r in ape_daily), default=''),
        'reddit_posts': max((r[1] for r in red_daily), default=''),
        'stocktwits': max((r[1] for r in st_daily), default=''),
    }
    for source in ('apewisdom', 'stocktwits', 'reddit_posts'):
        if source in source_status:
            continue
        disabled = (
            source == 'reddit_posts'
            and not (os.environ.get('REDDIT_CLIENT_ID') and os.environ.get('REDDIT_CLIENT_SECRET'))
        )
        source_status[source] = {
            'status': 'disabled' if disabled else 'partial',
            'trade_date': latest_by_source[source],
            'requested': None,
            'covered': None,
            'rows': None,
            'detail': (
                '未配置 REDDIT_CLIENT_ID/SECRET'
                if disabled else '升级前样本没有覆盖审计；请重新采集'
            ),
            'as_of': '',
        }
    db.close()

    tags = _monitored()
    ape_state = source_status.get('apewisdom', {})
    red_state = source_status.get('reddit_posts', {})
    st_state = source_status.get('stocktwits', {})
    ape_z = _zscore_map(
        ape_daily,
        current_complete=ape_state.get('status') == 'ok',
        current_date=ape_state.get('trade_date'),
    )
    red_z = _zscore_map(
        red_daily,
        current_complete=red_state.get('status') == 'ok',
        current_date=red_state.get('trade_date'),
    )
    # 当前轮被 Cloudflare/网络完全阻断时，不能把本地旧样本伪装为本轮情绪。
    st_map = (
        _sentiment_map(st_daily, current_date=st_state.get('trade_date'))
        if st_state.get('status') in ('ok', 'partial') else {}
    )
    # 最新采样日的全站排名 / StockTwits 情绪
    ape_today = max((r[1] for r in ape_daily), default='')
    ranks = {r[0]: r[3] for r in ape_daily if r[1] == ape_today}

    rows = []
    for sym in sorted(set(ape_z) | set(red_z) | set(st_map)):
        if sym not in tags:
            continue
        a = ape_z.get(sym, {})
        rd = red_z.get(sym, {})
        st = st_map.get(sym, {})
        z = a.get('z')
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
            'bull_pct':    st.get('bull_pct'),
            'bull_cnt':    st.get('bull'),
            'bear_cnt':    st.get('bear'),
            'labeled_cnt': st.get('labeled'),
            'sampled_cnt': st.get('sampled'),
            'bull_baseline': st.get('bull_baseline'),
            'bull_delta_pp': st.get('bull_delta_pp'),
            'sentiment_status': st.get('sentiment_status', 'unavailable'),
            'sentiment_shift': st.get('sentiment_shift', 'unknown'),
            'sentiment_reliable': st.get('sentiment_reliable', False),
            'heat_status': a.get('status', 'unavailable'),
            'sample_at': st.get('sample_at'),
            'titles':      titles.get(sym, []),
            'spike':       bool(z is not None and z >= SPIKE_Z),
        })
    # 持仓情绪恶化/热度异动优先，其余按 z 与今日提及排序。
    rows.sort(key=lambda r: (
        0 if r['tag'] == '持仓' and r['sentiment_shift'] == 'deteriorating' else 1,
        0 if r['tag'] == '持仓' and r['spike'] else 1,
        -(r['z'] if r['z'] is not None else -99),
        -(r['mentions'] or 0),
    ))

    ape_dates = sorted({r[1] for r in ape_daily})
    latest_date = ape_state.get('trade_date') or (ape_dates[-1] if ape_dates else '')
    baseline_days = len([d for d in ape_dates[:-1] if _is_weekday(d)])
    now_cn = datetime.now(_CN)
    now_et = now_cn.astimezone(_ET)
    result = {
        'schema_version': BOARD_SCHEMA_VERSION,
        'as_of': now_cn.isoformat(timespec='seconds'),
        'as_of_cn': now_cn.strftime('%Y-%m-%d %H:%M'),
        'as_of_et': now_et.strftime('%Y-%m-%d %H:%M ET'),
        'trade_date': latest_date,
        'non_trading_day': bool(latest_date and not _is_weekday(latest_date)),
        'baseline_days': baseline_days,
        'spike_z': SPIKE_Z,
        'sentiment_shift_pp': SENTIMENT_SHIFT_PP,
        'sentiment_min_labels': SENTIMENT_MIN_LABELS,
        'sentiment_reliable_labels': SENTIMENT_RELIABLE_LABELS,
        'source_status': source_status,
        'alerts': [r['symbol'] for r in rows if r['spike'] and r['tag'] == '持仓'],
        'sentiment_alerts': [
            r['symbol'] for r in rows
            if r['sentiment_shift'] == 'deteriorating' and r['tag'] == '持仓'
        ],
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
            cached = json.loads(_BUZZ_CACHE.read_text('utf-8'))
            if cached.get('schema_version') != BOARD_SCHEMA_VERSION:
                return build_board()
            return cached
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
