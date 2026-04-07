"""
yfinance vs IBKR 数据比对工具

功能：
  - 对齐两个数据源的日期索引，找出各自缺失的交易日
  - 逐日比较 OHLC 价格差异（超过 tolerance 则标记）
  - 逐日比较成交量差异（超过 vol_tolerance 则标记）
  - 输出终端报告 + 返回结构化 dict（供 Web API 使用）

用法：
  python -m tools.compare_data --symbols AAPL NVDA --start 2024-01-01
  python -m tools.compare_data --universe sp500 --sample 30 --start 2024-06-01
  python -m tools.compare_data --symbols MSFT --start 2024-01-01 --host 127.0.0.1 --port 4002

注意：
  - 需要 IB Gateway 运行（模拟盘 4002 / 实盘 4001）
  - IBKR 数据会缓存到 data/stocks_ibkr/，再次运行无需重新拉取
  - 价格差异容忍度 0.5%（price_tolerance=0.005），成交量 10%（vol_tolerance=0.10）
  - 价格差异主要来源：yfinance 使用复权价（adj close），IBKR 使用原始价格
    → 如果某只股票有分拆/分红历史，价格差异正常，不代表数据质量问题
"""
from __future__ import annotations

import argparse
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class SymbolReport:
    symbol:              str
    yf_rows:             int
    ibkr_rows:           int
    aligned_rows:        int
    missing_in_yf:       list[str] = field(default_factory=list)   # IBKR 有，yfinance 无
    missing_in_ibkr:     list[str] = field(default_factory=list)   # yfinance 有，IBKR 无
    price_discrepancies: list[dict] = field(default_factory=list)  # 价格超容忍度的日期
    vol_mismatches:      list[dict] = field(default_factory=list)  # 成交量超容忍度的日期
    max_price_diff_pct:  float      = 0.0
    max_vol_diff_pct:    float      = 0.0

    @property
    def has_issues(self) -> bool:
        return bool(
            self.missing_in_yf or self.missing_in_ibkr
            or self.price_discrepancies or self.vol_mismatches
        )


@dataclass
class ComparisonReport:
    symbols_checked:      int
    symbols_with_issues:  int
    symbols_ok:           int
    start:                str
    end:                  str
    price_tolerance:      float
    vol_tolerance:        float
    details:              dict[str, SymbolReport] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'summary': {
                'symbols_checked':     self.symbols_checked,
                'symbols_with_issues': self.symbols_with_issues,
                'symbols_ok':          self.symbols_ok,
                'start':               self.start,
                'end':                 self.end,
                'price_tolerance':     self.price_tolerance,
                'vol_tolerance':       self.vol_tolerance,
            },
            'details': {
                sym: {
                    'yf_rows':             r.yf_rows,
                    'ibkr_rows':           r.ibkr_rows,
                    'aligned_rows':        r.aligned_rows,
                    'missing_in_yf':       r.missing_in_yf[:10],     # 最多返回10条
                    'missing_in_ibkr':     r.missing_in_ibkr[:10],
                    'price_discrepancies': r.price_discrepancies[:10],
                    'vol_mismatches':      r.vol_mismatches[:10],
                    'max_price_diff_pct':  r.max_price_diff_pct,
                    'max_vol_diff_pct':    r.max_vol_diff_pct,
                    'has_issues':          r.has_issues,
                }
                for sym, r in self.details.items()
            },
        }

    def print_report(self) -> None:
        """打印终端可读报告。"""
        print()
        print("═" * 70)
        print(f"  yfinance vs IBKR 数据比对报告")
        print(f"  时间范围：{self.start} → {self.end}")
        print(f"  价格容忍度：{self.price_tolerance*100:.1f}%  成交量容忍度：{self.vol_tolerance*100:.0f}%")
        print("═" * 70)
        print(f"  检查股票数：{self.symbols_checked}")
        print(f"  数据一致：  {self.symbols_ok}")
        print(f"  存在差异：  {self.symbols_with_issues}")
        print()

        for sym, r in self.details.items():
            status = "❌ 差异" if r.has_issues else "✓  一致"
            print(f"  {sym:<8} {status}  "
                  f"yf={r.yf_rows}行  ibkr={r.ibkr_rows}行  对齐={r.aligned_rows}行  "
                  f"max价格差={r.max_price_diff_pct*100:.2f}%  max量差={r.max_vol_diff_pct*100:.1f}%")

            if r.missing_in_yf:
                print(f"           yfinance 缺失 {len(r.missing_in_yf)} 天：{r.missing_in_yf[:3]}{'...' if len(r.missing_in_yf)>3 else ''}")
            if r.missing_in_ibkr:
                print(f"           IBKR 缺失 {len(r.missing_in_ibkr)} 天：{r.missing_in_ibkr[:3]}{'...' if len(r.missing_in_ibkr)>3 else ''}")
            if r.price_discrepancies:
                worst = max(r.price_discrepancies, key=lambda x: x['max_pct_diff'])
                print(f"           价格差异 {len(r.price_discrepancies)} 天，最大：{worst['date']}  "
                      f"{worst['field']} yf={worst['yf']:.2f} ibkr={worst['ibkr']:.2f} "
                      f"(+{worst['max_pct_diff']*100:.2f}%)")
            if r.vol_mismatches:
                print(f"           成交量差异 {len(r.vol_mismatches)} 天")

        print()
        print("─" * 70)
        if self.symbols_with_issues == 0:
            print("  ✓  所有股票数据一致，质量良好。")
        else:
            print(f"  ⚠  {self.symbols_with_issues} 只股票存在差异，建议检查上方明细。")
            print("  提示：价格差异通常源于股票分拆/分红的复权调整，属正常现象。")
            print("        成交量差异通常源于 yfinance 含盘后或 IBKR 仅含 RTH。")
        print()


# ── 核心比对逻辑 ──────────────────────────────────────────────

def compare_ohlcv(
    yf_data:       dict[str, pd.DataFrame],
    ibkr_data:     dict[str, pd.DataFrame],
    start:         str,
    end:           str,
    price_tolerance: float = 0.005,   # 0.5%
    vol_tolerance:   float = 0.10,    # 10%
) -> ComparisonReport:
    """
    比对两个数据源的 OHLCV 数据。

    参数：
      yf_data / ibkr_data : DataStore.get() 返回的 dict[str, DataFrame]
      price_tolerance     : OHLC 价格差异容忍比例
      vol_tolerance       : 成交量差异容忍比例
    """
    all_syms = set(yf_data) | set(ibkr_data)
    details: dict[str, SymbolReport] = {}

    for sym in sorted(all_syms):
        yf_df   = yf_data.get(sym)
        ibkr_df = ibkr_data.get(sym)

        if yf_df is None and ibkr_df is None:
            continue

        r = SymbolReport(
            symbol    = sym,
            yf_rows   = len(yf_df)   if yf_df   is not None else 0,
            ibkr_rows = len(ibkr_df) if ibkr_df is not None else 0,
            aligned_rows = 0,
        )

        if yf_df is None or ibkr_df is None:
            # 其中一个完全没有数据
            r.missing_in_yf   = [] if yf_df   is not None else ['<整段缺失>']
            r.missing_in_ibkr = [] if ibkr_df is not None else ['<整段缺失>']
            details[sym] = r
            continue

        # 对齐索引（只取两者都在 [start, end] 范围内的日期）
        yf_idx   = yf_df.index.normalize()
        ibkr_idx = ibkr_df.index.normalize()

        r.missing_in_yf   = [d.strftime('%Y-%m-%d') for d in ibkr_idx.difference(yf_idx)]
        r.missing_in_ibkr = [d.strftime('%Y-%m-%d') for d in yf_idx.difference(ibkr_idx)]

        common = yf_idx.intersection(ibkr_idx)
        r.aligned_rows = len(common)

        if len(common) == 0:
            details[sym] = r
            continue

        yf_aligned   = yf_df.loc[yf_df.index.normalize().isin(common)]
        ibkr_aligned = ibkr_df.loc[ibkr_df.index.normalize().isin(common)]

        # 价格比对（OHLC）
        for field_name in ('open', 'high', 'low', 'close'):
            if field_name not in yf_aligned.columns or field_name not in ibkr_aligned.columns:
                continue
            yf_col   = yf_aligned[field_name].values
            ibkr_col = ibkr_aligned[field_name].values
            rel_diff = abs(yf_col - ibkr_col) / (abs(ibkr_col) + 1e-9)
            mask = rel_diff > price_tolerance
            if mask.any():
                dates_with_diff = yf_aligned.index[mask]
                for dt, yd, bd, diff in zip(
                    dates_with_diff,
                    yf_col[mask],
                    ibkr_col[mask],
                    rel_diff[mask],
                ):
                    r.price_discrepancies.append({
                        'date':         dt.strftime('%Y-%m-%d'),
                        'field':        field_name,
                        'yf':           round(float(yd), 4),
                        'ibkr':         round(float(bd), 4),
                        'max_pct_diff': round(float(diff), 6),
                    })
                r.max_price_diff_pct = max(r.max_price_diff_pct, float(rel_diff[mask].max()))

        # 成交量比对
        if 'volume' in yf_aligned.columns and 'volume' in ibkr_aligned.columns:
            yv = yf_aligned['volume'].values
            bv = ibkr_aligned['volume'].values
            vol_diff = abs(yv - bv) / (abs(bv) + 1)
            mask = vol_diff > vol_tolerance
            if mask.any():
                dates_with_diff = yf_aligned.index[mask]
                for dt, yval, bval, diff in zip(
                    dates_with_diff,
                    yv[mask],
                    bv[mask],
                    vol_diff[mask],
                ):
                    r.vol_mismatches.append({
                        'date':         dt.strftime('%Y-%m-%d'),
                        'yf_vol':       int(yval),
                        'ibkr_vol':     int(bval),
                        'pct_diff':     round(float(diff), 4),
                    })
                r.max_vol_diff_pct = max(r.max_vol_diff_pct, float(vol_diff[mask].max()))

        details[sym] = r

    issues = sum(1 for r in details.values() if r.has_issues)
    return ComparisonReport(
        symbols_checked      = len(details),
        symbols_with_issues  = issues,
        symbols_ok           = len(details) - issues,
        start                = start,
        end                  = end,
        price_tolerance      = price_tolerance,
        vol_tolerance        = vol_tolerance,
        details              = details,
    )


# ── CLI 入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='比对 yfinance 和 IBKR 数据质量',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python -m tools.compare_data --symbols AAPL NVDA --start 2024-01-01
  python -m tools.compare_data --universe sp500 --sample 30 --start 2024-06-01
        """,
    )
    parser.add_argument('--symbols',   nargs='+', help='股票代码列表（与 --universe 二选一）')
    parser.add_argument('--universe',  default='sp500', help='股票池（sp500/nasdaq100）')
    parser.add_argument('--sample',    type=int, default=0, help='从股票池中随机抽取 N 只比对（0=全量）')
    parser.add_argument('--start',     required=True, help='起始日期 YYYY-MM-DD')
    parser.add_argument('--end',       default=date.today().strftime('%Y-%m-%d'), help='结束日期')
    parser.add_argument('--host',      default='127.0.0.1', help='IB Gateway 地址')
    parser.add_argument('--port',      type=int, default=4002, help='IB Gateway 端口')
    parser.add_argument('--tolerance', type=float, default=0.005, help='价格容忍度（默认 0.005=0.5%%）')
    parser.add_argument('--vol-tol',   type=float, default=0.10,  dest='vol_tol',
                        help='成交量容忍度（默认 0.10=10%%）')
    args = parser.parse_args()

    # 确定股票列表
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        from core.universe import get_tickers
        symbols = get_tickers(args.universe)

    if args.sample and args.sample < len(symbols):
        symbols = random.sample(symbols, args.sample)
        print(f"随机抽取 {len(symbols)} 只：{symbols}")

    print(f"\n[1/3] 从 yfinance 加载数据（{len(symbols)} 只，{args.start} ~ {args.end}）...")
    from core.data_store import DataStore
    yf_store = DataStore()
    yf_data = yf_store.get(symbols, start=args.start, end=args.end, min_rows=1, auto_update=True)
    print(f"      yfinance 加载成功 {len(yf_data)} 只")

    print(f"\n[2/3] 从 IBKR 加载数据（需要 IB Gateway {args.host}:{args.port}）...")
    from core.ibkr_data_store import IBKRDataStore
    ibkr_store = IBKRDataStore()
    try:
        ibkr_store.connect(host=args.host, port=args.port)
        ibkr_data = ibkr_store.get(symbols, start=args.start, end=args.end,
                                   min_rows=1, auto_update=True)
        print(f"      IBKR 加载成功 {len(ibkr_data)} 只")
    except Exception as e:
        print(f"      ⚠ IBKR 连接失败：{e}")
        print("      尝试读取已缓存的 IBKR Parquet 数据（离线模式）...")
        ibkr_store = IBKRDataStore()
        ibkr_data = ibkr_store.get(symbols, start=args.start, end=args.end,
                                   min_rows=1, auto_update=False)
        print(f"      离线缓存加载成功 {len(ibkr_data)} 只")
    finally:
        ibkr_store.disconnect()

    print(f"\n[3/3] 比对中...")
    report = compare_ohlcv(
        yf_data, ibkr_data,
        start           = args.start,
        end             = args.end,
        price_tolerance = args.tolerance,
        vol_tolerance   = args.vol_tol,
    )
    report.print_report()


if __name__ == '__main__':
    main()
