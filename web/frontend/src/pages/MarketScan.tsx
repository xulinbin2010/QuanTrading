import { useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { scanFactors, getStockDetail, getStockNews, getInsiderData, getFiveBaggerScreener, listNarrativeWatchlist, upsertNarrativeEntry, deleteNarrativeEntry } from '../api/client'
import type { NarrativeEntryBody } from '../api/client'
import ReactECharts from 'echarts-for-react'

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

function ScoreBar({ score, max = 19 }: { score: number; max?: number }) {
  const pct = (score / max) * 100
  const color = score >= 13 ? '#22c55e' : score >= 8 ? '#f59e0b' : '#64748b'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono font-semibold" style={{ color }}>{score}</span>
      <span className="text-xs text-slate-600">/{max}</span>
    </div>
  )
}

// 8 维分解 tooltip 内容
const BREAKDOWN_LABELS: Record<string, string> = {
  size: '规模', rev_growth: '营收增长', rev_accel: '营收加速',
  ps_ratio: 'PS估值', gross_margin: '毛利率', insider: '内幕买入',
  rs_momentum: '价格动量', fin_health: '财务健康',
}
const BREAKDOWN_MAX: Record<string, number> = {
  size: 2, rev_growth: 3, rev_accel: 2, ps_ratio: 3,
  gross_margin: 2, insider: 3, rs_momentum: 2, fin_health: 2,
}

function BreakdownBadges({ bd }: { bd: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-0.5">
      {Object.entries(BREAKDOWN_LABELS).map(([k, label]) => {
        const v = bd[k] ?? 0
        const max = BREAKDOWN_MAX[k] ?? 2
        const filled = v > 0
        return (
          <span
            key={k}
            title={`${label}: ${v}/${max}`}
            className={`px-1 py-0.5 rounded text-[10px] font-mono ${filled ? 'bg-blue-900/60 text-blue-300' : 'bg-slate-700/60 text-slate-600'}`}
          >
            {label[0]}{v}
          </span>
        )
      })}
    </div>
  )
}

// ── 单股详情面板 ──────────────────────────────────────────

function StockDetailPanel({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [activeTab, setActiveTab] = useState<'tech' | 'analyst'>('tech')

  const { data, isLoading, isError } = useQuery({
    queryKey: ['stock-detail', symbol],
    queryFn: () => getStockDetail(symbol, 120),
  })

  // 分析师 & 公告：切到对应 tab 时才触发（enabled 控制）
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
                      <ReactECharts option={klineOption} style={{ height: 320 }} />
                    </div>

                    {/* RS 走势 */}
                    <div>
                      <div className="mb-2 text-xs text-slate-400">RS 相对强度</div>
                      <ReactECharts option={rsOption} style={{ height: 120 }} />
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
                    {/* 目标价 + 评级分布 */}
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

                    {/* 下次财报 + 季度营收 */}
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

                    {/* 近期评级变动 */}
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

                    {/* SEC 公告 */}
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

                    {/* 近期新闻 */}
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

// ── 主页面 ────────────────────────────────────────────────

export default function MarketScan() {
  const [tab, setTab] = useState<'scan' | 'insider' | 'tenbagger'>('scan')
  const [universe, setUniverse] = useState('sp500+ndx')
  const [selected, setSelected] = useState<string | null>(null)
  const [showCoverage, setShowCoverage] = useState(false)
  const [scanState, setScanState] = useState<'idle' | 'scanning' | 'done'>('idle')
  const queryClient = useQueryClient()

  // 因子扫描（disabled 自动加载，点按钮才触发）
  const { data: scanResult, dataUpdatedAt } = useQuery({
    queryKey: ['factors-scan', universe],
    queryFn: () => scanFactors(universe, 100),
    staleTime: Infinity,
    gcTime: 24 * 3_600_000,
    refetchOnWindowFocus: false,
    enabled: false,
  })

  // 内部人买入
  const { data: insiderRows = [], isLoading: insiderLoading, refetch: refetchInsider } = useQuery({
    queryKey: ['insider-data'],
    queryFn: getInsiderData,
    staleTime: 20 * 3_600_000,
    retry: false,
  })

  // 10x 猎手 — 筛选器
  const [tbForcing, setTbForcing] = useState(false)
  const { data: tbData, isLoading: tbLoading } = useQuery({
    queryKey: ['screener-fivebagger'],
    queryFn: () => getFiveBaggerScreener(false),
    staleTime: 7 * 24 * 3_600_000,
    retry: false,
  })

  // 10x 猎手 — 叙事错位观察名单
  const { data: narrativeRows = [], isLoading: narrativeLoading } = useQuery({
    queryKey: ['narrative-watchlist'],
    queryFn: listNarrativeWatchlist,
    staleTime: 60_000,
  })
  const [narrativeForm, setNarrativeForm] = useState<NarrativeEntryBody | null>(null)
  const upsertMutation = useMutation({
    mutationFn: upsertNarrativeEntry,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['narrative-watchlist'] })
      setNarrativeForm(null)
    },
  })
  const deleteMutation = useMutation({
    mutationFn: deleteNarrativeEntry,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['narrative-watchlist'] }),
  })

  const refreshFiveBagger = () => {
    setTbForcing(true)
    getFiveBaggerScreener(true)
      .then(d => {
        queryClient.setQueryData(['screener-fivebagger'], d)
        setTbForcing(false)
      })
      .catch(() => setTbForcing(false))
  }

  // 兼容新格式 {rows, coverage, total} 和旧格式 []
  const rows: any[] = Array.isArray(scanResult) ? scanResult : (scanResult?.rows ?? [])
  const coverage: Record<string, any> = scanResult?.coverage ?? {}
  const totalScanned: number = scanResult?.total ?? rows.length

  const refresh = () => {
    if (scanState === 'scanning') return
    setScanState('scanning')
    queryClient.invalidateQueries({ queryKey: ['factors-scan', universe] })
    scanFactors(universe, 100, true)
      .then(d => {
        queryClient.setQueryData(['factors-scan', universe], d)
        setScanState('done')
        setTimeout(() => setScanState('idle'), 3000)
      })
      .catch(() => setScanState('idle'))
  }

  const lastScan = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : null

  // 显示哪些基本面列（若有数据的话）
  const fundamentalCols = [
    { key: 'revenue_growth', label: '营收增长', render: (v: any) => <FmtPct v={v} /> },
    { key: 'earnings_growth', label: '盈利增长', render: (v: any) => <FmtPct v={v} /> },
    { key: 'roe', label: 'ROE', render: (v: any) => <FmtPct v={v} /> },
    { key: 'pe_ratio', label: 'PE', render: (v: any) => <FmtNum v={v} decimals={1} suffix="x" /> },
    { key: 'pb_ratio', label: 'PB', render: (v: any) => <FmtNum v={v} decimals={2} suffix="x" /> },
  ]

  const activeFundCols = fundamentalCols.filter(col => {
    const cov = coverage[col.key]
    return cov && cov.pct > 10
  })

  const coverageEntries = Object.entries(coverage).filter(([, v]: any) => v.total > 0)

  return (
    <div className="space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">市场扫描(根据63个交易日的RS对比SPY)</h1>
        <div className="flex items-center gap-3">
          {/* Universe 选择器 */}
          <div className="flex gap-1">
            {[
              { key: 'sp500+ndx', label: 'SP500+NDX' },
              { key: 'ai',        label: 'AI产业链', sub: '$10B–$500B' },
              { key: 'sp500',     label: 'SP500' },
            ].map(u => (
              <button
                key={u.key}
                onClick={() => {
                  if (u.key !== universe) {
                    setUniverse(u.key)
                    setScanState('idle')
                  }
                }}
                title={u.sub}
                className={`px-2.5 py-1 text-xs rounded transition-colors border ${
                  universe === u.key
                    ? 'bg-blue-600 border-blue-500 text-white'
                    : 'bg-slate-700 border-slate-600 text-slate-400 hover:text-white hover:border-slate-400'
                }`}
              >
                {u.label}
              </button>
            ))}
          </div>
          {tab === 'scan' && (
            <>
              {lastScan
                ? <span className="text-xs text-slate-400">上次扫描：{lastScan}</span>
                : scanState === 'scanning'
                  ? <span className="text-xs text-slate-500">扫描中...</span>
                  : <span className="text-xs text-slate-500">点击「开始扫描」获取数据</span>
              }
              {totalScanned > 0 && <span className="text-xs text-slate-500">{rows.length}/{totalScanned} 只</span>}
              <button
                onClick={() => setShowCoverage(s => !s)}
                className="px-2 py-1 text-xs border border-slate-600 text-slate-400 hover:text-white hover:border-slate-400 rounded transition-colors"
              >
                覆盖率
              </button>
              <button
                onClick={refresh}
                disabled={scanState === 'scanning'}
                className={`px-3 py-1 text-sm text-white rounded transition-colors disabled:cursor-not-allowed
                  ${scanState === 'done'
                    ? 'bg-green-600 hover:bg-green-600'
                    : 'bg-blue-600 hover:bg-blue-500 disabled:opacity-50'
                  }`}
              >
                {scanState === 'scanning' && (
                  <span className="inline-flex items-center gap-1.5">
                    <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                    </svg>
                    扫描中...
                  </span>
                )}
                {scanState === 'done' && '扫描完成 ✓'}
                {scanState === 'idle' && (rows.length === 0 ? '开始扫描' : '重新扫描')}
              </button>
            </>
          )}
          {tab === 'insider' && (
            <button
              onClick={() => refetchInsider()}
              disabled={insiderLoading}
              className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-300 rounded transition-colors"
            >
              {insiderLoading ? '加载中...' : '刷新'}
            </button>
          )}
        </div>
      </div>

      {/* Tab 导航 */}
      <div className="flex border-b border-slate-700 -mt-2">
        {([
          { key: 'scan',       label: '因子扫描' },
          { key: 'insider',    label: '内部人买入' },
          { key: 'tenbagger',  label: '5x 猎手' },
        ] as const).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
              ${tab === t.key ? 'border-blue-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab: 因子扫描 ──────────────────────────────────── */}
      {tab === 'scan' && (
        <>
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
                {scanState === 'scanning' && rows.length === 0 ? (
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
        </>
      )}

      {/* ── Tab: 内部人买入 ────────────────────────────────── */}
      {tab === 'insider' && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          {insiderLoading && <div className="text-xs text-slate-500 py-6 text-center">从 OpenInsider 拉取数据...</div>}

          {!insiderLoading && (insiderRows as any[]).length === 0 && (
            <div className="text-xs text-slate-500 py-6 text-center">暂无数据（网络失败或无近期记录）</div>
          )}

          {!insiderLoading && (insiderRows as any[]).length > 0 && (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-400 text-xs border-b border-slate-700">
                      <th className="px-3 py-2 text-left font-medium">#</th>
                      <th className="px-3 py-2 text-left font-medium">股票</th>
                      <th className="px-3 py-2 text-right font-medium">评分</th>
                      <th className="px-3 py-2 text-right font-medium">买入人数</th>
                      <th className="px-3 py-2 text-right font-medium">买入金额</th>
                      <th className="px-3 py-2 text-right font-medium">最近日期</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {(insiderRows as any[]).map((r: any, i: number) => (
                      <tr key={r.symbol} className={`hover:bg-slate-700/30 transition-colors ${!r.in_universe ? 'opacity-35' : ''}`}>
                        <td className="px-3 py-2 text-slate-500 text-xs">{i + 1}</td>
                        <td className="px-3 py-2 font-mono font-medium text-white">{r.symbol}</td>
                        <td className="px-3 py-2 text-right">
                          <span className="text-yellow-400">{'★'.repeat(r.score)}</span>
                          <span className="text-slate-600">{'★'.repeat(3 - r.score)}</span>
                        </td>
                        <td className="px-3 py-2 text-right text-slate-300 text-xs">{r.count} 人</td>
                        <td className="px-3 py-2 text-right text-green-400 font-mono text-xs">
                          ${r.total_value >= 1_000_000
                            ? `${(r.total_value / 1_000_000).toFixed(1)}M`
                            : `${(r.total_value / 1_000).toFixed(0)}K`}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-500 text-xs">{r.last_date}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="text-xs text-slate-600 mt-3 text-right">
                共 {(insiderRows as any[]).length} 条，淡色为 sp500+ndx 池外股票
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Tab: 10x 猎手 ───────────────────────────────────── */}
      {tab === 'tenbagger' && (
        <div className="flex flex-col gap-4">

          {/* 筛选器面板 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-medium text-white">5x 候选筛选器</h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  Russell 2000 · 市值 $0.2–5B · 8 维打分（满分 19）· 按总分排序
                </p>
              </div>
              <div className="flex items-center gap-3">
                {tbData?.last_updated && (
                  <span className="text-xs text-slate-500">
                    更新：{new Date(tbData.last_updated).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
                <button
                  onClick={refreshFiveBagger}
                  disabled={tbLoading || tbForcing}
                  className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed text-white rounded transition-colors"
                >
                  {tbForcing ? '运行中...' : '手动刷新'}
                </button>
              </div>
            </div>

            {(tbLoading || tbForcing) && !tbData && (
              <div className="text-xs text-slate-500 py-8 text-center">
                正在扫描 Russell 2000（首次运行约需 2–5 分钟）...
              </div>
            )}

            {tbData && (
              <>
                <div className="text-xs text-slate-500 mb-3">
                  共扫描 {tbData.total_scanned} 只，通过过滤 {tbData.total_passed} 只，展示前 {tbData.rows?.length ?? 0} 名
                </div>
                {tbData.rows?.length === 0 ? (
                  <div className="text-xs text-slate-500 py-6 text-center">无符合条件的股票</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-slate-400 text-xs border-b border-slate-700">
                          <th className="px-3 py-2 text-left font-medium">#</th>
                          <th className="px-3 py-2 text-left font-medium">股票</th>
                          <th className="px-3 py-2 text-left font-medium">总分</th>
                          <th className="px-3 py-2 text-left font-medium whitespace-nowrap">维度分解</th>
                          <th className="px-3 py-2 text-right font-medium">市值(B)</th>
                          <th className="px-3 py-2 text-right font-medium">营收 YoY</th>
                          <th className="px-3 py-2 text-right font-medium">加速</th>
                          <th className="px-3 py-2 text-right font-medium">PS</th>
                          <th className="px-3 py-2 text-right font-medium">毛利率</th>
                          <th className="px-3 py-2 text-right font-medium">内幕</th>
                          <th className="px-3 py-2 text-right font-medium">RS</th>
                          <th className="px-3 py-2 text-left font-medium">行业</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-700/50">
                        {(tbData.rows as any[]).map((r: any, i: number) => (
                          <tr key={r.symbol} className="hover:bg-slate-700/30 transition-colors">
                            <td className="px-3 py-2 text-slate-500 text-xs">{i + 1}</td>
                            <td className="px-3 py-2 font-mono font-medium text-white">{r.symbol}</td>
                            <td className="px-3 py-2"><ScoreBar score={r.score} /></td>
                            <td className="px-3 py-2"><BreakdownBadges bd={r.breakdown ?? {}} /></td>
                            <td className="px-3 py-2 text-right text-slate-300 text-xs font-mono">${r.market_cap_b?.toFixed(1)}</td>
                            <td className="px-3 py-2 text-right"><FmtPct v={r.revenue_growth} /></td>
                            <td className="px-3 py-2 text-right">
                              {r.rev_accel != null
                                ? <span className={`font-mono text-xs ${r.rev_accel > 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {r.rev_accel > 0 ? '+' : ''}{(r.rev_accel * 100).toFixed(1)}%
                                  </span>
                                : <span className="text-slate-600">-</span>}
                            </td>
                            <td className="px-3 py-2 text-right">
                              {r.ps_ratio != null
                                ? <span className="font-mono text-xs text-slate-300">{r.ps_ratio.toFixed(1)}x</span>
                                : <span className="text-slate-600">-</span>}
                            </td>
                            <td className="px-3 py-2 text-right"><FmtPct v={r.gross_margins} decimals={0} /></td>
                            <td className="px-3 py-2 text-right">
                              <span className="text-yellow-400">{'★'.repeat(r.insider_score)}</span>
                              <span className="text-slate-600">{'★'.repeat(Math.max(0, 3 - r.insider_score))}</span>
                            </td>
                            <td className="px-3 py-2 text-right"><FmtPct v={r.rs_score} /></td>
                            <td className="px-3 py-2 text-xs text-slate-400 max-w-32 truncate">{r.industry || r.sector || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}

            {!tbData && !tbLoading && !tbForcing && (
              <div className="text-xs text-slate-500 py-6 text-center">点击「手动刷新」开始扫描</div>
            )}
          </div>

          {/* 叙事错位观察名单 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-medium text-white">叙事错位观察名单</h3>
                <p className="text-xs text-slate-500 mt-0.5">记录被错误分类的股票——旧类别 → 新叙事</p>
              </div>
              <button
                onClick={() => setNarrativeForm({ symbol: '', old_category: '', new_narrative: '', thesis_notes: '', target_price: null })}
                className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-white rounded transition-colors"
              >
                + 新增
              </button>
            </div>

            {/* 新增/编辑表单 */}
            {narrativeForm !== null && (
              <div className="mb-4 p-3 bg-slate-700/50 rounded-lg border border-slate-600 flex flex-col gap-2">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">股票代码 *</label>
                    <input
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white font-mono uppercase focus:outline-none focus:border-blue-500"
                      placeholder="e.g. PLTR"
                      value={narrativeForm.symbol}
                      onChange={e => setNarrativeForm({ ...narrativeForm, symbol: e.target.value.toUpperCase() })}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">目标价（可选）</label>
                    <input
                      type="number"
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white font-mono focus:outline-none focus:border-blue-500"
                      placeholder="e.g. 50.00"
                      value={narrativeForm.target_price ?? ''}
                      onChange={e => setNarrativeForm({ ...narrativeForm, target_price: e.target.value ? parseFloat(e.target.value) : null })}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">旧类别</label>
                    <input
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                      placeholder="e.g. 传统 SaaS"
                      value={narrativeForm.old_category ?? ''}
                      onChange={e => setNarrativeForm({ ...narrativeForm, old_category: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-slate-400 mb-1 block">新叙事</label>
                    <input
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
                      placeholder="e.g. AI 基建核心"
                      value={narrativeForm.new_narrative ?? ''}
                      onChange={e => setNarrativeForm({ ...narrativeForm, new_narrative: e.target.value })}
                    />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-slate-400 mb-1 block">投资逻辑</label>
                  <textarea
                    className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500 resize-none"
                    rows={2}
                    placeholder="简述为什么这个叙事会被重新定价..."
                    value={narrativeForm.thesis_notes ?? ''}
                    onChange={e => setNarrativeForm({ ...narrativeForm, thesis_notes: e.target.value })}
                  />
                </div>
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={() => setNarrativeForm(null)}
                    className="px-3 py-1 text-xs text-slate-400 hover:text-white transition-colors"
                  >
                    取消
                  </button>
                  <button
                    disabled={!narrativeForm.symbol || upsertMutation.isPending}
                    onClick={() => upsertMutation.mutate(narrativeForm)}
                    className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed text-white rounded transition-colors"
                  >
                    {upsertMutation.isPending ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            )}

            {narrativeLoading && <div className="text-xs text-slate-500 py-4 text-center">加载中...</div>}

            {!narrativeLoading && (narrativeRows as any[]).length === 0 && narrativeForm === null && (
              <div className="text-xs text-slate-500 py-6 text-center">暂无记录，点击「新增」添加第一条叙事</div>
            )}

            {!narrativeLoading && (narrativeRows as any[]).length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-400 text-xs border-b border-slate-700">
                      <th className="px-3 py-2 text-left font-medium">股票</th>
                      <th className="px-3 py-2 text-left font-medium">旧类别</th>
                      <th className="px-3 py-2 text-left font-medium">新叙事</th>
                      <th className="px-3 py-2 text-left font-medium">投资逻辑</th>
                      <th className="px-3 py-2 text-right font-medium">目标价</th>
                      <th className="px-3 py-2 text-right font-medium">添加日期</th>
                      <th className="px-3 py-2 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {(narrativeRows as any[]).map((r: any) => (
                      <tr key={r.id} className="hover:bg-slate-700/30 transition-colors">
                        <td className="px-3 py-2 font-mono font-medium text-white">{r.symbol}</td>
                        <td className="px-3 py-2 text-xs text-slate-400">{r.old_category || '-'}</td>
                        <td className="px-3 py-2 text-xs text-blue-400">{r.new_narrative || '-'}</td>
                        <td className="px-3 py-2 text-xs text-slate-400 max-w-48 truncate" title={r.thesis_notes}>{r.thesis_notes || '-'}</td>
                        <td className="px-3 py-2 text-right text-xs font-mono text-slate-300">
                          {r.target_price ? `$${r.target_price.toFixed(2)}` : '-'}
                        </td>
                        <td className="px-3 py-2 text-right text-xs text-slate-500">
                          {r.added_at ? r.added_at.slice(0, 10) : '-'}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <div className="flex gap-2 justify-end">
                            <button
                              onClick={() => setNarrativeForm({ symbol: r.symbol, old_category: r.old_category, new_narrative: r.new_narrative, thesis_notes: r.thesis_notes, target_price: r.target_price })}
                              className="text-xs text-slate-400 hover:text-white transition-colors"
                            >
                              编辑
                            </button>
                            <button
                              onClick={() => deleteMutation.mutate(r.id)}
                              className="text-xs text-red-500 hover:text-red-400 transition-colors"
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {selected && (
        <StockDetailPanel symbol={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
