import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getFactorRegistry, updateFactor, previewFactorSignals, checkTrailStops, getPositions, getProductionSignals, runProductionSignalsNow } from '../api/client'
import SymbolLink from '../components/SymbolLink'

// ── RSMomentum 策略买入条件模板（desc/param 由注册表参数动态填充） ──
const BUY_CONDITION_TEMPLATES = [
  {
    icon: '📈', label: 'RS 跑赢 SPY', registryKey: 'rs_score',
    buildDesc: (p: any) => p.weights
      ? `多窗口加权（${p.weights}）跑赢 SPY > 0`
      : `个股 ${p.period} 日收益率 - SPY ${p.period} 日收益率 > 0`,
    buildParam: (p: any) => p.weights ? `weights=${p.weights}` : `period=${p.period}`,
  },
  {
    icon: '🚀', label: '价格突破高点', registryKey: 'breakout',
    buildDesc: (p: any) => `收盘价 > 前 ${p.period} 日最高收盘价（排除当天）`,
    buildParam: (p: any) => `period=${p.period}`,
  },
  {
    icon: '🔊', label: '量能突破', registryKey: 'volume_surge',
    buildDesc: (p: any) => `当日成交量 > ${p.ma_period ?? 20} 日均量 × ${p.multiplier}`,
    buildParam: (p: any) => `multiplier=${p.multiplier}`,
  },
  {
    icon: '🛡️', label: '崩跌过滤', registryKey: 'drawdown_filter',
    buildDesc: (p: any) => `距 ${p.lookback} 日高点跌幅 ≤ ${Math.abs(p.max_drawdown * 100).toFixed(0)}%`,
    buildParam: (p: any) => `max_drawdown=${(p.max_drawdown * 100).toFixed(0)}%`,
  },
  {
    icon: '🌊', label: '趋势过滤', registryKey: 'trend_filter',
    buildDesc: (p: any) => `MA${p.fast} > MA${p.slow}（金叉过滤）`,
    buildParam: (p: any) => `MA${p.fast} > MA${p.slow}`,
  },
]

const SELL_CONDITIONS = [
  { icon: '⚠️', label: '量价背离', key: 'volume_divergence', desc: '价格创新高但成交量低于均量（顶部信号）' },
  { icon: '🔴', label: '硬止损', key: 'STOP_LOSS_PCT', desc: '跌破入场价 -15% 强制卖出' },
  { icon: '⏱️', label: '时间止损', key: 'TIME_STOP_DAYS', desc: '持仓超过 N 交易日仍未达到最低盈利门槛则卖出（Time Stop）' },
]

const CATEGORY_LABELS: Record<string, string> = {
  momentum: '动量', volume: '成交量', trend: '趋势',
  growth: '成长', quality: '质量', value: '估值',
}

const SIGNAL_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  filter:     { label: '买入过滤', color: 'bg-orange-900/50 text-orange-300 border-orange-700' },
  score:      { label: '评分',     color: 'bg-blue-900/50 text-blue-300 border-blue-700' },
  sell_alert: { label: '卖出信号', color: 'bg-red-900/50 text-red-300 border-red-700' },
}

const DISPLAY_ONLY_BADGE = { label: '仅展示 *', color: 'bg-slate-700/50 text-slate-400 border-slate-600' }

// ── 因子卡片（注册表中的每个因子） ────────────────────────
function FactorCard({
  factor, onToggle, onSaveParams,
}: {
  factor: any
  onToggle: (key: string, enabled: boolean) => void
  onSaveParams: (key: string, params: Record<string, string>) => void
}) {
  const sig = factor.display_only
    ? DISPLAY_ONLY_BADGE
    : (SIGNAL_TYPE_LABELS[factor.signal_type] ?? { label: factor.signal_type, color: 'bg-slate-700 text-slate-300 border-slate-600' })

  const paramEntries = Object.entries(factor.params ?? {}) as [string, any][]
  const [edits, setEdits] = useState<Record<string, string>>({})
  const dirty = Object.keys(edits).length > 0
  const valFor = (pname: string, pmeta: any) =>
    edits[pname] ?? String(pmeta.current ?? pmeta.default ?? '')

  return (
    <div className={`border rounded-lg transition-colors ${
      factor.display_only
        ? 'border-slate-700 bg-slate-800/30 opacity-60'
        : factor.enabled ? 'border-slate-600 bg-slate-800' : 'border-slate-700 bg-slate-800/40 opacity-60'
    }`}>
      <div className="flex items-center gap-3 px-4 py-3">
        {/* 开关（display_only 因子禁用开关） */}
        <button
          onClick={() => !factor.display_only && onToggle(factor.key, !factor.enabled)}
          disabled={factor.display_only}
          title={factor.display_only ? '仅用于 K 线详情展示，不参与信号/回测/优化' : undefined}
          className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
            factor.display_only ? 'bg-slate-700 cursor-not-allowed' : factor.enabled ? 'bg-blue-600' : 'bg-slate-600'
          }`}
        >
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${factor.enabled && !factor.display_only ? 'translate-x-4' : ''}`} />
        </button>

        {/* 名称 + 标签 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-sm font-medium ${factor.display_only ? 'text-slate-400' : 'text-white'}`}>{factor.name}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded border ${sig.color}`}>{sig.label}</span>
            <span className="text-xs text-slate-500 bg-slate-700/50 px-1.5 py-0.5 rounded">
              {CATEGORY_LABELS[factor.category] ?? factor.category}
            </span>
          </div>
          <div className="text-xs text-slate-500 font-mono mt-0.5">
            {factor.key}
            {factor.display_only && <span className="ml-2 text-slate-600">· 不参与信号 / 回测 / 优化</span>}
          </div>
        </div>

        {/* 参数（可编辑） */}
        {paramEntries.length > 0 && (
          <div className="flex items-center gap-2 flex-shrink-0">
            {paramEntries.map(([pname, pmeta]) => {
              const isWide = pmeta.type === 'str'
              return (
                <div key={pname} className="text-right">
                  <div className="text-xs text-slate-500" title={pmeta.desc}>{pname}</div>
                  <input
                    value={valFor(pname, pmeta)}
                    onChange={e => setEdits(prev => ({ ...prev, [pname]: e.target.value }))}
                    placeholder={String(pmeta.default ?? '')}
                    className={`mt-0.5 px-1.5 py-0.5 bg-slate-900 border border-slate-600 rounded font-mono text-xs text-slate-200 focus:outline-none focus:border-blue-500 ${isWide ? 'w-56' : 'w-20'} text-right`}
                  />
                </div>
              )
            })}
            {dirty && (
              <div className="flex flex-col gap-1 ml-1">
                <button
                  onClick={() => { onSaveParams(factor.key, edits); setEdits({}) }}
                  className="px-2 py-0.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded"
                >保存</button>
                <button
                  onClick={() => setEdits({})}
                  className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded"
                >取消</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── 信号标签列表 ───────────────────────────────────────────
function SignalTags({ signals, color }: { signals: any[]; color: 'green' | 'red' }) {
  const cls = color === 'green'
    ? 'bg-green-900/50 text-green-300 border-green-800'
    : 'bg-red-900/50 text-red-300 border-red-800'
  if (signals.length === 0) return <span className="text-xs text-slate-600">无</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {signals.map((r: any) => (
        <SymbolLink key={r.symbol} symbol={r.symbol}
          className={`px-2 py-0.5 rounded text-xs border font-mono ${cls}`}>
          {r.symbol}
          {r.rs_score != null && (
            <span className="ml-1 opacity-60">{(r.rs_score * 100).toFixed(0)}%</span>
          )}
        </SymbolLink>
      ))}
    </div>
  )
}

// ── 今日生产信号(top10 买入候选,复用 auto_trader.scan_signals)────────
function ProductionSignalsPanel() {
  const qc = useQueryClient()
  const { data, isFetching } = useQuery({
    queryKey: ['production-signals'],
    queryFn: getProductionSignals,
    refetchInterval: (q) => (q.state.data?.scanning ? 15000 : false),  // 扫描中 15s 轮询
  })
  const runNow = useMutation({
    mutationFn: runProductionSignalsNow,
    onSuccess: (d) => qc.setQueryData(['production-signals'], d),
  })
  const scanning = data?.scanning || runNow.isPending
  const rows: any[] = data?.rows ?? []

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-medium text-slate-300">
          🎯 今日生产信号 · Top {data?.top_n ?? 10} 买入候选
        </div>
        <button
          onClick={() => runNow.mutate()}
          disabled={scanning || isFetching}
          className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-300 rounded transition-colors"
        >
          {scanning ? '扫描中...' : '立即刷新'}
        </button>
      </div>

      {data && (
        <div className="text-xs text-slate-400 mb-3 flex flex-wrap gap-x-4 gap-y-1">
          {data.last_updated && <span>更新 {data.last_updated.replace('T', ' ').slice(0, 16)}</span>}
          <span className={data.spy_brake ? 'text-red-400' : 'text-emerald-400'}>
            SPY {data.spy_brake ? '✗ 熔断' : '✓'}
          </span>
          <span className={data.vix_brake ? 'text-red-400' : 'text-emerald-400'}>
            VIX {data.vix_close != null ? data.vix_close.toFixed(1) : '-'} {data.vix_brake ? '✗ 熔断' : '✓'}
          </span>
          <span className={data.breadth_cap ? 'text-amber-400' : 'text-emerald-400'}>
            宽度 {data.breadth_pct != null ? `${(data.breadth_pct * 100).toFixed(0)}%` : '-'}
            {data.breadth_cap && ' · 压仓 4'}
          </span>
          <span>共 {data.total_buy ?? 0} 只候选 · AI 池 {data.ai_pool_size ?? 0} 只</span>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="text-xs text-slate-500 py-3">
          {scanning
            ? '首次扫描进行中(约 30-60 秒),稍后自动刷新...'
            : '暂无缓存,点击「立即刷新」触发扫描。'}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-slate-400 border-b border-slate-700">
              <tr>
                <th className="text-left py-2 pr-2">#</th>
                <th className="text-left py-2 pr-2">Symbol</th>
                <th className="text-right py-2 px-2">入场分</th>
                <th className="text-right py-2 px-2">RS</th>
                <th className="text-right py-2 px-2">量比</th>
                <th className="text-left py-2 px-2">行业</th>
                <th className="text-left py-2 px-2">标识</th>
                <th className="text-right py-2 px-2">现价</th>
                <th className="text-right py-2 pl-2">ATR止损</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: any) => (
                <tr key={r.symbol} className="border-b border-slate-700/40 hover:bg-slate-700/30">
                  <td className="py-2 pr-2 text-slate-500">{r.rank}</td>
                  <td className="py-2 pr-2 font-medium"><SymbolLink symbol={r.symbol} /></td>
                  <td className="py-2 px-2 text-right text-emerald-300 font-mono">{r.entry_score?.toFixed(2)}</td>
                  <td className="py-2 px-2 text-right font-mono">{(r.rs_score * 100).toFixed(1)}%</td>
                  <td className="py-2 px-2 text-right font-mono">{r.vol_ratio?.toFixed(2)}x</td>
                  <td className="py-2 px-2 text-slate-300 truncate max-w-[150px]" title={r.industry || r.sector || '-'}>
                    {r.industry ?? r.sector ?? '-'}
                  </td>
                  <td className="py-2 px-2">
                    {r.ai_priority
                      ? <span className="text-amber-400">⭐ AI优先</span>
                      : r.ai_boost > 0
                        ? <span className="text-cyan-400">🤖 +{(r.ai_boost * 100).toFixed(0)}%</span>
                        : <span className="text-slate-600">·</span>}
                  </td>
                  <td className="py-2 px-2 text-right font-mono">${r.close?.toFixed(2)}</td>
                  <td className="py-2 pl-2 text-right font-mono text-slate-400">
                    {r.stop_price ? `$${r.stop_price.toFixed(2)}` : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────
export default function FactorDashboard() {
  const [showRegistry, setShowRegistry] = useState(true)
  const [universe] = useState('sp500+ndx')
  const [previewResult, setPreviewResult] = useState<any>(null)
  const [trailResults, setTrailResults] = useState<any[]>([])
  const [trailLoading, setTrailLoading] = useState(false)
  const [trailError, setTrailError] = useState('')
  const queryClient = useQueryClient()

  // 因子注册表
  const { data: registry = [], isLoading: registryLoading } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    staleTime: 300_000,
  })

  // 持仓数据（IB 不可用时返回空数组，不报错）
  const { data: positions = [], isLoading: positionsLoading, refetch: refetchPositions } = useQuery({
    queryKey: ['positions'],
    queryFn: () => getPositions(),
    staleTime: 60_000,
    retry: false,
  })
  const stockPositions = (positions as any[]).filter((p: any) => p.qty > 0 && !['SGOV','BIL','USFR'].includes(p.symbol))

  const runTrailCheck = async (pos: any[]) => {
    if (!pos.length) return
    setTrailLoading(true)
    setTrailError('')
    try {
      const res = await checkTrailStops(pos.map((p: any) => ({ symbol: p.symbol, avg_cost: p.avg_cost })))
      setTrailResults(res)
    } catch (e: any) {
      setTrailError(e?.response?.data?.detail ?? '检查失败，请确认 IB Gateway 已连接')
    } finally {
      setTrailLoading(false)
    }
  }

  // 从注册表动态构建买入条件（参数实时反映注册表配置）
  const registryMap = Object.fromEntries((registry as any[]).map((f: any) => [f.key, f]))
  const volMaParams = registryMap['volume_ma']?.params ?? {}
  const buyConditions = BUY_CONDITION_TEMPLATES.map(t => {
    const factor = registryMap[t.registryKey]
    // 取注册表 params 的 default 值，加上 volume_surge 需要的 ma_period
    const params = factor
      ? Object.fromEntries(Object.entries(factor.params).map(([k, v]: [string, any]) => [k, v.current ?? v.default]))
      : {}
    if (t.registryKey === 'volume_surge') {
      params.ma_period = (volMaParams as any).period?.current ?? (volMaParams as any).period?.default ?? 20
    }
    return { icon: t.icon, label: t.label, registryKey: t.registryKey, desc: t.buildDesc(params), param: t.buildParam(params) }
  })

  // 已启用的技术因子 key 列表（排除依赖项，如 volume_ma）
  const enabledTechFactors: string[] = (registry as any[])
    .filter((f: any) => f.data_type === 'technical' && !f.is_dependency && f.enabled)
    .map((f: any) => f.key)

  // 按分类分组
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical' && !f.is_dependency)
  const fundFactors = (registry as any[]).filter((f: any) => f.data_type === 'fundamental')
  const grouped = techFactors.reduce((acc: any, f: any) => {
    ;(acc[f.category] = acc[f.category] || []).push(f)
    return acc
  }, {} as Record<string, any[]>)

  const { mutate: toggleFactor } = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      updateFactor(key, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['factor-registry'] })
      setPreviewResult(null)   // 因子变化后清除上次预览
    },
  })

  const { mutate: saveFactorParams } = useMutation({
    mutationFn: ({ key, params }: { key: string; params: Record<string, string> }) =>
      updateFactor(key, { params }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['factor-registry'] })
      // 同时清除市场扫描缓存，让新参数立即生效
      queryClient.invalidateQueries({ queryKey: ['factors-scan'] })
      setPreviewResult(null)
    },
  })

  // 预览信号 mutation
  const { mutate: runPreview, isPending: previewing } = useMutation({
    mutationFn: () => previewFactorSignals(universe, enabledTechFactors),
    onSuccess: (data) => setPreviewResult(data),
  })

  const enabledCount = (registry as any[]).filter((f: any) => f.enabled).length

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">因子看板</h1>
        <span className="text-xs text-slate-400 bg-slate-700 border border-slate-600 rounded px-2 py-1">sp500+ndx</span>
      </div>

      {/* 今日生产信号(全宽置顶)*/}
      <ProductionSignalsPanel />

      {/* 策略概览 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 买入条件 */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
            买入信号（5 个条件同时满足）
          </div>
          <div className="space-y-2">
            {buyConditions.map(c => (
              <div key={c.label} className="flex items-start gap-2">
                <span className="text-base shrink-0">{c.icon}</span>
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm text-white">{c.label}</span>
                    <span className="text-xs font-mono text-slate-600">{c.registryKey}</span>
                  </div>
                  <div className="text-xs text-slate-500">{c.desc}</div>
                  <div className="text-xs font-mono text-slate-600 mt-0.5">{c.param}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 卖出条件 */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-red-400 inline-block" />
            卖出信号
          </div>
          <div className="space-y-2">
            {SELL_CONDITIONS.map(c => (
              <div key={c.label} className="flex items-start gap-2">
                <span className="text-base shrink-0">{c.icon}</span>
                <div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm text-white">{c.label}</span>
                    <span className="text-xs font-mono text-slate-600">{c.key}</span>
                  </div>
                  <div className="text-xs text-slate-500">{c.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── 持仓移动止损检查 ───────────────────────────────── */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="text-sm font-medium text-slate-300">🔒 持仓移动止损</div>
          <button
            onClick={async () => { await refetchPositions(); runTrailCheck(stockPositions) }}
            disabled={trailLoading || positionsLoading}
            className="px-3 py-1 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-slate-300 rounded transition-colors"
          >
            {trailLoading || positionsLoading ? '检查中...' : '刷新'}
          </button>
        </div>

        {trailError && <div className="text-xs text-red-400 mb-2">{trailError}</div>}

        {stockPositions.length === 0 && !positionsLoading && !trailError && (
          <div className="text-xs text-slate-500">无持仓（或 IB Gateway 未连接）</div>
        )}

        {stockPositions.length > 0 && trailResults.length === 0 && !trailLoading && (
          <div className="text-xs text-slate-500">
            检测到 {stockPositions.length} 只持仓，
            <button onClick={() => runTrailCheck(stockPositions)} className="text-blue-400 hover:text-blue-300 ml-1">点击检查</button>
          </div>
        )}

        {trailResults.length > 0 && (
          <div className="space-y-2">
            {trailResults.map((r: any) => {
              const triggered = r.status === 'triggered'
              const watching  = r.status === 'watching'
              const noData    = r.status === 'no_data'
              return (
                <div key={r.symbol}
                  className={`rounded-lg border px-3 py-2 text-xs ${
                    triggered ? 'border-red-700 bg-red-900/20'
                    : watching ? 'border-yellow-700 bg-yellow-900/10'
                    : 'border-slate-700 bg-slate-700/30'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <SymbolLink symbol={r.symbol} className="font-mono font-medium text-white" />
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      triggered ? 'bg-red-700 text-red-100'
                      : watching ? 'bg-yellow-700 text-yellow-100'
                      : noData ? 'bg-slate-600 text-slate-300'
                      : 'bg-slate-700 text-slate-400'
                    }`}>
                      {triggered ? '⚠ 触发止损' : watching ? '追踪中' : noData ? '无数据' : '未激活'}
                    </span>
                  </div>
                  {!noData && (
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-400">
                      <span>均价 <span className="text-white">${r.avg_cost}</span></span>
                      <span>现价 <span className="text-white">${r.cur_price}</span></span>
                      <span>峰值 <span className="text-white">${r.peak}</span>（+{(r.peak_ret * 100).toFixed(1)}%）</span>
                      <span>峰值回撤 <span className={r.trail_ret <= r.trail ? 'text-red-400' : 'text-slate-300'}>{(r.trail_ret * 100).toFixed(1)}%</span></span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 因子注册表 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <button
          className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-slate-300 hover:text-white transition-colors"
          onClick={() => setShowRegistry(s => !s)}
        >
          <div className="flex items-center gap-2">
            <span className={`transition-transform text-xs ${showRegistry ? 'rotate-90' : ''}`}>▶</span>
            因子注册表
            <span className="text-xs text-slate-500 font-normal">
              {registryLoading ? '加载中...' : `共 ${(registry as any[]).length} 个，已启用 ${enabledCount} 个`}
            </span>
          </div>
        </button>

        {showRegistry && (
          <div className="border-t border-slate-700 p-4 space-y-5">
            {/* 技术因子 */}
            {Object.entries(grouped).map(([cat, factors]: any) => (
              <div key={cat}>
                <div className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-2">
                  {CATEGORY_LABELS[cat] ?? cat} 因子
                </div>
                <div className="space-y-2">
                  {factors.map((f: any) => (
                    <FactorCard
                      key={f.key}
                      factor={f}
                      onToggle={(key, enabled) => toggleFactor({ key, enabled })}
                      onSaveParams={(key, params) => saveFactorParams({ key, params })}
                    />
                  ))}
                </div>
              </div>
            ))}

            {/* 基本面因子 */}
            {fundFactors.length > 0 && (
              <div>
                <div className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-2">基本面因子</div>
                <div className="space-y-2">
                  {fundFactors.map((f: any) => (
                    <FactorCard
                      key={f.key}
                      factor={f}
                      onToggle={(key, enabled) => toggleFactor({ key, enabled })}
                      onSaveParams={(key, params) => saveFactorParams({ key, params })}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* 预览信号按钮 */}
            <div className="border-t border-slate-700 pt-4 flex items-center gap-4 flex-wrap">
              <button
                onClick={() => runPreview()}
                disabled={previewing || enabledTechFactors.length < 1}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded font-medium transition-colors flex items-center gap-2"
              >
                {previewing
                  ? <><span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />扫描中（约 10-30 秒）...</>
                  : '▶ 预览当前因子组合信号'}
              </button>
              {enabledTechFactors.length > 0 && !previewing && (
                <span className="text-xs text-slate-500">
                  已启用：{enabledTechFactors.join(', ')}
                </span>
              )}
              {enabledTechFactors.length === 0 && (
                <span className="text-xs text-amber-500">请至少启用一个技术因子</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 预览结果 */}
      {previewResult && (
        <div className="bg-slate-800 rounded-lg border border-blue-700/50 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium text-blue-300 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />
              预览结果（自定义因子组合）
              <span className="text-xs text-slate-500 font-normal">
                共扫描 {previewResult.total} 只
              </span>
            </div>
            <button onClick={() => setPreviewResult(null)} className="text-slate-500 hover:text-slate-300 text-sm">✕</button>
          </div>

          <div className="text-xs text-slate-500">
            因子组合：{previewResult.factors?.join(' + ')}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-slate-400 mb-2">
                买入信号 <span className="text-green-400 font-medium">{previewResult.buy_count}</span> 只
              </div>
              <SignalTags
                signals={previewResult.rows?.filter((r: any) => r.signal === 1) ?? []}
                color="green"
              />
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-2">
                卖出信号 <span className="text-red-400 font-medium">{previewResult.sell_count}</span> 只
              </div>
              <SignalTags
                signals={previewResult.rows?.filter((r: any) => r.signal === -1) ?? []}
                color="red"
              />
            </div>
          </div>

          <div className="text-xs text-slate-600 border-t border-slate-700 pt-2">
            仅供研究参考，实盘交易使用「市场扫描」中的 RSMomentum 生产信号
          </div>
        </div>
      )}

    </div>
  )
}
