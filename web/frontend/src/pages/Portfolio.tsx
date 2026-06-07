import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { getOrders, getBalance, getPositions, refreshPositions, getEarningsDates, getPerformance, getStockDetail, getStockNews, placeSellOrder, cancelOrders, getConfig, updateConfig } from '../api/client'
import ReactECharts from 'echarts-for-react'
import { useState } from 'react'
import { useAccount } from '../App'
import SymbolLink from '../components/SymbolLink'
import RiskThermometer from '../components/RiskThermometer'

// ── 复用辅助组件 ──────────────────────────────────────────────
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
function BoolIcon({ v }: { v: boolean }) {
  return v ? <span className="text-green-400 font-bold">✓</span> : <span className="text-slate-600">✗</span>
}
function FmtPct({ v, decimals = 1 }: { v?: number | null; decimals?: number }) {
  if (v == null) return <span className="text-slate-600">-</span>
  const color = v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-slate-400'
  return <span className={`font-mono text-xs ${color}`}>{(v * 100).toFixed(decimals)}%</span>
}
function FmtNum({ v, decimals = 1, suffix = '' }: { v?: number | null; decimals?: number; suffix?: string }) {
  if (v == null) return <span className="text-slate-600">-</span>
  return <span className="font-mono text-xs text-slate-300">{v.toFixed(decimals)}{suffix}</span>
}

// ── 卖出弹窗 ─────────────────────────────────────────────────
function SellOrderModal({ position, onClose, onSuccess }: { position: any; onClose: () => void; onSuccess: () => void }) {
  const [orderType, setOrderType] = useState<'MKT' | 'LMT'>('MKT')
  const [qty, setQty] = useState(String(Math.abs(Math.round(position.qty))))
  const [limitPrice, setLimitPrice] = useState(position.market_price.toFixed(2))
  const [tif, setTif] = useState('DAY')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const maxQty = Math.abs(Math.round(position.qty))

  const handleSubmit = async () => {
    const qtyNum = parseInt(qty)
    if (!qtyNum || qtyNum <= 0) { setError('请输入有效数量'); return }
    if (qtyNum > maxQty) { setError(`数量不能超过持仓 ${maxQty} 股`); return }
    if (orderType === 'LMT' && (!limitPrice || parseFloat(limitPrice) <= 0)) {
      setError('请输入有效限价'); return
    }
    setLoading(true)
    setError(null)
    try {
      await placeSellOrder({
        symbol: position.symbol,
        qty: qtyNum,
        order_type: orderType,
        limit_price: orderType === 'LMT' ? parseFloat(limitPrice) : undefined,
        tif,
      })
      onSuccess()
      onClose()
    } catch (e: any) {
      setError(e.response?.data?.detail || '下单失败，请检查 IB Gateway 连接')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60]" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-[420px]" onClick={e => e.stopPropagation()}>

        {/* 标题 */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-red-500 inline-block" />
            <span className="font-mono font-bold text-white">卖出 {position.symbol}</span>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-lg leading-none">✕</button>
        </div>

        {/* 持仓摘要 */}
        <div className="grid grid-cols-4 gap-px bg-slate-700/30 border-b border-slate-700">
          {[
            { label: '持仓', value: `${maxQty} 股` },
            { label: '均价', value: `$${position.avg_cost.toFixed(2)}` },
            { label: '现价', value: `$${position.market_price.toFixed(2)}` },
            {
              label: '浮盈',
              value: `${position.unrealized_pnl_pct >= 0 ? '+' : ''}${(position.unrealized_pnl_pct * 100).toFixed(2)}%`,
              color: position.unrealized_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400',
            },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-slate-800 px-3 py-2 text-center">
              <div className="text-[10px] text-slate-500 mb-0.5">{label}</div>
              <div className={`text-xs font-semibold font-mono ${color ?? 'text-slate-200'}`}>{value}</div>
            </div>
          ))}
        </div>

        <div className="p-5 space-y-4">
          {/* 订单类型 */}
          <div>
            <div className="text-xs text-slate-400 mb-1.5">订单类型</div>
            <div className="grid grid-cols-2 gap-2">
              {(['MKT', 'LMT'] as const).map(t => (
                <button
                  key={t}
                  onClick={() => setOrderType(t)}
                  className={`py-2 rounded-lg text-sm font-medium transition-colors ${
                    orderType === t
                      ? 'bg-red-600 text-white'
                      : 'bg-slate-700 text-slate-400 hover:text-white'
                  }`}
                >
                  {t === 'MKT' ? '市价单' : '限价单'}
                </button>
              ))}
            </div>
          </div>

          {/* 数量 */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-slate-400">卖出数量</span>
              <button onClick={() => setQty(String(maxQty))} className="text-xs text-blue-400 hover:text-blue-300">全部</button>
            </div>
            <input
              type="number"
              value={qty}
              onChange={e => setQty(e.target.value)}
              min={1}
              max={maxQty}
              className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-red-500 font-mono"
            />
          </div>

          {/* 限价 */}
          {orderType === 'LMT' && (
            <div>
              <div className="text-xs text-slate-400 mb-1.5">限价（$）</div>
              <input
                type="number"
                step="0.01"
                value={limitPrice}
                onChange={e => setLimitPrice(e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-red-500 font-mono"
              />
            </div>
          )}

          {/* TIF */}
          <div>
            <div className="text-xs text-slate-400 mb-1.5">有效期</div>
            <div className="grid grid-cols-2 gap-2">
              {[['DAY', '当日有效'], ['OPG', '开盘集合竞价']].map(([v, label]) => (
                <button
                  key={v}
                  onClick={() => setTif(v)}
                  className={`py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    tif === v ? 'bg-slate-600 text-white' : 'bg-slate-700/60 text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {v} · {label}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">{error}</div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-red-600 hover:bg-red-700 active:bg-red-800 text-white font-semibold text-sm transition-colors disabled:opacity-40"
          >
            {loading ? '提交中...' : `确认卖出 ${qty} 股 ${position.symbol}`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 持仓详情弹窗 ──────────────────────────────────────────────
function PositionDetailModal({ position, onClose }: { position: any; onClose: () => void }) {
  const [activeTab, setActiveTab] = useState<'tech' | 'analyst'>('tech')

  const { data, isLoading, isError } = useQuery({
    queryKey: ['stock-detail', position.symbol],
    queryFn: () => getStockDetail(position.symbol, 120),
    staleTime: 5 * 60_000,
  })

  const { data: newsData, isLoading: newsLoading } = useQuery({
    queryKey: ['stock-news', position.symbol],
    queryFn: () => getStockNews(position.symbol),
    enabled: activeTab === 'analyst',
    staleTime: 2 * 60 * 60_000,
  })

  const last = data?.factors?.[data.factors.length - 1]
  const currentClose = data?.ohlcv?.[data.ohlcv.length - 1]?.close ?? null
  const atr14 = last?.atr14 ?? null
  const atrStop = currentClose && atr14 ? currentClose - 2.5 * atr14 : null
  const atrStopPct = currentClose && atrStop ? (atrStop - currentClose) / currentClose : null
  const avgCost: number = position.avg_cost

  const fund = data?.fundamental ?? {}
  const hasFundamental = Object.values(fund).some(v => v != null)

  const markLines = [
    { yAxis: avgCost, name: `均价 $${avgCost.toFixed(2)}`, lineStyle: { color: '#f97316', type: 'dashed', width: 1.5 } },
    ...(atrStop ? [{ yAxis: atrStop, name: `ATR止损 $${atrStop.toFixed(2)}`, lineStyle: { color: '#ef4444', type: 'dashed', width: 1 } }] : []),
  ]

  const klineOption = data ? {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'EMA7', 'EMA21'], textStyle: { color: '#94a3b8' }, top: 0 },
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
        markLine: {
          symbol: ['none', 'none'],
          label: { position: 'insideStartTop', fontSize: 10, formatter: (p: any) => p.name },
          data: markLines,
        },
      },
      {
        name: 'EMA7', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma_fast),
        lineStyle: { color: '#f59e0b', width: 1 },
      },
      {
        name: 'EMA21', type: 'line', xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        data: data.factors.map((f: any) => f.ma_slow),
        lineStyle: { color: '#8b5cf6', width: 1 },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: data.ohlcv.map((d: any) => ({
          value: d.volume,
          itemStyle: { color: d.close >= d.open ? '#22c55e' : '#ef4444' },
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

  const pnl: number = position.unrealized_pnl
  const pnlPct: number = position.unrealized_pnl_pct
  const entryDaysAgo = position.entry_date
    ? Math.round((Date.now() - new Date(position.entry_date).getTime()) / 86_400_000)
    : null

  const TABS = [
    { key: 'tech' as const,    label: '技术分析' },
    { key: 'analyst' as const, label: '分析师 & 公告' },
  ]

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-[960px] max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>

        {/* 标题栏 */}
        <div className="flex items-center gap-4 px-5 py-3 border-b border-slate-700 shrink-0">
          <span className="font-mono font-bold text-white text-base">{position.symbol}</span>
          {position.industry && <span className="text-xs text-slate-400">{position.industry}</span>}
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-white text-lg leading-none">✕</button>
        </div>

        {/* 持仓摘要条 */}
        <div className="grid grid-cols-6 gap-px bg-slate-700/40 border-b border-slate-700 shrink-0">
          {[
            { label: '买入均价', value: `$${avgCost.toFixed(2)}`, color: 'text-white' },
            { label: '现价', value: currentClose ? `$${currentClose.toFixed(2)}` : `$${position.market_price.toFixed(2)}`, color: 'text-white' },
            { label: '浮动盈亏', value: `${pnl >= 0 ? '+' : ''}$${pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}`, color: pnl >= 0 ? 'text-green-400' : 'text-red-400' },
            { label: '浮盈%', value: `${pnlPct >= 0 ? '+' : ''}${(pnlPct * 100).toFixed(2)}%`, color: pnlPct >= 0 ? 'text-green-400' : 'text-red-400' },
            { label: '持有天数', value: entryDaysAgo != null ? `${entryDaysAgo} 天` : '-', color: 'text-slate-300' },
            { label: 'ATR止损参考', value: atrStop ? `$${atrStop.toFixed(2)}` : '-', color: 'text-red-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-slate-800 px-4 py-2.5 text-center">
              <div className="text-[10px] text-slate-500 mb-0.5">{label}</div>
              <div className={`text-sm font-semibold font-mono ${color}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* Tab 栏 */}
        <div className="flex border-b border-slate-700 shrink-0">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-5 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === t.key
                  ? 'border-blue-500 text-blue-400'
                  : 'border-transparent text-slate-400 hover:text-slate-200'
              }`}
            >
              {t.label}
            </button>
          ))}
          {atrStopPct != null && (
            <div className="ml-auto flex items-center px-5 text-xs text-slate-500">
              ATR止损距现价&nbsp;<span className="text-red-400 font-mono">{(atrStopPct * 100).toFixed(1)}%</span>
            </div>
          )}
        </div>

        {/* 内容区 */}
        <div className="overflow-y-auto flex-1 p-5">

          {/* ── 技术分析 ── */}
          {activeTab === 'tech' && (
            <div className="space-y-4">
              {isLoading && <div className="text-slate-400 text-sm py-8 text-center">加载中...</div>}
              {isError && <div className="text-red-400 text-sm py-8 text-center">加载失败</div>}
              {data && last && (
                <>
                  <div className="grid grid-cols-4 gap-3">
                    {[
                      { label: 'RS 分数',  value: <RsBar v={last.rs_score ?? 0} /> },
                      { label: '趋势向上', value: <BoolIcon v={last.uptrend} /> },
                      { label: '价格突破', value: <BoolIcon v={last.breakout} /> },
                      { label: '放量',     value: <BoolIcon v={last.vol_surge} /> },
                    ].map(({ label, value }) => (
                      <div key={label} className="bg-slate-700/50 rounded p-2">
                        <div className="text-xs text-slate-400 mb-1">{label}</div>
                        <div>{value}</div>
                      </div>
                    ))}
                  </div>

                  {hasFundamental && (
                    <div>
                      <div className="text-xs text-slate-400 mb-2 font-medium">基本面快照</div>
                      <div className="grid grid-cols-4 gap-2">
                        {[
                          { label: '营收增长', key: 'revenue_growth',  render: (v: any) => <FmtPct v={v} /> },
                          { label: '盈利增长', key: 'earnings_growth', render: (v: any) => <FmtPct v={v} /> },
                          { label: 'ROE',      key: 'roe',             render: (v: any) => <FmtPct v={v} /> },
                          { label: 'D/E',      key: 'debt_to_equity',  render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
                          { label: 'FCF收益率', key: 'fcf_yield',      render: (v: any) => <FmtPct v={v} decimals={2} /> },
                          { label: 'PE',       key: 'pe_ratio',        render: (v: any) => <FmtNum v={v} decimals={1} suffix="x" /> },
                          { label: 'PB',       key: 'pb_ratio',        render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
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
                    <div className="mb-2 text-xs text-slate-400">K 线（含均价线 / ATR止损线 / EMA7 / EMA21）</div>
                    <ReactECharts option={klineOption} style={{ height: 340 }} />
                  </div>

                  <div>
                    <div className="mb-2 text-xs text-slate-400">RS 相对强度</div>
                    <ReactECharts option={rsOption} style={{ height: 120 }} />
                  </div>
                </>
              )}
            </div>
          )}

          {/* ── 分析师 & 公告 ── */}
          {activeTab === 'analyst' && (
            <div className="space-y-5">
              {newsLoading && <div className="text-slate-500 text-sm py-12 text-center animate-pulse">加载中...</div>}
              {!newsLoading && newsData && (() => {
                const an = newsData.analyst ?? {}
                const tp = an.target_price
                const rec = an.recommendation
                const changes: any[] = an.recent_changes ?? []
                const ne = an.next_earnings
                const qrev: any[] = an.quarterly_revenue ?? []
                const filings: any[] = newsData.sec_filings ?? []
                const news: any[] = newsData.news ?? []

                const actionLabel = (a: string) => {
                  if (a === 'up' || a === 'upgrade')     return { text: '上调', cls: 'text-emerald-400' }
                  if (a === 'down' || a === 'downgrade') return { text: '下调', cls: 'text-red-400' }
                  if (a === 'init')                       return { text: '首次', cls: 'text-blue-400' }
                  return { text: '维持', cls: 'text-slate-400' }
                }
                const formBadge: Record<string, string> = {
                  '8-K': 'bg-red-900/70 text-red-300', '8-K/A': 'bg-red-900/70 text-red-300',
                  '10-K': 'bg-blue-900/70 text-blue-300', '10-K/A': 'bg-blue-900/70 text-blue-300',
                  '10-Q': 'bg-slate-600 text-slate-300', '10-Q/A': 'bg-slate-600 text-slate-300',
                }

                return (
                  <>
                    {(tp || rec) && (
                      <div className="grid grid-cols-2 gap-4">
                        {tp && (
                          <div className="bg-slate-700/40 rounded-lg p-4">
                            <div className="text-xs text-slate-400 mb-2">分析师目标价</div>
                            <div className="flex items-baseline gap-2 mb-3">
                              <span className="text-2xl font-bold text-white">${tp.mean}</span>
                              {tp.current && (
                                <span className={`text-sm font-medium ${tp.mean > tp.current ? 'text-emerald-400' : 'text-red-400'}`}>
                                  {tp.mean > tp.current ? '▲' : '▼'}{Math.abs((tp.mean / tp.current - 1) * 100).toFixed(1)}%
                                </span>
                              )}
                            </div>
                            {tp.low != null && tp.high != null && tp.current != null && (() => {
                              const range = tp.high - tp.low || 1
                              const curPct  = Math.max(0, Math.min(100, (tp.current - tp.low) / range * 100))
                              const meanPct = Math.max(0, Math.min(100, (tp.mean   - tp.low) / range * 100))
                              return (
                                <div>
                                  <div className="relative h-2 bg-slate-600 rounded-full mb-1.5">
                                    <div className="absolute inset-0 bg-gradient-to-r from-red-500/50 to-emerald-500/50 rounded-full" />
                                    <div className="absolute top-1/2 -translate-y-1/2 w-1.5 h-5 bg-blue-400 rounded -translate-x-1/2" style={{ left: `${meanPct}%` }} />
                                    <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full border-2 border-slate-700 -translate-x-1/2" style={{ left: `${curPct}%` }} />
                                  </div>
                                  <div className="flex justify-between text-[10px] text-slate-500">
                                    <span>低 ${tp.low}</span><span>当前 ${tp.current}</span><span>高 ${tp.high}</span>
                                  </div>
                                </div>
                              )
                            })()}
                          </div>
                        )}
                        {rec && (() => {
                          const total = rec.strongBuy + rec.buy + rec.hold + rec.sell + rec.strongSell || 1
                          const buyPct  = Math.round((rec.strongBuy + rec.buy) / total * 100)
                          const holdPct = Math.round(rec.hold / total * 100)
                          const sellPct = 100 - buyPct - holdPct
                          return (
                            <div className="bg-slate-700/40 rounded-lg p-4">
                              <div className="text-xs text-slate-400 mb-2">机构评级分布（{total} 家）</div>
                              <div className="flex h-4 rounded-full overflow-hidden mb-3">
                                <div className="bg-emerald-500" style={{ width: `${buyPct}%` }} />
                                <div className="bg-amber-500/80" style={{ width: `${holdPct}%` }} />
                                <div className="bg-red-500/70" style={{ width: `${sellPct}%` }} />
                              </div>
                              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                                <div><div className="text-emerald-400 font-semibold text-base">{rec.strongBuy + rec.buy}</div><div className="text-slate-500">买入</div></div>
                                <div><div className="text-amber-400 font-semibold text-base">{rec.hold}</div><div className="text-slate-500">持有</div></div>
                                <div><div className="text-red-400 font-semibold text-base">{rec.sell + rec.strongSell}</div><div className="text-slate-500">卖出</div></div>
                              </div>
                            </div>
                          )
                        })()}
                      </div>
                    )}

                    {(ne || qrev.length > 0) && (
                      <div className="grid grid-cols-2 gap-4">
                        {ne && (
                          <div className="bg-slate-700/40 rounded-lg p-4">
                            <div className="text-xs text-slate-400 mb-2">下次财报</div>
                            <div className="text-base font-semibold text-white mb-2">{ne.date}</div>
                            {ne.eps_avg != null && (
                              <div className="text-sm text-slate-300 mb-1">EPS 预期&nbsp;<span className="text-emerald-400 font-semibold">${ne.eps_avg?.toFixed(2)}</span><span className="text-slate-500 text-xs ml-1">(${ne.eps_low?.toFixed(2)}–${ne.eps_high?.toFixed(2)})</span></div>
                            )}
                            {ne.rev_avg_b != null && <div className="text-xs text-slate-400">营收预期：<span className="text-slate-300">${ne.rev_avg_b}B</span></div>}
                          </div>
                        )}
                        {qrev.length > 0 && (() => {
                          const sorted = [...qrev].reverse()
                          const maxRev = Math.max(...sorted.map((q: any) => q.revenue_b))
                          return (
                            <div className="bg-slate-700/40 rounded-lg p-4">
                              <div className="text-xs text-slate-400 mb-3">季度营收趋势</div>
                              <div className="flex items-end gap-1.5 h-14">
                                {sorted.map((q: any, i: number) => (
                                  <div key={i} className="flex-1 flex flex-col items-center gap-1">
                                    <div className="w-full bg-blue-500/70 rounded-t" style={{ height: `${Math.max(4, Math.round(q.revenue_b / maxRev * 48))}px` }} title={`${q.quarter}：$${q.revenue_b}B`} />
                                  </div>
                                ))}
                              </div>
                              <div className="flex justify-between text-[10px] text-slate-500 mt-1.5">
                                <span>{sorted[0]?.quarter}</span>
                                <span className="text-slate-200 font-medium">${sorted[sorted.length - 1]?.revenue_b}B</span>
                              </div>
                            </div>
                          )
                        })()}
                      </div>
                    )}

                    {changes.length > 0 && (
                      <div>
                        <div className="text-xs text-slate-400 mb-2 font-medium uppercase tracking-wide">近期评级变动</div>
                        <div className="space-y-1">
                          {changes.map((c, i) => {
                            const { text, cls } = actionLabel(c.action)
                            return (
                              <div key={i} className="flex items-center gap-3 text-sm py-1.5 px-3 rounded-lg bg-slate-700/30">
                                <span className="text-slate-500 text-xs w-24 shrink-0">{c.date}</span>
                                <span className="text-slate-200 flex-1">{c.firm}</span>
                                <span className={`${cls} font-semibold text-xs w-10 text-right shrink-0`}>{text}</span>
                                <span className="text-slate-300 text-xs w-24 text-right shrink-0">{c.to_grade}</span>
                                {c.target && <span className="text-slate-400 text-xs w-14 text-right shrink-0">${c.target}</span>}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}

                    {filings.length > 0 && (
                      <div>
                        <div className="text-xs text-slate-400 mb-2 font-medium uppercase tracking-wide">SEC 公告</div>
                        <div className="space-y-2">
                          {filings.map((f, i) => (
                            <a key={i} href={f.url} target="_blank" rel="noopener noreferrer"
                               className={`block rounded-lg p-3 hover:bg-slate-700/70 transition-colors border ${f.priority ? 'border-amber-700/60 bg-amber-900/10' : 'border-slate-700/50 bg-slate-700/20'}`}>
                              <div className="flex items-center gap-2.5 mb-1.5">
                                <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded shrink-0 ${formBadge[f.form] ?? 'bg-slate-600 text-slate-300'}`}>{f.form}</span>
                                <span className="text-xs text-slate-500 shrink-0">{f.date}</span>
                                <span className="text-sm font-medium text-slate-100 flex-1">{f.label}</span>
                                <span className="text-slate-500 text-xs shrink-0">↗</span>
                              </div>
                              {f.snippet && <p className="text-xs text-slate-400 leading-relaxed line-clamp-2 pl-0.5">{f.snippet}</p>}
                            </a>
                          ))}
                        </div>
                      </div>
                    )}

                    {news.length > 0 && (
                      <div>
                        <div className="text-xs text-slate-400 mb-2 font-medium uppercase tracking-wide">近期新闻</div>
                        <div className="space-y-1">
                          {news.map((n, i) => (
                            <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
                               className="flex items-start gap-3 py-2 px-3 rounded-lg hover:bg-slate-700/50 transition-colors group">
                              <span className="text-xs text-slate-500 shrink-0 w-20 pt-0.5">{n.date}</span>
                              <span className="text-sm text-slate-300 group-hover:text-white leading-snug flex-1">{n.title}</span>
                              <span className="text-[10px] text-slate-500 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">{n.publisher} ↗</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}

                    {!an.target_price && !an.recommendation && filings.length === 0 && news.length === 0 && (
                      <div className="text-slate-500 text-sm py-12 text-center">暂无数据</div>
                    )}
                  </>
                )
              })()}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-xl font-semibold ${color ?? 'text-white'}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  )
}

function fmt(n?: number) {
  if (n === undefined || n === null) return '-'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}


type PeriodKey = '2W' | '1M' | '3M' | 'YTD'
const PERIOD_DAYS: Record<PeriodKey, () => number> = {
  '2W':  () => 14,
  '1M':  () => 30,
  '3M':  () => 90,
  'YTD': () => {
    const now = new Date()
    return Math.round((now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) / 86_400_000)
  },
}

function pct(v?: number, prefix = true) {
  if (v === undefined || v === null) return '-'
  const sign = v >= 0 ? (prefix ? '+' : '') : ''
  return `${sign}${v.toFixed(2)}%`
}

function PerfCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-slate-900/60 rounded-lg px-4 py-3 border border-slate-700/60 text-center">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-base font-semibold tabular-nums ${color ?? 'text-white'}`}>{value}</div>
    </div>
  )
}

function PerformanceSection() {
  const [period, setPeriod] = useState<PeriodKey>('1M')
  const days = PERIOD_DAYS[period]()

  const { data, isLoading } = useQuery({
    queryKey: ['performance', period],
    queryFn: () => getPerformance(days),
    staleTime: 10 * 60_000,
    retry: false,
  })

  const nav: { date: string; value: number }[]  = data?.nav  ?? []
  const spy: { date: string; value: number }[]  = data?.spy  ?? []
  const m = data?.metrics ?? {}

  const chartOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params: any[]) =>
        `${params[0].name}<br/>` +
        params.map((p: any) => `<span style="color:${p.color}">●</span> ${p.seriesName}: ${p.value?.toFixed(2)}`).join('<br/>'),
    },
    legend: {
      data: ['Portfolio', 'SPY'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 4,
      right: 8,
    },
    grid: { left: 48, right: 16, top: 32, bottom: 36 },
    xAxis: {
      type: 'category',
      data: nav.map(d => d.date),
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisLine: { lineStyle: { color: '#1e293b' } },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      min: (e: { min: number }) => parseFloat((e.min * 0.995).toFixed(2)),
      max: (e: { max: number }) => parseFloat((e.max * 1.005).toFixed(2)),
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (v: number) => v.toFixed(1) },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: 'Portfolio',
        type: 'line',
        data: nav.map(d => d.value),
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#3b82f6', width: 2 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.18)' }, { offset: 1, color: 'rgba(59,130,246,0.01)' }] } },
      },
      {
        name: 'SPY',
        type: 'line',
        data: spy.map(d => d.value),
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#f97316', width: 1.5, type: 'dashed' },
      },
    ],
  }

  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      {/* 标题 + 周期选择 */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-slate-300">业绩复盘</span>
        <div className="flex gap-1">
          {(['2W', '1M', '3M', 'YTD'] as PeriodKey[]).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2.5 py-0.5 text-xs rounded font-medium transition-colors
                ${period === p
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:text-white'}`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* 指标卡片 */}
      {isLoading ? (
        <div className="text-slate-500 text-xs py-2">计算中...</div>
      ) : !data?.has_data ? (
        <div className="text-slate-500 text-sm py-4 text-center">暂无账户快照数据，请先连接 IB Gateway</div>
      ) : (
        <>
          <div className="grid grid-cols-5 gap-2 mb-4">
            <PerfCard
              label="区间收益"
              value={pct(m.total_return)}
              color={(m.total_return ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
            <PerfCard
              label="年化收益"
              value={pct(m.annualized)}
              color={(m.annualized ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
            <PerfCard
              label="Sharpe"
              value={m.sharpe !== undefined ? m.sharpe.toFixed(2) : '-'}
              color={(m.sharpe ?? 0) >= 1 ? 'text-green-400' : (m.sharpe ?? 0) >= 0 ? 'text-yellow-400' : 'text-red-400'}
            />
            <PerfCard
              label="最大回撤"
              value={pct(m.max_drawdown)}
              color="text-red-400"
            />
            <PerfCard
              label="超额 SPY"
              value={pct(m.excess_spy)}
              color={(m.excess_spy ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
          </div>
          <ReactECharts option={chartOption} style={{ height: 220 }} />
          <div className="text-right text-xs text-slate-600 mt-1">
            {m.data_from ? `${m.data_from} 起` : ''} · {m.period_days} 个快照，两线均归一至 100
          </div>
        </>
      )}
    </div>
  )
}

// 每种颜色: [背景色, 是否用深色文字]
const SEGMENT_PALETTE: [string, boolean][] = [
  ['#60a5fa', false], // blue-400
  ['#34d399', true],  // emerald-400
  ['#fbbf24', true],  // amber-400
  ['#c084fc', false], // purple-400
  ['#f472b6', false], // pink-400
  ['#22d3ee', true],  // cyan-400
  ['#fb923c', false], // orange-400
  ['#4ade80', true],  // green-400
]

function AllocationBar({ positions, balance }: { positions: any[]; balance: any }) {
  if (!positions?.length && !balance) return null

  // 用 net_liquidation 作分母：IB 实时净值，总和恒等于 100%
  // market_value 各持仓占 netLiq 的比例；Cash = netLiq 减去所有持仓市值的余量
  const netLiq = balance?.net_liquidation
  if (!netLiq || netLiq <= 0) return null

  // ── 期权价差智能合并 ──────────────────────────────────────────────────────
  // 策略：同底层+方向+到期日的期权中，将每个空头与最近行权价的多头配对成价差；
  //       未配对的多头单独显示。这样 C260(多)+C325(多)+C340(空) → C325/340价差 + C260单腿
  type BarItem = { label: string; value: number }
  interface OptLeg { strike: number; mv: number; qty: number; symbol: string }
  const optGroups: Record<string, OptLeg[]> = {}
  const barItems: BarItem[] = []

  for (const p of (positions ?? [])) {
    const mv: number = p.market_value ?? 0
    const parts: string[] = (p.symbol as string).trim().split(/\s+/)
    if (parts.length === 3) {
      const underlying = parts[0]
      const right = parts[1][0]
      const strike = parseFloat(parts[1].slice(1))
      const expiry = parts[2]
      const key = `${underlying}_${right}_${expiry}`
      if (!optGroups[key]) optGroups[key] = []
      optGroups[key].push({ strike, mv, qty: p.qty ?? 0, symbol: p.symbol })
    } else {
      barItems.push({ label: p.symbol, value: mv })
    }
  }

  for (const legs of Object.values(optGroups)) {
    const longs  = legs.filter(l => l.qty > 0).sort((a, b) => a.strike - b.strike)
    const shorts = legs.filter(l => l.qty < 0).sort((a, b) => a.strike - b.strike)
    const paired = new Set<number>()   // longs 中已配对的索引

    // 配对规则：① 数量绝对值相同（+2/-2 才是价差），② 数量相同时取行权价最近的
    for (const s of shorts) {
      const absQty = Math.abs(s.qty)
      let bestIdx = -1, bestDist = Infinity
      longs.forEach((lng, i) => {
        if (paired.has(i)) return
        if (lng.qty !== absQty) return          // 数量必须匹配
        const d = Math.abs(lng.strike - s.strike)
        if (d < bestDist) { bestDist = d; bestIdx = i }
      })
      // 若无精确数量匹配（非标准价差），fallback 到任意未配对多头（按最近行权价）
      if (bestIdx < 0) {
        longs.forEach((lng, i) => {
          if (paired.has(i)) return
          const d = Math.abs(lng.strike - s.strike)
          if (d < bestDist) { bestDist = d; bestIdx = i }
        })
      }
      if (bestIdx >= 0) {
        paired.add(bestIdx)
        const lng = longs[bestIdx]
        const netMv = lng.mv + s.mv
        if (netMv > 0) {
          const [lo, hi] = [lng.strike, s.strike].sort((a, b) => a - b)
          const sym0Parts = lng.symbol.split(' ')
          barItems.push({ label: `${sym0Parts[0]} ${sym0Parts[1][0]}${lo}/${hi}`, value: netMv })
        }
      }
    }

    // 未配对的多头单独显示
    longs.forEach((lng, i) => {
      if (!paired.has(i) && lng.mv > 0) barItems.push({ label: lng.symbol, value: lng.mv })
    })
  }

  const positiveMv = barItems.reduce((s, x) => s + Math.max(x.value, 0), 0)
  const cashPct = Math.max((netLiq - positiveMv) / netLiq * 100, 0)

  let colorIdx = 0
  const segments: { label: string; value: number; pct: number; bg: string; darkText: boolean }[] = [
    ...barItems.filter(x => x.value > 0).map(x => {
      const [bg, darkText] = SEGMENT_PALETTE[colorIdx++ % SEGMENT_PALETTE.length]
      return { label: x.label, value: x.value, pct: (x.value / netLiq) * 100, bg, darkText }
    }),
    {
      label: 'Cash',
      value: netLiq - positiveMv,
      pct: cashPct,
      bg: '#94a3b8',
      darkText: true,
    },
  ].filter(s => s.pct > 0.1)

  return (
    <div className="px-4 pt-3 pb-4 border-t border-slate-700/60">
      <div className="text-xs font-medium text-slate-400 mb-2 dark:text-slate-400">资产配比</div>

      {/* 堆积 bar — 圆角、段间留 2px 透明间隔 */}
      <div className="flex w-full h-7 rounded-full overflow-hidden" style={{ gap: '2px' }}>
        {segments.map(s => (
          <div
            key={s.label}
            style={{ width: `${s.pct}%`, backgroundColor: s.bg, minWidth: s.pct > 0.5 ? undefined : '3px' }}
            className="relative group flex items-center justify-center overflow-visible shrink-0 transition-all duration-200 hover:brightness-110 hover:z-10 cursor-default"
          >
            {/* 段内标签：宽度 >= 4% 显示，太窄时截断显示前3字符 */}
            {s.pct >= 4 && (
              <span
                className="text-[11px] font-bold leading-none pointer-events-none select-none overflow-hidden"
                style={{
                  color: s.darkText ? '#1e293b' : '#ffffff',
                  maxWidth: '90%',
                  display: 'block',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  padding: '0 2px',
                }}
              >
                {s.pct < 7 ? s.label.slice(0, 4) : s.label}
              </span>
            )}

            {/* hover tooltip */}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-20
                            bg-slate-900 dark:bg-slate-800 border border-slate-600
                            rounded-lg shadow-xl px-3 py-1.5 text-xs whitespace-nowrap
                            opacity-0 group-hover:opacity-100 pointer-events-none
                            transition-opacity duration-150">
              <span className="font-semibold text-white">{s.label}</span>
              <span className="text-slate-300 mx-1.5">·</span>
              <span className="text-slate-200">{s.pct.toFixed(1)}%</span>
              <span className="text-slate-400 ml-1.5">
                {s.value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 图例 */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-2.5">
        {segments.map(s => (
          <div key={s.label} className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
              style={{ backgroundColor: s.bg }}
            />
            <span className="text-xs text-slate-200 font-bold">{s.label}</span>
            <span className="text-xs text-slate-300 font-semibold">{s.pct.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── 实盘参数面板 ───────────────────────────────────────────────
const LIVE_PARAM_GROUPS = [
  {
    label: '仓位控制',
    params: [
      { key: 'MAX_POSITIONS',     label: '最多持仓数',     type: 'int',   unit: '只' },
      { key: 'POSITION_PCT',      label: '单仓比例',       type: 'pct' },
      { key: 'MAX_ENTRY_SLIPPAGE',label: 'OPG滑点上限',    type: 'pct' },
    ],
  },
  {
    label: '止损线',
    params: [
      { key: 'STOP_LOSS_PCT',           label: '硬止损',          type: 'pct' },
      { key: 'ATR_STOP_MULTIPLIER',     label: 'ATR止损倍数',     type: 'float' },
      { key: 'ATR_STOP_FLOOR',          label: 'ATR止损下限',     type: 'pct' },
      { key: 'TRAIL_STOP_ACTIVATE_PCT', label: '移动止损激活',    type: 'pct' },
      { key: 'TRAIL_STOP_PCT',          label: '移动止损触发',    type: 'pct' },
      { key: 'TRAIL_STOP_TIER1_THRESHOLD', label: '第2档激活门槛', type: 'pct' },
      { key: 'TRAIL_STOP_TIER1_PCT',    label: '第2档触发线',     type: 'pct' },
      { key: 'TRAIL_STOP_TIER2_THRESHOLD', label: '第3档激活门槛', type: 'pct' },
      { key: 'TRAIL_STOP_TIER2_PCT',    label: '第3档触发线',     type: 'pct' },
    ],
  },
  {
    label: '出场策略',
    params: [
      { key: 'RS_DECAY_ENABLED',   label: 'RS衰退出场',       type: 'bool' },
      { key: 'RS_DECAY_THRESHOLD', label: 'RS衰退阈值',       type: 'pct' },
      { key: 'RS_DECAY_MIN_PROFIT',label: 'RS衰退最低浮盈',   type: 'pct' },
      { key: 'TIME_STOP_DAYS',     label: '时间止损天数',     type: 'int', unit: '天' },
      { key: 'TIME_STOP_MIN_RETURN', label: '时间止损最低盈利', type: 'pct' },
    ],
  },
]

function TradingParamsPanel() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [edits, setEdits] = useState<Record<string, string>>({})

  const { data: cfg } = useQuery({ queryKey: ['config'], queryFn: getConfig })
  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: (params: { key: string; value: string }[]) => updateConfig(params),
    onSuccess: () => {
      setEdits({})
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })

  const cfgMap: Record<string, string> = {}
  ;(cfg?.strategy ?? []).forEach((p: any) => { cfgMap[p.key] = p.value })

  const val = (key: string) => edits[key] ?? cfgMap[key] ?? ''
  const isDirty = Object.keys(edits).length > 0

  function handleChange(key: string, v: string) {
    setEdits(e => ({ ...e, [key]: v }))
  }
  function handleSave() {
    const params = Object.entries(edits).map(([key, value]) => ({ key, value }))
    save(params)
  }
  function renderInput(p: { key: string; label: string; type: string; unit?: string }) {
    const v = val(p.key)
    if (p.type === 'bool') {
      return (
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="checkbox"
            className="w-3.5 h-3.5 accent-blue-500"
            checked={v === 'true' || v === 'True' || v === '1'}
            onChange={e => handleChange(p.key, e.target.checked ? 'true' : 'false')}
          />
          <span className="text-xs text-slate-400">{v === 'true' || v === 'True' || v === '1' ? '开启' : '关闭'}</span>
        </label>
      )
    }
    const displayVal = (p.type === 'pct' && v) ? (parseFloat(v) * 100).toFixed(1) : v
    return (
      <div className="flex items-center gap-1">
        <input
          type="number"
          step={p.type === 'int' ? '1' : '0.1'}
          className="w-16 text-xs px-1.5 py-0.5 bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:outline-none focus:border-blue-500"
          value={displayVal}
          onChange={e => {
            const raw = parseFloat(e.target.value)
            handleChange(p.key, p.type === 'pct' ? String(raw / 100) : e.target.value)
          }}
        />
        {p.unit && <span className="text-xs text-slate-500">{p.unit}</span>}
        {p.type === 'pct' && <span className="text-xs text-slate-500">%</span>}
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-300 hover:text-white"
        onClick={() => setOpen(o => !o)}
      >
        <span>实盘交易参数</span>
        <span className="text-slate-500 text-xs">{open ? '▾' : '▸'} 止损 · 仓位 · 出场</span>
      </button>
      {open && (
        <div className="border-t border-slate-700 px-4 pt-3 pb-4 space-y-4">
          {LIVE_PARAM_GROUPS.map(group => (
            <div key={group.label}>
              <div className="text-xs font-medium text-slate-400 mb-2">{group.label}</div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-2.5">
                {group.params.map(p => (
                  <div key={p.key} className="flex items-center justify-between gap-2 min-w-0">
                    <span className="text-xs text-slate-400 truncate">{p.label}</span>
                    {renderInput(p)}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {isDirty && (
            <div className="flex items-center gap-3 pt-1">
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded font-medium disabled:opacity-50"
              >
                {saving ? '保存中…' : '保存修改'}
              </button>
              <button
                onClick={() => setEdits({})}
                className="px-3 py-1 text-xs text-slate-400 hover:text-white"
              >
                撤销
              </button>
              <span className="text-xs text-amber-400">· 有未保存的修改</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Portfolio() {
  const [orderSymbol, setOrderSymbol] = useState('')
  const [refreshMsg, setRefreshMsg] = useState<'ok' | 'error' | null>(null)
  const [spinning, setSpinning] = useState(false)
  const [selectedPos, setSelectedPos] = useState<any>(null)
  const [sellPos, setSellPos] = useState<any>(null)
  const [cancellingSym, setCancellingSym] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const handleCancelOrders = async (symbol: string) => {
    if (!confirm(`确认撤销 ${symbol} 的全部未成交挂单？`)) return
    setCancellingSym(symbol)
    try {
      const r = await cancelOrders(symbol)
      queryClient.invalidateQueries({ queryKey: ['orders'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      alert(`已撤销 ${symbol} 的 ${r.count} 笔未成交挂单`)
    } catch (e: any) {
      alert(e.response?.data?.detail || '撤单失败（如需撤其他会话的单，请确认 Gateway 已设 Master API client ID）')
    } finally {
      setCancellingSym(null)
    }
  }

  const { data: balance, isError: balanceErr, refetch: refetchBalance } = useQuery({
    queryKey: ['balance'],
    queryFn: getBalance,
    refetchInterval: 60_000,
    retry: false,
  })

  const { selectedAccount } = useAccount()

  const { data: positions, isError: posErr, refetch: refetchPositions } = useQuery({
    queryKey: ['positions', selectedAccount],
    queryFn: () => getPositions(selectedAccount),
    refetchInterval: 60_000,
    retry: false,
  })

  const { data: orders = [] } = useQuery({
    queryKey: ['orders', orderSymbol],
    queryFn: () => getOrders(orderSymbol || undefined, 100),
    refetchInterval: 30_000,
  })

  const posSymbols = (positions ?? []).map((p: any) => p.symbol).filter(Boolean)
  const { data: earningsDates } = useQuery<Record<string, string | null>>({
    queryKey: ['earnings', posSymbols.join(',')],
    queryFn: () => posSymbols.length ? getEarningsDates(posSymbols) : Promise.resolve({}),
    enabled: posSymbols.length > 0,
    staleTime: 12 * 60 * 60_000,
  })

  const ibOffline = balanceErr && posErr

  return (
    <>
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-white">持仓总览</h1>
      </div>

      {ibOffline && (
        <div className="bg-slate-700 border border-slate-600 rounded-lg px-4 py-2 text-sm text-slate-300">
          IB Gateway 未连接，账户余额/持仓不可用。订单历史和净值记录仍正常显示。
        </div>
      )}

      {/* 余额卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="净值" value={fmt(balance?.net_liquidation)} />
        <StatCard label="现金" value={fmt(balance?.total_cash)} />
        <StatCard
          label="浮动盈亏"
          value={fmt(balance?.unrealized_pnl)}
          color={(balance?.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard
          label="实现盈亏"
          value={fmt(balance?.realized_pnl)}
          color={(balance?.realized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard label="购买力" value={fmt(balance?.buying_power)} />
      </div>

      {/* 风险温度计（减仓预警：VIX 期限结构 + 组合相关性） */}
      <RiskThermometer />

      {/* 实盘参数 */}
      <TradingParamsPanel />

      {/* 业绩复盘 */}
      <PerformanceSection />

      {/* 持仓表格 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300 flex items-center justify-between">
          <span>当前持仓 {positions ? `（${positions.length} 只）` : ''}</span>
          <div className="flex items-center gap-2">
            {refreshMsg && (
              <span className={`text-xs ${refreshMsg === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
                {refreshMsg === 'ok' ? '已刷新' : 'IB 未连接'}
              </span>
            )}
            {positions && positions.length > 0 && (
              <button
                onClick={() => {
                  const headers = ['股票', '行业', '买入日', '数量', '均价', '现价', '市值', '浮盈', '浮盈%']
                  const rows = positions.map((p: any) => [
                    p.symbol,
                    p.industry ?? '',
                    p.entry_date ?? '',
                    p.qty,
                    p.avg_cost.toFixed(2),
                    p.market_price.toFixed(2),
                    p.market_value.toFixed(2),
                    p.unrealized_pnl.toFixed(2),
                    (p.unrealized_pnl_pct * 100).toFixed(2) + '%',
                  ])
                  const csv = [headers, ...rows].map(r => r.join(',')).join('\n')
                  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `positions_${new Date().toISOString().slice(0, 10)}.csv`
                  a.click()
                  URL.revokeObjectURL(url)
                }}
                className="flex items-center gap-1 text-xs text-slate-400 hover:text-white transition-colors"
              >
                ↓ 导出
              </button>
            )}
            <button
              onClick={async () => {
                setSpinning(true)
                setRefreshMsg(null)
                try {
                  // 强制断线重连以获取 IB 最新持仓（绕过缓存）
                  await refreshPositions(selectedAccount)
                  await Promise.all([refetchPositions(), refetchBalance()])
                  setRefreshMsg('ok')
                } catch {
                  setRefreshMsg('error')
                } finally {
                  setSpinning(false)
                  setTimeout(() => setRefreshMsg(null), 2500)
                }
              }}
              disabled={spinning}
              className="flex items-center gap-1 text-xs text-slate-400 hover:text-white disabled:opacity-40 transition-colors"
            >
              <span className={spinning ? 'animate-spin inline-block' : 'inline-block'}>↻</span>
              {spinning ? '刷新中...' : '刷新'}
            </button>
          </div>
        </div>
        {!positions || positions.length === 0 ? (
          <div className="px-4 py-6 text-slate-500 text-sm text-center">
            {ibOffline ? '需要 IB Gateway 连接' : '当前无持仓'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs border-b border-slate-700">
                  {['股票', '行业', '买入日', '数量', '均价', '现价', '市值', '浮盈', '浮盈%', '财报', ''].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p: any) => (
                  <tr
                    key={p.symbol}
                    className="border-b border-slate-700/50 hover:bg-slate-700/30 cursor-pointer"
                    onClick={() => setSelectedPos(p)}
                  >
                    <td className="px-4 py-2"><SymbolLink symbol={p.symbol} className="font-mono text-blue-300 hover:text-blue-200" /></td>
                    <td className="px-4 py-2 text-slate-400 text-xs max-w-[120px] truncate" title={p.industry ?? ''}>
                      {p.industry ?? '-'}
                    </td>
                    <td className="px-4 py-2 text-slate-400">{p.entry_date ?? '-'}</td>
                    <td className="px-4 py-2">{p.qty}</td>
                    <td className="px-4 py-2 font-mono">${p.avg_cost.toFixed(2)}</td>
                    <td className="px-4 py-2 font-mono">${p.market_price.toFixed(2)}</td>
                    <td className="px-4 py-2">{fmt(p.market_value)}</td>
                    <td className={`px-4 py-2 font-mono ${p.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {fmt(p.unrealized_pnl)}
                    </td>
                    <td className={`px-4 py-2 font-mono font-medium ${p.unrealized_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {p.unrealized_pnl_pct >= 0 ? '+' : ''}{(p.unrealized_pnl_pct * 100).toFixed(2)}%
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {(() => {
                        const d = earningsDates?.[p.symbol]
                        if (!d) return <span className="text-slate-500">-</span>
                        const days = Math.round((new Date(d).getTime() - Date.now()) / 86400_000)
                        const color = days <= 2 ? 'text-red-400 font-semibold' : days <= 7 ? 'text-yellow-400' : 'text-slate-400'
                        return <span className={color} title={`${days}天后`}>{d.slice(5)}</span>
                      })()}
                    </td>
                    <td className="px-4 py-2" onClick={e => e.stopPropagation()}>
                      {!p.symbol.includes(' ') && (
                        <button
                          onClick={() => setSellPos(p)}
                          className="px-2.5 py-1 text-xs rounded bg-red-900/40 text-red-400 hover:bg-red-800/60 hover:text-red-300 transition-colors border border-red-800/50"
                        >
                          卖出
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {positions && positions.length > 0 && (
          <AllocationBar positions={positions} balance={balance} />
        )}
      </div>

      {/* 订单历史 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
          <div className="text-sm font-medium text-slate-300">订单历史</div>
          <input
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white placeholder-slate-400 w-24 focus:outline-none focus:border-blue-500"
            placeholder="过滤股票"
            value={orderSymbol}
            onChange={e => setOrderSymbol(e.target.value.toUpperCase())}
          />
        </div>
        {orders.length === 0 ? (
          <div className="px-4 py-6 text-slate-500 text-sm text-center">暂无订单记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs border-b border-slate-700">
                  {['时间', '股票', '方向', '类型', '数量', '价格', '成交价', '状态', '操作'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {orders.map((o: any) => {
                  const filled = o.status === 'Filled' || o.status === 'PartialFill'
                  const pending = ['PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel'].includes(o.status)
                  const dim = !filled ? 'opacity-40' : ''
                  return (
                  <tr key={o.id} className={`border-b border-slate-700/50 hover:bg-slate-700/30 text-xs ${dim}`}>
                    <td className="px-4 py-2 text-slate-400 font-mono">{o.created_at}</td>
                    <td className="px-4 py-2"><SymbolLink symbol={o.symbol} className={`font-mono ${filled ? 'text-white' : 'text-slate-400'}`} /></td>
                    <td className={`px-4 py-2 font-semibold ${filled ? (o.action === 'BUY' ? 'text-green-400' : 'text-red-400') : 'text-slate-500'}`}>
                      {o.action}
                    </td>
                    <td className="px-4 py-2 text-slate-400">{o.order_type}</td>
                    <td className="px-4 py-2">{o.quantity}</td>
                    <td className="px-4 py-2">{o.price ? `$${o.price.toFixed(2)}` : '市价'}</td>
                    <td className="px-4 py-2">{o.filled_price ? `$${o.filled_price.toFixed(2)}` : '-'}</td>
                    <td className={`px-4 py-2 ${filled ? 'text-slate-300' : 'text-slate-500'}`}>{o.status}</td>
                    <td className="px-4 py-2">
                      {pending && (
                        <button
                          onClick={() => handleCancelOrders(o.symbol)}
                          disabled={cancellingSym === o.symbol}
                          className="px-2 py-0.5 rounded text-[11px] bg-slate-700 hover:bg-red-700 text-slate-300 hover:text-white transition-colors disabled:opacity-40"
                          title={`撤销 ${o.symbol} 的全部未成交挂单`}
                        >
                          {cancellingSym === o.symbol ? '撤销中…' : '撤单'}
                        </button>
                      )}
                    </td>
                  </tr>
                )})}

              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>

    {selectedPos && (
      <PositionDetailModal position={selectedPos} onClose={() => setSelectedPos(null)} />
    )}
    {sellPos && (
      <SellOrderModal
        position={sellPos}
        onClose={() => setSellPos(null)}
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: ['orders'] })
          queryClient.invalidateQueries({ queryKey: ['positions'] })
        }}
      />
    )}
    </>
  )
}
