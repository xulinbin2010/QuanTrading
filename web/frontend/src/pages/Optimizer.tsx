import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { runOptimizer, getOptimizerStatus, getOptimizerResult, getFactorRegistry } from '../api/client'

// ── 工具函数 ───────────────────────────────────────────────
function pct(v: number, decimals = 1) {
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(decimals) + '%'
}

function OverfitBadge({ score }: { score: number }) {
  const [cls, label] =
    score < 0.3  ? ['bg-green-900/50 text-green-300 border-green-800', '低'] :
    score < 0.8  ? ['bg-yellow-900/50 text-yellow-300 border-yellow-800', '中'] :
                   ['bg-red-900/50 text-red-300 border-red-800', '高']
  return (
    <span className={`px-1.5 py-0.5 rounded border text-xs font-medium ${cls}`}>
      {score.toFixed(2)} {label}
    </span>
  )
}

function FactorPills({ factors, mandatory }: { factors: string[]; mandatory: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {factors.map(f => (
        <span key={f} className={`px-1.5 py-0.5 rounded text-xs font-mono border
          ${mandatory.includes(f)
            ? 'bg-blue-900/50 text-blue-300 border-blue-700'
            : 'bg-slate-700 text-slate-300 border-slate-600'}`}>
          {f}
        </span>
      ))}
    </div>
  )
}

// ── 结果展示 ───────────────────────────────────────────────
function OptimizerResult({ taskId, mandatory }: { taskId: string; mandatory: string[] }) {
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
    const pct = status?.total > 0 ? (status.current / status.total * 100).toFixed(0) : 0
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-6 space-y-4">
        <div className="text-sm text-slate-300 font-medium">优化进行中...</div>
        <div className="w-full h-2 bg-slate-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="text-xs text-slate-400">
          {status?.current ?? 0} / {status?.total ?? '?'} 组合
          {status?.current_combo?.length > 0 && (
            <span className="ml-2 text-slate-500">当前：{status.current_combo.join(', ')}</span>
          )}
        </div>
      </div>
    )
  }

  const rows: any[] = result.results ?? []

  return (
    <div className="space-y-3">
      {/* 摘要 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 px-4 py-3 flex items-center gap-6 flex-wrap text-sm">
        <div>
          <span className="text-slate-400">共测试 </span>
          <span className="text-white font-medium">{result.total_tested}</span>
          <span className="text-slate-400"> / {result.total_combos} 组合</span>
        </div>
        <div><span className="text-slate-400">训练期：</span><span className="text-slate-300">{result.train_period}</span></div>
        <div><span className="text-slate-400">测试期：</span><span className="text-slate-300">{result.test_period}</span></div>
        <div><span className="text-slate-400">排名指标：</span><span className="text-slate-300">{result.metric}</span></div>
      </div>

      {/* 防过拟合说明 */}
      <div className="text-xs text-slate-500 bg-slate-800/50 border border-slate-700 rounded px-3 py-2">
        <span className="text-slate-400 font-medium">防过拟合：</span>
        按<strong className="text-white">测试期</strong>指标排名（非训练期）。过拟合分数 = 训练 Sharpe - 测试 Sharpe，越低越稳定。
        相同分数优先选因子数少的组合。
      </div>

      {/* 排名表 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-slate-400 border-b border-slate-700 bg-slate-800/80">
              {['#', '因子组合', '数量', '过拟合', '训练收益', '训练Sharpe', '测试收益', '测试Sharpe', '测试回撤', '胜率', '交易数'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                <td className="px-3 py-2"><FactorPills factors={r.factors} mandatory={mandatory} /></td>
                <td className="px-3 py-2 text-slate-400">{r.factor_count}</td>
                <td className="px-3 py-2"><OverfitBadge score={r.overfit_score} /></td>
                <td className={`px-3 py-2 font-mono ${r.train.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(r.train.return)}</td>
                <td className="px-3 py-2 font-mono text-slate-300">{r.train.sharpe.toFixed(2)}</td>
                <td className={`px-3 py-2 font-mono font-medium ${r.test.return >= 0 ? 'text-green-400' : 'text-red-400'}`}>{pct(r.test.return)}</td>
                <td className="px-3 py-2 font-mono font-medium text-white">{r.test.sharpe.toFixed(2)}</td>
                <td className="px-3 py-2 font-mono text-red-400">{pct(r.test.max_dd)}</td>
                <td className="px-3 py-2 font-mono text-slate-300">{pct(r.test.win_rate, 0)}</td>
                <td className="px-3 py-2 text-slate-400">{r.test.trades}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={11} className="px-4 py-8 text-center text-slate-500">无有效结果（所有组合的测试期交易笔数均不足 5）</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────
const UNIVERSES = ['sp500', 'nasdaq100', 'russell2000']
const PERIODS   = ['1y', '2y', '3y', '5y']
const METRICS   = [
  { value: 'sharpe',        label: 'Sharpe 比率' },
  { value: 'total_return',  label: '总收益率' },
  { value: 'excess_return', label: '超额收益（vs SPY）' },
]

export default function Optimizer() {
  const queryClient = useQueryClient()
  const [activeTask, setActiveTask] = useState<string | null>(null)
  const [params, setParams] = useState({
    universe:    'sp500',
    period:      '3y',
    mandatory_factors: ['rs_score'],
    min_combo_size: 2,
    max_combo_size: 5,
    train_ratio: 0.7,
    metric:      'sharpe',
    top_n_results: 20,
    bt_top_n:    6,
  })

  // 因子注册表（用于显示可选因子复选框）
  const { data: registry = [] } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    staleTime: 300_000,
  })
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical')

  const toggleMandatory = (key: string) => {
    setParams(p => {
      const m = p.mandatory_factors
      return {
        ...p,
        mandatory_factors: m.includes(key) ? m.filter(k => k !== key) : [...m, key],
      }
    })
  }

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => runOptimizer(params),
    onSuccess: (data) => {
      setActiveTask(data.task_id)
      queryClient.invalidateQueries({ queryKey: ['opt-history'] })
    },
  })

  // 预估组合数
  const mandatoryCount = params.mandatory_factors.length
  const optionalCount  = techFactors.length - mandatoryCount
  const estimateCombos = (() => {
    let n = 0
    for (let s = Math.max(0, params.min_combo_size - mandatoryCount);
             s <= params.max_combo_size - mandatoryCount && s <= optionalCount; s++) {
      // C(optionalCount, s)
      let c = 1
      for (let i = 0; i < s; i++) c = c * (optionalCount - i) / (i + 1)
      n += Math.round(c)
    }
    return n
  })()

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold text-white">因子优化器</h1>

      {/* 说明 */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-lg px-4 py-3 text-xs text-slate-400 space-y-1">
        <div>自动枚举技术因子的组合，每种组合分别在<strong className="text-white">训练期</strong>和<strong className="text-white">测试期</strong>各跑一次回测，按测试期指标排名。</div>
        <div className="text-slate-500">防过拟合：按测试期（非训练期）排名 · 过拟合分数越低越可靠 · 等分时优先因子数少的组合 · 测试期交易 &lt; 5 笔自动过滤</div>
      </div>

      {/* 参数配置 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4 space-y-4">
        <div className="text-sm font-medium text-slate-300">优化参数</div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* 股票池 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">股票池</label>
            <select className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
              value={params.universe} onChange={e => setParams(p => ({ ...p, universe: e.target.value }))}>
              {UNIVERSES.map(u => <option key={u}>{u}</option>)}
            </select>
          </div>

          {/* 总回测区间 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">总回测区间</label>
            <div className="flex gap-1">
              {PERIODS.map(p => (
                <button key={p} onClick={() => setParams(prev => ({ ...prev, period: p }))}
                  className={`flex-1 py-1.5 text-xs rounded border transition-colors
                    ${params.period === p ? 'bg-blue-600 border-blue-500 text-white' : 'border-slate-600 text-slate-400 hover:border-slate-400'}`}>
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* 训练比例 */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">
              训练/测试比例：{Math.round(params.train_ratio * 100)}% / {Math.round((1 - params.train_ratio) * 100)}%
            </label>
            <input type="range" min={50} max={85} step={5}
              value={params.train_ratio * 100}
              onChange={e => setParams(p => ({ ...p, train_ratio: +e.target.value / 100 }))}
              className="w-full accent-blue-500" />
          </div>

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

        {/* 必选因子（锁定在所有组合中）*/}
        <div>
          <div className="text-xs text-slate-400 mb-2">
            必选因子（蓝色 = 锁定在每个组合中，灰色 = 加入可选池）
          </div>
          <div className="flex flex-wrap gap-2">
            {techFactors.map((f: any) => {
              const isMandatory = params.mandatory_factors.includes(f.key)
              return (
                <button key={f.key}
                  onClick={() => toggleMandatory(f.key)}
                  className={`px-2.5 py-1 rounded border text-xs font-mono transition-colors
                    ${isMandatory
                      ? 'bg-blue-700/50 border-blue-500 text-blue-200'
                      : 'border-slate-600 text-slate-400 hover:border-slate-400'}`}>
                  {f.key}
                </button>
              )
            })}
          </div>
        </div>

        {/* 预估 + 启动 */}
        <div className="flex items-center gap-4 flex-wrap">
          <button
            onClick={() => submit()}
            disabled={isPending}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors"
          >
            {isPending ? '提交中...' : '▶ 开始优化'}
          </button>
          <div className="text-xs text-slate-400">
            预估 <span className="text-white font-medium">{estimateCombos}</span> 个组合
            × 2（训练+测试）≈
            <span className="text-white font-medium"> {Math.round(estimateCombos * 2 * 3 / 60)} 分钟</span>
            （4 线程并行）
          </div>
        </div>
      </div>

      {/* 结果 */}
      {activeTask && (
        <OptimizerResult taskId={activeTask} mandatory={params.mandatory_factors} />
      )}
    </div>
  )
}
