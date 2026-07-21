import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getLeverageDashboard } from '../api/client'
import SymbolLink from '../components/SymbolLink'

type ScoreComponent = {
  name: string
  value: number | null
  points: number
  max_points: number
  evidence_points?: number
  quality?: number
  provisional?: boolean
}

type MarketAggregate = {
  available_products: number
  configured_products: number
  as_of: string | null
  older_bar_count: number
  long_ret_1d: number | null
  long_volume_ratio: number | null
  inverse_ret_1d: number | null
  inverse_volume_ratio: number | null
  median_abs_tracking_gap_1d: number | null
  unwind_score: number | null
  unwind_level: 'low' | 'mid' | 'high'
  evidence_coverage?: number
  confidence?: 'low' | 'medium' | 'high'
  score_components: ScoreComponent[]
}

type FundingPoint = {
  date: string
  debit_usd_m?: number
  credit_krw_100m?: number
}

type FundingData = {
  available: boolean
  label?: string
  source: string
  source_url?: string
  frequency?: string
  unit?: string
  as_of?: string
  latest?: number
  mom?: number | null
  yoy?: number | null
  percentile_36?: number | null
  zscore_36?: number | null
  history_window?: number
  crowding_score?: number
  history?: FundingPoint[]
  publication_lag?: string
  error?: string
  setup_required?: boolean
  setup_hint?: string
}

type ProductRow = {
  symbol: string
  name: string
  market: 'US' | 'KR'
  leverage: number
  benchmark: string
  theme: string
  provider: string
  available: boolean
  error?: string
  direction?: 'long' | 'inverse'
  latest_date?: string
  price?: number
  currency?: string
  ret_1d?: number | null
  ret_5d?: number | null
  ret_20d?: number | null
  drawdown_20d?: number | null
  realized_vol_20d?: number | null
  volume_ratio?: number | null
  volume_estimated?: boolean
  tracking_gap_1d?: number | null
  tracking_drag_20d?: number | null
  anomaly_score?: number
}

type LeverageDashboard = {
  generated_at: string
  market_data_source: string
  market_data_quality: string
  is_stale: boolean
  stale_reason?: string
  summary: {
    unwind_score: number | null
    unwind_level: 'low' | 'mid' | 'high'
    dominant_market?: 'US' | 'KR' | null
    trigger_score?: number | null
    trigger_band?: 'low' | 'mid' | 'high'
    crowding_score?: number | null
    crowding_band?: 'low' | 'mid' | 'high'
    state?: string
    state_label?: string
    confidence?: 'low' | 'medium' | 'high'
    evidence_coverage?: number
    trigger_method?: string
    crowding_method?: string
  }
  posture?: {
    code: string
    label: string
    level: 'low' | 'mid' | 'high'
    reason: string
    core: string
    tactical: string
    automated_action: boolean
  }
  personal?: {
    available: boolean
    error?: string
    setup_hint?: string
    source?: string
    as_of?: string
    net_liq?: number
    gross_long?: number
    maint_margin?: number
    excess_liquidity?: number
    margin_cushion_pct?: number
    pressure_level?: string
    margin_debt?: number
    broker_leverage?: number
    embedded_extra_exposure?: number
    effective_exposure?: number
    effective_leverage?: number
    market_weights?: Record<'US' | 'KR', number>
    leveraged_positions?: Array<{ symbol: string; market_value_usd: number; leverage: number; exposure_usd: number }>
    stress_trigger_shock?: number | null
  }
  markets: { us: MarketAggregate; kr: MarketAggregate }
  funding: { us: FundingData; kr: FundingData }
  evidence?: Array<{
    key: string
    layer: 'trigger' | 'crowding' | 'account'
    market: 'US' | 'KR' | 'ACCOUNT'
    label: string
    value?: number | null
    points?: number
    max_points?: number
    comparison?: { mom?: number | null; yoy?: number | null; percentile?: number | null }
    status: 'low' | 'mid' | 'high'
    source?: string
    as_of?: string
    frequency?: string
    confidence?: 'low' | 'medium' | 'high'
    provisional?: boolean
  }>
  history?: Array<{
    generated_at: string
    trigger_score?: number | null
    crowding_score?: number | null
    state?: string
    confidence?: string
    evidence_coverage?: number
    margin_cushion_pct?: number | null
    effective_leverage?: number | null
  }>
  products: ProductRow[]
  warnings: string[]
  methodology_version?: string
}

const pct = (value: number | null | undefined, digits = 1) =>
  value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`

const ratio = (value: number | null | undefined) =>
  value == null ? '—' : `${value.toFixed(2)}x`

function numberCompact(value: number | null | undefined, currency?: string) {
  if (value == null) return '—'
  if (currency === 'KRW')
    return `₩${Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)}`
  return `$${Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value)}`
}

function moveColor(value: number | null | undefined) {
  if (value == null) return 'text-slate-500'
  return value > 0 ? 'text-emerald-400' : value < 0 ? 'text-red-400' : 'text-slate-300'
}

function levelStyle(level?: string) {
  if (level === 'high') return {
    band: 'border-red-700/70 bg-red-950/30',
    text: 'text-red-300',
    fill: 'bg-red-500',
    label: '高压',
  }
  if (level === 'mid') return {
    band: 'border-yellow-700/70 bg-yellow-950/20',
    text: 'text-yellow-300',
    fill: 'bg-yellow-500',
    label: '升温',
  }
  return {
    band: 'border-emerald-800/70 bg-emerald-950/20',
    text: 'text-emerald-300',
    fill: 'bg-emerald-500',
    label: '平稳',
  }
}

function DecisionCard({ title, value, unit, level, subtitle, meta }: {
  title: string
  value: string
  unit?: string
  level?: string
  subtitle: string
  meta?: string
}) {
  const style = levelStyle(level)
  return (
    <div className={`rounded-xl border px-4 py-3 min-h-[132px] ${style.band}`}>
      <div className="text-sm text-slate-300">{title}</div>
      <div className="flex items-baseline gap-2 mt-2">
        <span className={`text-2xl font-semibold ${style.text}`}>{value}</span>
        {unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
      <div className="text-xs text-slate-300 mt-2 leading-relaxed">{subtitle}</div>
      {meta && <div className="text-[11px] text-slate-500 mt-1">{meta}</div>}
    </div>
  )
}

function ConfidenceBadge({ value }: { value?: string }) {
  const label = value === 'high' ? '高' : value === 'medium' ? '中' : '低'
  const cls = value === 'high'
    ? 'bg-emerald-900/40 text-emerald-300'
    : value === 'medium'
      ? 'bg-yellow-900/40 text-yellow-300'
      : 'bg-slate-700 text-slate-300'
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${cls}`}>置信度 {label}</span>
}

function MarketCard({ market, data }: { market: 'US' | 'KR'; data: MarketAggregate }) {
  const style = levelStyle(data.unwind_level)
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-white">{market === 'US' ? '🇺🇸 美国' : '🇰🇷 韩国'}产品压力</div>
          <div className="text-xs text-slate-500 mt-0.5">
            as-of {data.as_of ?? '—'} · 可用 {data.available_products}/{data.configured_products}
            {data.older_bar_count > 0 && <span className="text-yellow-400"> · {data.older_bar_count} 只较旧</span>}
          </div>
        </div>
        <div className={`text-2xl font-mono font-semibold ${style.text}`}>
          {data.unwind_score == null ? '—' : data.unwind_score.toFixed(0)}
        </div>
      </div>
      <div className="flex items-center gap-2 mt-2">
        <span className="text-[11px] text-slate-500">证据覆盖 {data.evidence_coverage?.toFixed(0) ?? '—'}%</span>
        <ConfidenceBadge value={data.confidence} />
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 mt-4 text-xs">
        <div>
          <div className="text-slate-500">多头杠杆 1D</div>
          <div className={`font-mono mt-0.5 ${moveColor(data.long_ret_1d)}`}>{pct(data.long_ret_1d)}</div>
        </div>
        <div>
          <div className="text-slate-500">多头成交 pace</div>
          <div className="font-mono text-slate-200 mt-0.5">{ratio(data.long_volume_ratio)}</div>
        </div>
        <div>
          <div className="text-slate-500">反向产品 1D</div>
          <div className={`font-mono mt-0.5 ${moveColor(data.inverse_ret_1d)}`}>{pct(data.inverse_ret_1d)}</div>
        </div>
        <div>
          <div className="text-slate-500">反向成交 pace</div>
          <div className="font-mono text-slate-200 mt-0.5">{ratio(data.inverse_volume_ratio)}</div>
        </div>
      </div>
      <div className="mt-4 space-y-1.5">
        {data.score_components.map(c => (
          <div key={c.name} className="flex items-center gap-2 text-[11px]">
            <span className="text-slate-400 w-24 shrink-0">{c.name}</span>
            <div className="h-1.5 flex-1 bg-slate-700 rounded-full overflow-hidden">
              <div className="h-full bg-blue-500 rounded-full" style={{ width: `${c.max_points ? c.points / c.max_points * 100 : 0}%` }} />
            </div>
            <span className="font-mono text-slate-400 w-12 text-right">{c.points}/{c.max_points}</span>
            {c.provisional && <span className="text-[9px] text-yellow-400">盘中</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function HistoryCard({ history }: { history: NonNullable<LeverageDashboard['history']> }) {
  const shown = history.slice(-120)
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 42, right: 44, top: 30, bottom: 34 },
    legend: {
      top: 0,
      textStyle: { color: '#94a3b8', fontSize: 10 },
      data: ['Unwind Trigger', 'Crowding', '账户缓冲', '有效杠杆'],
    },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: shown.map(p => new Date(p.generated_at).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      })),
      axisLabel: { color: '#64748b', fontSize: 9, hideOverlap: true },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: [
      {
        type: 'value', min: 0, max: 100,
        axisLabel: { color: '#64748b', fontSize: 9, formatter: '{value}' },
        splitLine: { lineStyle: { color: '#33415555' } },
      },
      {
        type: 'value', min: 0, max: (value: { max: number }) => Math.max(2, Math.ceil(value.max * 1.15)),
        axisLabel: { color: '#64748b', fontSize: 9, formatter: '{value}×' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'Unwind Trigger', type: 'line', showSymbol: false, connectNulls: true,
        data: shown.map(p => p.trigger_score), lineStyle: { color: '#f87171', width: 2 },
        markLine: {
          symbol: 'none', label: { color: '#94a3b8', fontSize: 9 },
          lineStyle: { type: 'dashed', color: '#64748b66' },
          data: [{ yAxis: 35, name: '升温' }, { yAxis: 65, name: '高压' }],
        },
      },
      {
        name: 'Crowding', type: 'line', showSymbol: false, connectNulls: true,
        data: shown.map(p => p.crowding_score), lineStyle: { color: '#a78bfa', width: 1.5 },
      },
      {
        name: '账户缓冲', type: 'line', showSymbol: false, connectNulls: true,
        data: shown.map(p => p.margin_cushion_pct), lineStyle: { color: '#38bdf8', width: 1.5 },
      },
      {
        name: '有效杠杆', type: 'line', yAxisIndex: 1, showSymbol: false, connectNulls: true,
        data: shown.map(p => p.effective_leverage), lineStyle: { color: '#facc15', width: 1.5 },
      },
    ],
  }
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-white">风险状态历史</div>
          <div className="text-xs text-slate-500 mt-0.5">保存 raw-derived 快照，方法版本变化后仍可区分</div>
        </div>
        <span className="text-xs text-slate-500">{shown.length} 个快照</span>
      </div>
      {shown.length > 0
        ? <ReactECharts option={option} style={{ height: 260 }} notMerge />
        : <div className="h-[260px] flex items-center justify-center text-sm text-slate-500">刷新后开始积累历史</div>}
    </div>
  )
}

function evidenceValue(row: NonNullable<LeverageDashboard['evidence']>[number]) {
  if (row.layer === 'account')
    return row.label.includes('缓冲') ? `${row.value?.toFixed(1) ?? '—'}%` : `${row.value?.toFixed(2) ?? '—'}×`
  if (row.layer === 'crowding')
    return row.comparison?.percentile == null ? '—' : `${(row.comparison.percentile * 100).toFixed(0)}%分位`
  if (row.label.includes('成交')) return ratio(row.value)
  return pct(row.value, row.label.includes('偏差') ? 2 : 1)
}

function EvidenceLedger({ rows }: { rows: NonNullable<LeverageDashboard['evidence']> }) {
  const statusText = { low: '正常', mid: '观察', high: '高压' } as const
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-700">
        <div className="text-sm font-semibold text-white">今日证据账本</div>
        <div className="text-xs text-slate-500 mt-0.5">每条结论都保留数值、来源、as-of 与置信度</div>
      </div>
      <div className="overflow-x-auto max-h-[430px]">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-800">
            <tr className="border-b border-slate-700 text-xs text-slate-500">
              <th className="text-left px-4 py-2 font-medium">层级 / 指标</th>
              <th className="text-right px-3 py-2 font-medium">当前</th>
              <th className="text-center px-3 py-2 font-medium">状态</th>
              <th className="text-left px-3 py-2 font-medium">来源 / 时点</th>
              <th className="text-right px-4 py-2 font-medium">质量</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.key} className="border-b border-slate-700/40">
                <td className="px-4 py-2">
                  <div className="text-slate-200">{row.label}</div>
                  <div className="text-[10px] text-slate-500">{row.layer.toUpperCase()} · {row.market}</div>
                </td>
                <td className="px-3 py-2 text-right font-mono text-slate-200">{evidenceValue(row)}</td>
                <td className="px-3 py-2 text-center">
                  <span className={`text-[11px] px-2 py-0.5 rounded ${
                    row.status === 'high' ? 'bg-red-900/40 text-red-300'
                      : row.status === 'mid' ? 'bg-yellow-900/40 text-yellow-300'
                        : 'bg-emerald-900/30 text-emerald-300'
                  }`}>{statusText[row.status]}</span>
                </td>
                <td className="px-3 py-2">
                  <div className="text-xs text-slate-300">{row.source || '—'}</div>
                  <div className="text-[10px] text-slate-500">{row.as_of || '—'} · {row.frequency || '—'}</div>
                </td>
                <td className="px-4 py-2 text-right">
                  <ConfidenceBadge value={row.confidence} />
                  {row.provisional && <div className="text-[9px] text-yellow-400 mt-1">Preliminary</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function InterpretationCard({ data }: { data: LeverageDashboard }) {
  const personal = data.personal
  const history = data.history ?? []
  const previous = history.length >= 2 ? history[history.length - 2] : undefined
  const current = history.length ? history[history.length - 1] : undefined
  const delta = current?.trigger_score != null && previous?.trigger_score != null
    ? current.trigger_score - previous.trigger_score
    : null
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-4 space-y-4">
      <div>
        <div className="text-sm font-semibold text-white">风险解释</div>
        <div className="text-xs text-slate-500 mt-0.5">确定性规则生成；AI 不参与计分</div>
      </div>
      <div className="rounded-lg bg-slate-900/50 border border-slate-700 px-3 py-2">
        <div className="text-xs text-slate-500">当前状态</div>
        <div className="text-lg font-semibold text-slate-100 mt-0.5">{data.summary.state_label || '—'}</div>
        <div className="text-xs text-slate-400 mt-1">
          Crowding {data.summary.crowding_score?.toFixed(0) ?? '—'} × Trigger {data.summary.trigger_score?.toFixed(0) ?? '—'}
          {delta != null && <span className={delta > 0 ? 'text-red-300' : delta < 0 ? 'text-emerald-300' : ''}> · 较上次 {delta >= 0 ? '+' : ''}{delta.toFixed(1)}</span>}
        </div>
      </div>
      <div className="space-y-2 text-sm text-slate-300 leading-relaxed">
        <div>• {data.posture?.reason || '等待更多可用证据。'}</div>
        {personal?.available
          ? <>
              <div>• 账户缓冲 {personal.margin_cushion_pct?.toFixed(1) ?? '—'}%，margin debt {numberCompact(personal.margin_debt, 'USD')}。</div>
              <div>• Broker leverage {personal.broker_leverage?.toFixed(2) ?? '—'}×；计入 2X/3X 后 {personal.effective_leverage?.toFixed(2) ?? '—'}×。</div>
            </>
          : <div>• 未关联有效实盘诊断快照，当前姿态只反映市场，不反映你的爆仓距离。</div>}
      </div>
      <div className="border-t border-slate-700 pt-3 space-y-2">
        <div>
          <div className="text-xs text-slate-500">核心中长期仓</div>
          <div className="text-sm text-slate-200 mt-0.5">{data.posture?.core}</div>
        </div>
        <div>
          <div className="text-xs text-slate-500">杠杆 / 波段仓</div>
          <div className="text-sm text-slate-200 mt-0.5">{data.posture?.tactical}</div>
        </div>
      </div>
      <div className="text-[11px] text-slate-500">
        Evidence coverage {data.summary.evidence_coverage?.toFixed(0) ?? '—'}% · methodology {data.methodology_version || '—'} · 不触发自动交易
      </div>
    </div>
  )
}

function FundingCard({ market, data }: { market: 'US' | 'KR'; data: FundingData }) {
  if (!data.available) {
    return (
      <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-4 min-h-[260px]">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold text-white">
            {market === 'US' ? '🇺🇸 美国融资余额' : '🇰🇷 韩国信用融资'}
          </div>
          <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-400">Unavailable</span>
        </div>
        <div className="mt-10 text-sm text-slate-400 text-center">
          <div>{data.error || '官方数据暂不可用'}</div>
          {data.setup_hint && <div className="text-xs text-slate-500 mt-2">{data.setup_hint}</div>}
          {data.source_url && (
            <a href={data.source_url} target="_blank" rel="noreferrer"
              className="inline-block text-blue-400 hover:text-blue-300 mt-3">
              打开官方数据申请页 ↗
            </a>
          )}
        </div>
      </div>
    )
  }

  const history = data.history ?? []
  const valueKey = market === 'US' ? 'debit_usd_m' : 'credit_krw_100m'
  const chart = {
    backgroundColor: 'transparent',
    grid: { left: 52, right: 12, top: 16, bottom: 30 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: history.map(p => p.date.slice(0, 7)),
      axisLabel: { color: '#64748b', fontSize: 10, showMaxLabel: true },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: {
        color: '#64748b', fontSize: 10,
        formatter: (v: number) => market === 'US' ? `$${(v / 1_000).toFixed(0)}B` : `${(v / 10_000).toFixed(0)}兆`,
      },
      splitLine: { lineStyle: { color: '#33415555' } },
    },
    series: [{
      type: 'line',
      smooth: true,
      symbol: 'none',
      data: history.map(p => (p as unknown as Record<string, number>)[valueKey]),
      lineStyle: { color: market === 'US' ? '#38bdf8' : '#a78bfa', width: 2 },
      areaStyle: { color: market === 'US' ? 'rgba(56,189,248,.08)' : 'rgba(167,139,250,.08)' },
    }],
  }

  const latestText = market === 'US'
    ? `$${((data.latest ?? 0) / 1_000).toFixed(1)}B`
    : `₩${((data.latest ?? 0) / 10_000).toFixed(2)}兆`

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/70 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-white">{market === 'US' ? '🇺🇸 美国融资余额' : '🇰🇷 韩国信用融资'}</div>
          <div className="flex items-baseline gap-2 mt-1.5">
            <span className="text-xl font-mono font-semibold text-slate-100">{latestText}</span>
            <span className="text-xs text-slate-500">as-of {data.as_of}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-500">Crowding</div>
          <div className={`font-mono text-lg ${(data.crowding_score ?? 0) >= 70 ? 'text-red-300' : (data.crowding_score ?? 0) >= 40 ? 'text-yellow-300' : 'text-emerald-300'}`}>
            {data.crowding_score?.toFixed(0) ?? '—'}
          </div>
        </div>
      </div>
      <div className="flex gap-4 text-xs mt-2">
        <span className="text-slate-500">环比 <b className={moveColor(data.mom)}>{pct(data.mom)}</b></span>
        <span className="text-slate-500">同比 <b className={moveColor(data.yoy)}>{pct(data.yoy)}</b></span>
        <span className="text-slate-500">{data.history_window ?? 36}期分位 <b className="text-slate-200">{pct(data.percentile_36, 0)}</b></span>
      </div>
      <ReactECharts option={chart} style={{ height: 150 }} notMerge />
      <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500 mt-1">
        <span>{data.publication_lag}</span>
        {data.source_url && <a href={data.source_url} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">{data.source} ↗</a>}
      </div>
    </div>
  )
}

function ProductTable({ rows }: { rows: ProductRow[] }) {
  const [market, setMarket] = useState<'ALL' | 'US' | 'KR'>('ALL')
  const [direction, setDirection] = useState<'ALL' | 'long' | 'inverse'>('ALL')
  const shown = useMemo(
    () => rows
      .filter(r => market === 'ALL' || r.market === market)
      .filter(r => direction === 'ALL' || r.direction === direction)
      .sort((a, b) => (b.anomaly_score ?? -1) - (a.anomaly_score ?? -1)),
    [rows, market, direction],
  )

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/60 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center gap-2 flex-wrap">
        <div className="text-sm font-semibold text-white mr-2">杠杆产品异常榜</div>
        {(['ALL', 'US', 'KR'] as const).map(v => (
          <button key={v} onClick={() => setMarket(v)}
            className={`px-2.5 py-1 rounded text-xs ${market === v ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
            {v === 'ALL' ? '全部市场' : v === 'US' ? '🇺🇸 美国' : '🇰🇷 韩国'}
          </button>
        ))}
        <span className="w-px h-5 bg-slate-600 mx-1" />
        {([
          ['ALL', '全部方向'],
          ['long', '正向杠杆'],
          ['inverse', '反向产品'],
        ] as const).map(([v, label]) => (
          <button key={v} onClick={() => setDirection(v)}
            className={`px-2.5 py-1 rounded text-xs ${direction === v ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
            {label}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-500">按异常度排序 · {shown.length} 只</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-xs text-slate-500">
              <th className="text-left px-4 py-2 font-medium">产品 / 标的</th>
              <th className="text-center px-2 py-2 font-medium">倍数</th>
              <th className="text-right px-2 py-2 font-medium">价格</th>
              <th className="text-right px-2 py-2 font-medium">1D</th>
              <th className="text-right px-2 py-2 font-medium">5D</th>
              <th className="text-right px-2 py-2 font-medium">20D</th>
              <th className="text-right px-2 py-2 font-medium">成交 pace</th>
              <th className="text-right px-2 py-2 font-medium" title="产品单日收益 - 目标倍数 × 基准单日收益">1D 偏差</th>
              <th className="text-right px-2 py-2 font-medium" title="实际 20 日累计收益 - 基于每日目标倍数复利得到的理论收益">20D drag</th>
              <th className="text-right px-4 py-2 font-medium">异常度</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(r => (
              <tr key={r.symbol} className="border-b border-slate-700/40 hover:bg-slate-700/20">
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{r.market === 'US' ? '🇺🇸' : '🇰🇷'}</span>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <SymbolLink symbol={r.symbol} className="font-mono font-semibold text-slate-100">
                          {r.symbol.replace('.KS', '')}
                        </SymbolLink>
                        <span className="text-[10px] px-1 rounded bg-slate-700 text-slate-400">{r.theme}</span>
                      </div>
                      <div className="text-[11px] text-slate-500 truncate max-w-[290px]">
                        {r.name} · benchmark <SymbolLink symbol={r.benchmark} className="font-mono text-slate-400" /> · {r.latest_date ?? '—'}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="px-2 py-2 text-center">
                  <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${r.leverage > 0 ? 'bg-blue-900/50 text-blue-300' : 'bg-red-900/40 text-red-300'}`}>
                    {r.leverage > 0 ? '+' : ''}{r.leverage}x
                  </span>
                </td>
                {r.available ? (
                  <>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">{numberCompact(r.price, r.currency)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${moveColor(r.ret_1d)}`}>{pct(r.ret_1d)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${moveColor(r.ret_5d)}`}>{pct(r.ret_5d)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${moveColor(r.ret_20d)}`}>{pct(r.ret_20d)}</td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {ratio(r.volume_ratio)}
                      {r.volume_estimated && <span className="ml-1 text-[9px] text-yellow-400" title="盘中按已过交易时间折算">估</span>}
                    </td>
                    <td className={`px-2 py-2 text-right font-mono ${Math.abs(r.tracking_gap_1d ?? 0) >= .01 ? 'text-yellow-300' : 'text-slate-400'}`}>{pct(r.tracking_gap_1d, 2)}</td>
                    <td className={`px-2 py-2 text-right font-mono ${(r.tracking_drag_20d ?? 0) < -.03 ? 'text-red-300' : 'text-slate-400'}`}>{pct(r.tracking_drag_20d, 1)}</td>
                    <td className="px-4 py-2 text-right">
                      <span className={`inline-flex min-w-10 justify-center px-2 py-1 rounded text-xs font-mono font-semibold ${
                        (r.anomaly_score ?? 0) >= 65 ? 'bg-red-900/50 text-red-300'
                        : (r.anomaly_score ?? 0) >= 35 ? 'bg-yellow-900/40 text-yellow-300'
                        : 'bg-slate-700 text-slate-300'
                      }`}>{r.anomaly_score?.toFixed(0) ?? '—'}</span>
                    </td>
                  </>
                ) : (
                  <td colSpan={8} className="px-3 py-2 text-center text-xs text-slate-500">{r.error || '行情不可用'}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function LeverageMonitor({ embedded = false }: { embedded?: boolean }) {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data, isLoading } = useQuery<LeverageDashboard>({
    queryKey: ['leverage-dashboard'],
    queryFn: () => getLeverageDashboard(false),
    staleTime: 10 * 60_000,
    refetchInterval: 15 * 60_000,
    retry: false,
  })

  async function refresh() {
    setRefreshing(true)
    setError(null)
    try {
      const fresh = await getLeverageDashboard(true)
      qc.setQueryData(['leverage-dashboard'], fresh)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } }; message?: string }
      setError(e.response?.data?.detail || e.message || '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }

  if (isLoading || !data) {
    return (
      <div className="space-y-4">
        <div className="h-20 rounded-xl bg-slate-800/70 animate-pulse" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <div key={i} className="h-40 rounded-xl bg-slate-800/70 animate-pulse" />)}
        </div>
        <div className="text-sm text-slate-400 text-center">正在获取韩国与美国杠杆产品行情及官方融资余额…</div>
      </div>
    )
  }

  const updated = new Date(data.generated_at).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    hour12: false,
  })
  const totalAvailable = data.products.filter(p => p.available).length
  const personal = data.personal
  const personalLevel = personal?.pressure_level === 'critical' || personal?.pressure_level === 'high'
    ? 'high'
    : personal?.pressure_level === 'mid' ? 'mid' : 'low'

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            {!embedded && <h1 className="text-lg font-semibold text-white">杠杆压力监控 ⚡</h1>}
            {embedded && <h2 className="text-sm font-semibold text-white">美韩杠杆与流动性压力</h2>}
            {data.is_stale && <span className="text-xs px-2 py-0.5 rounded bg-yellow-900/50 text-yellow-300">Stale cache</span>}
          </div>
          <p className="text-sm text-slate-400 mt-1">
            分开观察杠杆堆积（Crowding）与去杠杆压力（Unwind），覆盖美国和韩国主要 2X / 3X / inverse 产品。
          </p>
          <div className="text-xs text-slate-500 mt-1">
            获取于 {updated} 北京 · bar as-of：US {data.markets.us.as_of ?? '—'} / KR {data.markets.kr.as_of ?? '—'} · {data.market_data_source}（delayed / near-real-time）· 行情可用 {totalAvailable}/{data.products.length}
          </div>
        </div>
        <button onClick={refresh} disabled={refreshing}
          className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white text-sm font-medium transition-colors disabled:opacity-50">
          {refreshing ? '刷新中…' : '↻ 强制刷新'}
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-800/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">{error}</div>}
      {data.is_stale && (
        <div className="rounded-lg border border-yellow-800/60 bg-yellow-950/20 px-3 py-2 text-sm text-yellow-300">
          本次外部行情不可用，当前展示最近成功缓存：{data.stale_reason || '原因未知'}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <DecisionCard
          title="市场去杠杆状态"
          value={data.summary.state_label || '—'}
          level={data.summary.unwind_level}
          subtitle={`Crowding ${data.summary.crowding_score?.toFixed(0) ?? '—'} × Trigger ${data.summary.trigger_score?.toFixed(0) ?? '—'}`}
          meta={`证据覆盖 ${data.summary.evidence_coverage?.toFixed(0) ?? '—'}% · 置信度 ${data.summary.confidence === 'high' ? '高' : data.summary.confidence === 'medium' ? '中' : '低'}`}
        />
        <DecisionCard
          title="我的保证金缓冲"
          value={personal?.available && personal.margin_cushion_pct != null ? `${personal.margin_cushion_pct.toFixed(1)}%` : '未关联'}
          level={personal?.available ? personalLevel : 'low'}
          subtitle={personal?.available
            ? `Excess ${numberCompact(personal.excess_liquidity, 'USD')} / EWL`
            : personal?.setup_hint || '先在实盘诊断保存账户快照'}
          meta={personal?.as_of ? `快照 ${personal.as_of}` : '不接正式账户 API'}
        />
        <DecisionCard
          title="穿透后有效杠杆"
          value={personal?.available && personal.effective_leverage != null ? personal.effective_leverage.toFixed(2) : '—'}
          unit={personal?.available ? '× 净值' : undefined}
          level={(personal?.effective_leverage ?? 0) >= 1.5 ? 'high' : (personal?.effective_leverage ?? 0) >= 1.15 ? 'mid' : 'low'}
          subtitle={personal?.available
            ? `Broker ${personal.broker_leverage?.toFixed(2) ?? '—'}× + ETF embedded`
            : '需要持仓市值与 2X/3X 产品映射'}
          meta={personal?.available ? `融资负债 ${numberCompact(personal.margin_debt, 'USD')}` : undefined}
        />
        <DecisionCard
          title="当前风控姿态"
          value={data.posture?.label || '—'}
          level={data.posture?.level}
          subtitle={data.posture?.reason || '等待更多证据'}
          meta="Guardrail，不是自动买卖信号"
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <MarketCard market="US" data={data.markets.us} />
        <MarketCard market="KR" data={data.markets.kr} />
        <HistoryCard history={data.history ?? []} />
      </div>

      <div>
        <div className="flex items-end justify-between mb-2">
          <div>
            <h2 className="text-sm font-semibold text-white">融资与信用杠杆 · 慢变量</h2>
            <p className="text-xs text-slate-500 mt-0.5">高位代表脆弱性，环比收缩才更接近正在发生的 deleveraging。</p>
          </div>
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <FundingCard market="US" data={data.funding.us} />
          <FundingCard market="KR" data={data.funding.kr} />
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.65fr_1fr] gap-4">
        <EvidenceLedger rows={data.evidence ?? []} />
        <InterpretationCard data={data} />
      </div>

      <ProductTable rows={data.products} />

      {/* 说明 */}
      <div className="text-sm text-slate-400 space-y-1">
        <div>· <span className="text-slate-300">二维状态</span> 把慢变量 Crowding 与快变量 Unwind Trigger 分开；FINRA/KOFIA 融资数据不再混入盘中触发分数。</div>
        <div>· <span className="text-slate-300">Evidence coverage</span> 使用固定权重；缺数据会降低覆盖率与置信度，不会把少量可用指标重新归一化成 100 分。</div>
        <div>· <span className="text-slate-300">个人账户</span> 只读取本地“实盘诊断”快照；穿透杠杆自动识别注册表中的 MUU、RAM、ARMG 等每日 2X/3X 产品，不连接正式账户 API。</div>
        <div>· <span className="text-slate-300">Tracking drag</span> 按“每日目标倍数”逐日复利计算，不用错误的“20 日指数涨幅 × 2/3”近似；震荡行情中的 volatility decay 会自然反映出来。</div>
        <div>· <span className="text-slate-300">成交 pace</span> 在交易时段内按已过时间比例折算，标有“估”的值只作 preliminary 展示、不进入正式 Trigger；yfinance 免费行情不能当作交易所 real-time feed。</div>
        {data.warnings.map((warning, i) => <div key={i}>· {warning}</div>)}
      </div>
    </div>
  )
}
