import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import DatePicker from '../components/DatePicker'
import SymbolLink from '../components/SymbolLink'

// ── 主题感知配色（dark / day / night）─────────────────────────
function useChartTheme() {
  const get = () => document.documentElement.classList.contains('dark')
  const [isDark, setIsDark] = useState<boolean>(get)
  useEffect(() => {
    const obs = new MutationObserver(() => setIsDark(get()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  if (isDark) return {
    text: '#cbd5e1', axis: '#94a3b8', grid: '#1e293b',
    close: '#e2e8f0', emaFast: '#f97316', emaSlow: '#8b5cf6',
    bh: '#22c55e', bhBase: '#84cc16', rs: '#3b82f6', spy: '#94a3b8',
    buy: '#22c55e', sell: '#ef4444',
  }
  // light（day / night 通用）
  return {
    text: '#1f2937', axis: '#475569', grid: '#cbd5e1',
    close: '#0f172a', emaFast: '#c2410c', emaSlow: '#6d28d9',
    bh: '#15803d', bhBase: '#4d7c0f', rs: '#1d4ed8', spy: '#475569',
    buy: '#15803d', sell: '#b91c1c',
  }
}
import {
  runSingleBacktest, getSingleBtStatus, getSingleBtResult,
  getSingleBtHistory,
} from '../api/client'

// ── 工具 ───────────────────────────────────────────────────────

function pct(v: number | null | undefined, digits = 1) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}
function fmtUsd(v: number | null | undefined) {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function defaultStart(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}
function defaultEnd(): string {
  return new Date().toISOString().slice(0, 10)
}

// ── 主页面 ─────────────────────────────────────────────────────

export default function SingleBacktest() {
  const [symbol, setSymbol] = useState('NVDA')
  const [start, setStart]   = useState(defaultStart())
  const [end, setEnd]       = useState(defaultEnd())
  const [initialCash, setInitialCash] = useState(60_000)
  const [basePct, setBasePct]         = useState(0.5)
  const [addSizeMult, setAddSizeMult] = useState(0.5)
  const [maxAdds, setMaxAdds]         = useState(2)
  const [touchTol, setTouchTol]       = useState(0.01)
  const [sellAtrMult, setSellAtrMult] = useState(2.5)
  const [stopEma, setStopEma]         = useState(50)
  const [emaFast, setEmaFast]         = useState(21)
  const [entryMode, setEntryMode]     = useState<'rs_momentum' | 'ema_relaxed'>('rs_momentum')
  const [allowMargin, setAllowMargin] = useState(false)
  const [maxLeverage, setMaxLeverage] = useState(1.5)
  const [marginRate, setMarginRate]   = useState(0.06)

  const [taskId, setTaskId]   = useState<string | null>(null)
  const [status, setStatus]   = useState<string>('idle')
  const [error, setError]     = useState<string | null>(null)
  const [result, setResult]   = useState<any | null>(null)
  const [history, setHistory] = useState<any[]>([])

  // 拉历史
  const loadHistory = () =>
    getSingleBtHistory(30).then(r => setHistory(r.items || [])).catch(() => {})

  useEffect(() => { loadHistory() }, [])

  // 轮询任务状态
  useEffect(() => {
    if (!taskId || status === 'completed' || status === 'failed') return
    const id = setInterval(async () => {
      try {
        const s = await getSingleBtStatus(taskId)
        setStatus(s.status)
        if (s.status === 'completed') {
          const r = await getSingleBtResult(taskId)
          setResult(r)
          loadHistory()
        } else if (s.status === 'failed') {
          setError(s.error || '任务失败')
        }
      } catch (e: any) {
        setError(e?.message || '查询失败')
      }
    }, 800)
    return () => clearInterval(id)
  }, [taskId, status])

  const handleRun = async () => {
    setError(null)
    setResult(null)
    setStatus('pending')
    try {
      const r = await runSingleBacktest({
        symbol: symbol.trim().toUpperCase(),
        start, end,
        initial_cash: initialCash,
        base_pct: basePct,
        add_size_mult: addSizeMult,
        max_adds: maxAdds,
        touch_tol: touchTol,
        sell_atr_mult: sellAtrMult,
        stop_ema_period: stopEma,
        ema_fast: emaFast,
        entry_mode: entryMode,
        allow_margin: allowMargin,
        max_leverage: maxLeverage,
        margin_rate: marginRate,
      })
      setTaskId(r.task_id)
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '提交失败')
      setStatus('failed')
    }
  }

  const loadHistoryItem = async (item: any) => {
    setError(null); setResult(null); setStatus('pending')
    setTaskId(item.task_id)
    try {
      const r = await getSingleBtResult(item.task_id)
      setResult(r); setStatus('completed')
    } catch (e: any) {
      setError(e?.message || '加载失败'); setStatus('failed')
    }
  }

  const running = status === 'pending' || status === 'running'

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <div>
        <h1 className="text-lg font-semibold text-slate-200">单股回测 — EMA21 补仓策略</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          底仓首次 RSMomentum 信号建仓 · 回踩 EMA21 ±{(touchTol*100).toFixed(0)}% 且 EMA21&gt;EMA{stopEma} 补 {(addSizeMult*100).toFixed(0)}% 底仓 ·
          偏离 EMA21 &gt; {sellAtrMult}×ATR 卖加仓批次 · 跌破 EMA{stopEma} 全平
        </p>
        <p className="text-[11px] text-slate-500 mt-1">
          对比口径：<span className="text-emerald-400">B&amp;H 同底仓</span>（用 base_pct × 资金做无脑持有，
          与策略<em>同等暴露</em>，是公平基准）·
          <span className="text-emerald-400">B&amp;H 满仓</span>（100% 持有，仅作上限参考，牛市单股常常无法被战胜）
        </p>
      </div>

      {/* 参数面板 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 space-y-3">
        {/* 入场模式开关（显著） */}
        <div className="flex items-center gap-3 pb-2 border-b border-slate-700">
          <span className="text-xs text-slate-400">入场模式：</span>
          <button onClick={() => setEntryMode('rs_momentum')}
            className={`px-3 py-1 text-xs rounded transition-colors ${
              entryMode === 'rs_momentum' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
            RSMomentum 严苛
          </button>
          <button onClick={() => setEntryMode('ema_relaxed')}
            className={`px-3 py-1 text-xs rounded transition-colors ${
              entryMode === 'ema_relaxed' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
            EMA 宽松 (close&gt;EMA21 且 EMA21&gt;EMA50)
          </button>
          <span className="text-[11px] text-slate-500 ml-2">
            {entryMode === 'rs_momentum'
              ? '5 条件同时满足才建仓，一年通常 1-3 次（信号稀缺）'
              : 'EMA 多头排列即建仓，信号频次高 5-10 倍'}
          </span>
        </div>
        {/* 融资杠杆 — 牛市最猛打法（模拟）*/}
        <div className="flex items-center gap-3 pb-2 border-b border-slate-700">
          <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
            <input type="checkbox" checked={allowMargin}
              onChange={e => setAllowMargin(e.target.checked)}
              className="accent-blue-500" />
            允许融资杠杆
          </label>
          {allowMargin && (
            <>
              <span className="text-xs text-slate-400">最大杠杆</span>
              <input type="number" step={0.1} min={1.0} max={3.0}
                value={maxLeverage}
                onChange={e => setMaxLeverage(Math.max(1.0, Number(e.target.value) || 1.0))}
                className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white font-mono focus:outline-none focus:border-blue-500" />
              <span className="text-xs text-slate-400">×</span>
              <span className="text-xs text-slate-400 ml-2">年化利率</span>
              <input type="number" step={0.005} min={0} max={0.20}
                value={marginRate}
                onChange={e => setMarginRate(Math.max(0, Number(e.target.value) || 0))}
                className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white font-mono focus:outline-none focus:border-blue-500" />
              <span className="text-xs text-slate-400">({(marginRate * 100).toFixed(1)}%)</span>
              <span className="text-[11px] text-amber-400 ml-2">⚠ 模拟杠杆 — 实盘需 IBKR margin 账户，含爆仓风险</span>
            </>
          )}
          {!allowMargin && (
            <span className="text-[11px] text-slate-500">勾选后允许总持仓 &gt; 初始资金（融资买入），常用于牛市单股放大暴露</span>
          )}
        </div>
        {/* 仓位预设 + 实时最大暴露指标 */}
        <PositionPresetsRow
          basePct={basePct} addSizeMult={addSizeMult} maxAdds={maxAdds}
          allowMargin={allowMargin} maxLeverage={maxLeverage}
          onApply={(p) => {
            setBasePct(p.base); setAddSizeMult(p.mult); setMaxAdds(p.adds)
            setAllowMargin(p.margin); if (p.margin) setMaxLeverage(p.lev)
          }}
        />
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <div>
            <label className="text-xs text-slate-400 block mb-1">股票代码</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === 'Enter' && !running) handleRun() }}
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500" />
          </div>
          <DatePicker value={start} onChange={setStart} label="开始日期" />
          <DatePicker value={end}   onChange={setEnd}   label="结束日期" />
          <NumInput label="初始资金" value={initialCash} onChange={setInitialCash} step={1000} />
          <NumInput label="底仓占比" value={basePct} onChange={setBasePct} step={0.05} digits={2} />
          <NumInput label="单次补仓倍数" value={addSizeMult} onChange={setAddSizeMult} step={0.1} digits={2} />
          <NumInput label="最大补仓次数" value={maxAdds} onChange={v => setMaxAdds(Math.round(v))} step={1} digits={0} />
          <NumInput label="EMA21 容差" value={touchTol} onChange={setTouchTol} step={0.005} digits={3} />
          <NumInput label="偏离卖出 ATR×" value={sellAtrMult} onChange={setSellAtrMult} step={0.1} digits={2} />
          <NumInput label="止损 EMA 周期" value={stopEma} onChange={v => setStopEma(Math.round(v))} step={5} digits={0} />
          <NumInput label="EMA 快线周期" value={emaFast} onChange={v => setEmaFast(Math.round(v))} step={1} digits={0} />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={handleRun} disabled={running || !symbol.trim()}
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white transition-colors">
            {running ? '回测中…' : '开始回测'}
          </button>
          {status !== 'idle' && (
            <span className={`text-xs ${
              status === 'completed' ? 'text-emerald-400'
              : status === 'failed' ? 'text-red-400'
              : 'text-amber-400'}`}>
              {status === 'pending' && '排队中'}
              {status === 'running' && '运行中…'}
              {status === 'completed' && '完成'}
              {status === 'failed' && '失败'}
            </span>
          )}
          {error && <span className="text-xs text-red-400">{error}</span>}
        </div>
      </div>

      {/* 结果展示 */}
      {result && <ResultPanel data={result} />}

      {/* 历史 */}
      {history.length > 0 && (
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="text-sm font-medium text-slate-200 mb-2">最近回测</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-slate-700">
                  <th className="text-left px-2 py-1.5 font-medium">时间</th>
                  <th className="text-left px-2 py-1.5 font-medium">标的</th>
                  <th className="text-left px-2 py-1.5 font-medium">区间</th>
                  <th className="text-right px-2 py-1.5 font-medium">EMA21 收益</th>
                  <th className="text-right px-2 py-1.5 font-medium">Sharpe</th>
                  <th className="text-center px-2 py-1.5 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.task_id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-2 py-1.5 text-slate-400 font-mono">{h.created_at}</td>
                    <td className="px-2 py-1.5"><SymbolLink symbol={h.symbol} className="text-white font-mono" /></td>
                    <td className="px-2 py-1.5 text-slate-400">{h.start} ~ {h.end}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${(h.total_return ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {h.total_return != null ? pct(h.total_return) : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-slate-300">
                      {h.sharpe != null ? h.sharpe.toFixed(2) : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <button onClick={() => loadHistoryItem(h)}
                        className="text-blue-400 hover:underline">载入</button>
                    </td>
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

// ── 子组件 ─────────────────────────────────────────────────────

function NumInput({ label, value, onChange, step = 1, digits = 0 }: {
  label: string; value: number; onChange: (v: number) => void; step?: number; digits?: number
}) {
  return (
    <div>
      <label className="text-xs text-slate-400 block mb-1">{label}</label>
      <input type="number" value={digits === 0 ? value : Number(value.toFixed(digits))}
        step={step}
        onChange={e => {
          const v = Number(e.target.value)
          if (!Number.isNaN(v)) onChange(v)
        }}
        className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500" />
    </div>
  )
}

type PresetSpec = {
  key: string; label: string; hint: string
  base: number; mult: number; adds: number
  margin: boolean; lev: number
}

const PRESETS: PresetSpec[] = [
  { key: 'cons',     label: '保守',        hint: '60% 上限，留大量现金',
    base: 0.30, mult: 0.50, adds: 2, margin: false, lev: 1.0 },
  { key: 'pyramid',  label: '金字塔标准',  hint: '50%底仓 + 2次0.5x补仓，刚好满仓',
    base: 0.50, mult: 0.50, adds: 2, margin: false, lev: 1.0 },
  { key: 'even',     label: '均匀加仓',    hint: '50%底仓 + 一次性补满',
    base: 0.50, mult: 1.00, adds: 1, margin: false, lev: 1.0 },
  { key: 'aggr',     label: '牛市激进',    hint: '满仓底仓 + 融资到 2x',
    base: 1.00, mult: 0.50, adds: 2, margin: true,  lev: 2.0 },
  { key: 'ultra',    label: '极激进',      hint: '满仓 + 融资到 3x（高风险）',
    base: 1.00, mult: 1.00, adds: 2, margin: true,  lev: 3.0 },
]

function PositionPresetsRow({ basePct, addSizeMult, maxAdds, allowMargin, maxLeverage, onApply }: {
  basePct: number; addSizeMult: number; maxAdds: number
  allowMargin: boolean; maxLeverage: number
  onApply: (p: PresetSpec) => void
}) {
  const exposure = basePct * (1 + maxAdds * addSizeMult)
  const cap      = allowMargin ? maxLeverage : 1.0
  const exceed   = exposure > cap + 1e-9
  // 当前匹配哪个预设？
  const matched = PRESETS.find(p =>
    Math.abs(p.base - basePct) < 1e-3 &&
    Math.abs(p.mult - addSizeMult) < 1e-3 &&
    p.adds === maxAdds &&
    p.margin === allowMargin &&
    (!p.margin || Math.abs(p.lev - maxLeverage) < 1e-3)
  )?.key
  return (
    <div className="flex items-center gap-2 flex-wrap pb-2 border-b border-slate-700">
      <span className="text-xs text-slate-400 mr-1">仓位预设：</span>
      {PRESETS.map(p => (
        <button key={p.key} onClick={() => onApply(p)}
          title={`base=${(p.base*100).toFixed(0)}% · mult=${p.mult.toFixed(1)}× · adds=${p.adds} · ${p.margin ? `融资 ${p.lev}x` : '无杠杆'}\n${p.hint}`}
          className={`px-2.5 py-1 text-xs rounded transition-colors ${
            matched === p.key ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
          {p.label}
        </button>
      ))}
      <span className="text-slate-700 mx-1">|</span>
      <span className="text-xs text-slate-400">最大总暴露：</span>
      <span className={`text-sm font-mono font-semibold ${
        exceed ? 'text-amber-400' : exposure >= 1.0 ? 'text-emerald-400' : 'text-slate-200'}`}>
        {(exposure * 100).toFixed(0)}%
      </span>
      <span className="text-[11px] text-slate-500">
        = {(basePct*100).toFixed(0)}% × (1 + {maxAdds} × {addSizeMult.toFixed(1)})
      </span>
      {exceed && (
        <span className="text-[11px] text-amber-400 ml-1">
          ⚠ 超出{allowMargin ? `杠杆上限 ${cap.toFixed(1)}x` : '现金上限'}，补仓将静默失效 — {allowMargin ? '调大最大杠杆' : '勾选融资杠杆'}
        </span>
      )}
    </div>
  )
}

function SignalStats({ stats }: { stats: any }) {
  const totalDays  = stats.total_trading_days ?? 0
  const triggers   = stats.rs_signal_triggers ?? 0
  const ema        = stats.ema_pullback || {}
  const rs         = stats.rs_only      || {}
  const flatHigh   = (v: number) => v >= 0.5
  const item = (label: string, value: string, hint?: string, danger = false) => (
    <div className={`rounded p-2 border ${danger ? 'border-amber-700/60 bg-amber-900/10' : 'border-slate-700 bg-slate-800'}`}>
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={`text-sm font-mono font-semibold ${danger ? 'text-amber-300' : 'text-slate-200'}`}>{value}</div>
      {hint && <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>}
    </div>
  )
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
      <div className="text-sm text-slate-300 mb-2">信号统计 <span className="text-xs text-slate-500">— 看清楚策略到底"动了多少次"</span></div>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2">
        {item('回测交易日', `${totalDays} 天`)}
        {item('RSMomentum 信号触发', `${triggers} 次`,
              triggers <= 3 ? '信号稀缺，多数时间空仓' : undefined,
              triggers <= 3)}
        {item('EMA21 — 建仓段数', `${ema.entries ?? 0}`)}
        {item('EMA21 — 平均持仓', `${ema.avg_holding_days ?? 0} 天`)}
        {item('EMA21 — 空仓占比', `${((ema.flat_days_pct ?? 0) * 100).toFixed(0)}%`,
              flatHigh(ema.flat_days_pct ?? 0) ? '大半时间没上车' : undefined,
              flatHigh(ema.flat_days_pct ?? 0))}
        {item('RS 纯 — 建仓段数', `${rs.entries ?? 0}`)}
        {item('RS 纯 — 平均持仓', `${rs.avg_holding_days ?? 0} 天`)}
        {item('RS 纯 — 空仓占比', `${((rs.flat_days_pct ?? 0) * 100).toFixed(0)}%`,
              flatHigh(rs.flat_days_pct ?? 0) ? '大半时间没上车' : undefined,
              flatHigh(rs.flat_days_pct ?? 0))}
      </div>
    </div>
  )
}

function ResultPanel({ data }: { data: any }) {
  const summaries = data.summaries || {}
  const equity    = data.equity_curve || []
  const emaTrades = data.ema_trades   || []
  const rsTrades  = data.rs_trades    || []
  const stats     = data.signal_stats || null

  return (
    <div className="space-y-4">
      {/* 信号统计 — 解释"为什么策略没赚钱" */}
      {stats && <SignalStats stats={stats} />}

      {/* 指标对比卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {(['ema_pullback', 'rs_only', 'buy_hold_base', 'buy_hold', 'spy'] as const).map(key => {
          const s = summaries[key]
          if (!s) return null
          const tone = (s.total_return ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'
          return (
            <div key={key} className="bg-slate-800 rounded-lg border border-slate-700 p-3">
              <div className="text-xs text-slate-400 mb-1">{s.label}</div>
              <div className={`text-xl font-mono font-bold ${tone}`}>{pct(s.total_return)}</div>
              <div className="text-[11px] text-slate-500 mt-1 space-y-0.5 font-mono">
                <div>最终 {fmtUsd(s.final_equity)}</div>
                <div>Sharpe {s.sharpe != null ? s.sharpe.toFixed(2) : '—'} · 最大回撤 {pct(s.max_drawdown)}</div>
                <div>交易 {s.num_trades} 次{s.win_rate != null ? ` · 胜率 ${(s.win_rate*100).toFixed(0)}%` : ''}</div>
                {s.interest_paid != null && s.interest_paid > 0 && (
                  <div className="text-amber-400">融资利息 -{fmtUsd(s.interest_paid)}</div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* 权益曲线 */}
      <EquityChart equity={equity} />

      {/* 价格 + EMA + 买卖点 */}
      <PriceChart equity={equity} emaTrades={emaTrades} />

      {/* 交易明细 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <TradeTable title="EMA21 补仓 — 交易明细" trades={emaTrades} />
        <TradeTable title="RSMomentum 纯策略 — 交易明细" trades={rsTrades} />
      </div>
    </div>
  )
}

function EquityChart({ equity }: { equity: any[] }) {
  const c = useChartTheme()
  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    grid: { left: 60, right: 30, top: 30, bottom: 30 },
    legend: { textStyle: { color: c.text, fontSize: 11 }, top: 4 },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) =>
      v?.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }) },
    xAxis: { type: 'category', data: equity.map(p => p.date), axisLabel: { color: c.axis, fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: c.axis, fontSize: 10 }, splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { name: 'EMA21 补仓', type: 'line', smooth: true, symbol: 'none',
        data: equity.map(p => p.ema_equity), lineStyle: { color: c.emaFast, width: 2 } },
      { name: 'RSMomentum',  type: 'line', smooth: true, symbol: 'none',
        data: equity.map(p => p.rs_equity),  lineStyle: { color: c.rs, width: 2 } },
      { name: 'B&H 满仓',     type: 'line', smooth: true, symbol: 'none',
        data: equity.map(p => p.bh_equity),      lineStyle: { color: c.bh, width: 1.5 } },
      { name: 'B&H 同底仓',   type: 'line', smooth: true, symbol: 'none',
        data: equity.map(p => p.bh_base_equity), lineStyle: { color: c.bhBase, width: 1.5, type: 'dashed' } },
      { name: 'SPY',          type: 'line', smooth: true, symbol: 'none',
        data: equity.map(p => p.spy_equity),     lineStyle: { color: c.spy, width: 1, type: 'dashed' } },
    ],
  }), [equity, c])
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
      <div className="text-sm text-slate-300 mb-1">权益曲线</div>
      <ReactECharts option={option} style={{ height: 320 }} notMerge />
    </div>
  )
}

function PriceChart({ equity, emaTrades }: { equity: any[]; emaTrades: any[] }) {
  const c = useChartTheme()
  const dateIdx: Record<string, number> = {}
  equity.forEach((p, i) => { dateIdx[p.date] = i })
  const buyPts: any[] = []
  const sellPts: any[] = []
  emaTrades.forEach(t => {
    const i = dateIdx[t.date]
    if (i == null) return
    const p = equity[i]
    if (t.action === 'buy') buyPts.push([t.date, p.close, t.kind, t.shares])
    else                    sellPts.push([t.date, p.close, t.kind, t.shares, t.pnl])
  })

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    grid: { left: 60, right: 30, top: 30, bottom: 30 },
    legend: { textStyle: { color: c.text, fontSize: 11 }, top: 4 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: equity.map(p => p.date), axisLabel: { color: c.axis, fontSize: 10 } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: c.axis, fontSize: 10 }, splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { name: '收盘', type: 'line', symbol: 'none', smooth: false,
        data: equity.map(p => p.close), lineStyle: { color: c.close, width: 1.5 } },
      { name: 'EMA21', type: 'line', symbol: 'none', smooth: true,
        data: equity.map(p => p.ema_fast), lineStyle: { color: c.emaFast, width: 1.5 } },
      { name: 'EMA50', type: 'line', symbol: 'none', smooth: true,
        data: equity.map(p => p.ema_slow), lineStyle: { color: c.emaSlow, width: 1.5, type: 'dashed' } },
      { name: '买入', type: 'scatter', symbolSize: 10, itemStyle: { color: c.buy },
        data: buyPts.map(([d, px, kind, sh]) => ({
          value: [d, px],
          symbol: kind === 'base' ? 'triangle' : 'circle',
          tooltip: { formatter: () => `${d}<br/>${kind === 'base' ? '建底仓' : '补仓'} ${sh} 股 @ ${px}` },
        })) },
      { name: '卖出', type: 'scatter', symbolSize: 10, itemStyle: { color: c.sell },
        data: sellPts.map(([d, px, kind, sh, pnl]) => ({
          value: [d, px],
          symbol: 'diamond',
          tooltip: { formatter: () => `${d}<br/>${kind} ${sh} 股 @ ${px}<br/>盈亏 ${pnl?.toFixed(0) ?? '-'}` },
        })) },
    ],
  }), [equity, emaTrades, c])
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
      <div className="text-sm text-slate-300 mb-1">价格 + EMA + 买卖点（EMA21 策略）</div>
      <ReactECharts option={option} style={{ height: 320 }} notMerge />
    </div>
  )
}

function TradeTable({ title, trades }: { title: string; trades: any[] }) {
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
      <div className="text-sm text-slate-300 mb-2">{title}（{trades.length}）</div>
      <div className="max-h-[300px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-800">
            <tr className="text-slate-500 border-b border-slate-700">
              <th className="text-left px-2 py-1 font-medium">日期</th>
              <th className="text-left px-2 py-1 font-medium">动作</th>
              <th className="text-left px-2 py-1 font-medium">类型</th>
              <th className="text-right px-2 py-1 font-medium">价格</th>
              <th className="text-right px-2 py-1 font-medium">股数</th>
              <th className="text-right px-2 py-1 font-medium">盈亏</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const isBuy = t.action === 'buy'
              const pnlClr = t.pnl == null ? 'text-slate-500'
                : t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
              return (
                <tr key={i} className="border-b border-slate-700/40">
                  <td className="px-2 py-1 text-slate-400 font-mono">{t.date}</td>
                  <td className={`px-2 py-1 font-medium ${isBuy ? 'text-emerald-400' : 'text-red-400'}`}>{isBuy ? '买' : '卖'}</td>
                  <td className="px-2 py-1 text-slate-400">{t.kind}</td>
                  <td className="px-2 py-1 text-right font-mono text-slate-300">{t.price?.toFixed(2)}</td>
                  <td className="px-2 py-1 text-right font-mono text-slate-300">{t.shares}</td>
                  <td className={`px-2 py-1 text-right font-mono ${pnlClr}`}>
                    {t.pnl != null ? t.pnl.toFixed(0) : '—'}
                  </td>
                </tr>
              )
            })}
            {trades.length === 0 && (
              <tr><td colSpan={6} className="text-center py-4 text-slate-500">无交易</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
