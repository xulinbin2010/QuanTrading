import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import DatePicker from '../components/DatePicker'
import SymbolLink from '../components/SymbolLink'
import { submitAStockBacktest, getAStockBacktestTask } from '../api/client'

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !isFinite(v)) return '-'
  return `${(v * 100).toFixed(digits)}%`
}
function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !isFinite(v)) return '-'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export default function AStockMomentumBacktest() {
  // 默认起止日期:最近 1 年
  const oneYearAgo = useMemo(() => {
    const d = new Date(); d.setFullYear(d.getFullYear() - 1)
    return d.toISOString().slice(0, 10)
  }, [])
  const today = useMemo(() => new Date().toISOString().slice(0, 10), [])

  const [start, setStart] = useState(oneYearAgo)
  const [end, setEnd] = useState(today)
  const [initialCash, setInitialCash] = useState(100_000)
  const [topN, setTopN] = useState(4)
  const [strategy, setStrategy] = useState<'momentum' | 'momentum_filtered' | 'momentum_trend' | 'sector_rotation' | 'quality_momentum'>('momentum_filtered')
  const [rebalanceFreq, setRebalanceFreq] = useState<'daily' | 'weekly' | 'biweekly' | 'monthly'>('weekly')
  const [applyCosts, setApplyCosts] = useState(false)
  const [stopLoss, setStopLoss] = useState<'none' | 'ema21' | 'fixed_pct'>('none')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'running' | 'completed' | 'failed'>('idle')
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  // 轮询:任务运行中每 2s 查一次
  useEffect(() => {
    if (!taskId || status !== 'running') return
    let cancelled = false
    const poll = async () => {
      try {
        const t = await getAStockBacktestTask(taskId)
        if (cancelled) return
        if (t.status === 'completed') {
          setResult(t.result); setStatus('completed')
        } else if (t.status === 'failed') {
          setError(t.error || '未知错误'); setStatus('failed')
        } else {
          pollRef.current = window.setTimeout(poll, 2000)
        }
      } catch (e: any) {
        if (!cancelled) { setError(e?.message || '查询失败'); setStatus('failed') }
      }
    }
    poll()
    return () => {
      cancelled = true
      if (pollRef.current) window.clearTimeout(pollRef.current)
    }
  }, [taskId, status])

  const runBacktest = async () => {
    setStatus('running'); setResult(null); setError(null)
    try {
      const r = await submitAStockBacktest({
        start_date: start, end_date: end,
        initial_cash: initialCash, top_n: topN,
        strategy,
        rebalance_freq: rebalanceFreq,
        apply_costs: applyCosts,
        stop_loss: stopLoss,
      })
      setTaskId(r.task_id)
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '提交失败')
      setStatus('failed')
    }
  }

  // ECharts:净值曲线(组合 vs HS300)
  const chartOption = useMemo(() => {
    if (!result?.equity_curve?.length) return null
    const dates = result.equity_curve.map((p: any) => p.date)
    const port = result.equity_curve.map((p: any) => p.portfolio)
    const bench = result.equity_curve.map((p: any) => p.benchmark)
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { textStyle: { color: '#cbd5e1' }, top: 0 },
      grid: { left: 60, right: 24, top: 36, bottom: 30 },
      xAxis: {
        type: 'category', data: dates, boundaryGap: false,
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        axisLine: { lineStyle: { color: '#475569' } },
      },
      yAxis: {
        type: 'value', scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => (v / 10000).toFixed(0) + '万' },
        axisLine: { lineStyle: { color: '#475569' } },
        splitLine: { lineStyle: { color: '#1e293b' } },
      },
      series: [
        { name: '组合净值', type: 'line', data: port, smooth: true, symbol: 'none',
          lineStyle: { color: '#22c55e', width: 2 } },
        { name: '沪深300', type: 'line', data: bench, smooth: true, symbol: 'none',
          lineStyle: { color: '#94a3b8', width: 1.5 } },
      ],
    }
  }, [result])

  const isRunning = status === 'running'

  return (
    <div className="space-y-4">
      {/* 表单 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="text-sm font-medium text-slate-300 mb-3">A 股动能轮动回测 · 每周一 rebalance · 188 只 AI 产业链</div>
        <div className="flex flex-wrap gap-4 items-end">
          <DatePicker value={start} onChange={setStart} label="开始日期" />
          <DatePicker value={end} onChange={setEnd} label="结束日期" />
          <div>
            <div className="text-xs text-slate-400 mb-1">初始资金 (元)</div>
            <input type="number" value={initialCash} onChange={e => setInitialCash(Number(e.target.value))}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-32" />
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">持仓数 Top N</div>
            <input type="number" value={topN} onChange={e => setTopN(Number(e.target.value))} min={1} max={20}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-20" />
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">策略</div>
            <select value={strategy} onChange={e => setStrategy(e.target.value as any)}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-56">
              <option value="momentum">纯动能(基线,顶部易翻车)</option>
              <option value="momentum_filtered">动能 + EMA21 过滤 ⭐</option>
              <option value="momentum_trend">动能 + 趋势质量过滤(新高新鲜度)</option>
              <option value="sector_rotation">板块轮动(强势板块取龙头)</option>
              <option value="quality_momentum">质量动能(PE 加权)</option>
            </select>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">Rebalance 频率</div>
            <select value={rebalanceFreq} onChange={e => setRebalanceFreq(e.target.value as any)}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-32">
              <option value="daily">每个交易日</option>
              <option value="weekly">每周一 ⭐</option>
              <option value="biweekly">每双周</option>
              <option value="monthly">每月初</option>
            </select>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1">止损规则</div>
            <select value={stopLoss} onChange={e => setStopLoss(e.target.value as any)}
              className="bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white w-44"
              title="每日检查;触发后当日开盘价卖,优先于 rebalance">
              <option value="none">无(仅 rebalance 卖)</option>
              <option value="ema21">EMA21 破位即卖 ⭐</option>
              <option value="fixed_pct">固定 -15% 止损</option>
            </select>
          </div>
          <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer select-none pb-2"
                 title="扣印花税(卖 0.05%)+ 佣金(双边 0.025%)+ 滑点(0.1%)→ 实战估算">
            <input type="checkbox" checked={applyCosts} onChange={e => setApplyCosts(e.target.checked)}
              className="rounded border-slate-600" />
            扣实盘成本
          </label>
          <button onClick={runBacktest} disabled={isRunning}
            className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded transition-colors">
            {isRunning ? '回测中...' : '跑回测'}
          </button>
        </div>
        <div className="text-xs text-slate-500 mt-3">
          评分 composite = z_mom5×0.35 + z_mom3×0.20 + z_rs_group×0.20 + z_vol×0.15 + flow×0.10(加速 +0.5)。
          策略区别:纯动能=取前 N;EMA21 过滤=前 N 且 close ≥ EMA21;板块轮动=先取前 2 板块再取龙头;质量动能=composite × (1+0.5×归一化 EP)。
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-3 text-sm text-red-300">
          回测失败:{error}
        </div>
      )}

      {/* 运行中提示 */}
      {isRunning && !result && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 text-sm text-slate-400 text-center">
          回测进行中... 1 年期约 30-60 秒(取决于数据下载量)
        </div>
      )}

      {/* 结果 */}
      {result && (
        <>
          {/* 关键指标卡片 */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
            <Metric label="总收益" value={fmtPct(result.total_return)} highlight={result.total_return >= 0 ? 'green' : 'red'} />
            <Metric label="年化" value={fmtPct(result.annualized_return)} highlight={result.annualized_return >= 0 ? 'green' : 'red'} />
            <Metric label="HS300 同期" value={fmtPct(result.benchmark_return)} highlight={'neutral'} />
            <Metric label="超额收益" value={fmtPct(result.excess_return)} highlight={result.excess_return >= 0 ? 'green' : 'red'} />
            <Metric label="Sharpe" value={result.sharpe?.toFixed(2)} highlight={result.sharpe >= 1 ? 'green' : 'neutral'} />
            <Metric label="最大回撤" value={fmtPct(result.max_drawdown)} highlight={'red'} />
            <Metric label="已实现盈亏" value={`¥${fmtNum(result.realized_pnl, 0)}`} highlight={result.realized_pnl >= 0 ? 'green' : 'red'} />
            <Metric label="胜率" value={`${fmtPct(result.win_rate, 1)} (${result.n_wins}赢/${result.n_losses}亏)`} highlight={result.win_rate >= 0.5 ? 'green' : 'neutral'} />
          </div>

          {/* 净值曲线 */}
          {chartOption && (
            <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
              <div className="text-sm font-medium text-slate-300 mb-2">净值曲线</div>
              <ReactECharts option={chartOption} style={{ height: 320 }} notMerge />
            </div>
          )}

          {/* 交易明细 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="text-sm font-medium text-slate-300 mb-3">
              交易明细 · 共 {result.trades.length} 笔 · {result.n_rebalances} 次 rebalance
            </div>
            <div className="overflow-x-auto max-h-[480px]">
              <table className="w-full text-xs">
                <thead className="text-slate-400 border-b border-slate-700 sticky top-0 bg-slate-800">
                  <tr>
                    <th className="text-left py-2 pr-2">日期</th>
                    <th className="text-left py-2 px-2">动作</th>
                    <th className="text-left py-2 px-2">代码</th>
                    <th className="text-left py-2 px-2">名称</th>
                    <th className="text-right py-2 px-2">股数</th>
                    <th className="text-right py-2 px-2">价格</th>
                    <th className="text-right py-2 px-2">金额</th>
                    <th className="text-right py-2 px-2">开仓价</th>
                    <th className="text-right py-2 px-2">盈亏</th>
                    <th className="text-right py-2 px-2">盈亏%</th>
                    <th className="text-right py-2 pl-2">持仓天</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t: any, i: number) => {
                    const isSell = t.action === 'SELL'
                    const win = isSell && t.profit > 0
                    const loss = isSell && t.profit < 0
                    return (
                      <tr key={i} className="border-b border-slate-700/40 hover:bg-slate-700/30">
                        <td className="py-1.5 pr-2 font-mono text-slate-400">{t.date}</td>
                        <td className="py-1.5 px-2">
                          <span className={isSell ? 'text-red-400' : 'text-emerald-400'}>
                            {isSell ? '卖出' : '买入'}
                          </span>
                        </td>
                        <td className="py-1.5 px-2 font-mono"><SymbolLink symbol={t.symbol} /></td>
                        <td className="py-1.5 px-2 text-slate-300 truncate max-w-[100px]" title={t.name}>{t.name}</td>
                        <td className="py-1.5 px-2 text-right font-mono">{t.qty}</td>
                        <td className="py-1.5 px-2 text-right font-mono">¥{t.price.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right font-mono">¥{fmtNum(t.amount, 0)}</td>
                        <td className="py-1.5 px-2 text-right font-mono text-slate-500">
                          {isSell && t.entry_price != null ? `¥${t.entry_price.toFixed(2)}` : '-'}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono ${win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-slate-600'}`}>
                          {isSell ? `${t.profit >= 0 ? '+' : ''}¥${fmtNum(t.profit, 0)}` : '-'}
                        </td>
                        <td className={`py-1.5 px-2 text-right font-mono ${win ? 'text-emerald-400' : loss ? 'text-red-400' : 'text-slate-600'}`}>
                          {isSell ? `${t.profit_pct >= 0 ? '+' : ''}${(t.profit_pct * 100).toFixed(2)}%` : '-'}
                        </td>
                        <td className="py-1.5 pl-2 text-right font-mono text-slate-500">
                          {isSell ? t.hold_days : '-'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Metric({ label, value, highlight }: { label: string; value: any; highlight?: 'green' | 'red' | 'neutral' }) {
  const color = highlight === 'green' ? 'text-emerald-400'
    : highlight === 'red' ? 'text-red-400'
    : 'text-slate-200'
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-base font-mono mt-0.5 ${color}`}>{value}</div>
    </div>
  )
}
