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
  }
  markets: { us: MarketAggregate; kr: MarketAggregate }
  funding: { us: FundingData; kr: FundingData }
  products: ProductRow[]
  warnings: string[]
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

function ScoreCard({ title, score, level, subtitle }: {
  title: string
  score: number | null
  level?: string
  subtitle: string
}) {
  const style = levelStyle(level)
  return (
    <div className={`rounded-xl border px-4 py-3 ${style.band}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm text-slate-300">{title}</div>
        <span className={`text-xs font-medium ${style.text}`}>{style.label}</span>
      </div>
      <div className="flex items-end gap-2 mt-1">
        <span className={`text-3xl font-semibold font-mono ${style.text}`}>
          {score == null ? '—' : score.toFixed(0)}
        </span>
        <span className="text-xs text-slate-500 mb-1">/ 100</span>
      </div>
      <div className="h-1.5 rounded-full bg-slate-700 mt-2 overflow-hidden">
        <div className={`h-full rounded-full ${style.fill}`} style={{ width: `${Math.max(0, Math.min(score ?? 0, 100))}%` }} />
      </div>
      <div className="text-xs text-slate-400 mt-2">{subtitle}</div>
    </div>
  )
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
          </div>
        ))}
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
  const global = levelStyle(data.summary.unwind_level)

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

      <div className={`rounded-xl border px-5 py-4 ${global.band}`}>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="text-xs text-slate-400">当前去杠杆压力 · 取美韩两市场较高者</div>
            <div className="flex items-baseline gap-3 mt-1">
              <span className={`text-4xl font-mono font-semibold ${global.text}`}>
                {data.summary.unwind_score?.toFixed(0) ?? '—'}
              </span>
              <span className={`text-sm font-medium ${global.text}`}>{global.label}</span>
              {data.summary.dominant_market && (
                <span className="text-xs px-2 py-0.5 rounded bg-slate-800/70 text-slate-300">
                  主导市场 {data.summary.dominant_market === 'US' ? '🇺🇸 美国' : '🇰🇷 韩国'}
                </span>
              )}
            </div>
          </div>
          <div className="max-w-xl text-sm text-slate-300 leading-relaxed">
            {data.summary.unwind_level === 'high'
              ? '多项即时信号正在共振：重点检查融资仓、2X/3X 产品和高 beta 主仓的隔夜跳空风险。'
              : data.summary.unwind_level === 'mid'
              ? '去杠杆信号开始升温，但尚未全面共振；关注反向 ETF 成交是否继续放大。'
              : '即时去杠杆信号尚未形成明显共振；高融资余额仍可能代表潜在脆弱性。'}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <ScoreCard
          title="跨市场 Unwind"
          score={data.summary.unwind_score}
          level={data.summary.unwind_level}
          subtitle="即时价格 + 成交 pace + tracking dislocation"
        />
        <MarketCard market="US" data={data.markets.us} />
        <MarketCard market="KR" data={data.markets.kr} />
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

      <ProductTable rows={data.products} />

      {/* 说明 */}
      <div className="text-sm text-slate-400 space-y-1">
        <div>· <span className="text-slate-300">Unwind score</span> 是透明的观察指标，不直接触发交易：多头杠杆下跌、成交放大、反向产品上涨/放量、跟踪偏差和融资收缩按可用项归一化为 0–100。</div>
        <div>· <span className="text-slate-300">Tracking drag</span> 按“每日目标倍数”逐日复利计算，不用错误的“20 日指数涨幅 × 2/3”近似；震荡行情中的 volatility decay 会自然反映出来。</div>
        <div>· <span className="text-slate-300">成交 pace</span> 在交易时段内按已过时间比例折算，标有“估”的值不是完整日成交量；yfinance 免费行情可能延迟，不能当作交易所 real-time feed。</div>
        {data.warnings.map((warning, i) => <div key={i}>· {warning}</div>)}
      </div>
    </div>
  )
}
