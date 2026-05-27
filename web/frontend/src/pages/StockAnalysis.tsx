import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getWatchlist, addToWatchlist, removeFromWatchlist, getStockDetail } from '../api/client'
import ReactECharts from 'echarts-for-react'
import SymbolLink from '../components/SymbolLink'

// ── 辅助组件（与 MarketScan 保持一致） ────────────────────

function BoolIcon({ v }: { v: boolean }) {
  return v
    ? <span className="text-green-400 font-bold">✓</span>
    : <span className="text-slate-600">✗</span>
}

function RsBar({ v }: { v: number }) {
  const pct = Math.min(Math.max((v + 0.5) / 1 * 100, 0), 100)
  const color = v > 0.1 ? '#22c55e' : v > 0 ? '#86efac' : v > -0.1 ? '#fca5a5' : '#ef4444'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{(v * 100).toFixed(1)}%</span>
    </div>
  )
}

function FmtPct({ v, decimals = 1 }: { v: number | null | undefined; decimals?: number }) {
  if (v == null) return <span className="text-slate-600">-</span>
  const color = v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-slate-400'
  return <span className={`font-mono text-xs ${color}`}>{(v * 100).toFixed(decimals)}%</span>
}

function FmtNum({ v, decimals = 1, suffix = '' }: { v: number | null | undefined; decimals?: number; suffix?: string }) {
  if (v == null) return <span className="text-slate-600">-</span>
  return <span className="font-mono text-xs text-slate-300">{v.toFixed(decimals)}{suffix}</span>
}

// ── 单股卡片 ──────────────────────────────────────────────

function StockCard({ symbol, onRemove }: { symbol: string; onRemove: () => void }) {
  const [expanded, setExpanded] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['stock-detail', symbol],
    queryFn: () => getStockDetail(symbol, 120),
    staleTime: 5 * 60 * 1000,
  })

  // 从因子数据推导关键信息
  const last = data?.factors?.[data.factors.length - 1]
  const lastOhlcv = data?.ohlcv?.[data.ohlcv.length - 1]

  // 信号状态
  const signal: number = last?.signal ?? 0

  // 最近一次突破价（供入场参考）
  const lastBreakout = data?.factors
    ? [...data.factors].reverse().find((f: any) => f.breakout)
    : null
  const breakoutPrice = lastBreakout
    ? data.ohlcv.find((d: any) => d.date === lastBreakout.date)?.close
    : null

  // ATR 止损价
  const currentClose = lastOhlcv?.close ?? null
  const atr14 = last?.atr14 ?? null
  const atrStop = currentClose && atr14 ? currentClose - 2.5 * atr14 : null
  const atrStopPct = currentClose && atrStop ? (atrStop - currentClose) / currentClose : null

  // 最近信号历史（最多5条）
  const signalHistory: { date: string; type: number }[] = data?.factors
    ? data.factors
        .filter((f: any) => f.signal !== 0)
        .slice(-5)
        .reverse()
    : []

  // K线图配置
  const klineOption = data ? {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'MA10', 'MA20'], textStyle: { color: '#94a3b8' }, top: 0 },
    grid: [
      { left: 55, right: 15, top: 28, bottom: 100 },
      { left: 55, right: 15, top: '72%', bottom: 30 },
    ],
    xAxis: [
      { type: 'category', data: data.ohlcv.map((d: any) => d.date), axisLabel: { color: '#94a3b8', fontSize: 9 }, axisLine: { lineStyle: { color: '#334155' } }, gridIndex: 0 },
      { type: 'category', data: data.ohlcv.map((d: any) => d.date), axisLabel: { show: false }, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: '#1e293b' } }, gridIndex: 0 },
      { scale: true, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: '#1e293b' } }, gridIndex: 1 },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: data.ohlcv.map((d: any) => [d.open, d.close, d.low, d.high]),
        itemStyle: { color: '#22c55e', color0: '#ef4444', borderColor: '#22c55e', borderColor0: '#ef4444' },
      },
      {
        name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma_fast),
        lineStyle: { color: '#f59e0b', width: 1 },
      },
      {
        name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma_slow),
        lineStyle: { color: '#8b5cf6', width: 1 },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: data.ohlcv.map((d: any, i: number) => ({
          value: d.volume,
          itemStyle: { color: data.ohlcv[i].close >= data.ohlcv[i].open ? '#22c55e44' : '#ef444444' },
        })),
      },
    ],
  } : {}

  // RS 走势图配置
  const rsOption = data ? {
    backgroundColor: 'transparent',
    grid: { left: 55, right: 15, top: 8, bottom: 28 },
    xAxis: {
      type: 'category',
      data: data.factors.map((f: any) => f.date),
      axisLabel: { color: '#94a3b8', fontSize: 9 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      axisLabel: { color: '#94a3b8', fontSize: 9, formatter: (v: number) => (v * 100).toFixed(0) + '%' },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      data: data.factors.map((f: any) => f.rs_score),
      lineStyle: { color: '#3b82f6', width: 2 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(59,130,246,0.3)' },
            { offset: 1, color: 'rgba(59,130,246,0.02)' },
          ],
        },
      },
    }],
  } : {}

  const fund = data?.fundamental ?? {}
  const hasFundamental = Object.values(fund).some(v => v != null)

  return (
    <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
      {/* 卡片头部 */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700">
        <SymbolLink symbol={symbol} className="font-mono font-bold text-white text-base" />

        {/* 信号徽标 */}
        {isLoading ? (
          <span className="px-2 py-0.5 rounded text-xs bg-slate-700 text-slate-500 animate-pulse">加载中</span>
        ) : signal === 1 ? (
          <span className="px-2 py-0.5 rounded text-xs bg-green-900/60 text-green-300 font-medium border border-green-700">买入信号</span>
        ) : signal === -1 ? (
          <span className="px-2 py-0.5 rounded text-xs bg-red-900/60 text-red-300 font-medium border border-red-700">卖出预警</span>
        ) : (
          <span className="px-2 py-0.5 rounded text-xs bg-slate-700 text-slate-400">持观</span>
        )}

        {/* 现价 */}
        {currentClose && (
          <span className="font-mono text-sm text-slate-300 ml-auto mr-2">
            ${currentClose.toFixed(2)}
          </span>
        )}

        {/* 移除按钮 */}
        <button
          onClick={onRemove}
          className="text-slate-500 hover:text-red-400 transition-colors text-sm leading-none"
          title="移出自选"
        >✕</button>
      </div>

      {isError && (
        <div className="px-4 py-6 text-center text-red-400 text-sm">数据加载失败</div>
      )}

      {data && last && (
        <>
          {/* 因子摘要 */}
          <div className="px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-2 border-b border-slate-700/60">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">RS 强度</span>
              <RsBar v={last.rs_score ?? 0} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">量比</span>
              <span className={`text-xs font-mono ${(last.vol_ratio ?? 0) >= 1.5 ? 'text-green-400' : 'text-slate-300'}`}>
                {last.vol_ratio?.toFixed(1) ?? '-'}x
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">趋势向上</span>
              <BoolIcon v={last.uptrend} />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">价格突破</span>
              <BoolIcon v={last.breakout} />
            </div>
          </div>

          {/* 择时参考 */}
          <div className="px-4 py-3 border-b border-slate-700/60">
            <div className="text-xs text-slate-400 mb-2 font-medium">择时参考</div>
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-slate-700/40 rounded-lg px-3 py-2">
                <div className="text-xs text-slate-500 mb-0.5">最近突破价</div>
                <div className="text-sm font-mono text-white">
                  {breakoutPrice ? `$${breakoutPrice.toFixed(2)}` : '-'}
                </div>
                {lastBreakout && (
                  <div className="text-xs text-slate-500 mt-0.5">{lastBreakout.date}</div>
                )}
              </div>
              <div className="bg-slate-700/40 rounded-lg px-3 py-2">
                <div className="text-xs text-slate-500 mb-0.5">ATR 止损参考</div>
                <div className={`text-sm font-mono ${atrStop ? 'text-red-400' : 'text-slate-500'}`}>
                  {atrStop ? `$${atrStop.toFixed(2)}` : '-'}
                </div>
                {atrStopPct && (
                  <div className="text-xs text-slate-500 mt-0.5">{(atrStopPct * 100).toFixed(1)}%</div>
                )}
              </div>
            </div>
          </div>

          {/* 信号历史 */}
          {signalHistory.length > 0 && (
            <div className="px-4 py-3 border-b border-slate-700/60">
              <div className="text-xs text-slate-400 mb-2 font-medium">最近信号</div>
              <div className="flex flex-wrap gap-2">
                {signalHistory.map((s, i) => (
                  <div key={i} className="flex items-center gap-1.5 bg-slate-700/40 rounded px-2 py-1">
                    {s.type === 1
                      ? <span className="text-xs text-green-400">↑ 买入</span>
                      : <span className="text-xs text-red-400">↓ 卖出预警</span>}
                    <span className="text-xs text-slate-500">{s.date}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 展开/收起 K 线 */}
          <button
            onClick={() => setExpanded(v => !v)}
            className="w-full flex items-center justify-between px-4 py-2.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-700/30 transition-colors"
          >
            <span>{expanded ? '▲ 收起详情' : '▼ 展开 K 线 + 因子详情'}</span>
          </button>

          {expanded && (
            <div className="px-4 pb-4 space-y-4 border-t border-slate-700/40 pt-3">
              {/* 技术因子状态 */}
              <div className="grid grid-cols-4 gap-2">
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

              {/* K 线图 */}
              <div>
                <div className="text-xs text-slate-400 mb-1">K 线（MA10 / MA20）</div>
                <ReactECharts option={klineOption} style={{ height: 300 }} />
              </div>

              {/* RS 走势 */}
              <div>
                <div className="text-xs text-slate-400 mb-1">RS 相对强度走势</div>
                <ReactECharts option={rsOption} style={{ height: 100 }} />
              </div>

              {/* 基本面快照 */}
              {hasFundamental && (
                <div>
                  <div className="text-xs text-slate-400 mb-2 font-medium">基本面快照</div>
                  <div className="grid grid-cols-4 gap-2">
                    {[
                      { label: '营收增长', key: 'revenue_growth', render: (v: any) => <FmtPct v={v} /> },
                      { label: '盈利增长', key: 'earnings_growth', render: (v: any) => <FmtPct v={v} /> },
                      { label: 'ROE', key: 'roe', render: (v: any) => <FmtPct v={v} /> },
                      { label: 'D/E', key: 'debt_to_equity', render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
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
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────

export default function StockAnalysis() {
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const { data: wlData, isLoading: wlLoading } = useQuery({
    queryKey: ['watchlist'],
    queryFn: getWatchlist,
    staleTime: 60_000,
  })

  const symbols: string[] = wlData?.symbols ?? []

  const { mutate: addSymbol, isPending: isAdding } = useMutation({
    mutationFn: (sym: string) => addToWatchlist(sym),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      setInput('')
      setError('')
    },
    onError: () => setError('添加失败，请检查股票代码'),
  })

  const { mutate: removeSymbol } = useMutation({
    mutationFn: (sym: string) => removeFromWatchlist(sym),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const handleAdd = () => {
    const sym = input.trim().toUpperCase()
    if (!sym) return
    if (symbols.includes(sym)) {
      setError(`${sym} 已在自选列表中`)
      return
    }
    addSymbol(sym)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleAdd()
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">自选分析</h1>
        <span className="text-xs text-slate-500">手动选股，系统给出择时参考</span>
      </div>

      {/* 添加区域 */}
      <div className="bg-slate-800 rounded-xl border border-slate-700 p-4">
        <div className="flex items-end gap-3">
          <div className="flex-1 max-w-xs">
            <label className="block text-xs text-slate-400 mb-1">添加股票代码</label>
            <input
              ref={inputRef}
              value={input}
              onChange={e => { setInput(e.target.value.toUpperCase()); setError('') }}
              onKeyDown={handleKeyDown}
              placeholder="如 NVDA、AAPL"
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            {error && <div className="text-xs text-red-400 mt-1">{error}</div>}
          </div>
          <button
            onClick={handleAdd}
            disabled={isAdding || !input.trim()}
            className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            + 添加
          </button>
        </div>

        {/* 当前自选列表 chips */}
        {symbols.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {symbols.map(sym => (
              <div key={sym} className="flex items-center gap-1 bg-slate-700 border border-slate-600 rounded-full px-3 py-1">
                <span className="text-xs font-mono text-slate-200">{sym}</span>
                <button
                  onClick={() => removeSymbol(sym)}
                  className="text-slate-500 hover:text-red-400 transition-colors text-xs leading-none ml-1"
                >✕</button>
              </div>
            ))}
          </div>
        )}

        {!wlLoading && symbols.length === 0 && (
          <div className="mt-3 text-xs text-slate-500">还没有自选股，输入代码添加</div>
        )}
      </div>

      {/* 股票卡片区域 */}
      {symbols.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {symbols.map(sym => (
            <StockCard
              key={sym}
              symbol={sym}
              onRemove={() => removeSymbol(sym)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
