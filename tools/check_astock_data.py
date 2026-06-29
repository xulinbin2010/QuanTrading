"""A 股本地 Parquet 数据体检 + 自愈工具。

针对的核心 bug：盘中实时快照 topup 把「今日 bar」续到陈旧 bar 上（中间缺交易日），
导致「今日涨幅」被算成跨多日涨幅（如 601133 06-17→06-29 算出假 +27%，真值 +10%）。

检测 6 类问题（全部离线，用 HS300 指数作交易日历，自动区分节假日 vs 真缺口）：
  1. tail_gap     最近窗口内缺交易日（最可能是「topup 续旧 bar」bug，可自愈）
  2. old_gap      历史中缺交易日（多为停牌，合理；仅提示，不默认修）
  3. abnormal     无缺口的相邻两根涨跌幅超过该股涨跌停板（数据错/复权跳变/代码错位）
  4. stale        最后一根落后指数最新交易日多日（数据没更新/退市/长停）
  5. future       存在未来日期 bar（异常）
  6. bad_value    OHLC 含 NaN/<=0，或 high<low 等逻辑错误

用法：
  python -m tools.check_astock_data                 # 体检主题池(默认)，仅报告
  python -m tools.check_astock_data --all           # 体检 data/stocks_a 下所有票
  python -m tools.check_astock_data --fix            # 报告 + 自愈(重拉缺口票覆盖本地)
  python -m tools.check_astock_data --symbols 601133 300433
  python -m tools.check_astock_data --recent-days 30 # tail_gap 判定窗口(默认30日历天)
"""
from __future__ import annotations

import argparse
import glob
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from core.astock_data_store import AStockDataStore

STOCKS_DIR = Path('data/stocks_a')
BENCHMARK = 'HS300'


def _limit_band(code: str) -> float:
    """该股单日涨跌停幅度（按板块；ST 无法仅凭代码识别，按主板 10% 处理）。"""
    if code.startswith(('30', '68')):          # 创业板 / 科创板
        return 0.20
    if code.startswith(('8', '4', '92', '43', '83', '87', '88')):  # 北交所(含新 920 前缀)
        return 0.30
    return 0.10                                # 沪深主板 60/00/601/603/605...


def _load_index_calendar() -> list[date] | None:
    """HS300 指数日线的交易日序列，作为「应有交易日」基准日历。"""
    p = STOCKS_DIR / f'_idx_{BENCHMARK}.parquet'
    if not p.exists():
        return None
    try:
        idx = pd.read_parquet(p)
    except Exception:
        return None
    if idx.empty:
        return None
    return [d.date() for d in idx.index]


def _theme_symbols() -> list[str]:
    import core.astock_universe as au
    syms: list[str] = []
    for gv in au.load_themes().get('groups', {}).values():
        syms += [str(s).zfill(6) for s in gv.get('symbols', [])]
    return list(dict.fromkeys(syms))


def _all_local_symbols() -> list[str]:
    out = []
    for f in sorted(glob.glob(str(STOCKS_DIR / '[0-9]*.parquet'))):
        out.append(os.path.basename(f)[:6])
    return out


def check_one(code: str, cal: list[date], cal_set: set[date],
              recent_days: int, today: date) -> dict:
    """对单只做离线体检，返回 {code, issues:[...], last_date, ...}。issues 为问题列表。"""
    p = STOCKS_DIR / f'{code}.parquet'
    res: dict = {'code': code, 'issues': [], 'last_date': None, 'rows': 0}
    if not p.exists():
        res['issues'].append(('missing_file', '本地无数据文件'))
        return res
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        res['issues'].append(('unreadable', f'读取失败：{e}'))
        return res
    if df.empty:
        res['issues'].append(('empty', '空文件'))
        return res

    df = df.sort_index()
    dates = [d.date() for d in df.index]
    res['rows'] = len(df)
    res['last_date'] = str(dates[-1])
    band = _limit_band(code)

    # 6. bad_value：NaN / <=0 / high<low / close 越界
    for col in ('open', 'high', 'low', 'close', 'volume'):
        if col not in df.columns:
            res['issues'].append(('bad_value', f'缺列 {col}'))
    if all(c in df.columns for c in ('open', 'high', 'low', 'close')):
        bad = df[(df[['open', 'high', 'low', 'close']] <= 0).any(axis=1)
                 | df[['open', 'high', 'low', 'close']].isna().any(axis=1)]
        if not bad.empty:
            res['issues'].append(('bad_value', f'{len(bad)} 根 OHLC 含 NaN/<=0，'
                                                f'首例 {bad.index[0].date()}'))
        hl = df[df['high'] < df['low']]
        if not hl.empty:
            res['issues'].append(('bad_value', f'{len(hl)} 根 high<low，首例 {hl.index[0].date()}'))

    # 5. future：未来日期
    fut = [d for d in dates if d > today]
    if fut:
        res['issues'].append(('future', f'{len(fut)} 根未来日期，首例 {fut[0]}'))

    # 重复日期
    dup = df.index[df.index.duplicated()]
    if len(dup):
        res['issues'].append(('dup_date', f'{len(dup)} 个重复日期，首例 {dup[0].date()}'))

    # 用指数日历找「应有但缺失」的交易日（仅在本股 [first,last] 区间内、且 <= today）
    if cal:
        first, last = dates[0], dates[-1]
        stk_set = set(dates)
        recent_cut = today - timedelta(days=recent_days)
        missing = [d for d in cal if first < d <= last and d not in stk_set]
        if missing:
            recent_missing = [d for d in missing if d >= recent_cut]
            if recent_missing:
                res['tail_missing'] = recent_missing   # 供 main 联网甄别停牌 vs 漏抓
                res['issues'].append(('tail_gap',
                    f'最近 {recent_days} 天内缺 {len(recent_missing)} 个交易日：'
                    f'{recent_missing[0]}…{recent_missing[-1]}'))
            old_missing = [d for d in missing if d < recent_cut]
            if old_missing:
                res['issues'].append(('old_gap',
                    f'历史缺 {len(old_missing)} 个交易日(多为停牌)：'
                    f'{old_missing[0]}…{old_missing[-1]}'))

        # 4. stale：最后一根落后指数最新交易日 > 3 个交易日
        idx_after = [d for d in cal if d > last]
        if len(idx_after) > 3:
            res['issues'].append(('stale',
                f'最后一根 {last} 落后指数最新交易日 {len(idx_after)} 个交易日'))

    # 3. abnormal：相邻「无缺口」两根涨跌幅超板（跳过新股前 5 根、跳过有缺口的相邻对）
    if cal and 'close' in df.columns and len(df) > 6:
        closes = df['close'].values
        for i in range(6, len(df)):
            d_prev, d_cur = dates[i - 1], dates[i]
            # 该相邻对之间若有指数交易日 => 是缺口，多日涨幅自然大，不算 abnormal
            gap_between = any(d_prev < d < d_cur for d in cal_set)
            if gap_between:
                continue
            cp = closes[i - 1]
            if cp <= 0:
                continue
            pct = closes[i] / cp - 1
            if abs(pct) > band + 0.015:
                res['issues'].append(('abnormal',
                    f'{d_cur} 单日 {pct*100:+.1f}% 超 {band*100:.0f}% 涨跌停板'
                    f'（{cp:.2f}→{closes[i]:.2f}）'))
                break   # 报首例即可
    return res


def _snapshot(codes: list[str]) -> dict:
    out = {}
    for c in codes:
        p = STOCKS_DIR / f'{c}.parquet'
        if p.exists():
            try:
                d = pd.read_parquet(p)
                out[c] = (str(d.index[-1].date()), len(d))
            except Exception:
                out[c] = None
    return out


def _full_redownload(store: AStockDataStore, codes: list[str]) -> int:
    """全量重拉前复权日线、整文件覆盖（修前复权基准断裂/坏值/未来日期：必须丢弃旧文件
    重算，因为旧 bar 整段在错误 qfq 基准上，merge 无法纠正）。start 取原文件最早日期，
    保持原覆盖范围。"""
    n = 0
    for c in codes:
        p = STOCKS_DIR / f'{c}.parquet'
        start = '2023-01-01'
        if p.exists():
            try:
                old = pd.read_parquet(p)
                if not old.empty:
                    start = str(old.index[0].date())
            except Exception:
                pass
        fresh = store._download(c, start)
        if not fresh.empty:
            fresh.to_parquet(p)   # 整文件覆盖，不 merge
            n += 1
    return n


def fix_codes(full_codes: list[str], merge_codes: list[str], days: int = 25) -> dict:
    """两类修复：full_codes 全量覆盖（qfq 断裂/坏值），merge_codes 增量合并（缺口/落后）。"""
    store = AStockDataStore()
    all_codes = sorted(set(full_codes) | set(merge_codes))
    before = _snapshot(all_codes)
    nf = _full_redownload(store, full_codes) if full_codes else 0
    nm = store.refresh_recent(merge_codes, days=days) if merge_codes else 0
    after = _snapshot(all_codes)
    return {'n_full': nf, 'n_merge': nm, 'before': before, 'after': after}


# 可自愈问题类型 → 修复策略
# 全量覆盖（旧数据整段错，merge 救不了）
FULL_FIX = {'abnormal', 'bad_value', 'future', 'dup_date'}
# 增量合并即可（只是近端缺/落后）
MERGE_FIX = {'tail_gap', 'stale'}
FIXABLE = FULL_FIX | MERGE_FIX


def main():
    ap = argparse.ArgumentParser(description='A 股本地数据体检 + 自愈')
    ap.add_argument('--all', action='store_true', help='体检 data/stocks_a 下所有票（默认仅主题池）')
    ap.add_argument('--symbols', nargs='+', help='只检指定代码')
    ap.add_argument('--fix', action='store_true', help='对可修复问题重拉日线覆盖本地')
    ap.add_argument('--recent-days', type=int, default=30, help='tail_gap 判定窗口（日历天，默认30）')
    ap.add_argument('--offline', action='store_true', help='不联网甄别 tail_gap（停牌 vs 漏抓）')
    args = ap.parse_args()

    cal = _load_index_calendar()
    cal_set = set(cal) if cal else set()
    today = date.today()
    if not cal:
        print('⚠️  缺 HS300 指数日历(data/stocks_a/_idx_HS300.parquet)，'
              '缺口/stale 检测跳过，仅做数值/未来日期检测')

    if args.symbols:
        symbols = [str(s).zfill(6) for s in args.symbols]
    elif args.all:
        symbols = _all_local_symbols()
    else:
        symbols = _theme_symbols()

    print(f'体检 {len(symbols)} 只 | 基准交易日历最新：'
          f'{cal[-1] if cal else "N/A"} | today={today}\n')

    results = [check_one(c, cal, cal_set, args.recent_days, today) for c in symbols]

    # tail_gap 甄别：缺口区间联网拉一次，akshare 也无该日 → 真停牌(降级)，有 → 真漏抓(保留红)
    if not args.offline:
        store = AStockDataStore()
        cands = [r for r in results if r.get('tail_missing')]
        if cands:
            print(f'甄别 {len(cands)} 只 tail_gap 候选（联网核对停牌/漏抓）...')
            for r in cands:
                miss = r['tail_missing']
                got = store._download(r['code'], (min(miss)).strftime('%Y-%m-%d'))
                got_dates = set(d.date() for d in got.index) if not got.empty else set()
                still_missing = [d for d in miss if d in got_dates]   # akshare 有但本地无 = 真漏抓
                r['issues'] = [(t, m) for t, m in r['issues'] if t != 'tail_gap']
                if still_missing:
                    r['issues'].append(('tail_gap',
                        f'漏抓 {len(still_missing)} 个交易日(akshare 有数据)：'
                        f'{still_missing[0]}…{still_missing[-1]}'))
                else:
                    r['issues'].append(('recent_suspend',
                        f'最近停牌 {len(miss)} 个交易日(akshare 也无，数据正常)：'
                        f'{miss[0]}…{miss[-1]}'))
            print()

    flagged = [r for r in results if r['issues']]

    # 按问题类型归类打印
    by_type: dict[str, list] = {}
    for r in flagged:
        for typ, msg in r['issues']:
            by_type.setdefault(typ, []).append((r['code'], msg))

    order = ['tail_gap', 'abnormal', 'future', 'dup_date', 'bad_value',
             'stale', 'recent_suspend', 'old_gap', 'missing_file', 'empty', 'unreadable']
    type_label = {
        'tail_gap': '🔴 最近漏抓交易日(akshare有但本地无，今日涨幅会算错)',
        'abnormal': '🔴 单日涨跌幅超板(数据错/复权跳变/代码错位)',
        'future':   '🔴 未来日期 bar',
        'dup_date': '🟠 重复日期',
        'bad_value':'🟠 OHLC 数值异常',
        'stale':    '🟡 数据落后(未更新/长停/退市)',
        'recent_suspend': '⚪ 最近停牌(akshare 也无数据，正常)',
        'old_gap':  '⚪ 历史缺交易日(多为停牌，通常合理)',
        'missing_file': '⚪ 无本地文件',
        'empty':    '⚪ 空文件',
        'unreadable':'🟠 读取失败',
    }
    for typ in order:
        items = by_type.get(typ)
        if not items:
            continue
        print(f'{type_label.get(typ, typ)}  共 {len(items)} 只')
        for code, msg in items:
            print(f'    {code}  {msg}')
        print()

    if not flagged:
        print('✅ 全部正常，未发现问题。')
        return

    # 分两类自愈目标（old_gap/missing_file/empty 不自动修）
    full_codes = sorted({r['code'] for r in flagged
                         if any(t in FULL_FIX for t, _ in r['issues'])})
    merge_codes = sorted({r['code'] for r in flagged
                          if any(t in MERGE_FIX for t, _ in r['issues'])
                          and r['code'] not in full_codes})
    fix_targets = sorted(set(full_codes) | set(merge_codes))
    print(f'可自愈票 {len(fix_targets)} 只 '
          f'(全量覆盖 {len(full_codes)} / 增量合并 {len(merge_codes)})：'
          f'{" ".join(fix_targets) if fix_targets else "无"}')

    if args.fix and fix_targets:
        print('\n── 开始自愈：qfq断裂/坏值=全量覆盖；缺口/落后=增量合并 ──')
        rep = fix_codes(full_codes, merge_codes)
        print(f'全量重拉 {rep["n_full"]} 只 + 增量合并 {rep["n_merge"]} 只\n')
        for c in fix_targets:
            tag = '全量' if c in full_codes else '增量'
            print(f'    [{tag}] {c}  {rep["before"].get(c)} → {rep["after"].get(c)}')
        print()
        # 复检
        results2 = [check_one(c, cal, cal_set, args.recent_days, today) for c in fix_targets]
        still = [r for r in results2 if any(t in FIXABLE for t, _ in r['issues'])]
        if still:
            print(f'⚠️  复检仍有 {len(still)} 只存在问题（可能真停牌/退市，非数据缺失）：')
            for r in still:
                print(f'    {r["code"]}  ' + '; '.join(m for _, m in r['issues']))
        else:
            print('✅ 自愈完成，复检全部通过。')
    elif fix_targets:
        print('\n（加 --fix 执行自愈重拉）')


if __name__ == '__main__':
    main()
