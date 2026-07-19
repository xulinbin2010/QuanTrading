import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { getRiskDashboard } from '../api/client'
import RiskThermometer from '../components/RiskThermometer'
import LeverageMonitor from './LeverageMonitor'

type Level = 'low' | 'mid' | 'high' | 'unknown'

type Pillar = {
  label: string
  available: boolean
  score: number | null
  level: Level
  detail: string
}

type RiskDashboard = {
  generated_at: string
  overall: { level: Level; label: string; method: string }
  pillars: Record<'market' | 'portfolio' | 'leverage', Pillar>
  reasons: string[]
  advice: { core: string; tactical: string; leveraged: string }
  data_quality: {
    thermometer_updated_at?: string
    leverage_generated_at?: string
    leverage_is_stale: boolean
    market_data_quality?: string
    portfolio_source: string
  }
  thermometer: unknown
  leverage: unknown
  automated_action: boolean
}

const TABS = [
  { key: 'overview', label: '风险总览' },
  { key: 'signals', label: '市场与组合' },
  { key: 'leverage', label: '杠杆压力' },
] as const

type Tab = typeof TABS[number]['key']

function levelStyle(level: Level) {
  if (level === 'high') return {
    band: 'border-red-700/70 bg-red-950/30',
    text: 'text-red-300',
    dot: 'bg-red-500',
    label: '高风险',
  }
  if (level === 'mid') return {
    band: 'border-yellow-700/70 bg-yellow-950/20',
    text: 'text-yellow-300',
    dot: 'bg-yellow-500',
    label: '警惕',
  }
  if (level === 'unknown') return {
    band: 'border-slate-700 bg-slate-800/70',
    text: 'text-slate-400',
    dot: 'bg-slate-500',
    label: '数据不足',
  }
  return {
    band: 'border-emerald-700/60 bg-emerald-950/20',
    text: 'text-emerald-300',
    dot: 'bg-emerald-500',
    label: '低风险',
  }
}

function formatTime(value?: string) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

function Overview({ data }: { data: RiskDashboard }) {
  const global = levelStyle(data.overall.level)
  const source = data.data_quality.portfolio_source
  const sourceLabel = source === 'ib' ? 'IB 真实持仓' : source === 'ai_universe' ? 'AI 关注池代理' : '无组合数据'

  return (
    <div className="space-y-5">
      <div className={`rounded-xl border px-5 py-4 ${global.band}`}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="text-sm text-slate-400">综合风险状态</div>
            <div className="flex items-center gap-3 mt-1">
              <span className={`w-3 h-3 rounded-full ${global.dot}`} />
              <span className={`text-3xl font-semibold ${global.text}`}>{data.overall.label}</span>
            </div>
          </div>
          <div className="max-w-2xl text-sm text-slate-300 leading-relaxed">
            {data.reasons.map((reason, i) => <div key={i}>· {reason}</div>)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {(Object.keys(data.pillars) as Array<keyof typeof data.pillars>).map(key => {
          const item = data.pillars[key]
          const style = levelStyle(item.level)
          return (
            <div key={key} className={`rounded-xl border p-4 ${style.band}`}>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-300">{item.label}</span>
                <span className={`text-xs font-medium ${style.text}`}>{style.label}</span>
              </div>
              <div className="flex items-baseline gap-2 mt-3">
                <span className={`text-3xl font-mono font-semibold ${style.text}`}>
                  {item.score == null ? '—' : item.score.toFixed(0)}
                </span>
                <span className="text-xs text-slate-500">/ 100</span>
              </div>
              <div className="text-sm text-slate-400 mt-3 leading-relaxed">{item.detail}</div>
            </div>
          )
        })}
      </div>

      <div>
        <h2 className="text-sm font-semibold text-white mb-2">按仓位类型给出的行动建议</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {[
            ['核心中长期仓', data.advice.core],
            ['短线 / 波段仓', data.advice.tactical],
            ['杠杆 / 高 Beta 仓', data.advice.leveraged],
          ].map(([title, text]) => (
            <div key={title} className="rounded-xl border border-slate-700 bg-slate-800 p-4">
              <div className="text-sm font-medium text-slate-200">{title}</div>
              <div className="text-sm text-slate-400 mt-2 leading-relaxed">{text}</div>
            </div>
          ))}
        </div>
      </div>

      {source !== 'ib' && (
        <div className="rounded-lg border border-yellow-800/60 bg-yellow-950/20 px-4 py-3 text-sm text-yellow-300">
          当前组合结构来源为「{sourceLabel}」。它只作为观察代理，不应据此触发真实持仓减仓；连接 IB 后才会按实际持仓市值权重计算。
        </div>
      )}

      <div className="text-sm text-slate-400 space-y-1">
        <div>· 组合数据：{sourceLabel}；风险温度计更新于 {data.data_quality.thermometer_updated_at || '—'}。</div>
        <div>· 杠杆数据更新于 {formatTime(data.data_quality.leverage_generated_at)}；行情质量：{data.data_quality.market_data_quality || 'unknown'}{data.data_quality.leverage_is_stale ? '（当前为 stale cache）' : ''}。</div>
        <div>· 综合灯号采用“风险共振”规则：任一维度高风险，或至少两个维度同时警惕，才升级为高风险。</div>
        <div>· 本页只提供 monitoring 与决策辅助，不会自动卖出、自动减仓或修改策略参数。</div>
      </div>
    </div>
  )
}

export default function RiskMonitor() {
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: Tab = TABS.some(item => item.key === requested) ? requested as Tab : 'overview'
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery<RiskDashboard>({
    queryKey: ['risk-dashboard'],
    queryFn: () => getRiskDashboard(false),
    staleTime: 10 * 60_000,
    refetchInterval: 15 * 60_000,
    retry: false,
  })

  const selectTab = (next: Tab) => {
    setParams(next === 'overview' ? {} : { tab: next }, { replace: true })
  }

  const refreshAll = async () => {
    setRefreshing(true)
    setRefreshError(null)
    try {
      const fresh = await getRiskDashboard(true)
      qc.setQueryData(['risk-dashboard'], fresh)
      qc.setQueryData(['risk-thermometer'], fresh.thermometer)
      qc.setQueryData(['leverage-dashboard'], fresh.leverage)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setRefreshError(e.response?.data?.detail || e.message || '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold text-white">风险监控 🛡️</h1>
          <p className="text-sm text-slate-400 mt-1">
            分层观察市场环境、组合结构与跨市场杠杆压力；综合灯号用于识别风险共振。
          </p>
        </div>
        <button onClick={refreshAll} disabled={refreshing}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white text-sm font-medium transition-colors disabled:opacity-50">
          {refreshing ? '刷新中…' : '↻ 全部刷新'}
        </button>
      </div>

      {(refreshError || error) && (
        <div className="rounded-lg border border-red-800/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">
          {refreshError || (error as Error)?.message || '风险数据加载失败'}
        </div>
      )}

      <div className="border-b border-slate-700 flex gap-5">
        {TABS.map(item => (
          <button key={item.key} onClick={() => selectTab(item.key)}
            className={`pb-2 -mb-px text-sm font-medium border-b-2 transition-colors ${
              tab === item.key
                ? 'border-blue-500 text-white'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}>
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        isLoading || !data
          ? <div className="h-56 rounded-xl bg-slate-800/70 animate-pulse" />
          : <Overview data={data} />
      )}
      {tab === 'signals' && <RiskThermometer />}
      {tab === 'leverage' && <LeverageMonitor embedded />}
    </div>
  )
}
