/**
 * A 股主题股票池基本面研究。
 *
 * 这是一个 research-only 视图：不参与动能评分、回测或调仓计划。
 * Summary 只在用户切到 Tab 后加载；单股详情和历史 PE 按需加载。
 */
import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import type { EChartsType } from 'echarts'
import type { ReactNode } from 'react'
import { getAStockFundamentalDetail, getAStockFundamentals } from '../api/client'
import SymbolLink from './SymbolLink'

type SourceState = {
  name?: string
  status?: 'ok' | 'partial' | 'unavailable' | string
  fetched_at?: string | null
  data_as_of?: string | null
  delay?: string | null
  coverage?: number
  total?: number
  stale?: boolean
  error?: string | null
  field_coverage?: Record<string, number>
  fallback_used?: boolean
  fallback_from?: string | null
  notes?: string | null
}

type FundamentalRow = {
  symbol: string
  name: string
  group?: string | null
  group_label?: string | null
  subcat?: string | null
  subcat_label?: string | null
  price?: number | null
  quote_change_pct?: number | null
  total_market_cap_yi?: number | null
  float_market_cap_yi?: number | null
  pe_ttm?: number | null
  pe_dynamic?: number | null
  pb?: number | null
  pe_state?: string | null
  earnings_yield?: number | null
  quality_status?: string | null
  ttm_growth_state?: string | null
  ttm_revenue_yi?: number | null
  ttm_net_profit_yi?: number | null
  ttm_revenue_yoy?: number | null
  ttm_net_profit_yoy?: number | null
  latest_report_period?: string | null
  latest_report_label?: string | null
  latest_announcement_date?: string | null
  announcement_date?: string | null
  revenue_yi?: number | null
  revenue_yoy?: number | null
  net_profit_yi?: number | null
  net_profit_yoy?: number | null
  eps?: number | null
  roe?: number | null
  gross_margin?: number | null
  financial_period_count?: number
  financial_status?: string | null
}

type ReportPeriod = FundamentalRow & {
  report_period: string
  report_label: string
  report_year?: number | null
  report_quarter?: number | null
  is_cumulative?: boolean
  single_quarter_revenue_yi?: number | null
  single_quarter_net_profit_yi?: number | null
  single_quarter_derivable?: boolean
}

type SummaryResponse = {
  status?: string
  as_of?: string | null
  last_updated?: string | null
  universe_count?: number
  coverage?: { valuation?: number; financial?: number }
  source_status?: { valuation?: SourceState; financial?: SourceState }
  rows?: FundamentalRow[]
}

type DetailResponse = {
  status?: string
  as_of?: string | null
  symbol: string
  summary: FundamentalRow
  periods?: ReportPeriod[]
  pe_history?: Array<{ date: string; value?: number | null }>
  pe_percentile_3y?: number | null
  source_status?: {
    summary?: { valuation?: SourceState; financial?: SourceState }
    pe_history?: SourceState
  }
}

type SortKey = 'earnings_yield' | 'pe_ttm' | 'pe_dynamic' | 'total_market_cap_yi' | 'ttm_revenue_yoy' | 'ttm_net_profit_yoy' | 'roe' | 'gross_margin'

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

function numFmt(v: number | null | undefined, digits = 1, suffix = '') {
  return finite(v) ? `${v.toFixed(digits)}${suffix}` : '—'
}

function moneyFmt(v: number | null | undefined) {
  if (!finite(v)) return '—'
  if (v >= 10_000) return `${(v / 10_000).toFixed(2)}万亿`
  if (v >= 100) return `${v.toFixed(0)}亿`
  return `${v.toFixed(1)}亿`
}

function pctFmt(v: number | null | undefined, digits = 1) {
  return finite(v) ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%` : '—'
}

function earningsYieldFmt(v: number | null | undefined) {
  if (!finite(v)) return '—'
  const percent = v * 100
  const digits = Math.abs(percent) < 0.1 ? 3 : Math.abs(percent) < 10 ? 2 : 1
  return `${percent >= 0 ? '+' : ''}${percent.toFixed(digits)}%`
}

function pctClass(v: number | null | undefined) {
  if (!finite(v)) return 'text-slate-500'
  if (v > 0) return 'text-emerald-400'
  if (v < 0) return 'text-red-400'
  return 'text-slate-400'
}

function dateFmt(value?: string | null) {
  return value ? value.slice(0, 10) : '—'
}

function sourceLabel(source?: SourceState) {
  if (!source) return '未知'
  if (source.fallback_used) return '备用源'
  if (source.status === 'ok' && !source.stale) return '正常'
  if (source.stale) return '过期缓存'
  if (source.status === 'partial') return '部分可用'
  return '不可用'
}

function statusClass(source?: SourceState) {
  if (source?.fallback_used) return 'text-amber-400'
  if (source?.status === 'ok' && !source.stale) return 'text-emerald-400'
  if (source?.status === 'partial' || source?.stale) return 'text-amber-400'
  return 'text-red-400'
}

function qualityLabel(status?: string | null) {
  if (status === 'strong') return '盈利质量强'
  if (status === 'mixed') return '盈利质量中等'
  if (status === 'weak') return '盈利质量偏弱'
  if (status === 'loss_making') return 'TTM 亏损'
  return '质量缺失'
}

function qualityColor(status?: string | null) {
  if (status === 'strong') return '#10b981'
  if (status === 'mixed') return '#f59e0b'
  if (status === 'weak') return '#64748b'
  if (status === 'loss_making') return '#ef4444'
  return '#94a3b8'
}

function PeState({ state }: { state?: string | null }) {
  if (state === 'loss_making') return <span className="text-red-400">亏损</span>
  if (state === 'zero_earnings') return <span className="text-amber-400">零利润</span>
  if (state === 'not_derivable') return <span className="text-amber-400">不可推导</span>
  if (state === 'missing') return <span className="text-slate-500">缺失</span>
  return <span className="font-mono text-slate-200">{state === 'valid' ? '有效' : '—'}</span>
}

function Metric({ label, value, className = '' }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-slate-500">{label}</span>
      <span className={`font-mono ${className}`}>{value}</span>
    </div>
  )
}

function ReportChart({ periods, mode }: { periods: ReportPeriod[]; mode: 'cumulative' | 'single' }) {
  if (!periods.length) return <div className="py-8 text-center text-sm text-slate-500">暂无可用报告期数据</div>
  const revenueKey = mode === 'cumulative' ? 'revenue_yi' : 'single_quarter_revenue_yi'
  const profitKey = mode === 'cumulative' ? 'net_profit_yi' : 'single_quarter_net_profit_yi'
  const label = mode === 'cumulative' ? '累计值' : '单季度推导值'
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 48, top: 36, bottom: 28 },
    legend: { data: ['营业收入', '净利润'], top: 2, textStyle: { color: '#64748b', fontSize: 10 }, itemHeight: 8 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: unknown) => finite(v) ? `${Number(v).toFixed(2)}亿` : '—',
    },
    xAxis: {
      type: 'category', data: periods.map(p => p.report_label),
      axisLabel: { color: '#64748b', fontSize: 10 }, axisLine: { lineStyle: { color: '#64748b' } },
    },
    yAxis: {
      type: 'value', name: '亿元', nameTextStyle: { color: '#64748b', fontSize: 10 },
      axisLabel: { color: '#64748b', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.25)' } },
    },
    series: [
      { name: '营业收入', type: 'bar', barMaxWidth: 24, data: periods.map(p => p[revenueKey as keyof ReportPeriod] ?? null), itemStyle: { color: '#2563eb' } },
      { name: '净利润', type: 'line', smooth: true, symbolSize: 6, data: periods.map(p => p[profitKey as keyof ReportPeriod] ?? null), itemStyle: { color: '#059669' }, lineStyle: { color: '#059669', width: 2 } },
    ],
  }
  return (
    <div>
      <div className="text-xs text-slate-500 mb-1">报告期趋势 · {label}</div>
      <ReactECharts option={option} style={{ height: 250 }} />
    </div>
  )
}

function PeHistoryChart({ history }: { history: Array<{ date: string; value?: number | null }> }) {
  const usable = history.filter(p => finite(p.value))
  if (!usable.length) return <div className="py-8 text-center text-sm text-slate-500">暂无历史 PE(TTM) 数据</div>
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 42, right: 12, top: 14, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => finite(v) ? `${Number(v).toFixed(1)}x` : '—' },
    xAxis: { type: 'category', data: usable.map(p => p.date), axisLabel: { color: '#64748b', fontSize: 9 }, axisLine: { lineStyle: { color: '#64748b' } } },
    yAxis: { type: 'value', axisLabel: { color: '#64748b', fontSize: 10, formatter: '{value}x' }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.25)' } } },
    series: [{ type: 'line', smooth: true, symbol: 'none', data: usable.map(p => p.value), lineStyle: { color: '#f59e0b', width: 2 }, areaStyle: { color: 'rgba(245,158,11,0.12)' } }],
  }
  return <ReactECharts option={option} style={{ height: 210 }} />
}

type ScatterPoint = {
  value: [number, number]
  symbol: string
  name: string
  row: FundamentalRow
  symbolSize: number
  itemStyle: { color: string; opacity: number; borderColor?: string; borderWidth?: number }
  label?: {
    show: boolean
    formatter: string
    position: 'top'
    color: string
    fontSize: number
    backgroundColor: string
    borderRadius: number
    padding: number[]
  }
}

type ScatterEvent = { data?: ScatterPoint }

function median(values: number[]) {
  if (!values.length) return null
  const sorted = values.slice().sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

function quantile(values: number[], ratio: number) {
  if (!values.length) return null
  const sorted = values.slice().sort((a, b) => a - b)
  const position = (sorted.length - 1) * ratio
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  if (lower === upper) return sorted[lower]
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower)
}

function focusRange(values: number[]): [number, number] | null {
  if (values.length < 10) return null
  const low = quantile(values, 0.03)
  const high = quantile(values, 0.97)
  if (low == null || high == null || high <= low) return null
  const padding = (high - low) * 0.06
  return [low - padding, high + padding]
}

function ScatterResearchChart({
  rows,
  growthAxis,
  selectedSymbol,
  onSelect,
}: {
  rows: FundamentalRow[]
  growthAxis: 'revenue' | 'profit'
  selectedSymbol: string | null
  onSelect: (symbol: string) => void
}) {
  const chartRef = useRef<EChartsType | null>(null)
  const [rangeMode, setRangeMode] = useState<'focus' | 'all'>('focus')
  const [zoomSelecting, setZoomSelecting] = useState(false)
  const xLabel = growthAxis === 'revenue' ? 'TTM 营收同比' : 'TTM 净利同比'
  const points = useMemo(() => {
    const usable = rows.filter(row => {
      const growth = growthAxis === 'revenue' ? row.ttm_revenue_yoy : row.ttm_net_profit_yoy
      return finite(growth) && finite(row.earnings_yield)
    })
    return usable.map((row): ScatterPoint => {
      const growth = (growthAxis === 'revenue' ? row.ttm_revenue_yoy : row.ttm_net_profit_yoy) as number
      const earningsYield = row.earnings_yield as number
      const selected = row.symbol === selectedSymbol
      return {
        value: [growth, earningsYield],
        symbol: row.symbol,
        name: row.name,
        row,
        symbolSize: selected ? 11 : 7,
        itemStyle: {
          color: qualityColor(row.quality_status),
          opacity: selected ? 1 : 0.66,
          ...(selected ? { borderColor: '#f8fafc', borderWidth: 2 } : {}),
        },
        ...(selected ? {
          label: {
            show: true,
            formatter: `${row.name} ${row.symbol}`,
            position: 'top',
            color: '#f8fafc',
            fontSize: 10,
            backgroundColor: 'rgba(15,23,42,0.88)',
            borderRadius: 3,
            padding: [3, 5],
          },
        } : {}),
      }
    })
  }, [rows, growthAxis, selectedSymbol])

  const medianX = median(points.map(point => point.value[0]))
  const medianY = median(points.map(point => point.value[1]))
  const bounds = useMemo(() => ({
    x: focusRange(points.map(point => point.value[0])),
    y: focusRange(points.map(point => point.value[1])),
  }), [points])
  const selectedPoint = useMemo(
    () => points.find(point => point.symbol === selectedSymbol) ?? null,
    [points, selectedSymbol],
  )
  const visiblePointCount = useMemo(() => {
    if (rangeMode === 'all' || !bounds.x || !bounds.y) return points.length
    return points.filter(point => (
      point.value[0] >= bounds.x![0] && point.value[0] <= bounds.x![1]
      && point.value[1] >= bounds.y![0] && point.value[1] <= bounds.y![1]
    )).length
  }, [points, bounds, rangeMode])
  const quadrantCounts = useMemo(() => {
    if (medianX == null || medianY == null) return { topRight: 0, highGrowth: 0, cheap: 0, weak: 0 }
    return points.reduce((counts, point) => {
      const highGrowth = point.value[0] >= medianX
      const highYield = point.value[1] >= medianY
      if (highGrowth && highYield) counts.topRight += 1
      else if (highGrowth) counts.highGrowth += 1
      else if (highYield) counts.cheap += 1
      else counts.weak += 1
      return counts
    }, { topRight: 0, highGrowth: 0, cheap: 0, weak: 0 })
  }, [points, medianX, medianY])

  if (!points.length) {
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-8 text-center text-sm text-slate-500">
        当前筛选条件下没有同时具备 {xLabel} 和盈利收益率的数据点；缺失值不会用 0 填充。
      </div>
    )
  }

  const focused = rangeMode === 'focus'
  const changeRangeMode = (mode: 'focus' | 'all') => {
    setZoomSelecting(false)
    chartRef.current?.dispatchAction({
      type: 'takeGlobalCursor',
      key: 'dataZoomSelect',
      dataZoomSelectActive: false,
    })
    setRangeMode(mode)
  }
  const toggleBoxZoom = () => {
    const next = !zoomSelecting
    setZoomSelecting(next)
    chartRef.current?.dispatchAction({
      type: 'takeGlobalCursor',
      key: 'dataZoomSelect',
      dataZoomSelectActive: next,
    })
  }
  const resetChart = () => {
    setZoomSelecting(false)
    chartRef.current?.dispatchAction({
      type: 'takeGlobalCursor',
      key: 'dataZoomSelect',
      dataZoomSelectActive: false,
    })
    chartRef.current?.dispatchAction({ type: 'restore' })
  }

  const option = {
    backgroundColor: 'transparent',
    animationDurationUpdate: 180,
    aria: {
      enabled: true,
      description: `A 股估值地图，横轴为${xLabel}，纵轴为盈利收益率，共 ${points.length} 个可用数据点。`,
    },
    grid: { left: 68, right: 52, top: 44, bottom: 70 },
    tooltip: {
      trigger: 'item',
      confine: true,
      extraCssText: 'max-width: 300px; line-height: 1.6;',
      formatter: (params: unknown) => {
        const item = (Array.isArray(params) ? params[0] : params) as ScatterEvent | undefined
        const point = item?.data
        if (!point) return ''
        const row = point.row
        const growth = growthAxis === 'revenue' ? row.ttm_revenue_yoy : row.ttm_net_profit_yoy
        return [
          `<strong>${row.name}（${row.symbol}）</strong>`,
          `${xLabel}：${pctFmt(growth)}`,
          `盈利收益率 E/P：${earningsYieldFmt(row.earnings_yield)}`,
          `PE(TTM)：${finite(row.pe_ttm) ? `${row.pe_ttm.toFixed(1)}x` : '亏损/缺失'}`,
          `总市值：${moneyFmt(row.total_market_cap_yi)}`,
          `ROE：${pctFmt(row.roe)} · ${qualityLabel(row.quality_status)}`,
          '<span style="color:#94a3b8">点击固定选择并加载财报详情</span>',
        ].join('<br/>')
      },
    },
    toolbox: {
      show: true,
      right: 48,
      top: 4,
      itemSize: 15,
      itemGap: 8,
      iconStyle: { borderColor: '#94a3b8' },
      emphasis: { iconStyle: { borderColor: '#e2e8f0' } },
      feature: {
        dataZoom: {
          title: { zoom: '框选放大', back: '退回上一步' },
          xAxisIndex: 0,
          yAxisIndex: 0,
          filterMode: 'none',
          brushStyle: { color: 'rgba(59,130,246,0.12)', borderColor: '#60a5fa', borderWidth: 1 },
        },
      },
    },
    xAxis: {
      type: 'value',
      scale: true,
      min: focused && bounds.x ? bounds.x[0] : undefined,
      max: focused && bounds.x ? bounds.x[1] : undefined,
      name: xLabel,
      nameLocation: 'middle',
      nameGap: 34,
      nameTextStyle: { color: '#94a3b8', fontSize: 11 },
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (value: number) => pctFmt(value, 1) },
      axisLine: { lineStyle: { color: '#64748b' } },
      splitLine: { lineStyle: { color: 'rgba(100,116,139,0.18)' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      min: focused && bounds.y ? bounds.y[0] : undefined,
      max: focused && bounds.y ? bounds.y[1] : undefined,
      name: '盈利收益率 E/P',
      nameLocation: 'middle',
      nameGap: 46,
      nameTextStyle: { color: '#94a3b8', fontSize: 11 },
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (value: number) => earningsYieldFmt(value) },
      axisLine: { lineStyle: { color: '#64748b' } },
      splitLine: { lineStyle: { color: 'rgba(100,116,139,0.18)' } },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        yAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: true,
        throttle: 40,
      },
      {
        type: 'slider',
        xAxisIndex: 0,
        filterMode: 'none',
        left: 68,
        right: 52,
        bottom: 8,
        height: 16,
        showDetail: false,
        showDataShadow: false,
        brushSelect: true,
        borderColor: 'rgba(100,116,139,0.45)',
        backgroundColor: 'rgba(15,23,42,0.18)',
        fillerColor: 'rgba(59,130,246,0.18)',
        handleSize: '90%',
        handleStyle: { color: '#64748b', borderColor: '#94a3b8' },
        moveHandleSize: 5,
      },
      {
        type: 'slider',
        yAxisIndex: 0,
        orient: 'vertical',
        filterMode: 'none',
        right: 8,
        top: 44,
        bottom: 70,
        width: 16,
        showDetail: false,
        showDataShadow: false,
        brushSelect: true,
        borderColor: 'rgba(100,116,139,0.45)',
        backgroundColor: 'rgba(15,23,42,0.18)',
        fillerColor: 'rgba(59,130,246,0.18)',
        handleSize: '90%',
        handleStyle: { color: '#64748b', borderColor: '#94a3b8' },
        moveHandleSize: 5,
      },
    ],
    series: [{
      type: 'scatter',
      symbol: 'circle',
      data: points,
      cursor: 'pointer',
      progressive: 600,
      progressiveThreshold: 1_000,
      emphasis: {
        focus: 'self',
        scale: 1.8,
        itemStyle: { opacity: 1, borderColor: '#f8fafc', borderWidth: 1.5 },
        label: {
          show: true,
          formatter: (params: ScatterEvent) => params.data ? `${params.data.name} ${params.data.symbol}` : '',
          position: 'top',
          color: '#f8fafc',
          fontSize: 10,
          backgroundColor: 'rgba(15,23,42,0.88)',
          borderRadius: 3,
          padding: [3, 5],
        },
      },
      markLine: {
        silent: true,
        symbol: 'none',
        label: { show: false },
        lineStyle: { color: 'rgba(148,163,184,0.55)', type: 'dashed', width: 1 },
        data: [
          { xAxis: 0 },
          { yAxis: 0 },
          ...(medianX == null ? [] : [{ xAxis: medianX }]),
          ...(medianY == null ? [] : [{ yAxis: medianY }]),
        ],
      },
    }],
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="text-sm font-medium text-slate-200">估值地图 · {xLabel} × E/P</div>
          <div className="text-xs text-slate-500 mt-0.5">统一小点 · 颜色 = 盈利质量 · 市值保留在悬停信息中</div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2 text-xs">
          <div className="flex rounded overflow-hidden border border-slate-600">
            {([['focus', '主体区'], ['all', '全量']] as const).map(([key, label]) => (
              <button key={key} onClick={() => changeRangeMode(key)}
                className={`px-2.5 py-1.5 transition-colors ${rangeMode === key ? 'bg-slate-600 text-slate-100' : 'bg-slate-900 text-slate-400 hover:bg-slate-700'}`}>
                {label}
              </button>
            ))}
          </div>
          <button onClick={toggleBoxZoom}
            className={`px-2.5 py-1.5 rounded border transition-colors ${zoomSelecting ? 'border-blue-400 bg-blue-500/20 text-blue-200' : 'border-slate-600 bg-slate-900 text-slate-400 hover:bg-slate-700'}`}>
            {zoomSelecting ? '框选中…' : '框选放大'}
          </button>
          <button onClick={resetChart}
            className="px-2.5 py-1.5 rounded border border-slate-600 bg-slate-900 text-slate-400 hover:bg-slate-700 transition-colors">
            复位
          </button>
          <span className="text-slate-400 whitespace-nowrap">显示 {visiblePointCount} / 可用 {points.length}</span>
        </div>
      </div>
      <div className="rounded border border-slate-700/70 bg-slate-900/30 px-2.5 py-2 text-xs text-slate-400 flex flex-wrap gap-x-4 gap-y-1">
        <span>滚轮：双轴缩放</span>
        <span>拖动图面：平移</span>
        <span>底部 / 右侧范围条：单轴缩放与移动</span>
        {focused && visiblePointCount < points.length && <span className="text-amber-400">主体区按 3%–97% 分位聚焦，{points.length - visiblePointCount} 个极端点可在“全量”查看</span>}
      </div>
      {selectedPoint && (
        <div className="rounded border border-blue-500/40 bg-blue-500/5 px-2.5 py-2 text-xs flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-blue-300">已选</span>
          <SymbolLink symbol={selectedPoint.symbol} market="a" className="font-medium text-slate-200">
            {selectedPoint.name}（{selectedPoint.symbol}）
          </SymbolLink>
          <span className="text-slate-400">{xLabel} {pctFmt(selectedPoint.value[0])}</span>
          <span className="text-slate-400">E/P {earningsYieldFmt(selectedPoint.value[1])}</span>
          <span className="text-slate-500">下方同步加载财报详情</span>
        </div>
      )}
      <ReactECharts
        key={`${growthAxis}-${rangeMode}`}
        option={option}
        style={{ height: 470 }}
        onChartReady={(instance) => { chartRef.current = instance }}
        onEvents={{ click: (params: ScatterEvent) => { if (params.data?.symbol) onSelect(params.data.symbol) } }}
      />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
        {[
          ['右上 · 增长 + 便宜', '优先研究', quadrantCounts.topRight, 'border-emerald-500/40 bg-emerald-500/5'],
          ['右下 · 增长 + 贵', '高增长高估', quadrantCounts.highGrowth, 'border-amber-500/40 bg-amber-500/5'],
          ['左上 · 低增长 + 便宜', '便宜但需核验', quadrantCounts.cheap, 'border-blue-500/40 bg-blue-500/5'],
          ['左下 · 低增长 + 贵', '相对弱', quadrantCounts.weak, 'border-slate-600 bg-slate-900/30'],
        ].map(([title, subtitle, count, className]) => (
          <div key={title as string} className={`rounded border px-2.5 py-2 ${className as string}`}>
            <div className="text-slate-300">{title as string}</div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-slate-500">{subtitle as string}</span>
              <span className="font-mono text-slate-200">{count as number}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        <span><i className="inline-block w-2 h-2 rounded-full bg-emerald-400 mr-1" />盈利质量强</span>
        <span><i className="inline-block w-2 h-2 rounded-full bg-amber-400 mr-1" />中等</span>
        <span><i className="inline-block w-2 h-2 rounded-full bg-slate-400 mr-1" />偏弱/缺失</span>
        <span><i className="inline-block w-2 h-2 rounded-full bg-red-400 mr-1" />TTM 亏损</span>
        <span className="text-slate-400">右上角只是研究优先级，不是自动买入信号；缩放不会删除或改写数据。</span>
      </div>
    </div>
  )
}

function DetailPanel({ detail, mode, setMode }: { detail: DetailResponse; mode: 'cumulative' | 'single'; setMode: (mode: 'cumulative' | 'single') => void }) {
  const row = detail.summary
  const periods = detail.periods ?? []
  const latest = periods[periods.length - 1]
  const detailSources = detail.source_status?.summary
  return (
    <div className="bg-slate-800 rounded-lg border border-blue-500/50 p-4 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-baseline gap-2">
            <SymbolLink symbol={row.symbol} market="a" className="text-lg font-semibold text-white">{row.name}（{row.symbol}）</SymbolLink>
            <span className="text-xs text-slate-400">{row.group_label ?? '—'} · {row.subcat_label ?? '—'}</span>
          </div>
          <div className="text-xs text-slate-500 mt-1">最新报告：{row.latest_report_label ?? '—'} · 公告：{dateFmt(row.latest_announcement_date)}</div>
        </div>
        <div className="text-right text-xs text-slate-500">
          <div>估值状态：<span className={statusClass(detailSources?.valuation)}>{sourceLabel(detailSources?.valuation)}</span></div>
          <div>财报状态：<span className={statusClass(detailSources?.financial)}>{sourceLabel(detailSources?.financial)}</span></div>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-10 gap-x-4 gap-y-2 text-xs">
        <Metric label="PE(TTM)" value={row.pe_ttm != null ? `${numFmt(row.pe_ttm, 1)}x` : <PeState state={row.pe_state} />} />
        <Metric label="盈利收益率 E/P" value={earningsYieldFmt(row.earnings_yield)} className={pctClass(row.earnings_yield)} />
        <Metric label="动态PE" value={row.pe_dynamic != null ? `${numFmt(row.pe_dynamic, 1)}x` : '—'} />
        <Metric label="PB" value={row.pb != null ? `${numFmt(row.pb, 2)}x` : '—'} />
        <Metric label="总市值" value={moneyFmt(row.total_market_cap_yi)} />
        <Metric label="TTM营收同比" value={pctFmt(row.ttm_revenue_yoy)} className={pctClass(row.ttm_revenue_yoy)} />
        <Metric label="TTM净利同比" value={pctFmt(row.ttm_net_profit_yoy)} className={pctClass(row.ttm_net_profit_yoy)} />
        <Metric label="盈利质量" value={qualityLabel(row.quality_status)} />
        <Metric label="毛利率" value={pctFmt(row.gross_margin)} />
        <Metric label="ROE" value={pctFmt(row.roe)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,1fr)]">
        <div className="bg-slate-900/40 border border-slate-700/60 rounded-lg p-3">
          <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
            <div className="text-sm font-medium text-slate-300">营收 / 净利润</div>
            <div className="flex rounded overflow-hidden border border-slate-700 text-xs">
              {([['cumulative', '累计值'], ['single', '单季度']] as const).map(([key, label]) => (
                <button key={key} onClick={() => setMode(key)}
                  className={`px-2.5 py-1 ${mode === key ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {mode === 'single' && periods.some(p => !p.single_quarter_derivable) && (
            <div className="text-[11px] text-amber-400 mb-1">部分报告期缺少相邻累计值，单季度值会显示为空。</div>
          )}
          <ReportChart periods={periods} mode={mode} />
        </div>
        <div className="bg-slate-900/40 border border-slate-700/60 rounded-lg p-3">
          <div className="flex items-baseline justify-between mb-1">
            <div className="text-sm font-medium text-slate-300">PE(TTM) 历史</div>
            <div className="text-xs text-slate-400">
              percentile {detail.pe_percentile_3y != null ? `${(detail.pe_percentile_3y * 100).toFixed(0)}%` : '—'}
            </div>
          </div>
          <PeHistoryChart history={detail.pe_history ?? []} />
        </div>
      </div>

      <div className="overflow-x-auto border border-slate-700/60 rounded-lg">
        <table className="w-full min-w-[720px] text-xs">
          <thead className="bg-slate-900/60 text-slate-500">
            <tr>
              <th className="text-left px-3 py-2 font-normal">报告期</th>
              <th className="text-right px-2 py-2 font-normal">营收累计</th>
              <th className="text-right px-2 py-2 font-normal">营收单季</th>
              <th className="text-right px-2 py-2 font-normal">净利累计</th>
              <th className="text-right px-2 py-2 font-normal">净利单季</th>
              <th className="text-right px-2 py-2 font-normal">营收同比</th>
              <th className="text-right px-2 py-2 font-normal">净利同比</th>
              <th className="text-right px-2 py-2 font-normal">公告日期</th>
            </tr>
          </thead>
          <tbody>
            {periods.slice().reverse().map(p => (
              <tr key={p.report_period} className="border-t border-slate-700/50">
                <td className="px-3 py-2 text-slate-300">{p.report_label}</td>
                <td className="px-2 py-2 text-right font-mono text-slate-300">{moneyFmt(p.revenue_yi)}</td>
                <td className={`px-2 py-2 text-right font-mono ${p.single_quarter_derivable ? 'text-slate-300' : 'text-slate-600'}`}>{moneyFmt(p.single_quarter_revenue_yi)}</td>
                <td className={`px-2 py-2 text-right font-mono ${finite(p.net_profit_yi) && p.net_profit_yi < 0 ? 'text-red-400' : 'text-slate-300'}`}>{moneyFmt(p.net_profit_yi)}</td>
                <td className={`px-2 py-2 text-right font-mono ${p.single_quarter_derivable ? 'text-slate-300' : 'text-slate-600'}`}>{moneyFmt(p.single_quarter_net_profit_yi)}</td>
                <td className={`px-2 py-2 text-right font-mono ${pctClass(p.revenue_yoy)}`}>{pctFmt(p.revenue_yoy)}</td>
                <td className={`px-2 py-2 text-right font-mono ${pctClass(p.net_profit_yoy)}`}>{pctFmt(p.net_profit_yoy)}</td>
                <td className="px-2 py-2 text-right font-mono text-slate-400">{dateFmt(p.announcement_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-sm text-slate-400 space-y-1">
        <div>· 累计值来自披露报告期；单季度值由相邻累计值相减推导，不可推导时保留为空。</div>
        <div>· PE(TTM) = 总市值 ÷ 最近十二个月净利润；亏损或零利润公司不显示负数 PE。</div>
        <div>· 历史 PE 仅使用正 PE 观测值计算 percentile；异常值和源站缺失以数据状态为准。</div>
        {latest?.announcement_date && <div>· 最近一条报告公告日期：{dateFmt(latest.announcement_date)}。</div>}
      </div>
    </div>
  )
}

export default function AStockFundamentalResearch() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [group, setGroup] = useState('all')
  const [subcat, setSubcat] = useState('all')
  const [profitState, setProfitState] = useState<'all' | 'valid' | 'loss_making' | 'missing'>('all')
  const [peMax, setPeMax] = useState('')
  const [revenueMin, setRevenueMin] = useState('')
  const [profitMin, setProfitMin] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('earnings_yield')
  const [sortDesc, setSortDesc] = useState(true)
  const [researchView, setResearchView] = useState<'scatter' | 'table'>('scatter')
  const [growthAxis, setGrowthAxis] = useState<'revenue' | 'profit'>('revenue')
  const [selected, setSelected] = useState<string | null>(null)
  const [reportMode, setReportMode] = useState<'cumulative' | 'single'>('cumulative')
  const [forcing, setForcing] = useState(false)

  const { data, isLoading, isFetching, error } = useQuery<SummaryResponse>({
    queryKey: ['astock-fundamentals'],
    queryFn: () => getAStockFundamentals(false),
    staleTime: 15 * 60_000,
    refetchInterval: 15 * 60_000,
    retry: false,
  })

  const { data: detail, isLoading: detailLoading, error: detailError } = useQuery<DetailResponse>({
    queryKey: ['astock-fundamental-detail', selected],
    queryFn: () => getAStockFundamentalDetail(selected as string),
    enabled: Boolean(selected),
    staleTime: 24 * 60 * 60_000,
    retry: false,
  })

  const rows = useMemo(() => data?.rows ?? [], [data?.rows])
  const groupOptions = useMemo(() => Array.from(new Map(rows.map(r => [r.group, r.group_label])).entries()).filter(([key]) => key), [rows])
  const subcatOptions = useMemo(() => {
    const scope = group === 'all' ? rows : rows.filter(r => r.group === group)
    return Array.from(new Map(scope.map(r => [r.subcat, r.subcat_label])).entries()).filter(([key]) => key)
  }, [rows, group])

  const toNumber = (value: string) => {
    if (!value.trim()) return null
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  const peLimit = toNumber(peMax)
  const revenueFloor = toNumber(revenueMin)
  const profitFloor = toNumber(profitMin)

  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return rows.filter(r => {
      if (needle && !`${r.symbol} ${r.name}`.toLowerCase().includes(needle)) return false
      if (group !== 'all' && r.group !== group) return false
      if (subcat !== 'all' && r.subcat !== subcat) return false
      const normalizedProfitState = r.pe_state === 'valid'
        ? 'valid'
        : r.pe_state === 'loss_making'
          ? 'loss_making'
          : 'missing'
      if (profitState !== 'all' && normalizedProfitState !== profitState) return false
      if (peLimit != null && (!finite(r.pe_ttm) || r.pe_ttm > peLimit)) return false
      if (revenueFloor != null && (!finite(r.ttm_revenue_yoy) || r.ttm_revenue_yoy < revenueFloor / 100)) return false
      if (profitFloor != null && (!finite(r.ttm_net_profit_yoy) || r.ttm_net_profit_yoy < profitFloor / 100)) return false
      return true
    }).sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (!finite(av) && !finite(bv)) return a.symbol.localeCompare(b.symbol)
      if (!finite(av)) return 1
      if (!finite(bv)) return -1
      return sortDesc ? bv - av : av - bv
    })
  }, [rows, search, group, subcat, profitState, peLimit, revenueFloor, profitFloor, sortKey, sortDesc])

  const refresh = async () => {
    setForcing(true)
    try {
      const fresh = await getAStockFundamentals(true)
      qc.setQueryData(['astock-fundamentals'], fresh)
    } finally {
      setForcing(false)
    }
  }

  const chooseSort = (key: SortKey) => {
    if (sortKey === key) setSortDesc(v => !v)
    else { setSortKey(key); setSortDesc(true) }
  }

  const valuationSource = data?.source_status?.valuation
  const financialSource = data?.source_status?.financial
  const statusText = isLoading ? '加载中' : data?.status === 'ok' ? '正常' : data?.status === 'partial' ? '部分可用' : '不可用'
  const statusColor = isLoading ? 'text-slate-400' : data?.status === 'ok' ? 'text-emerald-400' : data?.status === 'partial' ? 'text-amber-400' : 'text-red-400'
  const universeLabel = isLoading ? '—' : String(data?.universe_count ?? rows.length)
  const valuationCoverage = isLoading ? '—' : `${data?.coverage?.valuation ?? 0}/${data?.universe_count ?? rows.length}`
  const financialCoverage = isLoading ? '—' : `${data?.coverage?.financial ?? 0}/${data?.universe_count ?? rows.length}`

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold text-slate-200">📚 基本面研究</h2>
          <p className="text-xs text-slate-500 mt-0.5">当前 A 股 AI/硬件主题池 · 估值地图（TTM 增长 × 盈利收益率）+ 财报详情</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {data?.as_of && <span>抓取于 {dateFmt(data.as_of)} {data.as_of.slice(11, 16)}</span>}
          <button onClick={refresh} disabled={forcing || isFetching}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-300 transition-colors">
            {forcing ? '刷新中…' : '刷新基本面'}
          </button>
        </div>
      </div>

      <div className="bg-slate-900/40 border border-slate-700/60 rounded-lg px-3 py-2 text-xs flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>整体：<span className={statusColor}>{statusText}</span></span>
        <span>股票池：<span className="font-mono text-slate-300">{universeLabel}</span></span>
        <span>估值覆盖：<span className={statusClass(valuationSource)}>{valuationCoverage}</span></span>
        <span>财报覆盖：<span className={statusClass(financialSource)}>{financialCoverage}</span></span>
        <span>估值源：<span className={statusClass(valuationSource)}>{valuationSource?.name ?? 'eastmoney_spot'} · {sourceLabel(valuationSource)}</span></span>
        <span>财报源：<span className={statusClass(financialSource)}>{financialSource?.name ?? 'eastmoney_yjbb'} · {sourceLabel(financialSource)}</span></span>
        {valuationSource?.fallback_used && <span className="text-amber-400">{valuationSource.notes ?? '估值已切换备用源，请注意 PE 口径可能不同'}</span>}
        {!isLoading && data?.status !== 'ok' && <span className="text-amber-400">缺失值保留为 —，不以 0 代替</span>}
      </div>

      {error && <div className="text-sm text-red-400 bg-red-950/20 border border-red-900/50 rounded-lg px-3 py-2">基本面加载失败：{String(error)}</div>}
      {data?.status === 'partial' && <div className="text-sm text-amber-400 bg-amber-950/20 border border-amber-900/50 rounded-lg px-3 py-2">部分数据来自可用缓存或上游覆盖不完整，请结合来源状态和公告日期阅读。</div>}
      {data?.status === 'unavailable' && <div className="text-sm text-red-400 bg-red-950/20 border border-red-900/50 rounded-lg px-3 py-2">当前没有可用基本面数据，请稍后刷新；动能扫描不受影响。</div>}

      <div className="bg-slate-800/60 rounded-lg border border-slate-700 p-3 flex flex-wrap items-center gap-2">
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索名称 / 代码"
          className="w-40 px-2 py-1.5 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
        <select value={group} onChange={e => { setGroup(e.target.value); setSubcat('all') }}
          className="px-2 py-1.5 text-xs bg-slate-900 border border-slate-600 rounded text-slate-300">
          <option value="all">全部板块</option>
          {groupOptions.map(([key, label]) => <option key={key} value={key as string}>{label as string}</option>)}
        </select>
        <select value={subcat} onChange={e => setSubcat(e.target.value)}
          className="px-2 py-1.5 text-xs bg-slate-900 border border-slate-600 rounded text-slate-300">
          <option value="all">全部细分</option>
          {subcatOptions.map(([key, label]) => <option key={key} value={key as string}>{label as string}</option>)}
        </select>
        <select value={profitState} onChange={e => setProfitState(e.target.value as typeof profitState)}
          className="px-2 py-1.5 text-xs bg-slate-900 border border-slate-600 rounded text-slate-300">
          <option value="all">全部盈利状态</option>
          <option value="valid">TTM 盈利</option>
          <option value="loss_making">TTM 亏损</option>
          <option value="missing">TTM 缺失</option>
        </select>
        <label className="flex items-center gap-1 text-xs text-slate-400">PE≤
          <input value={peMax} onChange={e => setPeMax(e.target.value.replace(/[^\d.]/g, ''))} placeholder="不限" inputMode="decimal"
            className="w-16 px-1.5 py-1.5 text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-400">TTM营收同比≥
          <input value={revenueMin} onChange={e => setRevenueMin(e.target.value.replace(/[^\d.-]/g, ''))} placeholder="不限" inputMode="decimal"
            className="w-16 px-1.5 py-1.5 text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />%
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-400">TTM净利同比≥
          <input value={profitMin} onChange={e => setProfitMin(e.target.value.replace(/[^\d.-]/g, ''))} placeholder="不限" inputMode="decimal"
            className="w-16 px-1.5 py-1.5 text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />%
        </label>
        <div className="flex rounded overflow-hidden border border-slate-600 text-xs ml-auto">
          {([['scatter', '估值地图'], ['table', '明细表']] as const).map(([key, label]) => (
            <button key={key} onClick={() => setResearchView(key)}
              className={`px-2.5 py-1.5 transition-colors ${researchView === key ? 'bg-blue-600 text-white' : 'bg-slate-900 text-slate-400 hover:bg-slate-700'}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="flex rounded overflow-hidden border border-slate-600 text-xs">
          <span className="px-2 py-1.5 bg-slate-900 text-slate-500">横轴</span>
          {([['revenue', '营收增长'], ['profit', '净利增长']] as const).map(([key, label]) => (
            <button key={key} onClick={() => setGrowthAxis(key)}
              className={`px-2.5 py-1.5 transition-colors ${growthAxis === key ? 'bg-slate-600 text-slate-100' : 'bg-slate-900 text-slate-400 hover:bg-slate-700'}`}>
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-slate-500">符合 {filteredRows.length} / {rows.length}</span>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-sm text-slate-500">正在加载主题股票池基本面，首次可能需要几十秒…</div>
      ) : researchView === 'scatter' ? (
        <ScatterResearchChart rows={filteredRows} growthAxis={growthAxis} selectedSymbol={selected} onSelect={setSelected} />
      ) : (
        <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto max-h-[62vh]">
          <table className="w-full min-w-[1360px] text-sm">
            <thead className="sticky top-0 z-10 bg-slate-800">
              <tr className="text-xs text-slate-400 border-b border-slate-700">
                <th className="text-left px-3 py-2.5 font-medium">名称（代码）</th>
                <th className="text-left px-2 py-2.5 font-medium">板块 / 细分</th>
                <th className="text-right px-2 py-2.5 font-medium">现价</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('total_market_cap_yi')}>总市值</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('earnings_yield')}>E/P</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('pe_ttm')}>PE(TTM)</th>
                <th title="主源为动态 PE；估值主源不可用时使用腾讯 PE(TTM) 备用值" className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('pe_dynamic')}>动态PE</th>
                <th className="text-right px-2 py-2.5 font-medium">PB</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('ttm_revenue_yoy')}>TTM营收同比</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('ttm_net_profit_yoy')}>TTM净利同比</th>
                <th className="text-right px-2 py-2.5 font-medium">盈利质量</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('gross_margin')}>毛利率</th>
                <th className="text-right px-2 py-2.5 font-medium cursor-pointer" onClick={() => chooseSort('roe')}>ROE</th>
                <th className="text-right px-2 py-2.5 font-medium">最新报告</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map(r => (
                <tr key={r.symbol} onClick={() => setSelected(r.symbol)}
                  className={`border-b border-slate-700/50 hover:bg-slate-750 cursor-pointer transition-colors ${selected === r.symbol ? 'bg-blue-500/10' : ''}`}>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <SymbolLink symbol={r.symbol} market="a" className="text-sm font-semibold text-slate-100">{r.name}<span className="font-mono text-xs text-slate-400 font-normal">（{r.symbol}）</span></SymbolLink>
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap">
                    <div className="text-xs text-slate-300">{r.group_label ?? '—'}</div>
                    <div className="text-[11px] text-slate-500">{r.subcat_label ?? '—'}</div>
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{numFmt(r.price, 2)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{moneyFmt(r.total_market_cap_yi)}</td>
                  <td className={`px-2 py-2 text-right font-mono text-xs ${pctClass(r.earnings_yield)}`}>{earningsYieldFmt(r.earnings_yield)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">{r.pe_ttm != null ? `${numFmt(r.pe_ttm, 1)}x` : <PeState state={r.pe_state} />}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{r.pe_dynamic != null ? `${numFmt(r.pe_dynamic, 1)}x` : '—'}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{r.pb != null ? `${numFmt(r.pb, 2)}x` : '—'}</td>
                  <td className={`px-2 py-2 text-right font-mono text-xs ${pctClass(r.ttm_revenue_yoy)}`}>{pctFmt(r.ttm_revenue_yoy)}</td>
                  <td className={`px-2 py-2 text-right font-mono text-xs ${pctClass(r.ttm_net_profit_yoy)}`}>{pctFmt(r.ttm_net_profit_yoy)}</td>
                  <td className="px-2 py-2 text-right text-xs" style={{ color: qualityColor(r.quality_status) }}>{qualityLabel(r.quality_status)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{pctFmt(r.gross_margin)}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-slate-300">{pctFmt(r.roe)}</td>
                  <td className="px-2 py-2 text-right whitespace-nowrap text-xs text-slate-400">{r.latest_report_label ?? '—'}</td>
                </tr>
              ))}
              {!filteredRows.length && <tr><td colSpan={14} className="text-center py-10 text-sm text-slate-500">没有符合条件的股票</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {selected && detailLoading && <div className="text-center py-6 text-sm text-slate-500">正在加载 {selected} 的详细财报和历史 PE…</div>}
      {selected && detailError && <div className="text-sm text-red-400 bg-red-950/20 border border-red-900/50 rounded-lg px-3 py-2">详情加载失败：{String(detailError)}</div>}
      {detail && <DetailPanel detail={detail} mode={reportMode} setMode={setReportMode} />}

      <div className="text-sm text-slate-400 space-y-1">
        <div>· 地图纵轴 E/P = TTM 净利润 ÷ 总市值；对盈利公司约等于 1 ÷ PE(TTM)，越高通常代表估值越便宜；亏损公司显示负 E/P，仅作风险提示。</div>
        <div>· 地图右上角表示“增长较快 + 盈利收益率较高”的相对研究优先级，不等同于优秀评级或买入信号；中线按当前筛选样本的中位数划分。</div>
        <div>· 地图使用统一小点减少遮挡，总市值只保留在悬停信息中；颜色只做盈利质量提示，强/中等/偏弱的判定结合 ROE、利润状态和最新利润同比，不能替代逐项核验。</div>
        <div>· “主体区”按横纵轴各自 3%–97% 分位聚焦密集样本，极端点没有删除，可切换“全量”或拖动范围条查看。</div>
        <div>· PE(TTM) 为总市值 ÷ 最近十二个月净利润；动态 PE 是源站快照，两者口径不同。</div>
        <div>· TTM 增长由最近报告期与上年同期滚动十二个月比较；上年利润为负或数据不全时，净利增速会保留为空。</div>
        <div>· A 股半年报 / 三季报通常为累计值；“单季度”由相邻累计报告相减得到，无法推导时保留为空。</div>
        <div>· 估值快照展示抓取时间；源站没有可验证的交易所延迟 SLA 时，不把抓取时间等同于严格实时成交时间。</div>
        {valuationSource?.fallback_used && <div>· 当前估值使用备用行情源；备用源的 PE 字段为 PE(TTM)，与主源动态 PE 不是完全同一口径。</div>}
        <div>· 本 Tab 仅用于基本面研究，不会改变动能分数、回测结果或半自动调仓计划。</div>
      </div>
    </div>
  )
}
