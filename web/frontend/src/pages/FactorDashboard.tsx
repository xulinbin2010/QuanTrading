import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getFactorRegistry, updateFactor, scanFactors } from '../api/client'

// ── RSMomentum 策略买入/卖出条件定义（静态说明） ──────────
const BUY_CONDITIONS = [
  { icon: '📈', label: 'RS 跑赢 SPY', desc: '个股 63 日收益率 - SPY 63 日收益率 > 0', param: 'rs_period=63', factorKey: 'rs_momentum' },
  { icon: '🚀', label: '价格突破 50 日高点', desc: '收盘价 > 前 50 日最高收盘价（排除当天）', param: 'breakout_period=50', factorKey: 'breakout_50' },
  { icon: '🔊', label: '放量确认', desc: '当日成交量 > 20 日均量 × 1.5', param: 'vol_multiplier=1.5', factorKey: 'vol_surge' },
  { icon: '🛡️', label: '崩跌过滤', desc: '距 52 周高点跌幅 ≤ 30%', param: 'max_drawdown=-30%', factorKey: 'not_crashed' },
  { icon: '🌊', label: '趋势向上', desc: 'MA50 > MA200（黄金交叉过滤）', param: 'MA50 > MA200', factorKey: 'uptrend' },
]

const SELL_CONDITIONS = [
  { icon: '⚠️', label: '量价背离（顶部信号）', desc: '价格创 50 日新高但成交量低于均量' },
  { icon: '🔴', label: '硬止损', desc: '跌破入场价 -15% 强制卖出' },
]

const CATEGORY_LABELS: Record<string, string> = {
  momentum: '动量', volume: '成交量', trend: '趋势',
  growth: '成长', quality: '质量', value: '估值',
}

const SIGNAL_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  filter: { label: '过滤', color: 'bg-orange-900/50 text-orange-300 border-orange-700' },
  score:  { label: '评分', color: 'bg-blue-900/50 text-blue-300 border-blue-700' },
}

// ── 因子卡片（注册表中的每个因子） ────────────────────────
function FactorCard({ factor, onToggle }: { factor: any; onToggle: (key: string, enabled: boolean) => void }) {
  const [expanded, setExpanded] = useState(false)
  const sig = SIGNAL_TYPE_LABELS[factor.signal_type] ?? { label: factor.signal_type, color: 'bg-slate-700 text-slate-300 border-slate-600' }

  return (
    <div className={`border rounded-lg transition-colors ${factor.enabled ? 'border-slate-600 bg-slate-800' : 'border-slate-700 bg-slate-800/40 opacity-60'}`}>
      <div className="flex items-center gap-3 px-4 py-3">
        {/* 开关 */}
        <button
          onClick={() => onToggle(factor.key, !factor.enabled)}
          className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${factor.enabled ? 'bg-blue-600' : 'bg-slate-600'}`}
        >
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${factor.enabled ? 'translate-x-4' : ''}`} />
        </button>

        {/* 名称 + 标签 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-white">{factor.name}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded border ${sig.color}`}>{sig.label}</span>
            <span className="text-xs text-slate-500 bg-slate-700/50 px-1.5 py-0.5 rounded">
              {CATEGORY_LABELS[factor.category] ?? factor.category}
            </span>
          </div>
          <div className="text-xs text-slate-500 font-mono mt-0.5">{factor.key}</div>
        </div>

        {/* 参数展开 */}
        {Object.keys(factor.params ?? {}).length > 0 && (
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors flex-shrink-0"
          >
            参数 {expanded ? '▲' : '▼'}
          </button>
        )}
      </div>

      {/* 参数详情 */}
      {expanded && Object.keys(factor.params ?? {}).length > 0 && (
        <div className="border-t border-slate-700 px-4 py-3 grid grid-cols-2 md:grid-cols-3 gap-3">
          {Object.entries(factor.params).map(([pname, pmeta]: [string, any]) => (
            <div key={pname} className="bg-slate-700/40 rounded p-2">
              <div className="text-xs text-slate-400">{pmeta.desc || pname}</div>
              <div className="font-mono text-sm text-white mt-0.5">
                {String(pmeta.default)}
                <span className="text-xs text-slate-500 ml-1">({pmeta.type})</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────
export default function FactorDashboard() {
  const [showRegistry, setShowRegistry] = useState(true)
  const queryClient = useQueryClient()

  // 因子注册表
  const { data: registry = [], isLoading: registryLoading } = useQuery({
    queryKey: ['factor-registry'],
    queryFn: getFactorRegistry,
    staleTime: 300_000,
  })

  // 最新扫描结果（仅取买入信号，不触发自动扫描）
  const { data: scanResult } = useQuery({
    queryKey: ['factors-scan', 'sp500'],
    queryFn: () => scanFactors('sp500', 100),
    staleTime: 3_600_000,
    gcTime: 3_600_000,
  })
  const rows: any[] = Array.isArray(scanResult) ? scanResult : (scanResult?.rows ?? [])
  const buySignals = rows.filter((r: any) => r.signal === 1)
  const sellSignals = rows.filter((r: any) => r.signal === -1)

  // 按分类分组
  const techFactors = (registry as any[]).filter((f: any) => f.data_type === 'technical')
  const fundFactors = (registry as any[]).filter((f: any) => f.data_type === 'fundamental')
  const grouped = techFactors.reduce((acc: any, f: any) => {
    ;(acc[f.category] = acc[f.category] || []).push(f)
    return acc
  }, {} as Record<string, any[]>)

  const { mutate: toggleFactor } = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      updateFactor(key, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['factor-registry'] }),
  })

  const enabledCount = (registry as any[]).filter((f: any) => f.enabled).length

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold text-white">因子看板</h1>

      {/* 策略概览 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 买入条件 */}
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
          <div className="text-sm font-medium text-slate-300 mb-3 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
            买入信号（5 个条件同时满足）
          </div>
          <div className="space-y-2">
            {BUY_CONDITIONS.map(c => (
              <div key={c.factorKey} className="flex items-start gap-2">
                <span className="text-base shrink-0">{c.icon}</span>
                <div>
                  <div className="text-sm text-white">{c.label}</div>
                  <div className="text-xs text-slate-500">{c.desc}</div>
                  <div className="text-xs font-mono text-slate-600 mt-0.5">{c.param}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 卖出条件 + 当前信号摘要 */}
        <div className="space-y-4">
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
                    <div className="text-sm text-white">{c.label}</div>
                    <div className="text-xs text-slate-500">{c.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 当前信号摘要 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="text-sm font-medium text-slate-300 mb-3">
              当前信号（上次扫描）
            </div>
            {rows.length === 0 ? (
              <div className="text-xs text-slate-500">暂无数据，请前往「市场扫描」执行扫描</div>
            ) : (
              <div className="space-y-2">
                <div>
                  <div className="text-xs text-slate-400 mb-1.5">买入信号 {buySignals.length} 只</div>
                  <div className="flex flex-wrap gap-1.5">
                    {buySignals.length === 0
                      ? <span className="text-xs text-slate-600">无</span>
                      : buySignals.map((r: any) => (
                        <span key={r.symbol} className="px-2 py-0.5 rounded text-xs bg-green-900/50 text-green-300 border border-green-800 font-mono">
                          {r.symbol}
                        </span>
                      ))
                    }
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-400 mb-1.5">卖出信号 {sellSignals.length} 只</div>
                  <div className="flex flex-wrap gap-1.5">
                    {sellSignals.length === 0
                      ? <span className="text-xs text-slate-600">无</span>
                      : sellSignals.map((r: any) => (
                        <span key={r.symbol} className="px-2 py-0.5 rounded text-xs bg-red-900/50 text-red-300 border border-red-800 font-mono">
                          {r.symbol}
                        </span>
                      ))
                    }
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
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
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
