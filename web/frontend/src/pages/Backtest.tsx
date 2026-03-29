import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { runBacktest, getBacktestStatus, getBacktestResult, getBacktestHistory, getFactorRegistry } from '../api/client'
import ReactECharts from 'echarts-for-react'

const UNIVERSES = ['sp500', 'nasdaq100', 'russell2000']
const PERIODS = ['1mo', '3mo', '6mo', '1y']

// ── 年/月/日 三段式日期选择器 ──────────────────────────────
// value / onChange 均使用 "yyyy-mm-dd" 字符串（与后端兼容）
function DateSelect({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const parsed = value ? value.split('-') : ['', '', '']
  const [y, m, d] = parsed

  const currentYear = new Date().getFullYear()
  const years = Array.from({ length: 15 }, (_, i) => String(currentYear - 12 + i))
  const months = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'))
  const daysInMonth = y && m ? new Date(+y, +m, 0).getDate() : 31
  const days = Array.from({ length: daysInMonth }, (_, i) => String(i + 1).padStart(2, '0'))

  const set = (ny: string, nm: string, nd: string) => {
    if (ny && nm && nd) onChange(`${ny}-${nm}-${nd}`)
    else onChange('')
  }

  const sel = 'bg-slate-700 border border-slate-600 rounded px-1.5 py-1 text-sm text-white focus:outline-none focus:border-blue-500 cursor-pointer'

  return (
    <div className="flex flex-col gap-1">
      {label && <span className="text-xs text-slate-400">{label}</span>}
      <div className="flex items-center gap-1">
        <select className={sel} value={y} onChange={e => set(e.target.value, m, d)}>
          <option value="">年</option>
          {years.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <span className="text-slate-500 text-xs">/</span>
        <select className={sel} value={m} onChange={e => set(y, e.target.value, d)}>
          <option value="">月</option>
          {months.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
        <span className="text-slate-500 text-xs">/</span>
        <select className={sel} value={d} onChange={e => set(y, m, e.target.value)}>
          <option value="">日</option>
          {days.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </div>
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
          <div className="h-full bg-blue-500 rounded-full animate-pulse w-3/4" />
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
                {['股票', '买入日', '卖出日', '买入价', '卖出价', '收益率', '盈亏', '原因'].map(h => (
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

export default function Backtest() {
  const [params, setParams] = useState({
    period: '3mo', start: '', end: '',
    universe: 'sp500', top_n: 6,
    min_cap_b: 10, max_cap_b: 5000,
    deny_industries: [] as string[],
    useCustomDate: false,
  })
  const [activeTask, setActiveTask] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [showFactors, setShowFactors] = useState(false)
  const [selectedFactors, setSelectedFactors] = useState<string[]>([])  // 空 = 使用默认 RSMomentum
  const queryClient = useQueryClient()

  // 拉取因子注册表
  const { data: registry = [] } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    enabled: showFactors,
    staleTime: 300_000,
  })
  // 只显示技术因子（基本面因子无法参与时序回测）
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical')

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
      queryClient.invalidateQueries({ queryKey: ['bt-history'] })
    },
  })

  const { data: history = [] } = useQuery({
    queryKey: ['bt-history'],
    queryFn: getBacktestHistory,
    enabled: showHistory,
  })

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold text-white">策略回测</h1>

      {/* 参数表单 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="text-sm font-medium text-slate-300 mb-4">回测参数</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* 股票池 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">股票池</label>
            <select
              className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              value={params.universe}
              onChange={e => setParams(p => ({ ...p, universe: e.target.value }))}
            >
              {UNIVERSES.map(u => <option key={u}>{u}</option>)}
            </select>
          </div>

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
              <DateSelect
                label="开始日期"
                value={params.start}
                onChange={v => setParams(p => ({ ...p, start: v }))}
              />
              <span className="text-slate-400 text-sm pb-1">至</span>
              <DateSelect
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

        {/* 因子选择（可折叠） */}
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setShowFactors(s => !s)}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white transition-colors"
          >
            <span className={`transition-transform ${showFactors ? 'rotate-90' : ''}`}>▶</span>
            自定义因子组合
            {selectedFactors.length > 0
              ? <span className="ml-1 px-1.5 py-0.5 bg-blue-700 text-blue-200 rounded text-xs">{selectedFactors.length} 个因子已选</span>
              : <span className="ml-1 text-slate-500">（默认：RSMomentum 全部因子）</span>
            }
          </button>

          {showFactors && (
            <div className="mt-3 bg-slate-700/40 rounded-lg p-3 border border-slate-600/50">
              <div className="text-xs text-slate-400 mb-3">
                勾选后使用自定义因子组合回测（买入 = 所有过滤因子通过 + 得分因子 &gt; 0）。
                <span className="text-slate-500 ml-1">不勾选任何 = 默认 RSMomentum 策略。</span>
              </div>

              {/* 按分类展示 */}
              {Object.entries(
                techFactors.reduce((acc: any, f: any) => {
                  ;(acc[f.category] = acc[f.category] || []).push(f)
                  return acc
                }, {} as Record<string, any[]>)
              ).map(([cat, factors]: any) => (
                <div key={cat} className="mb-3">
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
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleFactor(f.key)}
                            className="hidden"
                          />
                          {f.name}
                          <span className="text-slate-500 text-xs">
                            {f.signal_type === 'filter' ? '✓过滤' : '↑分数'}
                          </span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              ))}

              {selectedFactors.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedFactors([])}
                  className="mt-1 text-xs text-slate-500 hover:text-slate-300"
                >
                  清除选择（恢复默认）
                </button>
              )}
            </div>
          )}
        </div>

        <div className="mt-4 flex gap-3">
          <button
            onClick={() => mutate()}
            disabled={isPending}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            {isPending ? '提交中...' : '▶ 运行回测'}
          </button>
          <button
            onClick={() => setShowHistory(s => !s)}
            className="px-4 py-2 border border-slate-600 text-slate-400 hover:text-white hover:border-slate-400 text-sm rounded transition-colors"
          >
            历史记录
          </button>
        </div>
      </div>

      {/* 历史记录 */}
      {showHistory && (
        <div className="bg-slate-800 rounded-lg border border-slate-700">
          <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300">历史回测</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  {['时间', '股票池', '区间', '总收益', 'Sharpe', '状态', '操作'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((h: any) => (
                  <tr key={h.task_id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-3 py-2 text-slate-400">{h.created_at}</td>
                    <td className="px-3 py-2">{h.universe}</td>
                    <td className="px-3 py-2 text-slate-400">{h.bt_start} ~ {h.bt_end}</td>
                    <td className={`px-3 py-2 font-mono ${(h.total_return ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {h.total_return != null ? pct(h.total_return) : '-'}
                    </td>
                    <td className="px-3 py-2">{h.sharpe?.toFixed(2) ?? '-'}</td>
                    <td className="px-3 py-2">{h.status}</td>
                    <td className="px-3 py-2">
                      <button
                        onClick={() => setActiveTask(h.task_id)}
                        className="text-blue-400 hover:text-blue-300"
                      >查看</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 回测结果 */}
      {activeTask && <BacktestResult taskId={activeTask} />}
    </div>
  )
}
