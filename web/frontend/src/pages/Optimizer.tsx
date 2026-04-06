import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { runOptimizer, getOptimizerStatus, getOptimizerResult, getOptimizerHistory, getFactorRegistry } from '../api/client'

// ── 工具函数 ───────────────────────────────────────────────
function pct(v: number, decimals = 1) {
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(decimals) + '%'
}

// ── 日期选择器（与回测页面一致）──────────────────────────────
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

function CalendarPanel({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const today = new Date()
  const sel = strToDate(value)
  const init = sel ?? today
  const [view, setView] = useState<'day' | 'month' | 'year'>('day')
  const [curYear, setCurYear] = useState(init.getFullYear())
  const [curMonth, setCurMonth] = useState(init.getMonth())

  const yearBase = Math.floor(curYear / 12) * 12

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
  const cell = (active: boolean, isToday: boolean) =>
    `w-8 h-7 flex items-center justify-center rounded text-xs cursor-pointer select-none transition-colors
    ${active ? 'bg-blue-600 text-white' : isToday ? 'border border-blue-500 text-blue-300 hover:bg-slate-600' : 'text-slate-300 hover:bg-slate-600'}`

  if (view === 'year') return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={() => setCurYear(yearBase - 12)}>‹</button>
        <span className="text-sm text-slate-400">{yearBase}–{yearBase + 11}</span>
        <button className={navBtn} onClick={() => setCurYear(yearBase + 12)}>›</button>
      </div>
      <div className="grid grid-cols-3 gap-1 p-3">
        {Array.from({ length: 12 }, (_, i) => yearBase + i).map(y => (
          <button key={y} onClick={() => { setCurYear(y); setView('month') }}
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
          <button key={i} onClick={() => { setCurMonth(i); setView('day') }}
            className={`py-1.5 rounded text-sm transition-colors
              ${i === curMonth && curYear === (sel?.getFullYear() ?? -1) ? 'bg-blue-600 text-white'
              : i === today.getMonth() && curYear === today.getFullYear() ? 'border border-blue-500 text-blue-300 hover:bg-slate-600'
              : 'text-slate-300 hover:bg-slate-600'}`}
          >{name}</button>
        ))}
      </div>
    </div>
  )

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
              <button key={i} onClick={() => onChange(dateToStr(new Date(curYear, curMonth, day)))}
                className={cell(isSelected, isToday)}>{day}</button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

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
      <button type="button" onClick={() => setOpen(s => !s)}
        className="flex items-center gap-2 bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm hover:border-slate-400 focus:outline-none focus:border-blue-500 transition-colors min-w-[140px]">
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

// ── 稳定性 Badge（Walk-Forward 专用）──────────────────────────
function StabilityBadge({ score }: { score: number }) {
  const [cls, label] =
    score >= 0.75 ? ['bg-green-900/50 text-green-300 border-green-800', '高'] :
    score >= 0.5  ? ['bg-yellow-900/50 text-yellow-300 border-yellow-800', '中'] :
                   ['bg-red-900/50 text-red-300 border-red-800', '低']
  return (
    <span className={`px-1.5 py-0.5 rounded border text-xs font-medium ${cls}`}>
      {Math.round(score * 100)}% {label}
    </span>
  )
}

// ── 过拟合 Badge ───────────────────────────────────────────
function OverfitBadge({ score }: { score: number }) {
  const [cls, label] =
    score < 0.3 ? ['bg-green-900/50 text-green-300 border-green-800', '低'] :
    score < 0.8 ? ['bg-yellow-900/50 text-yellow-300 border-yellow-800', '中'] :
                  ['bg-red-900/50 text-red-300 border-red-800', '高']
  return (
    <span className={`px-1.5 py-0.5 rounded border text-xs font-medium ${cls}`}>
      {score.toFixed(2)} {label}
    </span>
  )
}

function FactorPills({ factors, mandatory, registryMap = {} }: { factors: string[]; mandatory: string[]; registryMap?: Record<string, any> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {factors.map(f => (
        <span key={f} className={`px-1.5 py-0.5 rounded text-xs border
          ${mandatory.includes(f)
            ? 'bg-blue-900/50 text-blue-300 border-blue-700'
            : 'bg-slate-700 text-slate-300 border-slate-600'}`}>
          {registryMap[f]?.name ?? f}
          <span className="ml-1 font-mono opacity-50">{f}</span>
        </span>
      ))}
    </div>
  )
}

// ── 结果展示 ───────────────────────────────────────────────
function OptimizerResult({ taskId, mandatory, registryMap, onVerify }: {
  taskId: string
  mandatory: string[]
  registryMap: Record<string, any>
  onVerify: (factors: string[]) => void
}) {
  const { data: status } = useQuery({
    queryKey: ['opt-status', taskId],
    queryFn: () => getOptimizerStatus(taskId),
    refetchInterval: q => ['running', 'pending'].includes(q.state.data?.status) ? 2000 : false,
  })

  const { data: result } = useQuery({
    queryKey: ['opt-result', taskId],
    queryFn: () => getOptimizerResult(taskId),
    enabled: status?.status === 'completed',
  })

  if (status?.status === 'failed') {
    return (
      <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
        {status.error ?? '优化失败'}
      </div>
    )
  }

  if (status?.status !== 'completed' || !result) {
    const p = status?.total > 0 ? (status.current / status.total * 100).toFixed(0) : 0
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 space-y-4">
        <div className="text-sm text-slate-300 font-medium">优化进行中...</div>
        <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
          <div className="h-full bg-blue-500 rounded-full transition-all duration-500" style={{ width: `${p}%` }} />
        </div>
        <div className="text-xs text-slate-400">
          {status?.current ?? 0} / {status?.total ?? '?'} 组合
          {status?.current_combo?.length > 0 && (
            <span className="ml-2 text-slate-500">
              当前：{status.current_combo.map((k: string) => registryMap[k]?.name ?? k).join(' + ')}
            </span>
          )}
        </div>
      </div>
    )
  }

  const rows: any[] = result.results ?? []
  const isWF = result.mode === 'walkforward'

  return (
    <div className="space-y-3">
      {/* 摘要栏 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 px-4 py-3 flex items-center gap-6 flex-wrap text-sm">
        <div>
          <span className={`px-2 py-0.5 rounded text-xs font-medium mr-2 ${isWF ? 'bg-blue-900/60 text-blue-300 border border-blue-700' : 'bg-slate-700 text-slate-300'}`}>
            {isWF ? 'Walk-Forward' : '单次切分'}
          </span>
          <span className="text-slate-400">共测试 </span>
          <span className="text-white font-medium">{result.total_tested}</span>
          <span className="text-slate-400"> / {result.total_combos} 组合</span>
        </div>
        {isWF ? (
          <>
            <div>
              <span className="text-slate-400">窗口数：</span>
              <span className="text-slate-300 font-medium">{result.window_count}</span>
              <span className="text-slate-500 ml-1 text-xs">
                （训练 {result.wf_params?.train_months}m + 测试 {result.wf_params?.test_months}m，步长 {result.wf_params?.step_months}m）
              </span>
            </div>
            {result.windows_overlapping && (
              <div className="bg-yellow-900/40 border border-yellow-700/60 rounded px-3 py-1 text-xs text-yellow-300">
                ⚠ 步长 &lt; 测试窗口，测试期存在重叠——链式收益率已自动修正为非重叠子集
              </div>
            )}
            <div><span className="text-slate-400">整体区间：</span><span className="text-slate-300">{result.train_period?.split(' ~ ')[0]} ~ {result.test_period?.split('~ ')[1]?.trim()}</span></div>
          </>
        ) : (
          <>
            <div><span className="text-slate-400">训练期：</span><span className="text-slate-300">{result.train_period}</span></div>
            <div><span className="text-slate-400">测试期：</span><span className="text-slate-300">{result.test_period}</span></div>
          </>
        )}
        <div><span className="text-slate-400">排名指标：</span><span className="text-slate-300">{result.metric}</span></div>
      </div>

      <div className="text-xs text-slate-500 bg-slate-800/50 border border-slate-700 rounded px-3 py-2">
        {isWF ? (
          <><span className="text-slate-400 font-medium">Walk-Forward：</span>
          每个因子组合在 <strong className="text-white">{result.window_count}</strong> 个滚动窗口上独立训练+测试，
          按<strong className="text-white">均值测试 Sharpe − 0.3×标准差</strong>排名。稳定性 = 测试 Sharpe &gt; 0 的窗口占比。</>
        ) : (
          <><span className="text-slate-400 font-medium">防过拟合：</span>
          按<strong className="text-white">测试期</strong>指标排名（非训练期）。过拟合分数 = 训练 Sharpe - 测试 Sharpe，越低越稳定。</>
        )}
      </div>

      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-400 border-b border-slate-700 bg-slate-800/80">
              {isWF
                ? ['#', '因子组合', '数量', '稳定性', '均过拟合', '均训练Sharpe', '均测试Sharpe', '±std', '年化收益', '均窗口收益', '均超额收益', '均回撤', '均交易数', '有效窗口', '验证'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                  ))
                : ['#', '因子组合', '数量', '过拟合', '训练收益', '训练Sharpe', '测试收益', '测试Sharpe', '测试回撤', '胜率', '交易数', '验证'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                  ))
              }
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              isWF ? (
                <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                  <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                  <td className="px-3 py-2"><FactorPills factors={r.factors} mandatory={mandatory} registryMap={registryMap} /></td>
                  <td className="px-3 py-2 text-slate-400">{r.factor_count}</td>
                  <td className="px-3 py-2"><StabilityBadge score={r.stability} /></td>
                  <td className="px-3 py-2"><OverfitBadge score={r.avg_overfit} /></td>
                  <td className="px-3 py-2 font-mono text-slate-300">{r.avg_train.sharpe.toFixed(2)}</td>
                  <td className="px-3 py-2 font-mono font-medium text-white">{r.avg_test.sharpe.toFixed(2)}</td>
                  <td className="px-3 py-2 font-mono text-slate-500">±{r.avg_test.std_sharpe.toFixed(2)}</td>
                  <td className={`px-3 py-2 font-mono font-medium ${(r.avg_test.chain_annual_return ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}
                      title={`链式原始: ${pct(r.avg_test.total_return ?? 0, 1)}`}>
                    {pct(r.avg_test.chain_annual_return ?? r.avg_test.total_return ?? r.avg_test.return)}
                  </td>
                  <td className={`px-3 py-2 font-mono ${(r.avg_test.avg_window_return ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>
                    {pct(r.avg_test.avg_window_return ?? r.avg_test.return)}
                  </td>
                  <td className={`px-3 py-2 font-mono ${(r.avg_test.excess_return ?? 0) >= 0 ? 'text-green-300' : 'text-red-300'}`}>{pct(r.avg_test.excess_return ?? 0)}</td>
                  <td className="px-3 py-2 font-mono text-red-400">{pct(r.avg_test.max_dd)}</td>
                  <td className="px-3 py-2 text-slate-400">{r.avg_test.trades}</td>
                  <td className="px-3 py-2 text-slate-400">{r.window_count} / {result.window_count}</td>
                  <td className="px-3 py-2">
                    <button onClick={() => onVerify(r.factors)}
                      className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors whitespace-nowrap">
                      回测验证
                    </button>
                  </td>
                </tr>
              ) : (
                <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                  <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                  <td className="px-3 py-2"><FactorPills factors={r.factors} mandatory={mandatory} registryMap={registryMap} /></td>
                  <td className="px-3 py-2 text-slate-400">{r.factor_count}</td>
                  <td className="px-3 py-2"><OverfitBadge score={r.overfit_score} /></td>
                  <td className={`px-3 py-2 font-mono ${r.train.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(r.train.return)}</td>
                  <td className="px-3 py-2 font-mono text-slate-300">{r.train.sharpe.toFixed(2)}</td>
                  <td className={`px-3 py-2 font-mono font-medium ${r.test.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(r.test.return)}</td>
                  <td className="px-3 py-2 font-mono font-medium text-white">{r.test.sharpe.toFixed(2)}</td>
                  <td className="px-3 py-2 font-mono text-red-400">{pct(r.test.max_dd)}</td>
                  <td className="px-3 py-2 font-mono text-slate-300">{pct(r.test.win_rate, 0)}</td>
                  <td className="px-3 py-2 text-slate-400">{r.test.trades}</td>
                  <td className="px-3 py-2">
                    <button onClick={() => onVerify(r.factors)}
                      className="px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors whitespace-nowrap">
                      回测验证
                    </button>
                  </td>
                </tr>
              )
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={14} className="px-4 py-8 text-center text-slate-500">
                  无有效结果（交易笔数不足 / 有效窗口数不足一半）
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────
const PERIODS   = ['1y', '2y', '3y', '5y']
const METRICS   = [
  { value: 'sharpe',        label: 'Sharpe 比率' },
  { value: 'total_return',  label: '总收益率' },
  { value: 'excess_return', label: '超额收益（vs SPY）' },
]

// ── localStorage 持久化 ────────────────────────────────────
const STORAGE_KEY = 'optimizer_state'

type OptimizerParams = {
  universe:          string
  period:            string
  start:             string
  end:               string
  useCustomDate:     boolean
  mandatory_factors: string[]
  min_combo_size:    number
  max_combo_size:    number
  train_ratio:       number
  metric:            string
  top_n_results:     number
  bt_top_n:          number
  wf_mode:           boolean
  wf_train_months:   number
  wf_test_months:    number
  wf_step_months:    number
}

const DEFAULT_PARAMS: OptimizerParams = {
  universe:          'sp500+ndx',
  period:            '2y',
  start:             '',
  end:               '',
  useCustomDate:     false,
  mandatory_factors: ['rs_score'],
  min_combo_size:    2,
  max_combo_size:    5,
  train_ratio:       0.7,
  metric:            'sharpe',
  top_n_results:     20,
  bt_top_n:          6,
  wf_mode:           false,
  wf_train_months:   12,
  wf_test_months:    3,
  wf_step_months:    3,
}

function loadState(): { params: OptimizerParams; activeTask: string | null; tab: 'config' | 'history' } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { params: DEFAULT_PARAMS, activeTask: null, tab: 'config' }
    const saved = JSON.parse(raw)
    return {
      params:     { ...DEFAULT_PARAMS, ...saved.params } as OptimizerParams,
      activeTask: saved.activeTask ?? null,
      tab:        saved.tab ?? 'config',
    }
  } catch {
    return { params: DEFAULT_PARAMS, activeTask: null, tab: 'config' }
  }
}

export default function Optimizer() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const initial = loadState()
  const [tab, setTab] = useState<'config' | 'history'>(initial.tab)
  const [activeTask, setActiveTask] = useState<string | null>(initial.activeTask)
  const [params, setParams] = useState<OptimizerParams>(initial.params)

  // 任何状态变化都同步到 localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ params, activeTask, tab }))
  }, [params, activeTask, tab])

  const { data: registry = [] } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    staleTime: 300_000,
  })
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical' && !f.is_dependency)
  // key → {name} 映射，用于把 key 转为 "中文名 key" 格式
  const registryMap: Record<string, any> = Object.fromEntries((registry as any[]).map((f: any) => [f.key, f]))

  const handleVerify = (factors: string[]) => {
    // 将因子组合 + optimizer 参数注入到 Backtest 页面的 localStorage 键
    const btParams = {
      period:           params.useCustomDate ? '3mo' : params.period,
      start:            params.useCustomDate ? params.start : '',
      end:              params.useCustomDate ? params.end : '',
      universe:         params.universe,
      top_n:            params.bt_top_n,
      min_cap_b:        10,
      max_cap_b:        5000,
      deny_industries:  [] as string[],
      useCustomDate:    params.useCustomDate,
    }
    localStorage.setItem('bt_params',  JSON.stringify(btParams))
    localStorage.setItem('bt_factors', JSON.stringify(factors))
    navigate('/backtest')
  }

  const toggleMandatory = (key: string) => {
    setParams(p => {
      const m = p.mandatory_factors
      return { ...p, mandatory_factors: m.includes(key) ? m.filter(k => k !== key) : [...m, key] }
    })
  }

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => runOptimizer({
      universe:          params.universe,
      period:            params.useCustomDate ? undefined : params.period,
      start:             params.useCustomDate ? params.start : undefined,
      end:               params.useCustomDate ? params.end : undefined,
      mandatory_factors: params.mandatory_factors,
      min_combo_size:    params.min_combo_size,
      max_combo_size:    params.max_combo_size,
      train_ratio:       params.wf_mode ? undefined : params.train_ratio,
      metric:            params.metric,
      top_n_results:     params.top_n_results,
      bt_top_n:          params.bt_top_n,
      mode:              params.wf_mode ? 'walkforward' : 'single',
      wf_train_months:   params.wf_mode ? params.wf_train_months : undefined,
      wf_test_months:    params.wf_mode ? params.wf_test_months  : undefined,
      wf_step_months:    params.wf_mode ? params.wf_step_months  : undefined,
    }),
    onSuccess: (data) => {
      setActiveTask(data.task_id)
      queryClient.invalidateQueries({ queryKey: ['opt-history'] })
      setTab('config')
    },
  })

  // 历史记录
  const { data: history = [], refetch: refetchHistory } = useQuery({
    queryKey: ['opt-history'],
    queryFn: getOptimizerHistory,
    enabled: tab === 'history',
  })

  // 下载结果 JSON
  const downloadResult = async (taskId: string, createdAt: string) => {
    const result = await getOptimizerResult(taskId)
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `optimizer_${createdAt.replace(/[: ]/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  // 预估组合数 + WF 窗口数
  const mandatoryCount = params.mandatory_factors.length
  const optionalCount  = techFactors.length - mandatoryCount
  const estimateCombos = (() => {
    let n = 0
    for (let s = Math.max(0, params.min_combo_size - mandatoryCount);
             s <= params.max_combo_size - mandatoryCount && s <= optionalCount; s++) {
      let c = 1
      for (let i = 0; i < s; i++) c = c * (optionalCount - i) / (i + 1)
      n += Math.round(c)
    }
    return n
  })()
  const periodMonths = params.useCustomDate
    ? 24  // 自定义区间无法精确计算，用 24 月作估算
    : ({ '1y': 12, '2y': 24, '3y': 36, '5y': 60 } as Record<string, number>)[params.period] ?? 24
  const estimateWFWindows = params.wf_mode
    ? Math.max(0, Math.floor(
        (periodMonths - params.wf_train_months - params.wf_test_months) / params.wf_step_months
      ) + 1)
    : 0

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">因子优化器</h1>
      </div>

      {/* Tab 导航 */}
      <div className="flex gap-1 border-b border-slate-700">
        {[
          { key: 'config',  label: '优化配置' },
          { key: 'history', label: '历史记录' },
        ].map(t => (
          <button key={t.key}
            onClick={() => {
              setTab(t.key as any)
              if (t.key === 'history') refetchHistory()
            }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
              ${tab === t.key
                ? 'border-blue-500 text-white'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── 优化配置 Tab ──────────────────────────────────── */}
      {tab === 'config' && (
        <>
          <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-4 py-3 text-xs text-slate-400 space-y-1">
            <div>自动枚举技术因子的组合，每种组合分别在<strong className="text-white">训练期</strong>和<strong className="text-white">测试期</strong>各跑一次回测，按测试期指标排名。</div>
            <div className="text-slate-500">防过拟合：按测试期（非训练期）排名 · 过拟合分数越低越可靠 · 等分时优先因子数少的组合 · 测试期交易 &lt; 5 笔自动过滤</div>
          </div>

          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 space-y-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-slate-300">优化参数</div>
              {/* 模式切换 */}
              <div className="flex items-center gap-1 bg-slate-700 rounded-lg p-0.5 text-xs">
                {[
                  { key: false, label: '单次切分' },
                  { key: true,  label: 'Walk-Forward' },
                ].map(m => (
                  <button key={String(m.key)}
                    onClick={() => setParams(p => ({ ...p, wf_mode: m.key }))}
                    className={`px-3 py-1 rounded-md transition-colors ${
                      params.wf_mode === m.key
                        ? 'bg-blue-600 text-white font-medium'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}>
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {/* 训练比例（单次模式） / WF 窗口参数 */}
              {!params.wf_mode ? (
                <div>
                  <label className="block text-xs text-slate-400 mb-1">
                    训练/测试比例：{Math.round(params.train_ratio * 100)}% / {Math.round((1 - params.train_ratio) * 100)}%
                  </label>
                  <input type="range" min={50} max={85} step={5}
                    value={params.train_ratio * 100}
                    onChange={e => setParams(p => ({ ...p, train_ratio: +e.target.value / 100 }))}
                    className="w-full accent-blue-500" />
                </div>
              ) : (
                <>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">训练窗口（月）</label>
                    <input type="number" min={6} max={36}
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                      value={params.wf_train_months}
                      onChange={e => setParams(p => ({ ...p, wf_train_months: +e.target.value }))} />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">测试窗口（月）</label>
                    <input type="number" min={1} max={12}
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                      value={params.wf_test_months}
                      onChange={e => setParams(p => ({ ...p, wf_test_months: +e.target.value }))} />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">步长（月）</label>
                    <input type="number" min={1} max={12}
                      className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                      value={params.wf_step_months}
                      onChange={e => setParams(p => ({ ...p, wf_step_months: +e.target.value }))} />
                  </div>
                </>
              )}

              {/* 排名指标 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">排名指标</label>
                <select className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={params.metric} onChange={e => setParams(p => ({ ...p, metric: e.target.value }))}>
                  {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>

              {/* 最小因子数 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">最小组合因子数</label>
                <input type="number" min={1} max={params.max_combo_size}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={params.min_combo_size}
                  onChange={e => setParams(p => ({ ...p, min_combo_size: +e.target.value }))} />
              </div>

              {/* 最大因子数 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">最大组合因子数</label>
                <input type="number" min={params.min_combo_size} max={10}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={params.max_combo_size}
                  onChange={e => setParams(p => ({ ...p, max_combo_size: +e.target.value }))} />
              </div>

              {/* Top N 结果 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">显示 Top N 结果</label>
                <input type="number" min={5} max={50}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={params.top_n_results}
                  onChange={e => setParams(p => ({ ...p, top_n_results: +e.target.value }))} />
              </div>

              {/* 回测持仓候选 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">回测持仓候选数（Top N）</label>
                <input type="number" min={3} max={20}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
                  value={params.bt_top_n}
                  onChange={e => setParams(p => ({ ...p, bt_top_n: +e.target.value }))} />
              </div>
            </div>

            {/* 回测区间 */}
            <div>
              <label className="flex items-center gap-2 text-xs text-slate-400 mb-2 cursor-pointer">
                <input type="checkbox" checked={params.useCustomDate}
                  onChange={e => setParams(p => ({ ...p, useCustomDate: e.target.checked }))}
                  className="accent-blue-500" />
                使用自定义日期范围
              </label>
              {params.useCustomDate ? (
                <div className="flex items-end gap-3">
                  <DatePicker label="开始日期" value={params.start}
                    onChange={v => setParams(p => ({ ...p, start: v }))} />
                  <span className="text-slate-400 text-sm pb-1">至</span>
                  <DatePicker label="结束日期" value={params.end}
                    onChange={v => setParams(p => ({ ...p, end: v }))} />
                </div>
              ) : (
                <div className="flex gap-2">
                  {PERIODS.map(p => (
                    <button key={p} onClick={() => setParams(prev => ({ ...prev, period: p }))}
                      className={`px-3 py-1 text-sm rounded border transition-colors
                        ${params.period === p ? 'bg-blue-600 border-blue-500 text-white' : 'border-slate-600 text-slate-400 hover:border-slate-400'}`}>
                      {p}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 必选因子 */}
            <div>
              <div className="text-xs text-slate-400 mb-2">
                必选因子（蓝色 = 锁定在每个组合中，灰色 = 加入可选池）
              </div>
              <div className="flex flex-wrap gap-2">
                {techFactors.map((f: any) => {
                  const isMandatory = params.mandatory_factors.includes(f.key)
                  return (
                    <button key={f.key} onClick={() => toggleMandatory(f.key)}
                      className={`px-2.5 py-1 rounded border text-xs transition-colors
                        ${isMandatory
                          ? 'bg-blue-700/50 border-blue-500 text-blue-200'
                          : 'border-slate-600 text-slate-400 hover:border-slate-400'}`}>
                      {f.name}
                      <span className="ml-1 font-mono opacity-50">{f.key}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* 预估 + 启动 */}
            <div className="flex items-center gap-4 flex-wrap">
              <button onClick={() => submit()} disabled={isPending}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors">
                {isPending ? '提交中...' : '▶ 开始优化'}
              </button>
              <div className="text-xs text-slate-400">
                {params.wf_mode ? (
                  <>
                    预估 <span className="text-white font-medium">{estimateCombos}</span> 个组合
                    × <span className="text-white font-medium">{estimateWFWindows}</span> 窗口
                    × 2（训练+测试）≈
                    <span className="text-white font-medium"> {Math.max(1, Math.round(estimateCombos * estimateWFWindows * 2 * 0.3 / 60))} 分钟</span>
                    （因子预计算加速）
                  </>
                ) : (
                  <>
                    预估 <span className="text-white font-medium">{estimateCombos}</span> 个组合
                    × 2（训练+测试）≈
                    <span className="text-white font-medium"> {Math.max(1, Math.round(estimateCombos * 2 * 0.3 / 60))} 分钟</span>
                    （因子预计算加速）
                  </>
                )}
              </div>
            </div>
          </div>

          {/* 当前任务结果 */}
          {activeTask && (
            <OptimizerResult taskId={activeTask} mandatory={params.mandatory_factors} registryMap={registryMap} onVerify={handleVerify} />
          )}
        </>
      )}

      {/* ── 历史记录 Tab ──────────────────────────────────── */}
      {tab === 'history' && (
        <div className="bg-slate-800 rounded-lg border border-slate-700">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700 bg-slate-800/80">
                  {['时间', '模式', '回测区间', '最佳组合', '最佳得分', '指标', '组合数', '操作'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(history as any[]).map((h: any) => (
                  <tr key={h.task_id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-3 py-2 text-slate-400 whitespace-nowrap">{h.created_at}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {h.mode === 'walkforward'
                        ? <span className="px-1.5 py-0.5 rounded text-xs bg-blue-900/50 text-blue-300 border border-blue-700">WF {h.window_count}窗口</span>
                        : <span className="text-slate-500 text-xs">单次</span>
                      }
                    </td>
                    <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
                      {h.train_period && h.test_period
                        ? `${h.train_period.split(' ~ ')[0]} ~ ${(h.test_period.split('~ ')[1] ?? h.test_period.split(' ~ ')[1] ?? '').trim()}`
                        : '-'}
                    </td>
                    <td className="px-3 py-2">
                      {h.best_factors?.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {h.best_factors.map((f: string) => (
                            <span key={f} className="px-1.5 py-0.5 rounded text-xs bg-slate-700 border border-slate-600 text-slate-300">
                              {registryMap[f]?.name ?? f}
                              <span className="ml-1 font-mono opacity-50">{f}</span>
                            </span>
                          ))}
                        </div>
                      ) : '-'}
                    </td>
                    <td className="px-3 py-2 font-mono font-medium text-white">
                      {h.best_score != null ? h.best_score.toFixed(3) : '-'}
                    </td>
                    <td className="px-3 py-2 text-slate-400">{h.metric || '-'}</td>
                    <td className="px-3 py-2 text-slate-400">
                      {h.total_tested > 0 ? `${h.total_tested} / ${h.total_combos}` : '-'}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <div className="flex gap-3">
                        <button onClick={() => { setActiveTask(h.task_id); setTab('config') }}
                          className="text-blue-400 hover:text-blue-300 transition-colors">查看</button>
                        <button onClick={() => downloadResult(h.task_id, h.created_at)}
                          className="text-slate-400 hover:text-white transition-colors">下载</button>
                      </div>
                    </td>
                  </tr>
                ))}
                {(history as any[]).length === 0 && (
                  <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">暂无历史记录</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
