/**
 * 全局共享：股票详情弹窗（K 线 + EMA + 因子 + 分析师）。
 * 通过 <StockChartProvider> + useStockChart() 在任何位置调起。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getStockDetail, getStockNews } from '../api/client'

// ── 内嵌辅助组件（与 MarketScan 同源）────────────────────────

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

function FmtPct({ v, decimals = 1 }: { v: number | null | undefined; decimals?: number }) {
  const n = typeof v === 'number' ? v : (v != null ? Number(v) : NaN)
  if (!Number.isFinite(n)) return <span className="text-slate-600">-</span>
  const color = n > 0 ? 'text-green-400' : n < 0 ? 'text-red-400' : 'text-slate-400'
  return <span className={`font-mono text-xs ${color}`}>{(n * 100).toFixed(decimals)}%</span>
}

function FmtNum({ v, decimals = 1, suffix = '' }: { v: number | null | undefined; decimals?: number; suffix?: string }) {
  const n = typeof v === 'number' ? v : (v != null ? Number(v) : NaN)
  if (!Number.isFinite(n)) return <span className="text-slate-600">-</span>
  return <span className="font-mono text-xs text-slate-300">{n.toFixed(decimals)}{suffix}</span>
}

// ── 主组件 ──────────────────────────────────────────────────

export default function StockChartModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [activeTab, setActiveTab] = useState<'tech' | 'analyst'>('tech')

  const { data, isLoading, isError } = useQuery({
    queryKey: ['stock-detail', symbol],
    queryFn: () => getStockDetail(symbol, 120),
  })

  const { data: newsData, isLoading: newsLoading } = useQuery({
    queryKey: ['stock-news', symbol],
    queryFn: () => getStockNews(symbol),
    enabled: activeTab === 'analyst',
    staleTime: 2 * 60 * 60 * 1000,
  })

  const klineOption = data ? {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'MA10', 'MA20'], textStyle: { color: '#94a3b8' }, top: 0 },
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

  const TABS = [
    { key: 'tech',    label: '技术分析' },
    { key: 'analyst', label: '分析师 & 公告' },
  ] as const

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-[920px] max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>

        {/* 标题栏 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700 shrink-0">
          <div className="text-white font-semibold">{symbol} — 个股详情</div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-lg leading-none">✕</button>
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
        </div>

        {/* 内容区（可滚动） */}
        <div className="overflow-y-auto flex-1 p-5">

          {/* ── Tab 1：技术分析 ── */}
          {activeTab === 'tech' && (
            <div className="space-y-4">
              {isLoading && <div className="text-slate-400 text-sm py-8 text-center">加载中...</div>}
              {isError   && <div className="text-red-400 text-sm py-8 text-center">加载失败</div>}
              {data && (() => {
                const last = data.factors[data.factors.length - 1]
                return (
                  <>
                    {/* 技术因子状态 */}
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

                    {/* 基本面快照 */}
                    {hasFundamental && (
                      <div>
                        <div className="text-xs text-slate-400 mb-2 font-medium">基本面快照（最新）</div>
                        <div className="grid grid-cols-4 gap-2">
                          {[
                            { label: '营收增长',      key: 'revenue_growth',  render: (v: any) => <FmtPct v={v} /> },
                            { label: '盈利增长',      key: 'earnings_growth', render: (v: any) => <FmtPct v={v} /> },
                            { label: 'ROE',           key: 'roe',             render: (v: any) => <FmtPct v={v} /> },
                            { label: 'D/E',           key: 'debt_to_equity',  render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
                            { label: 'FCF 收益率',    key: 'fcf_yield',       render: (v: any) => <FmtPct v={v} decimals={2} /> },
                            { label: 'PE',            key: 'pe_ratio',        render: (v: any) => <FmtNum v={v} decimals={1} suffix="x" /> },
                            { label: 'PB',            key: 'pb_ratio',        render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
                          ].map(({ label, key, render }) => (
                            <div key={key} className="bg-slate-700/30 rounded p-2">
                              <div className="text-xs text-slate-500 mb-1">{label}</div>
                              <div>{render(fund[key as keyof typeof fund])}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* K 线 */}
                    <div>
                      <div className="mb-2 text-xs text-slate-400">K 线（含 MA10 / MA20）</div>
                      <ReactECharts option={klineOption} style={{ height: 320 }} notMerge />
                    </div>

                    {/* RS 走势 */}
                    <div>
                      <div className="mb-2 text-xs text-slate-400">RS 相对强度</div>
                      <ReactECharts option={rsOption} style={{ height: 120 }} notMerge />
                    </div>
                  </>
                )
              })()}
            </div>
          )}

          {/* ── Tab 2：分析师 & 公告 ── */}
          {activeTab === 'analyst' && (
            <div className="space-y-5">
              {newsLoading && (
                <div className="text-slate-500 text-sm py-12 text-center animate-pulse">加载中...</div>
              )}
              {!newsLoading && newsData && (() => {
                const an      = newsData.analyst ?? {}
                const tp      = an.target_price
                const rec     = an.recommendation
                const changes: any[] = an.recent_changes ?? []
                const ne      = an.next_earnings
                const qrev:   any[] = an.quarterly_revenue ?? []
                const filings: any[] = newsData.sec_filings ?? []
                const news:   any[] = newsData.news ?? []

                const actionLabel = (a: string) => {
                  if (a === 'up' || a === 'upgrade')     return { text: '上调', cls: 'text-emerald-400' }
                  if (a === 'down' || a === 'downgrade') return { text: '下调', cls: 'text-red-400' }
                  if (a === 'init')                       return { text: '首次', cls: 'text-blue-400' }
                  return { text: '维持', cls: 'text-slate-400' }
                }

                const formBadge: Record<string, string> = {
                  '8-K':   'bg-red-900/70 text-red-300',
                  '8-K/A': 'bg-red-900/70 text-red-300',
                  '10-K':  'bg-blue-900/70 text-blue-300',
                  '10-K/A':'bg-blue-900/70 text-blue-300',
                  '10-Q':  'bg-slate-600 text-slate-300',
                  '10-Q/A':'bg-slate-600 text-slate-300',
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
                                  {tp.mean > tp.current ? '▲' : '▼'}
                                  {Math.abs((tp.mean / tp.current - 1) * 100).toFixed(1)}%
                                </span>
                              )}
                            </div>
                            {tp.low != null && tp.high != null && tp.current != null && (() => {
                              const range   = tp.high - tp.low || 1
                              const curPct  = Math.max(0, Math.min(100, (tp.current - tp.low) / range * 100))
                              const meanPct = Math.max(0, Math.min(100, (tp.mean   - tp.low) / range * 100))
                              return (
                                <div>
                                  <div className="relative h-2 bg-slate-600 rounded-full mb-1.5">
                                    <div className="absolute inset-0 bg-gradient-to-r from-red-500/50 to-emerald-500/50 rounded-full" />
                                    <div className="absolute top-1/2 -translate-y-1/2 w-1.5 h-5 bg-blue-400 rounded -translate-x-1/2" style={{ left: `${meanPct}%` }} title={`均值 $${tp.mean}`} />
                                    <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full border-2 border-slate-700 -translate-x-1/2" style={{ left: `${curPct}%` }} title={`当前 $${tp.current}`} />
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
                          const total   = rec.strongBuy + rec.buy + rec.hold + rec.sell + rec.strongSell || 1
                          const buyPct  = Math.round((rec.strongBuy + rec.buy) / total * 100)
                          const holdPct = Math.round(rec.hold / total * 100)
                          const sellPct = 100 - buyPct - holdPct
                          return (
                            <div className="bg-slate-700/40 rounded-lg p-4">
                              <div className="text-xs text-slate-400 mb-2">机构评级分布（{total} 家）</div>
                              <div className="flex h-4 rounded-full overflow-hidden mb-3">
                                <div className="bg-emerald-500 transition-all" style={{ width: `${buyPct}%` }} />
                                <div className="bg-amber-500/80 transition-all" style={{ width: `${holdPct}%` }} />
                                <div className="bg-red-500/70 transition-all" style={{ width: `${sellPct}%` }} />
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
                              <div className="text-sm text-slate-300 mb-1">
                                EPS 预期&nbsp;
                                <span className="text-emerald-400 font-semibold">${ne.eps_avg?.toFixed(2)}</span>
                                <span className="text-slate-500 text-xs ml-1">(${ne.eps_low?.toFixed(2)}–${ne.eps_high?.toFixed(2)})</span>
                              </div>
                            )}
                            {ne.rev_avg_b != null && (
                              <div className="text-xs text-slate-400">营收预期：<span className="text-slate-300">${ne.rev_avg_b}B</span></div>
                            )}
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
                                    <div
                                      className="w-full bg-blue-500/70 hover:bg-blue-400/80 rounded-t transition-colors cursor-default"
                                      style={{ height: `${Math.max(4, Math.round(q.revenue_b / maxRev * 48))}px` }}
                                      title={`${q.quarter}：$${q.revenue_b}B`}
                                    />
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
                              <div key={i} className="flex items-center gap-3 text-sm py-1.5 px-3 rounded-lg bg-slate-700/30 hover:bg-slate-700/50 transition-colors">
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
                               className={`block rounded-lg p-3 hover:bg-slate-700/70 transition-colors border ${
                                 f.priority
                                   ? 'border-amber-700/60 bg-amber-900/10'
                                   : 'border-slate-700/50 bg-slate-700/20'
                               }`}>
                              <div className="flex items-center gap-2.5 mb-1.5">
                                <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded shrink-0 ${formBadge[f.form] ?? 'bg-slate-600 text-slate-300'}`}>
                                  {f.form}
                                </span>
                                <span className="text-xs text-slate-500 shrink-0">{f.date}</span>
                                <span className="text-sm font-medium text-slate-100 flex-1">{f.label}</span>
                                <span className="text-slate-500 text-xs shrink-0">↗</span>
                              </div>
                              {f.snippet && (
                                <p className="text-xs text-slate-400 leading-relaxed line-clamp-2 pl-0.5">{f.snippet}</p>
                              )}
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
