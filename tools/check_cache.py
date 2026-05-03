"""
Parquet 缓存健康检查与修复工具

检测两类问题：
  1. 历史缺失：本地最早日期比 yfinance 实际上市日晚 >30 天
  2. 价格偏移：本地首日收盘价与 yfinance 同日价格偏差 >15%（拆股/复权未同步）

用法：
  python -m tools.check_cache                  # 扫描全部缓存，仅检查不修复
  python -m tools.check_cache --fix            # 自动修复所有有问题的文件
  python -m tools.check_cache --symbols TSLL NVDL QLD  # 只检查指定标的
  python -m tools.check_cache --symbols TSLL --fix     # 只修复指定标的
"""
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import yfinance as yf

DATA_DIR  = Path('data/stocks')
YF_START  = '2018-01-01'   # 往回追溯的最远起点
GAP_DAYS  = 30             # 历史缺口超过此天数才报告
PRICE_TOL = 0.15           # 首日价格偏差超过此比例才报告


def fetch_yf(sym: str) -> pd.DataFrame | None:
    """从 yfinance 拉取完整历史，返回 DataFrame 或 None。"""
    try:
        hist = yf.Ticker(sym).history(start=YF_START, auto_adjust=True)
        if hist.empty:
            return None
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        hist.columns = [c.lower() for c in hist.columns]
        cols = [c for c in ['open','high','low','close','volume'] if c in hist.columns]
        return hist[cols].dropna()
    except Exception as e:
        print(f'    [yfinance error] {sym}: {e}')
        return None


def check_one(sym: str, verbose: bool = True) -> dict:
    """检查单只标的，返回诊断结果。"""
    path = DATA_DIR / f'{sym}.parquet'
    result = {
        'symbol': sym,
        'status': 'ok',
        'issue': '',
        'cached_start': None,
        'yf_start': None,
        'gap_days': 0,
        'price_ratio': 1.0,
        'needs_fix': False,
    }

    if not path.exists():
        result['status'] = 'missing'
        result['issue'] = '本地无缓存文件'
        result['needs_fix'] = True
        return result

    cached = pd.read_parquet(path)
    if cached.empty:
        result['status'] = 'empty'
        result['issue'] = '缓存文件为空'
        result['needs_fix'] = True
        return result

    cached_start = cached.index[0].date()
    result['cached_start'] = cached_start

    yf_df = fetch_yf(sym)
    if yf_df is None:
        result['status'] = 'yf_error'
        result['issue'] = 'yfinance 无数据（可能已退市）'
        return result

    yf_start = yf_df.index[0].date()
    result['yf_start'] = yf_start

    issues = []

    # ① 历史缺失
    gap = (cached_start - yf_start).days
    result['gap_days'] = gap
    if gap > GAP_DAYS:
        issues.append(f'历史缺失 {gap} 天（缓存从 {cached_start}，yfinance 从 {yf_start}）')

    # ② 价格偏移：找缓存最早日在 yfinance 中的同日价格对比
    if cached_start in yf_df.index:
        yf_price = float(yf_df.loc[cached_start, 'close'])
        cached_price = float(cached.loc[cached.index[0], 'close'])
        ratio = cached_price / yf_price if yf_price > 0 else 1.0
        result['price_ratio'] = round(ratio, 3)
        if abs(ratio - 1) > PRICE_TOL:
            issues.append(f'价格偏移 {ratio:.2f}x（缓存首日 {cached_price:.3f} vs yfinance {yf_price:.3f}）')

    if issues:
        result['status'] = 'bad'
        result['issue'] = '；'.join(issues)
        result['needs_fix'] = True

    if verbose:
        icon = '✓' if result['status'] == 'ok' else '✗'
        print(f'  {icon} {sym:<8} 缓存:{str(cached_start):<12} yf:{str(yf_start):<12} '
              f'gap={gap:>4}d  ratio={result["price_ratio"]:.2f}'
              + (f'  → {result["issue"]}' if result["issue"] else ''))

    return result


def fix_one(sym: str) -> bool:
    """用 yfinance 全量重建该标的的 parquet，返回是否成功。"""
    path = DATA_DIR / f'{sym}.parquet'
    print(f'    修复 {sym} ...', end=' ', flush=True)
    yf_df = fetch_yf(sym)
    if yf_df is None or yf_df.empty:
        print('失败（yfinance 无数据）')
        return False
    yf_df.index.name = 'date'
    path.parent.mkdir(parents=True, exist_ok=True)
    yf_df.to_parquet(path)
    print(f'完成  {yf_df.index[0].date()} → {yf_df.index[-1].date()}  {len(yf_df)}行')
    return True


def main():
    parser = argparse.ArgumentParser(description='Parquet 缓存健康检查与修复')
    parser.add_argument('--symbols', nargs='+', help='只检查指定标的（默认扫描全部）')
    parser.add_argument('--fix', action='store_true', help='自动修复检测到的问题')
    parser.add_argument('--gap', type=int, default=GAP_DAYS,
                        help=f'历史缺口告警阈值（天，默认 {GAP_DAYS}）')
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = sorted(p.stem for p in DATA_DIR.glob('*.parquet'))

    if not symbols:
        print('data/stocks/ 中无 parquet 文件，退出。')
        return

    print(f'\n检查 {len(symbols)} 只标的（历史缺口阈值 {args.gap} 天，价格偏移阈值 {PRICE_TOL*100:.0f}%）...\n')

    bad: list[dict] = []
    for i, sym in enumerate(symbols):
        result = check_one(sym)
        if result['needs_fix']:
            bad.append(result)
        # 每 20 只暂停一下，避免 yfinance 速率限制
        if (i + 1) % 20 == 0:
            time.sleep(2)

    print(f'\n── 汇总 ──────────────────────────────────────────')
    print(f'  总计: {len(symbols)} 只  正常: {len(symbols)-len(bad)} 只  有问题: {len(bad)} 只')

    if bad:
        print(f'\n  有问题的标的:')
        for r in bad:
            print(f'    {r["symbol"]:<8} {r["issue"]}')

    if bad and args.fix:
        print(f'\n── 开始修复 {len(bad)} 只 ──────────────────────────────')
        fixed = 0
        for r in bad:
            if fix_one(r['symbol']):
                fixed += 1
            time.sleep(0.5)
        print(f'\n  修复完成：{fixed}/{len(bad)} 只')
    elif bad and not args.fix:
        print(f'\n  运行 --fix 参数可自动修复以上问题。')

    print()


if __name__ == '__main__':
    main()
