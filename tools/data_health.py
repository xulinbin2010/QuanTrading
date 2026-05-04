"""
数据健康检查 + 自动修复系统

7 个检查维度：
  1. 历史缺失    本地最早日期 vs yfinance 真实上市日 (gap > 30d)
  2. 价格偏移    本地首日收盘价 vs yfinance 同日价格 (偏差 > 15%)
  3. 数据空洞    数据序列中间缺失的交易日 (参考 SPY 日历)
  4. 更新时效    最新数据 vs SPY 最新日期 (gap > 4d)
  5. 成交量异常  单日成交量 > 10x 近 20 日中位数 (可能拆股)
  6. pkl 缓存陈旧  earnings/insider/stock_info 超 TTL 未刷新
  7. 关键资产存在性  SPY / ^VIX / 行业 ETF / 杠杆 ETF 必须存在

用法：
  python -m tools.data_health                        # 仅扫描，输出报告
  python -m tools.data_health --fix                  # 扫描 + 自动修复
  python -m tools.data_health --fix --use-ibkr       # 修复时用 IBKR 兜底
  python -m tools.data_health --critical-only        # 只检查关键资产
  python -m tools.data_health --scope etf            # etf | leveraged | sp500 | full
  python -m tools.data_health --symbols NVDA QQQ     # 只检查指定标的
"""
from __future__ import annotations
import sys
import os
import time
import json
import pickle
import argparse
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
import yfinance as yf

# ── 路径 ─────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parents[1]
DATA_DIR   = ROOT / 'data' / 'stocks'
LOGS_DIR   = ROOT / 'logs'
REPORT_DIR = ROOT / 'data'

# ── 关键资产清单 ──────────────────────────────────────────────
CRITICAL_ASSETS = ['SPY', 'QQQ', 'IWM', '^VIX']
SECTOR_ETFS     = ['XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY']
LEVERAGED_ETFS  = ['NVDL', 'TSLL', 'MUU', 'MULL', 'AAPU', 'SSO', 'QLD', 'SQQQ', 'TQQQ', 'SOXL']

# ── pkl 缓存路径与 TTL ────────────────────────────────────────
PKL_CACHES = [
    {'path': ROOT / '.earnings_cache.pkl',   'ttl_hours': 12,  'name': 'earnings'},
    {'path': ROOT / '.insider_cache.pkl',    'ttl_hours': 20,  'name': 'insider'},
    {'path': ROOT / '.stock_info_cache.pkl', 'ttl_hours': 24,  'name': 'stock_info'},
]

# ── 检查阈值 ──────────────────────────────────────────────────
YF_HISTORY_START = '2018-01-01'
GAP_DAYS         = 30    # 历史缺口超过此天数才报告
PRICE_TOL        = 0.15  # 首日价格偏差超过此比例才报告
STALE_DAYS       = 4     # 最新数据比参考基准早超过此天数视为过期
VOLUME_MULT      = 10    # 成交量超过 N 倍中位数视为异常
PKL_WARN_MULT    = 2     # TTL 的 N 倍才发出 warning（避免刚过期就报警）


@dataclass
class HealthIssue:
    symbol:     str
    severity:   str    # 'critical' | 'warning' | 'info'
    category:   str    # 'missing' | 'history_gap' | 'price_offset' | 'data_hole' | 'stale' | 'volume_anomaly' | 'pkl_stale'
    message:    str
    fixable:    bool   = True
    auto_fixed: bool   = False
    details:    dict   = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# 检查函数
# ═══════════════════════════════════════════════════════════

def check_existence(symbols: list[str]) -> list[HealthIssue]:
    """维度 7：关键资产必须存在于本地缓存"""
    issues = []
    for sym in symbols:
        path = DATA_DIR / f'{sym}.parquet'
        sev = 'critical' if sym in CRITICAL_ASSETS else 'warning'
        if not path.exists():
            issues.append(HealthIssue(
                symbol=sym, severity=sev, category='missing',
                message=f'缓存文件不存在，需要下载',
            ))
    return issues


def check_history_and_price(symbols: list[str]) -> list[HealthIssue]:
    """维度 1+2：历史缺失 + 价格偏移（一次 yfinance 调用同时完成两项）"""
    issues = []
    for sym in symbols:
        path = DATA_DIR / f'{sym}.parquet'
        if not path.exists():
            continue   # 维度 7 已报告

        try:
            cached = pd.read_parquet(path)
        except Exception as e:
            issues.append(HealthIssue(
                symbol=sym, severity='warning', category='history_gap',
                message=f'parquet 读取失败: {e}',
            ))
            continue

        if cached.empty:
            issues.append(HealthIssue(
                symbol=sym, severity='warning', category='history_gap',
                message='缓存文件为空',
            ))
            continue

        cached_start = cached.index[0].date()

        # 拉 yfinance 最早数据
        try:
            hist = yf.Ticker(sym).history(start=YF_HISTORY_START, auto_adjust=True)
            if hist.empty:
                continue
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
        except Exception:
            continue

        yf_start = hist.index[0].date()
        gap = (cached_start - yf_start).days

        # 维度 1：历史缺失
        sev = 'critical' if sym in CRITICAL_ASSETS + SECTOR_ETFS else 'warning'
        if gap > GAP_DAYS:
            issues.append(HealthIssue(
                symbol=sym, severity=sev, category='history_gap',
                message=f'历史缺失 {gap} 天（缓存从 {cached_start}，yfinance 从 {yf_start}）',
                details={'cached_start': str(cached_start), 'yf_start': str(yf_start), 'gap_days': gap},
            ))

        # 维度 2：价格偏移
        ts = pd.Timestamp(cached_start)
        if ts in hist.index:
            yf_price = float(hist.loc[ts, 'Close'])
            cached_price = float(cached['close'].iloc[0])
            if yf_price > 0:
                ratio = cached_price / yf_price
                if abs(ratio - 1) > PRICE_TOL:
                    issues.append(HealthIssue(
                        symbol=sym, severity='warning', category='price_offset',
                        message=f'首日价格偏移 {ratio:.2f}x（缓存 {cached_price:.3f} vs yfinance {yf_price:.3f}）',
                        details={'ratio': round(ratio, 3), 'cached_price': cached_price, 'yf_price': yf_price},
                    ))

        time.sleep(0.2)   # 轻量节流

    return issues


def check_data_holes(symbols: list[str], spy_dates: set | None = None) -> list[HealthIssue]:
    """维度 3：数据序列中间是否有缺失交易日（参考 SPY 日历）"""
    issues = []
    if spy_dates is None:
        spy_path = DATA_DIR / 'SPY.parquet'
        if not spy_path.exists():
            return issues
        spy_df = pd.read_parquet(spy_path)
        spy_dates = set(spy_df.index.date)

    for sym in symbols:
        if sym == 'SPY':
            continue
        path = DATA_DIR / f'{sym}.parquet'
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty or len(df) < 10:
            continue

        sym_dates = set(df.index.date)
        start_d = df.index[0].date()
        end_d   = df.index[-1].date()

        # 只检查 sym 存续期间内 SPY 有但 sym 没有的日期
        expected = {d for d in spy_dates if start_d <= d <= end_d}
        missing  = expected - sym_dates
        if len(missing) > 5:   # 允许少量节假日差异
            issues.append(HealthIssue(
                symbol=sym, severity='info', category='data_hole',
                message=f'数据序列中间缺失 {len(missing)} 个交易日',
                details={'missing_count': len(missing),
                         'sample': sorted(str(d) for d in list(missing)[:5])},
                fixable=True,
            ))

    return issues


def check_staleness(symbols: list[str], ref_date: date | None = None) -> list[HealthIssue]:
    """维度 4：最新数据 vs 参考基准（SPY 最新日）"""
    issues = []
    if ref_date is None:
        spy_path = DATA_DIR / 'SPY.parquet'
        if spy_path.exists():
            spy_df = pd.read_parquet(spy_path)
            ref_date = spy_df.index[-1].date() if not spy_df.empty else date.today()
        else:
            ref_date = date.today()

    stale_limit = ref_date - timedelta(days=STALE_DAYS)

    for sym in symbols:
        path = DATA_DIR / f'{sym}.parquet'
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty:
            continue

        latest = df.index[-1].date()
        if latest < stale_limit:
            gap = (ref_date - latest).days
            sev = 'critical' if sym in CRITICAL_ASSETS else 'warning'
            issues.append(HealthIssue(
                symbol=sym, severity=sev, category='stale',
                message=f'数据过期 {gap} 天（最新 {latest}，参考基准 {ref_date}）',
                details={'latest_date': str(latest), 'ref_date': str(ref_date), 'gap_days': gap},
            ))

    return issues


def check_volume_anomaly(symbols: list[str]) -> list[HealthIssue]:
    """维度 5：单日成交量突然 >10x 中位数（可能拆股未复权）"""
    issues = []
    for sym in symbols:
        path = DATA_DIR / f'{sym}.parquet'
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df.empty or 'volume' not in df.columns or len(df) < 30:
            continue

        vol = df['volume'].dropna()
        median = vol.median()
        if median <= 0:
            continue

        spikes = vol[vol > median * VOLUME_MULT]
        if not spikes.empty:
            spike_dates = [str(d.date()) for d in spikes.index[:3]]
            issues.append(HealthIssue(
                symbol=sym, severity='info', category='volume_anomaly',
                message=f'发现 {len(spikes)} 个成交量异常日（>{VOLUME_MULT}x 中位数）',
                details={'spike_dates': spike_dates, 'median_vol': int(median)},
                fixable=False,   # 需人工判断是否真实拆股
            ))

    return issues


def check_pkl_caches() -> list[HealthIssue]:
    """维度 6：pkl 缓存陈旧检查"""
    issues = []
    for cfg in PKL_CACHES:
        path: Path = cfg['path']
        ttl_hours: int = cfg['ttl_hours']
        name: str = cfg['name']

        if not path.exists():
            continue   # 不存在代表未初始化，正常

        try:
            with open(path, 'rb') as f:
                stored = pickle.load(f)
            ts = stored.get('_time')
            if ts is None:
                continue
            age_hours = (datetime.now() - ts).total_seconds() / 3600
            if age_hours > ttl_hours * PKL_WARN_MULT:
                issues.append(HealthIssue(
                    symbol=f'[cache:{name}]',
                    severity='info', category='pkl_stale',
                    message=f'{name} 缓存已 {age_hours:.0f} 小时未更新（TTL={ttl_hours}h）',
                    details={'age_hours': round(age_hours, 1), 'ttl_hours': ttl_hours},
                    fixable=True,
                ))
        except Exception:
            pass

    return issues


# ═══════════════════════════════════════════════════════════
# 修复函数
# ═══════════════════════════════════════════════════════════

def _fix_with_yfinance(sym: str) -> bool:
    """用 yfinance 全量重建 parquet，返回是否成功。"""
    try:
        hist = yf.Ticker(sym).history(start=YF_HISTORY_START, auto_adjust=True)
        if hist.empty:
            return False
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        hist.columns = [c.lower() for c in hist.columns]
        cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in hist.columns]
        df = hist[cols].dropna()
        df.index.name = 'date'
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(DATA_DIR / f'{sym}.parquet')
        return True
    except Exception as e:
        _logger.warning(f'[DataHealth] yfinance 修复 {sym} 失败: {e}')
        return False


def _fix_with_ibkr(sym: str) -> bool:
    """用 IBKRDataStore 修复，返回是否成功（需 IB Gateway 运行）。"""
    try:
        from core.ibkr_data_store import IBKRDataStore
        ibkr = IBKRDataStore()
        ibkr.connect()
        df = ibkr.fetch_df(sym)
        ibkr.disconnect()
        if df is None or df.empty:
            return False
        df.index.name = 'date'
        df.to_parquet(DATA_DIR / f'{sym}.parquet')
        return True
    except Exception as e:
        _logger.warning(f'[DataHealth] IBKR 修复 {sym} 失败: {e}')
        return False


def _fix_pkl_cache(name: str) -> bool:
    """删除指定 pkl 缓存，下次访问时自动重建。"""
    for cfg in PKL_CACHES:
        if cfg['name'] == name and cfg['path'].exists():
            cfg['path'].unlink()
            return True
    return False


def auto_repair(issues: list[HealthIssue], use_ibkr: bool = False) -> list[HealthIssue]:
    """按严重性排序，自动修复所有 fixable 的问题。"""
    SEV_ORDER = {'critical': 0, 'warning': 1, 'info': 2}
    fixable = [i for i in issues if i.fixable and not i.auto_fixed]
    fixable.sort(key=lambda i: SEV_ORDER.get(i.severity, 9))

    # 合并同一 symbol 的多个问题，只修复一次
    repaired: set[str] = set()

    for issue in fixable:
        sym = issue.symbol

        # pkl 缓存单独处理
        if issue.category == 'pkl_stale':
            cache_name = sym.replace('[cache:', '').replace(']', '')
            ok = _fix_pkl_cache(cache_name)
            issue.auto_fixed = ok
            continue

        # 数据空洞：用 DataStore 增量修复（不需要全量重下）
        if issue.category == 'data_hole':
            try:
                from core.data_store import DataStore
                store = DataStore()
                store.update([sym], start=YF_HISTORY_START)
                issue.auto_fixed = True
            except Exception:
                issue.auto_fixed = False
            continue

        if sym in repaired:
            issue.auto_fixed = True   # 已被同 symbol 其他 issue 顺带修复
            continue

        print(f'  修复 {sym} ({issue.category}) ...', end=' ', flush=True)

        # 主路：yfinance
        ok = _fix_with_yfinance(sym)

        # 备路：IBKR（仅 yfinance 失败时）
        if not ok and use_ibkr:
            ok = _fix_with_ibkr(sym)

        issue.auto_fixed = ok
        if ok:
            repaired.add(sym)
            print(f'完成')
        else:
            print(f'失败')

        time.sleep(0.3)   # 节流

    return issues


# ═══════════════════════════════════════════════════════════
# 报告与汇总
# ═══════════════════════════════════════════════════════════

def _resolve_scope(scope: str, critical_only: bool, extra_symbols: list[str]) -> list[str]:
    if critical_only:
        return list(dict.fromkeys(CRITICAL_ASSETS + SECTOR_ETFS + LEVERAGED_ETFS))
    if extra_symbols:
        return [s.upper() for s in extra_symbols]

    base = list(dict.fromkeys(CRITICAL_ASSETS + SECTOR_ETFS + LEVERAGED_ETFS))
    if scope in ('full', 'sp500', 'all'):
        parquet_syms = sorted(p.stem for p in DATA_DIR.glob('*.parquet'))
        return list(dict.fromkeys(base + parquet_syms))
    if scope == 'etf':
        return list(dict.fromkeys(CRITICAL_ASSETS + SECTOR_ETFS))
    if scope == 'leveraged':
        return LEVERAGED_ETFS
    return base


def print_report(issues: list[HealthIssue], fixed: bool = False):
    SEV_ICON = {'critical': '🔴', 'warning': '🟡', 'info': '🔵'}
    counts = {'critical': 0, 'warning': 0, 'info': 0}
    fixed_count = 0

    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
        if i.auto_fixed:
            fixed_count += 1

    total = len(issues)
    if total == 0:
        print('\n  ✅ 所有检查通过，数据健康')
        return

    print(f'\n── 问题汇总（共 {total} 个）──────────────────────────────')
    for sev in ('critical', 'warning', 'info'):
        n = counts.get(sev, 0)
        if n:
            icon = SEV_ICON.get(sev, '⚪')
            print(f'  {icon} {sev}: {n} 个', end='')
            if fixed:
                fixed_sev = sum(1 for i in issues if i.severity == sev and i.auto_fixed)
                print(f'（已修复 {fixed_sev}）', end='')
            print()

    print()
    for i in sorted(issues, key=lambda x: {'critical':0,'warning':1,'info':2}.get(x.severity,3)):
        icon = SEV_ICON.get(i.severity, '⚪')
        fixed_tag = ' ✓' if i.auto_fixed else (' ✗' if fixed and i.fixable else '')
        print(f'  {icon} [{i.category}] {i.symbol}: {i.message}{fixed_tag}')


def save_report(issues: list[HealthIssue], fix_mode: bool):
    LOGS_DIR.mkdir(exist_ok=True)
    today = date.today().strftime('%Y%m%d')
    path = LOGS_DIR / f'data_health_{today}.json'
    payload = {
        'generated_at': datetime.now().isoformat(),
        'fix_mode': fix_mode,
        'summary': {
            'total': len(issues),
            'critical': sum(1 for i in issues if i.severity == 'critical'),
            'warning': sum(1 for i in issues if i.severity == 'warning'),
            'info': sum(1 for i in issues if i.severity == 'info'),
            'fixed': sum(1 for i in issues if i.auto_fixed),
        },
        'issues': [asdict(i) for i in issues],
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  报告已保存：{path}')


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='数据健康检查 + 自动修复')
    parser.add_argument('--fix',          action='store_true', help='自动修复发现的问题')
    parser.add_argument('--use-ibkr',     action='store_true', help='修复时用 IBKR 作为 yfinance 的备用数据源')
    parser.add_argument('--critical-only',action='store_true', help='只检查关键资产（SPY/VIX/行业ETF/杠杆ETF）')
    parser.add_argument('--scope',        default='full',
                        choices=['full', 'etf', 'leveraged', 'sp500'],
                        help='检查范围：full=全库 etf=仅ETF leveraged=仅杠杆ETF sp500=S&P500')
    parser.add_argument('--symbols',      nargs='+', help='只检查指定标的')
    parser.add_argument('--no-holes',     action='store_true', help='跳过数据空洞检查（较慢）')
    parser.add_argument('--no-volume',    action='store_true', help='跳过成交量异常检查')
    args = parser.parse_args()

    symbols = _resolve_scope(args.scope, args.critical_only, args.symbols or [])
    print(f'\n[DataHealth] 检查 {len(symbols)} 只标的 | 修复={args.fix} | IBKR={args.use_ibkr}')

    issues: list[HealthIssue] = []

    # 维度 7：关键资产存在性（最优先）
    print('  检查关键资产存在性...')
    all_key = list(dict.fromkeys(CRITICAL_ASSETS + SECTOR_ETFS + LEVERAGED_ETFS))
    issues += check_existence(all_key)

    # 若有缺失，且 fix 模式，先修关键资产再继续检查
    if args.fix:
        missing_issues = [i for i in issues if i.category == 'missing']
        if missing_issues:
            print(f'  修复 {len(missing_issues)} 个缺失的关键资产...')
            auto_repair(missing_issues, use_ibkr=args.use_ibkr)

    # 维度 1+2：历史缺失 + 价格偏移
    print(f'  检查历史完整性和价格一致性（{len(symbols)} 只）...')
    batch_issues = check_history_and_price(symbols)
    issues += batch_issues
    if batch_issues:
        print(f'    发现 {len(batch_issues)} 个问题')

    # 维度 4：更新时效
    print('  检查数据更新时效...')
    stale_issues = check_staleness(symbols)
    issues += stale_issues
    if stale_issues:
        print(f'    发现 {len(stale_issues)} 个过期标的')

    # 维度 3：数据空洞（可跳过）
    if not args.no_holes:
        print('  检查数据空洞...')
        issues += check_data_holes(symbols)

    # 维度 5：成交量异常（可跳过）
    if not args.no_volume:
        print('  检查成交量异常...')
        issues += check_volume_anomaly(symbols)

    # 维度 6：pkl 缓存陈旧
    print('  检查 pkl 缓存状态...')
    issues += check_pkl_caches()

    print_report(issues, fixed=False)

    # 自动修复
    if args.fix and issues:
        fixable = [i for i in issues if i.fixable]
        if fixable:
            print(f'\n── 开始修复 {len(fixable)} 个问题 ──────────────────────────')
            auto_repair(fixable, use_ibkr=args.use_ibkr)
            print_report(issues, fixed=True)

    save_report(issues, fix_mode=args.fix)

    # 有未修复的 critical 问题，退出码 1
    unfixed_critical = [i for i in issues if i.severity == 'critical' and not i.auto_fixed]
    sys.exit(1 if unfixed_critical else 0)


if __name__ == '__main__':
    main()
