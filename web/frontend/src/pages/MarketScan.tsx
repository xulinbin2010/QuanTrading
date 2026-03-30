import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { scanFactors, getStockDetail } from '../api/client'
import ReactECharts from 'echarts-for-react'

const UNIVERSES = ['sp500', 'nasdaq100', 'russell2000']

// ── 基础组件 ──────────────────────────────────────────────

function SignalBadge({ signal }: { signal: number }) {
  if (signal === 1) return <span className="px-1.5 py-0.5 rounded text-xs bg-green-900 text-green-300 font-medium">买入</span>
  if (signal === -1) return <span className="px-1.5 py-0.5 rounded text-xs bg-red-900 text-red-300 font-medium">卖出</span>
  return <span className="px-1.5 py-0.5 rounded text-xs bg-slate-700 text-slate-400">-</span>
}

function BoolIcon({ v }: { v: boolean }) {
  return v ? <span className="text-green-400">✓</span> : <span className="text-slate-600">✗</span>
}

function RsBar({ v }: { v: number }) {
  const pct = Math.min(Math.max((v + 0.5) / 1 * 100, 0), 100)
  const color = v > 0.1 ? '#22c55e' : v > 0 ? '#86efac' : v > -0.1 ? '#fca5a5' : '#ef4444'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{(v * 100).toFixed(1)}%</span>
    </div>
  )
}

// 覆盖率迷你条
function CoverageBar({ label, valid, total }: { label: string; valid: number; total: number }) {
  const pct = total > 0 ? valid / total * 100 : 0
  const color = pct > 90 ? '#22c55e' : pct > 60 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex flex-col gap-0.5 min-w-20">
      <div className="flex justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span style={{ color }}>{pct.toFixed(0)}%</span>
      </div>
      <div className="h-1 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <div className="text-xs text-slate-600">{valid}/{total}</div>
    </div>
  )
}

// 基本面数值格式化
function FmtPct({ v, decimals = 1 }: { v: number | null | undefined; decimals?: number }) {
  if (v == null) return <span className="text-slate-600">-</span>
  const color = v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-slate-400'
  return <span className={`font-mono text-xs ${color}`}>{(v * 100).toFixed(decimals)}%</span>
}

function FmtNum({ v, decimals = 1, suffix = '' }: { v: number | null | undefined; decimals?: number; suffix?: string }) {
  if (v == null) return <span className="text-slate-600">-</span>
  return <span className="font-mono text-xs text-slate-300">{v.toFixed(decimals)}{suffix}</span>
}

// ── 单股详情面板 ──────────────────────────────────────────

function StockDetailPanel({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['stock-detail', symbol],
    queryFn: () => getStockDetail(symbol, 120),
  })

  const klineOption = data ? {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'MA50', 'MA200'], textStyle: { color: '#94a3b8' }, top: 0 },
    grid: [
      { left: 60, right: 20, top: 30, bottom: 120 },
      { left: 60, right: 20, top: '70%', bottom: 40 },
    ],
    xAxis: [
      { type: 'category', data: data.ohlcv.map((d: any) => d.date), axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#334155' } }, gridIndex: 0 },
      { type: 'category', data: data.ohlcv.map((d: any) => d.date), axisLabel: { show: false }, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: '#94a3b8', fontSize: 10 }, splitLine: { lineStyle: { color: '#1e293b' } }, gridIndex: 0 },
      { scale: true, axisLabel: { color: '#94a3b8', fontSize: 10 }, splitLine: { lineStyle: { color: '#1e293b' } }, gridIndex: 1 },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: data.ohlcv.map((d: any) => [d.open, d.close, d.low, d.high]),
        itemStyle: { color: '#22c55e', color0: '#ef4444', borderColor: '#22c55e', borderColor0: '#ef4444' },
      },
      {
        name: 'MA50', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma50),
        lineStyle: { color: '#f59e0b', width: 1 },
      },
      {
        name: 'MA200', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma200),
        lineStyle: { color: '#8b5cf6', width: 1 },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: data.ohlcv.map((d: any, i: number) => ({
          value: d.volume,
          itemStyle: { color: data.ohlcv[i].close >= data.ohlcv[i].open ? '#22c55e' : '#ef4444' },
        })),
      },
    ],
  } : {}

  const rsOption = data ? {
    backgroundColor: 'transparent',
    grid: { left: 60, right: 20, top: 10, bottom: 30 },
    xAxis: { type: 'category', data: data.factors.map((f: any) => f.date), axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => (v * 100).toFixed(0) + '%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      data: data.factors.map((f: any) => f.rs_score),
      lineStyle: { color: '#3b82f6', width: 2 },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.3)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }] } },
    }],
  } : {}

  const fund = data?.fundamental ?? {}
  const hasFundamental = Object.values(fund).some(v => v != null)

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-[900px] max-h-[90vh] overflow-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700">
          <div className="text-white font-semibold">{symbol} — 因子详情</div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>
        <div className="p-5 space-y-4">
          {isLoading && <div className="text-slate-400 text-sm py-8 text-center">加载中...</div>}
          {isError && <div className="text-red-400 text-sm py-8 text-center">加载失败</div>}
          {data && (
            <>
              {/* 技术因子状态 */}
              {(() => {
                const last = data.factors[data.factors.length - 1]
                return (
                  <div className="grid grid-cols-4 gap-3">
                    {[
                      { label: 'RS 分数', value: <RsBar v={last.rs_score ?? 0} /> },
                      { label: '趋势向上', value: <BoolIcon v={last.uptrend} /> },
                      { label: '价格突破', value: <BoolIcon v={last.breakout} /> },
                      { label: '放量', value: <BoolIcon v={last.vol_surge} /> },
                    ].map(({ label, value }) => (
                      <div key={label} className="bg-slate-700/50 rounded p-2">
                        <div className="text-xs text-slate-400 mb-1">{label}</div>
                        <div>{value}</div>
                      </div>
                    ))}
                  </div>
                )
              })()}

              {/* 基本面快照（若有数据） */}
              {hasFundamental && (
                <div>
                  <div className="text-xs text-slate-400 mb-2 font-medium">基本面快照（最新）</div>
                  <div className="grid grid-cols-4 gap-2">
                    {[
                      { label: '营收增长', key: 'revenue_growth', render: (v: any) => <FmtPct v={v} /> },
                      { label: '盈利增长', key: 'earnings_growth', render: (v: any) => <FmtPct v={v} /> },
                      { label: 'ROE', key: 'roe', render: (v: any) => <FmtPct v={v} /> },
                      { label: 'D/E 比率', key: 'debt_to_equity', render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
                      { label: 'FCF 收益率', key: 'fcf_yield', render: (v: any) => <FmtPct v={v} decimals={2} /> },
                      { label: 'PE', key: 'pe_ratio', render: (v: any) => <FmtNum v={v} decimals={1} suffix="x" /> },
                      { label: 'PB', key: 'pb_ratio', render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
                    ].map(({ label, key, render }) => (
                      <div key={key} className="bg-slate-700/30 rounded p-2">
                        <div className="text-xs text-slate-500 mb-1">{label}</div>
                        <div>{render(fund[key as keyof typeof fund])}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div className="mb-2 text-xs text-slate-400">K 线（含 MA50 / MA200）</div>
                <ReactECharts option={klineOption} style={{ height: 320 }} />
              </div>
              <div>
                <div className="mb-2 text-xs text-slate-400">RS 相对强度</div>
                <ReactECharts option={rsOption} style={{ height: 120 }} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────

export default function MarketScan() {
  const [universe, setUniverse] = useState('sp500')
  const [selected, setSelected] = useState<string | null>(null)
  const [showCoverage, setShowCoverage] = useState(false)
  const queryClient = useQueryClient()

  const { data: scanResult, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ['factors-scan', universe],
    queryFn: () => scanFactors(universe, 100),
    staleTime: 3_600_000,
    gcTime: 3_600_000,
  })

  // 兼容新格式 {rows, coverage, total} 和旧格式 []
  const rows: any[] = Array.isArray(scanResult) ? scanResult : (scanResult?.rows ?? [])
  const coverage: Record<string, any> = scanResult?.coverage ?? {}
  const totalScanned: number = scanResult?.total ?? rows.length

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['factors-scan', universe] })
    scanFactors(universe, 100, true).then(d => {
      queryClient.setQueryData(['factors-scan', universe], d)
    })
  }

  const lastScan = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString('zh-CN') : '-'

  // 显示哪些基本面列（若有数据的话）
  const fundamentalCols = [
    { key: 'revenue_growth', label: '营收增长', render: (v: any) => <FmtPct v={v} /> },
    { key: 'earnings_growth', label: '盈利增长', render: (v: any) => <FmtPct v={v} /> },
    { key: 'roe', label: 'ROE', render: (v: any) => <FmtPct v={v} /> },
    { key: 'pe_ratio', label: 'PE', render: (v: any) => <FmtNum v={v} decimals={1} suffix="x" /> },
    { key: 'pb_ratio', label: 'PB', render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
  ]

  // 只显示有至少 10% 覆盖率的基本面列
  const activeFundCols = fundamentalCols.filter(col => {
    const cov = coverage[col.key]
    return cov && cov.pct > 10
  })

  // 覆盖率摘要展示的因子
  const coverageEntries = Object.entries(coverage).filter(([, v]: any) => v.total > 0)

  return (
    <div className="space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">市场扫描</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">上次扫描：{lastScan}</span>
          {totalScanned > 0 && <span className="text-xs text-slate-500">{rows.length}/{totalScanned} 只</span>}
          <select
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white focus:outline-none"
            value={universe}
            onChange={e => setUniverse(e.target.value)}
          >
            {UNIVERSES.map(u => <option key={u} value={u}>{u}</option>)}
          </select>
          <button
            onClick={() => setShowCoverage(s => !s)}
            className="px-2 py-1 text-xs border border-slate-600 text-slate-400 hover:text-white hover:border-slate-400 rounded transition-colors"
          >
            覆盖率
          </button>
          <button
            onClick={refresh}
            disabled={isFetching}
            className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded transition-colors"
          >
            {isFetching ? '扫描中...' : '重新扫描'}
          </button>
        </div>
      </div>

      {/* 覆盖率摘要栏（可折叠） */}
      {showCoverage && coverageEntries.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-3">
          <div className="text-xs text-slate-400 mb-3 font-medium">因子数据覆盖率（共 {totalScanned} 只股票）</div>
          <div className="flex flex-wrap gap-4">
            {coverageEntries.map(([key, cov]: any) => (
              <CoverageBar key={key} label={key} valid={cov.valid} total={cov.total} />
            ))}
          </div>
        </div>
      )}

      {/* 因子表格 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-slate-400 text-xs border-b border-slate-700 bg-slate-800/80">
              {['#', '股票', '收盘价', 'RS 强度', '量比', '突破', '放量', '趋势', '信号',
                ...activeFundCols.map(c => c.label),
                '行业', '市值(B)'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isFetching && rows.length === 0 ? (
              <tr>
                <td colSpan={11 + activeFundCols.length} className="px-4 py-10 text-center text-slate-500">
                  正在扫描因子数据，请稍候...
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={11 + activeFundCols.length} className="px-4 py-10 text-center text-slate-500">点击「重新扫描」获取数据</td>
              </tr>
            ) : rows.map((r: any, i: number) => (
              <tr
                key={r.symbol}
                className="border-b border-slate-700/50 hover:bg-slate-700/40 cursor-pointer transition-colors"
                onClick={() => setSelected(r.symbol)}
              >
                <td className="px-3 py-2 text-slate-500 text-xs">{i + 1}</td>
                <td className="px-3 py-2 font-mono font-medium text-white">{r.symbol}</td>
                <td className="px-3 py-2 font-mono">${r.close.toFixed(2)}</td>
                <td className="px-3 py-2"><RsBar v={r.rs_score} /></td>
                <td className="px-3 py-2 font-mono text-xs">{r.vol_ratio.toFixed(1)}x</td>
                <td className="px-3 py-2"><BoolIcon v={r.breakout} /></td>
                <td className="px-3 py-2"><BoolIcon v={r.vol_surge} /></td>
                <td className="px-3 py-2"><BoolIcon v={r.uptrend} /></td>
                <td className="px-3 py-2"><SignalBadge signal={r.signal} /></td>
                {activeFundCols.map(col => (
                  <td key={col.key} className="px-3 py-2">{col.render(r[col.key])}</td>
                ))}
                <td className="px-3 py-2 text-xs text-slate-400 max-w-28 truncate">{r.industry ?? '-'}</td>
                <td className="px-3 py-2 text-xs text-slate-400">{r.market_cap_b?.toFixed(0) ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <StockDetailPanel symbol={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
