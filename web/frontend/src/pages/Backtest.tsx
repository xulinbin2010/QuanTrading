import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { runBacktest, runWalkForward, getBacktestStatus, getBacktestResult, getBacktestHistory, getFactorRegistry, getVixAnalysis } from '../api/client'
import ReactECharts from 'echarts-for-react'

const PERIODS = ['1mo', '3mo', '6mo', '1y']

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
const MONTHS = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

function pad(n: number) { return String(n).padStart(2, '0') }
function dateToStr(d: Date) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }
function strToDate(s: string): Date | undefined {
  if (!s) return undefined
  const [y, m, d] = s.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return isNaN(dt.getTime()) ? undefined : dt
}

// ── 三层日历：日 → 月 → 年 ─────────────────────────────────
function CalendarPanel({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const today = new Date()
  const sel = strToDate(value)
  const init = sel ?? today
  const [view, setView] = useState<'day' | 'month' | 'year'>('day')
  const [curYear, setCurYear] = useState(init.getFullYear())
  const [curMonth, setCurMonth] = useState(init.getMonth())   // 0-based

  // 年份范围：每次显示 12 年，向下取整到12倍数
  const yearBase = Math.floor(curYear / 12) * 12

  // 日历格子
  const firstDay = new Date(curYear, curMonth, 1).getDay()
  const daysInMonth = new Date(curYear, curMonth + 1, 0).getDate()
  const cells: (number | null)[] = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  const hdr = 'flex items-center justify-between px-3 py-2 border-b border-slate-700'
  const navBtn = 'w-7 h-7 flex items-center justify-center rounded hover:bg-slate-600 text-slate-400 hover:text-white transition-colors text-sm select-none'
  const titleBtn = 'font-medium text-white hover:text-blue-400 transition-colors cursor-pointer text-sm px-1'
  const cell = (active: boolean, today: boolean, outside: boolean) =>
    `w-8 h-7 flex items-center justify-center rounded text-xs cursor-pointer select-none transition-colors
    ${active ? 'bg-blue-600 text-white' : today ? 'border border-blue-500 text-blue-300 hover:bg-slate-600' : outside ? 'text-slate-600' : 'text-slate-300 hover:bg-slate-600'}`

  if (view === 'year') return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={() => setCurYear(yearBase - 12)}>‹</button>
        <span className="text-sm text-slate-400">{yearBase}–{yearBase + 11}</span>
        <button className={navBtn} onClick={() => setCurYear(yearBase + 12)}>›</button>
      </div>
      <div className="grid grid-cols-3 gap-1 p-3">
        {Array.from({ length: 12 }, (_, i) => yearBase + i).map(y => (
          <button key={y}
            onClick={() => { setCurYear(y); setView('month') }}
            className={`py-1.5 rounded text-sm transition-colors
              ${y === curYear ? 'bg-blue-600 text-white' : y === today.getFullYear() ? 'border border-blue-500 text-blue-300 hover:bg-slate-600' : 'text-slate-300 hover:bg-slate-600'}`}
          >{y}</button>
        ))}
      </div>
    </div>
  )

  if (view === 'month') return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={() => setCurYear(y => y - 1)}>‹</button>
        <button className={titleBtn} onClick={() => setView('year')}>{curYear}年</button>
        <button className={navBtn} onClick={() => setCurYear(y => y + 1)}>›</button>
      </div>
      <div className="grid grid-cols-3 gap-1 p-3">
        {MONTHS.map((name, i) => (
          <button key={i}
            onClick={() => { setCurMonth(i); setView('day') }}
            className={`py-1.5 rounded text-sm transition-colors
              ${i === curMonth && curYear === (sel?.getFullYear() ?? -1) ? 'bg-blue-600 text-white'
              : i === today.getMonth() && curYear === today.getFullYear() ? 'border border-blue-500 text-blue-300 hover:bg-slate-600'
              : 'text-slate-300 hover:bg-slate-600'}`}
          >{name}</button>
        ))}
      </div>
    </div>
  )

  // day view
  const prevMonth = () => { if (curMonth === 0) { setCurYear(y => y - 1); setCurMonth(11) } else setCurMonth(m => m - 1) }
  const nextMonth = () => { if (curMonth === 11) { setCurYear(y => y + 1); setCurMonth(0) } else setCurMonth(m => m + 1) }

  return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={prevMonth}>‹</button>
        <div className="flex gap-1">
          <button className={titleBtn} onClick={() => setView('year')}>{curYear}年</button>
          <button className={titleBtn} onClick={() => setView('month')}>{MONTHS[curMonth]}</button>
        </div>
        <button className={navBtn} onClick={nextMonth}>›</button>
      </div>
      <div className="p-2">
        <div className="grid grid-cols-7 mb-1">
          {WEEKDAYS.map(w => <div key={w} className="w-8 text-center text-xs text-slate-500 py-1">{w}</div>)}
        </div>
        <div className="grid grid-cols-7 gap-y-0.5">
          {cells.map((day, i) => {
            if (!day) return <div key={i} className="w-8 h-7" />
            const isSelected = sel?.getFullYear() === curYear && sel?.getMonth() === curMonth && sel?.getDate() === day
            const isToday = today.getFullYear() === curYear && today.getMonth() === curMonth && today.getDate() === day
            return (
              <button key={i}
                onClick={() => onChange(dateToStr(new Date(curYear, curMonth, day)))}
                className={cell(isSelected, isToday, false)}
              >{day}</button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── 触发器 + 弹出面板 ──────────────────────────────────────
function DatePicker({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div className="relative" ref={ref}>
      {label && <span className="block text-xs text-slate-400 mb-1">{label}</span>}
      <button
        type="button"
        onClick={() => setOpen(s => !s)}
        className="flex items-center gap-2 bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm hover:border-slate-400 focus:outline-none focus:border-blue-500 transition-colors min-w-[140px]"
      >
        <svg className="w-4 h-4 text-slate-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
        <span className={value ? 'text-white' : 'text-slate-500'}>{value || '选择日期'}</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-2xl">
          <CalendarPanel value={value} onChange={v => { onChange(v); setOpen(false) }} />
          {value && (
            <div className="border-t border-slate-700 px-3 py-1.5">
              <button type="button" onClick={() => { onChange(''); setOpen(false) }}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors">清除</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function pct(v: number) {
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'
}
function fmt(v: number) {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function SummaryCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-slate-700/50 rounded-lg p-3 border border-slate-600/50">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-base font-semibold ${color ?? 'text-white'}`}>{value}</div>
    </div>
  )
}

function EquityChart({ data }: { data: { date: string; equity: number; spy_equity: number }[] }) {
  if (!data.length) return null
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略', 'SPY'], textStyle: { color: '#94a3b8' }, top: 0 },
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category', data: data.map(d => d.date),
      axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      scale: true, axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => '$' + (v / 1000).toFixed(0) + 'K' },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: '策略', type: 'line', smooth: true, symbol: 'none',
        data: data.map(d => d.equity), lineStyle: { color: '#3b82f6', width: 2 },
      },
      {
        name: 'SPY', type: 'line', smooth: true, symbol: 'none',
        data: data.map(d => d.spy_equity), lineStyle: { color: '#94a3b8', width: 1.5, type: 'dashed' },
      },
    ],
  }
  return <ReactECharts option={option} style={{ height: 280 }} />
}

function BacktestResult({ taskId }: { taskId: string }) {
  const { data: status } = useQuery({
    queryKey: ['bt-status', taskId],
    queryFn: () => getBacktestStatus(taskId),
    refetchInterval: (q) => q.state.data?.status === 'running' ? 2000 : false,
  })

  const { data: result } = useQuery({
    queryKey: ['bt-result', taskId],
    queryFn: () => getBacktestResult(taskId),
    enabled: status?.status === 'completed',
  })

  if (status?.status === 'failed') {
    return <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">{status.error ?? '回测失败'}</div>
  }

  if (status?.status !== 'completed' || !result) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-8 text-center">
        <div className="text-slate-400 text-sm mb-2">回测执行中...</div>
        <div className="w-48 h-1.5 bg-slate-700 rounded-full mx-auto overflow-hidden">
          <div className="h-full bg-amber-500 rounded-full animate-pulse w-3/4" />
        </div>
      </div>
    )
  }

  const s = result.summary
  const trades = result.trades ?? []
  const wins = trades.filter((t: any) => t.pnl > 0)

  return (
    <div className="space-y-4">
      {/* 摘要卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard label="总收益率" value={pct(s.total_return)} color={s.total_return >= 0 ? 'text-green-400' : 'text-red-400'} />
        <SummaryCard label="年化收益" value={pct(s.annual_return)} color={s.annual_return >= 0 ? 'text-green-400' : 'text-red-400'} />
        <SummaryCard label="超额收益 vs SPY" value={pct(s.excess_return)} color={s.excess_return >= 0 ? 'text-green-400' : 'text-red-400'} />
        <SummaryCard label="SPY 同期" value={pct(s.spy_return)} />
        <SummaryCard label="Sharpe" value={s.sharpe.toFixed(2)} />
        <SummaryCard label="最大回撤" value={pct(s.max_drawdown)} color="text-red-400" />
        <SummaryCard label="胜率" value={pct(s.win_rate)} />
        <SummaryCard label="交易笔数" value={String(s.total_trades)} />
      </div>

      {/* 净值曲线 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="text-sm text-slate-300 mb-3 font-medium">净值曲线 vs SPY</div>
        <EquityChart data={result.equity_curve ?? []} />
        <div className="mt-2 text-xs text-slate-500 text-right">
          初始资金：{fmt(s.initial_cash)} → 最终净值：{fmt(s.final_equity)} | 手续费：{fmt(s.total_commission)}
        </div>
      </div>

      {/* 交易明细 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300">
          已平仓交易（{trades.length} 笔，胜 {wins.length} 笔）
        </div>
        <div className="overflow-x-auto max-h-64">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-800">
              <tr className="text-slate-400 border-b border-slate-700">
                {['股票', '买入日', '卖出日', '持仓天数', '买入价', '卖出价', '收益率', '盈亏', '原因'].map(h => (
                  <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t: any, i: number) => (
                <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                  <td className="px-3 py-1.5 font-mono text-white">{t.symbol}</td>
                  <td className="px-3 py-1.5 text-slate-400">{t.entry_date}</td>
                  <td className="px-3 py-1.5 text-slate-400">{t.exit_date}</td>
                  <td className="px-3 py-1.5 text-slate-400">{t.days_held != null ? `${t.days_held}天` : '-'}</td>
                  <td className="px-3 py-1.5 font-mono">${t.entry_price.toFixed(2)}</td>
                  <td className="px-3 py-1.5 font-mono">${t.exit_price?.toFixed(2) ?? '-'}</td>
                  <td className={`px-3 py-1.5 font-mono ${t.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(t.return)}</td>
                  <td className={`px-3 py-1.5 font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmt(t.pnl)}</td>
                  <td className="px-3 py-1.5 text-slate-400">{t.exit_reason ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 未平仓持仓 */}
      {result.open_positions?.length > 0 && (
        <div className="bg-slate-800 rounded-lg border border-slate-700">
          <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300">
            未平仓持仓（{result.open_positions.length} 只）
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  {['股票', '买入日', '买入价', '现价', '收益率', '浮盈', '持仓天数'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.open_positions.map((p: any) => (
                  <tr key={p.symbol} className="border-b border-slate-700/50">
                    <td className="px-3 py-1.5 font-mono text-white">{p.symbol}</td>
                    <td className="px-3 py-1.5 text-slate-400">{p.entry_date}</td>
                    <td className="px-3 py-1.5 font-mono">${p.entry_price.toFixed(2)}</td>
                    <td className="px-3 py-1.5 font-mono">${p.cur_price.toFixed(2)}</td>
                    <td className={`px-3 py-1.5 font-mono ${p.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(p.return)}</td>
                    <td className={`px-3 py-1.5 font-mono ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmt(p.pnl)}</td>
                    <td className="px-3 py-1.5 text-slate-400">{p.days_held}天</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// 因子分类中文
const CATEGORY_LABELS: Record<string, string> = {
  momentum: '动量', volume: '成交量', trend: '趋势', growth: '成长', quality: '质量', value: '估值',
}

const DEFAULT_PARAMS = {
  period: '3mo', start: '', end: '',
  universe: 'sp500+ndx', top_n: 6,
  min_cap_b: 10, max_cap_b: 5000,
  deny_industries: [] as string[],
  useCustomDate: false,
}

function loadStored<T>(key: string, fallback: T): T {
  try {
    const s = localStorage.getItem(key)
    return s ? JSON.parse(s) : fallback
  } catch { return fallback }
}

// ── VIX 恐慌回测 ─────────────────────────────────────────────
function VixBacktest() {
  const [threshold, setThreshold] = useState(30)
  const [start, setStart] = useState('2010-01-01')
  const [end, setEnd] = useState('')
  const [symbol, setSymbol] = useState('SPY')
  const [mode, setMode] = useState<'spike' | 'peak'>('spike')
  const [viewMode, setViewMode] = useState<'win_rate' | 'avg_return'>('win_rate')
  const [enabled, setEnabled] = useState(false)

  const { data, isFetching, refetch } = useQuery({
    queryKey: ['vix-analysis', threshold, start, end, symbol, mode],
    queryFn: () => getVixAnalysis({ threshold, start, end: end || undefined, symbol, mode }),
    enabled,
    staleTime: 300_000,
  })

  const heatmap = data?.heatmap
  const horizons: number[] = heatmap?.horizons ?? []
  const buckets: string[] = heatmap?.buckets ?? []
  const matrix: (number | null)[][] = viewMode === 'win_rate' ? (heatmap?.win_rate ?? []) : (heatmap?.avg_return ?? [])
  const countMatrix: number[][] = heatmap?.count ?? []

  // ECharts 热力图 option
  const heatmapOption = heatmap ? (() => {
    const heatData: [number, number, number | null][] = []
    matrix.forEach((row, bi) => {
      row.forEach((val, hi) => { heatData.push([hi, bi, val]) })
    })
    const isWin = viewMode === 'win_rate'
    return {
      backgroundColor: 'transparent',
      tooltip: {
        formatter: (p: any) => {
          const [hi, bi, val] = p.data
          const cnt = countMatrix[bi]?.[hi] ?? 0
          const label = isWin ? `胜率 ${val?.toFixed(1)}%` : `均收益 ${val?.toFixed(2)}%`
          return `VIX ${buckets[bi]} / 持有 ${horizons[hi]}天<br/>${label}<br/>样本数 ${cnt}`
        },
      },
      grid: { top: 30, bottom: 60, left: 70, right: 20 },
      xAxis: {
        type: 'category',
        data: horizons.map(h => `${h}d`),
        axisLabel: { color: '#94a3b8' },
        axisLine: { lineStyle: { color: '#475569' } },
      },
      yAxis: {
        type: 'category',
        data: buckets,
        axisLabel: { color: '#94a3b8' },
        axisLine: { lineStyle: { color: '#475569' } },
      },
      visualMap: {
        min: isWin ? 40 : -5,
        max: isWin ? 90 : 10,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        inRange: { color: isWin ? ['#ef4444','#fbbf24','#22c55e'] : ['#ef4444','#fbbf24','#22c55e'] },
        textStyle: { color: '#94a3b8' },
      },
      series: [{
        type: 'heatmap',
        data: heatData,
        label: {
          show: true,
          formatter: (p: any) => p.data[2] != null
            ? (isWin ? `${p.data[2].toFixed(0)}%` : `${p.data[2].toFixed(1)}%`)
            : '-',
          color: '#fff',
          fontWeight: 'bold',
          fontSize: 12,
          textBorderColor: '#0f172a',
          textBorderWidth: 2,
        },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } },
      }],
    }
  })() : null

  // VIX 历史走势图
  const vixChartOption = data?.vix_series ? (() => {
    const signalSet = new Set(data.signal_dates ?? [])
    const dates = data.vix_series.map((d: any) => d.date)
    const vals = data.vix_series.map((d: any) => d.vix)
    const signalPoints = data.vix_series
      .filter((d: any) => signalSet.has(d.date))
      .map((d: any) => ({ name: d.date, coord: [d.date, d.vix] }))
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      grid: { top: 20, bottom: 40, left: 50, right: 20 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#475569' } } },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#334155' } } },
      series: [
        {
          type: 'line', data: vals, name: 'VIX', lineStyle: { color: '#f59e0b', width: 1.5 },
          areaStyle: { color: 'rgba(245,158,11,0.1)' }, symbol: 'none',
          markLine: {
            silent: true, symbol: 'none',
            data: [{ yAxis: threshold, lineStyle: { color: '#ef4444', type: 'dashed', width: 1.5 }, label: { formatter: `VIX ${threshold}`, color: '#ef4444' } }],
          },
          markPoint: {
            symbol: 'circle', symbolSize: 5,
            itemStyle: { color: '#ef4444' },
            data: signalPoints.slice(-200),
          },
        },
      ],
    }
  })() : null

  return (
    <div className="space-y-4">
      {/* 配置栏 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div>
            <div className="text-xs text-slate-400 mb-1">触发阈值</div>
            <select value={threshold} onChange={e => setThreshold(+e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white">
              {[20, 25, 30, 35, 40].map(v => <option key={v} value={v}>VIX &gt; {v}</option>)}
            </select>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">触发模式</div>
            <select value={mode} onChange={e => setMode(e.target.value as any)}
              className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white">
              <option value="spike">spike — 当日超阈值</option>
              <option value="peak">peak — 峰值回落（更准）</option>
            </select>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">标的</div>
            <select value={symbol} onChange={e => setSymbol(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white">
              {['SPY', 'QQQ', 'IWM', 'DIA'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">起始日期</div>
            <input type="date" value={start} onChange={e => setStart(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white" />
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">结束日期</div>
            <input type="date" value={end} onChange={e => setEnd(e.target.value)}
              placeholder="今天"
              className="bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white" />
          </div>
          <button
            onClick={() => { setEnabled(true); setTimeout(() => refetch(), 0) }}
            disabled={isFetching}
            className="px-4 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            {isFetching ? '计算中...' : '▶ 分析'}
          </button>
        </div>

        {/* 说明 */}
        <div className="mt-3 text-xs text-slate-500 space-y-0.5">
          <div><span className="text-amber-400">spike模式</span>：VIX当日收盘超过阈值即触发，包含所有恐慌日，样本量多</div>
          <div><span className="text-amber-400">peak模式</span>：VIX超阈值且当日低于昨日（峰值回落），避免接飞刀，买在恐慌缓解时</div>
        </div>
      </div>

      {data && (
        <>
          {/* 统计摘要 */}
          <div className="flex gap-3 text-sm">
            <div className="bg-slate-800 rounded border border-slate-700 px-4 py-2">
              <span className="text-slate-400">信号次数</span>
              <span className="ml-2 text-white font-bold">{data.total_events}</span>
            </div>
            <div className="bg-slate-800 rounded border border-slate-700 px-4 py-2">
              <span className="text-slate-400">模式</span>
              <span className="ml-2 text-amber-400">{data.mode} / VIX&gt;{data.threshold}</span>
            </div>
            <div className="bg-slate-800 rounded border border-slate-700 px-4 py-2">
              <span className="text-slate-400">标的</span>
              <span className="ml-2 text-white">{data.symbol}</span>
            </div>
          </div>

          {/* 热力图 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="flex items-center gap-3 mb-3">
              <span className="text-sm font-medium text-white">
                {viewMode === 'win_rate' ? '胜率热力图（%）' : '平均收益热力图（%）'}
              </span>
              <div className="flex gap-1 ml-auto">
                {(['win_rate', 'avg_return'] as const).map(v => (
                  <button key={v} onClick={() => setViewMode(v)}
                    className={`px-3 py-1 text-xs rounded transition-colors ${viewMode === v ? 'bg-amber-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-white'}`}>
                    {v === 'win_rate' ? '胜率' : '均收益'}
                  </button>
                ))}
              </div>
            </div>
            <div className="text-xs text-slate-500 mb-2">行 = VIX区间 / 列 = 买入后持有天数 / 格子内 = {viewMode === 'win_rate' ? '上涨概率' : '平均收益率'}</div>
            {heatmapOption && <ReactECharts option={heatmapOption} style={{ height: 280 }} />}
          </div>

          {/* VIX走势图 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="text-sm font-medium text-white mb-3">
              VIX历史走势 <span className="text-xs text-slate-400 ml-1">红点 = 触发信号日</span>
            </div>
            {vixChartOption && <ReactECharts option={vixChartOption} style={{ height: 200 }} />}
          </div>

          {/* 历史事件表 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="text-sm font-medium text-white mb-3">
              历史触发事件（最近{Math.min(data.events?.length ?? 0, 300)}条）
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="text-left py-2 pr-4">日期</th>
                    <th className="text-right pr-4">VIX</th>
                    {horizons.map(h => <th key={h} className="text-right pr-3">{h}d</th>)}
                  </tr>
                </thead>
                <tbody>
                  {(data.events ?? []).map((ev: any) => (
                    <tr key={ev.date} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className="py-1.5 pr-4 text-slate-300">{ev.date}</td>
                      <td className={`text-right pr-4 font-mono font-medium ${ev.vix >= 40 ? 'text-red-400' : ev.vix >= 30 ? 'text-amber-400' : 'text-slate-300'}`}>
                        {ev.vix}
                      </td>
                      {horizons.map(h => {
                        const v = ev[`ret_${h}d`]
                        return (
                          <td key={h} className={`text-right pr-3 font-mono ${v == null ? 'text-slate-600' : v >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {v != null ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '—'}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {!data && !isFetching && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-10 text-center text-slate-500">
          设置参数后点击「分析」，查看VIX恐慌指数触发后的历史胜率
        </div>
      )}
    </div>
  )
}

// ── Walk-Forward 验证 ─────────────────────────────────────────
function WalkForwardTab() {
  const [trainMonths, setTrainMonths] = useState(24)
  const [testMonths, setTestMonths] = useState(12)
  const [totalStart, setTotalStart] = useState('2020-01-01')
  const [totalEnd, setTotalEnd] = useState('')
  const [topN, setTopN] = useState(10)
  const [taskId, setTaskId] = useState<string | null>(null)

  const { mutate, isPending } = useMutation({
    mutationFn: () => runWalkForward({
      train_months: trainMonths, test_months: testMonths,
      total_start: totalStart, total_end: totalEnd || undefined,
      universe: 'sp500', top_n: topN,
    }),
    onSuccess: (data) => setTaskId(data.task_id),
  })

  const { data: status } = useQuery({
    queryKey: ['wf-status', taskId],
    queryFn: () => getBacktestStatus(taskId!),
    enabled: !!taskId,
    refetchInterval: (q) => q.state.data?.status === 'running' ? 2000 : false,
  })

  const { data: result } = useQuery({
    queryKey: ['wf-result', taskId],
    queryFn: () => getBacktestResult(taskId!),
    enabled: status?.status === 'completed',
  })

  const windows: any[] = result?.windows ?? []
  const summary = result?.summary

  return (
    <div className="space-y-4">
      {/* 配置 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="text-sm font-medium text-slate-300 mb-1">Walk-Forward 参数</div>
        <div className="text-xs text-slate-500 mb-4">
          固定策略参数，滚动验证样本外（OOS）表现。IS-OOS Sharpe 差距 &lt; 0.5 说明策略泛化性良好。
        </div>
        {/* 所有参数一行 */}
        <div className="flex flex-wrap items-end gap-4">
          <DatePicker label="起始日期" value={totalStart} onChange={setTotalStart} />
          <DatePicker label="结束日期（空=今天）" value={totalEnd} onChange={setTotalEnd} />
          <div>
            <label className="block text-xs text-slate-400 mb-1">训练窗口（月）</label>
            <input type="number" min={6} max={60} value={trainMonths}
              onChange={e => setTrainMonths(+e.target.value)}
              className="w-16 bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">测试窗口（月）</label>
            <input type="number" min={3} max={24} value={testMonths}
              onChange={e => setTestMonths(+e.target.value)}
              className="w-16 bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">Top N</label>
            <input type="number" min={3} max={20} value={topN}
              onChange={e => setTopN(+e.target.value)}
              className="w-16 bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
        </div>
        <div className="mt-4">
          <button
            onClick={() => mutate()}
            disabled={isPending || status?.status === 'running'}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            {isPending || status?.status === 'running' ? '提交中...' : '▶ 开始验证'}
          </button>
          {taskId && status?.status === 'running' && (
            <span className="ml-3 text-xs text-slate-400 animate-pulse">正在逐窗口回测，请等待...</span>
          )}
        </div>
      </div>

      {/* 运行中 */}
      {taskId && status?.status === 'running' && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-8 text-center">
          <div className="text-slate-400 text-sm mb-2">Walk-Forward 验证运行中...</div>
          <div className="w-48 h-1.5 bg-slate-700 rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-amber-500 rounded-full animate-pulse w-2/3" />
          </div>
          <div className="mt-2 text-xs text-slate-500">正在逐窗口加载数据并回测，耗时约 1~5 分钟</div>
        </div>
      )}

      {/* 错误 */}
      {status?.status === 'failed' && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
          {status.error ?? '验证失败'}
        </div>
      )}

      {/* 结果 */}
      {result && summary && (
        <div className="space-y-4">
          {/* 汇总指标 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <SummaryCard label="窗口数" value={String(summary.n_windows)} />
            <SummaryCard
              label="平均 IS Sharpe"
              value={summary.avg_is_sharpe?.toFixed(3) ?? '-'}
            />
            <SummaryCard
              label="平均 OOS Sharpe"
              value={summary.avg_oos_sharpe?.toFixed(3) ?? '-'}
              color={summary.avg_oos_sharpe >= 0.5 ? 'text-green-400' : summary.avg_oos_sharpe >= 0 ? 'text-yellow-400' : 'text-red-400'}
            />
            <SummaryCard
              label="IS-OOS 差距"
              value={summary.is_oos_gap != null ? (summary.is_oos_gap >= 0 ? '+' : '') + summary.is_oos_gap.toFixed(3) : '-'}
              color={summary.is_oos_gap <= 0.5 ? 'text-green-400' : summary.is_oos_gap <= 1.0 ? 'text-yellow-400' : 'text-red-400'}
            />
            <SummaryCard
              label="平均 OOS 收益"
              value={summary.avg_oos_return != null ? pct(summary.avg_oos_return) : '-'}
              color={summary.avg_oos_return >= 0 ? 'text-green-400' : 'text-red-400'}
            />
            <SummaryCard
              label="OOS 正 Sharpe 比例"
              value={summary.positive_oos_pct != null ? (summary.positive_oos_pct * 100).toFixed(0) + '%' : '-'}
              color={summary.positive_oos_pct >= 0.7 ? 'text-green-400' : summary.positive_oos_pct >= 0.5 ? 'text-yellow-400' : 'text-red-400'}
            />
          </div>

          {/* 过拟合判断 */}
          {summary.is_oos_gap != null && (
            <div className={`rounded-lg border px-4 py-3 text-sm font-medium
              ${summary.is_oos_gap <= 0.5 ? 'bg-green-900/20 border-green-700 text-green-300'
                : summary.is_oos_gap <= 1.0 ? 'bg-yellow-900/20 border-yellow-700 text-yellow-300'
                : 'bg-red-900/20 border-red-700 text-red-300'}`}>
              {summary.is_oos_gap <= 0.5
                ? '✓  IS-OOS 差距较小，策略泛化性良好，过拟合风险低'
                : summary.is_oos_gap <= 1.0
                ? '△  IS-OOS 差距中等，参数可能有一定过拟合，建议关注各窗口差异'
                : '⚠  IS-OOS 差距较大，存在明显过拟合风险，慎用当前参数实盘'}
            </div>
          )}

          {/* 逐窗口结果表 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700">
            <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300">
              逐窗口结果（{windows.length} 个测试期）
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-700">
                    {['测试期', 'IS 收益', 'IS Sharpe', 'OOS 收益', 'OOS Sharpe', '最大回撤', '交易数', '胜率'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {windows.map((w: any, i: number) => {
                    const oosNeg = w.oos_sharpe != null && w.oos_sharpe < 0
                    return (
                      <tr key={i} className={`border-b border-slate-700/50 hover:bg-slate-700/30 ${oosNeg ? 'bg-red-900/10' : ''}`}>
                        <td className="px-3 py-2 text-slate-300 whitespace-nowrap">
                          {w.test_start?.slice(0, 7)} ~ {w.test_end?.slice(0, 7)}
                        </td>
                        <td className={`px-3 py-2 font-mono ${w.is_return >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {w.is_return != null ? pct(w.is_return) : '-'}
                        </td>
                        <td className={`px-3 py-2 font-mono ${w.is_sharpe >= 0 ? 'text-slate-300' : 'text-red-400'}`}>
                          {w.is_sharpe?.toFixed(2) ?? '-'}
                        </td>
                        <td className={`px-3 py-2 font-mono font-medium ${w.oos_return >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {w.oos_return != null ? pct(w.oos_return) : '-'}
                        </td>
                        <td className={`px-3 py-2 font-mono font-medium ${oosNeg ? 'text-red-400' : w.oos_sharpe >= 0.5 ? 'text-green-400' : 'text-yellow-400'}`}>
                          {w.oos_sharpe?.toFixed(2) ?? '-'}
                          {oosNeg && ' !'}
                        </td>
                        <td className="px-3 py-2 font-mono text-red-400">
                          {w.oos_max_dd != null ? pct(w.oos_max_dd) : '-'}
                        </td>
                        <td className="px-3 py-2 text-slate-400">{w.oos_trades ?? '-'}</td>
                        <td className={`px-3 py-2 font-mono ${w.oos_win_rate >= 0.5 ? 'text-green-400' : 'text-slate-400'}`}>
                          {w.oos_win_rate != null ? (w.oos_win_rate * 100).toFixed(0) + '%' : '-'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* 提示 */}
          <div className="text-xs text-slate-500 space-y-1 px-1">
            <div>• <span className="text-slate-400">IS</span>（In-Sample）= 训练期回测，仅用于对比参考</div>
            <div>• <span className="text-slate-400">OOS</span>（Out-of-Sample）= 测试期，真正验证策略泛化能力</div>
            <div>• IS-OOS Sharpe 差距 &lt; 0.5 为健康；&gt; 1.0 建议重新审视参数</div>
          </div>
        </div>
      )}

      {!taskId && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-10 text-center text-slate-500 text-sm">
          设置参数后点击「开始验证」，验证策略是否在样本外保持一致
        </div>
      )}
    </div>
  )
}

export default function Backtest() {
  const [params, setParams] = useState(() => loadStored('bt_params', DEFAULT_PARAMS))
  const [activeTask, setActiveTask] = useState<string | null>(null)
  const [tab, setTab] = useState<'config' | 'history' | 'vix' | 'walkforward'>('config')
  const [selectedFactors, setSelectedFactors] = useState<string[]>(() => loadStored('bt_factors', []))

  useEffect(() => { localStorage.setItem('bt_params', JSON.stringify(params)) }, [params])
  useEffect(() => { localStorage.setItem('bt_factors', JSON.stringify(selectedFactors)) }, [selectedFactors])  // 空 = 使用默认 RSMomentum
  const queryClient = useQueryClient()

  // 拉取因子注册表
  const { data: registry = [] } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    staleTime: 300_000,
  })
  // 只显示技术因子（基本面因子无法参与时序回测）
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical' && !f.is_dependency)
  const registryMap: Record<string, any> = Object.fromEntries((registry as any[]).map((f: any) => [f.key, f]))

  const toggleFactor = (key: string) => {
    setSelectedFactors(prev =>
      prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]
    )
  }

  const { mutate, isPending } = useMutation({
    mutationFn: () => runBacktest({
      period: params.useCustomDate ? undefined : params.period,
      start: params.useCustomDate ? params.start : undefined,
      end: params.useCustomDate ? params.end : undefined,
      universe: params.universe,
      top_n: params.top_n,
      min_cap_b: params.min_cap_b,
      max_cap_b: params.max_cap_b,
      deny_industries: params.deny_industries.length ? params.deny_industries : undefined,
      factors: selectedFactors.length > 0 ? selectedFactors : undefined,
    }),
    onSuccess: (data) => {
      setActiveTask(data.task_id)
      setTab('config')
      queryClient.invalidateQueries({ queryKey: ['bt-history'] })
    },
  })

  const { data: history = [], refetch: refetchHistory } = useQuery({
    queryKey: ['bt-history'],
    queryFn: getBacktestHistory,
    enabled: tab === 'history',
  })

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">策略回测</h1>
      </div>

      {/* Tab 导航 */}
      <div className="flex gap-1 border-b border-slate-700">
        {[
          { key: 'config',       label: '回测配置' },
          { key: 'walkforward',  label: 'Walk-Forward 验证' },
          { key: 'history',      label: '历史记录' },
          { key: 'vix',          label: 'VIX恐慌策略' },
        ].map(t => (
          <button key={t.key}
            onClick={() => { setTab(t.key as any); if (t.key === 'history') refetchHistory() }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
              ${tab === t.key ? 'border-blue-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
          >{t.label}</button>
        ))}
      </div>

      {/* ── 回测配置 Tab ─────────────────────────────────────── */}
      {tab === 'config' && <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="text-sm font-medium text-slate-300 mb-4">回测参数</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Top N */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">Top N（最大持仓候选）</label>
            <input
              type="number" min={1} max={50}
              className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              value={params.top_n}
              onChange={e => setParams(p => ({ ...p, top_n: +e.target.value }))}
            />
          </div>

          {/* 最小市值 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">最小市值（$B）</label>
            <input
              type="number" min={0}
              className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              value={params.min_cap_b}
              onChange={e => setParams(p => ({ ...p, min_cap_b: +e.target.value }))}
            />
          </div>

          {/* 最大市值 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">最大市值（$B）</label>
            <input
              type="number" min={0}
              className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              value={params.max_cap_b}
              onChange={e => setParams(p => ({ ...p, max_cap_b: +e.target.value }))}
            />
          </div>
        </div>

        {/* 日期选择 */}
        <div className="mt-4">
          <label className="flex items-center gap-2 text-xs text-slate-400 mb-2 cursor-pointer">
            <input
              type="checkbox" checked={params.useCustomDate}
              onChange={e => setParams(p => ({ ...p, useCustomDate: e.target.checked }))}
              className="accent-blue-500"
            />
            使用自定义日期范围
          </label>
          {params.useCustomDate ? (
            <div className="flex items-end gap-3">
              <DatePicker
                label="开始日期"
                value={params.start}
                onChange={v => setParams(p => ({ ...p, start: v }))}
              />
              <span className="text-slate-400 text-sm pb-1">至</span>
              <DatePicker
                label="结束日期"
                value={params.end}
                onChange={v => setParams(p => ({ ...p, end: v }))}
              />
            </div>
          ) : (
            <div className="flex gap-2">
              {PERIODS.map(p => (
                <button
                  key={p}
                  onClick={() => setParams(prev => ({ ...prev, period: p }))}
                  className={`px-3 py-1 text-sm rounded border transition-colors ${
                    params.period === p
                      ? 'bg-blue-600 border-blue-500 text-white'
                      : 'border-slate-600 text-slate-400 hover:border-slate-400'
                  }`}
                >{p}</button>
              ))}
            </div>
          )}
        </div>

        {/* 因子组合 */}
        <div className="mt-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-slate-400">
              因子组合
              {selectedFactors.length === 0
                ? <span className="ml-1.5 text-slate-500">（未选择 = 默认 RSMomentum）</span>
                : <span className="ml-1.5 text-blue-400">{selectedFactors.length} 个已选</span>
              }
            </span>
            <button
              type="button"
              onClick={() => setSelectedFactors(['rs_score', 'breakout', 'volume_surge', 'volume_divergence', 'trend_filter', 'drawdown_filter'])}
              className="text-xs text-slate-500 hover:text-slate-200 transition-colors"
            >
              ↺ Reset RSMomentum
            </button>
          </div>

          <div className="bg-slate-700/40 rounded-lg p-3 border border-slate-600/50">
            {Object.entries(
              techFactors.reduce((acc: any, f: any) => {
                ;(acc[f.category] = acc[f.category] || []).push(f)
                return acc
              }, {} as Record<string, any[]>)
            ).map(([cat, factors]: any) => (
              <div key={cat} className="mb-3 last:mb-0">
                <div className="text-xs text-slate-500 mb-1.5 font-medium uppercase tracking-wide">
                  {CATEGORY_LABELS[cat] ?? cat}
                </div>
                <div className="flex flex-wrap gap-2">
                  {factors.map((f: any) => {
                    const checked = selectedFactors.includes(f.key)
                    return (
                      <label
                        key={f.key}
                        className={`flex items-center gap-1.5 px-2.5 py-1 rounded border text-xs cursor-pointer transition-colors ${
                          checked
                            ? 'bg-blue-700/50 border-blue-500 text-blue-200'
                            : 'border-slate-600 text-slate-400 hover:border-slate-400 hover:text-slate-200'
                        }`}
                      >
                        <input type="checkbox" checked={checked} onChange={() => toggleFactor(f.key)} className="hidden" />
                        {f.name}
                        <span className="font-mono opacity-50">{f.key}</span>
                        <span className="text-slate-500">
                          {f.signal_type === 'sell_alert' ? '卖出' : f.signal_type === 'filter' ? '✓过滤' : '↑分数'}
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-4 flex gap-3">
          <button
            onClick={() => mutate()}
            disabled={isPending}
            className="px-5 py-2 bg-blue-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            {isPending ? '提交中...' : '▶ 回测'}
          </button>
        </div>
      </div>}

      {/* 回测结果（config tab 下方） */}
      {tab === 'config' && activeTask && <BacktestResult taskId={activeTask} />}

      {/* ── 历史记录 Tab ──────────────────────────────────── */}
      {tab === 'history' && (
        <div className="bg-slate-800 rounded-lg border border-slate-700">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700 bg-slate-800/80">
                  {['时间', '因子组合', '区间', '总收益', 'Sharpe', '均持仓天', '状态', '操作'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(history as any[]).map((h: any) => (
                  <tr key={h.task_id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{h.created_at}</td>
                    <td className="px-3 py-2 max-w-[280px]">
                      {h.factors?.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {h.factors.map((f: string) => (
                            <span key={f} className="px-1.5 py-0.5 rounded text-xs bg-slate-700 border border-slate-600 text-slate-300">
                              {registryMap[f]?.name ?? f}
                              <span className="ml-1 font-mono opacity-50">{f}</span>
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-slate-500 italic">RSMomentum（默认）</span>
                      )}
                      <div className="text-slate-600 text-xs mt-0.5">{h.universe}</div>
                    </td>
                    <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{h.bt_start} ~ {h.bt_end}</td>
                    <td className={`px-3 py-2 font-mono ${(h.total_return ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {h.total_return != null ? pct(h.total_return) : '-'}
                    </td>
                    <td className="px-3 py-2">{h.sharpe?.toFixed(2) ?? '-'}</td>
                    <td className="px-3 py-2 text-slate-400">{h.avg_days != null ? `${h.avg_days}天` : '-'}</td>
                    <td className="px-3 py-2 text-slate-400">{h.status}</td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => { setActiveTask(h.task_id); setTab('config') }}
                        className="text-blue-400 hover:text-blue-300 transition-colors"
                      >查看</button>
                    </td>
                  </tr>
                ))}
                {(history as any[]).length === 0 && (
                  <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-500">暂无历史记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Walk-Forward 验证 Tab ────────────────────────── */}
      {tab === 'walkforward' && <WalkForwardTab />}

      {/* ── VIX 恐慌回测 Tab ──────────────────────────────── */}
      {tab === 'vix' && <VixBacktest />}
    </div>
  )
}
