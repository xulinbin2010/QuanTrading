import { useQuery } from '@tanstack/react-query'
import { getOrders, getBalance, getPositions, refreshPositions, getEarningsDates, getPerformance } from '../api/client'
import ReactECharts from 'echarts-for-react'
import { useState } from 'react'

function StatCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-xl font-semibold ${color ?? 'text-white'}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  )
}

function fmt(n?: number) {
  if (n === undefined || n === null) return '-'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}


type PeriodKey = '2W' | '1M' | '3M' | 'YTD'
const PERIOD_DAYS: Record<PeriodKey, () => number> = {
  '2W':  () => 14,
  '1M':  () => 30,
  '3M':  () => 90,
  'YTD': () => {
    const now = new Date()
    return Math.round((now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) / 86_400_000)
  },
}

function pct(v?: number, prefix = true) {
  if (v === undefined || v === null) return '-'
  const sign = v >= 0 ? (prefix ? '+' : '') : ''
  return `${sign}${v.toFixed(2)}%`
}

function PerfCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-slate-900/60 rounded-lg px-4 py-3 border border-slate-700/60 text-center">
      <div className="text-xs text-slate-400 mb-1">{label}</div>
      <div className={`text-base font-semibold tabular-nums ${color ?? 'text-white'}`}>{value}</div>
    </div>
  )
}

function PerformanceSection() {
  const [period, setPeriod] = useState<PeriodKey>('1M')
  const days = PERIOD_DAYS[period]()

  const { data, isLoading } = useQuery({
    queryKey: ['performance', period],
    queryFn: () => getPerformance(days),
    staleTime: 10 * 60_000,
    retry: false,
  })

  const nav: { date: string; value: number }[]  = data?.nav  ?? []
  const spy: { date: string; value: number }[]  = data?.spy  ?? []
  const m = data?.metrics ?? {}

  const chartOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params: any[]) =>
        `${params[0].name}<br/>` +
        params.map((p: any) => `<span style="color:${p.color}">●</span> ${p.seriesName}: ${p.value?.toFixed(2)}`).join('<br/>'),
    },
    legend: {
      data: ['Portfolio', 'SPY'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 4,
      right: 8,
    },
    grid: { left: 48, right: 16, top: 32, bottom: 36 },
    xAxis: {
      type: 'category',
      data: nav.map(d => d.date),
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisLine: { lineStyle: { color: '#1e293b' } },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      min: (e: { min: number }) => parseFloat((e.min * 0.995).toFixed(2)),
      max: (e: { max: number }) => parseFloat((e.max * 1.005).toFixed(2)),
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (v: number) => v.toFixed(1) },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: 'Portfolio',
        type: 'line',
        data: nav.map(d => d.value),
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#3b82f6', width: 2 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.18)' }, { offset: 1, color: 'rgba(59,130,246,0.01)' }] } },
      },
      {
        name: 'SPY',
        type: 'line',
        data: spy.map(d => d.value),
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#f97316', width: 1.5, type: 'dashed' },
      },
    ],
  }

  return (
    <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
      {/* 标题 + 周期选择 */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-slate-300">业绩复盘</span>
        <div className="flex gap-1">
          {(['2W', '1M', '3M', 'YTD'] as PeriodKey[]).map(p => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-2.5 py-0.5 text-xs rounded font-medium transition-colors
                ${period === p
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:text-white'}`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* 指标卡片 */}
      {isLoading ? (
        <div className="text-slate-500 text-xs py-2">计算中...</div>
      ) : !data?.has_data ? (
        <div className="text-slate-500 text-sm py-4 text-center">暂无账户快照数据，请先连接 IB Gateway</div>
      ) : (
        <>
          <div className="grid grid-cols-5 gap-2 mb-4">
            <PerfCard
              label="区间收益"
              value={pct(m.total_return)}
              color={(m.total_return ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
            <PerfCard
              label="年化收益"
              value={pct(m.annualized)}
              color={(m.annualized ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
            <PerfCard
              label="Sharpe"
              value={m.sharpe !== undefined ? m.sharpe.toFixed(2) : '-'}
              color={(m.sharpe ?? 0) >= 1 ? 'text-green-400' : (m.sharpe ?? 0) >= 0 ? 'text-yellow-400' : 'text-red-400'}
            />
            <PerfCard
              label="最大回撤"
              value={pct(m.max_drawdown)}
              color="text-red-400"
            />
            <PerfCard
              label="超额 SPY"
              value={pct(m.excess_spy)}
              color={(m.excess_spy ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
            />
          </div>
          <ReactECharts option={chartOption} style={{ height: 220 }} />
          <div className="text-right text-xs text-slate-600 mt-1">
            {m.data_from ? `${m.data_from} 起` : ''} · {m.period_days} 个快照，两线均归一至 100
          </div>
        </>
      )}
    </div>
  )
}

// 每种颜色: [背景色, 是否用深色文字]
const SEGMENT_PALETTE: [string, boolean][] = [
  ['#60a5fa', false], // blue-400
  ['#34d399', true],  // emerald-400
  ['#fbbf24', true],  // amber-400
  ['#c084fc', false], // purple-400
  ['#f472b6', false], // pink-400
  ['#22d3ee', true],  // cyan-400
  ['#fb923c', false], // orange-400
  ['#4ade80', true],  // green-400
]

function AllocationBar({ positions, balance }: { positions: any[]; balance: any }) {
  if (!positions?.length && !balance) return null

  const netLiq = balance?.net_liquidation
  const cash = balance?.total_cash ?? 0
  const totalMv = positions?.reduce((s: number, p: any) => s + (p.market_value ?? 0), 0) ?? 0
  const total = netLiq ?? (totalMv + cash)
  if (total <= 0) return null

  const segments: { label: string; value: number; pct: number; bg: string; darkText: boolean }[] = [
    ...(positions ?? []).map((p: any, i: number) => {
      const [bg, darkText] = SEGMENT_PALETTE[i % SEGMENT_PALETTE.length]
      return {
        label: p.symbol,
        value: p.market_value ?? 0,
        pct: ((p.market_value ?? 0) / total) * 100,
        bg,
        darkText,
      }
    }),
    {
      label: 'Cash',
      value: cash,
      pct: (cash / total) * 100,
      bg: '#94a3b8', // slate-400 — 在深色/浅色背景下均可辨
      darkText: true,
    },
  ].filter(s => s.pct > 0.1)

  return (
    <div className="px-4 pt-3 pb-4 border-t border-slate-700/60">
      <div className="text-xs font-medium text-slate-400 mb-2 dark:text-slate-400">资产配比</div>

      {/* 堆积 bar — 圆角、段间留 2px 透明间隔 */}
      <div className="flex w-full h-7 rounded-full overflow-hidden" style={{ gap: '2px' }}>
        {segments.map(s => (
          <div
            key={s.label}
            style={{ width: `${s.pct}%`, backgroundColor: s.bg, minWidth: s.pct > 0.5 ? undefined : '3px' }}
            className="relative group flex items-center justify-center overflow-visible shrink-0 transition-all duration-200 hover:brightness-110 hover:z-10 cursor-default"
          >
            {/* 段内标签：宽度足够时才显示 */}
            {s.pct >= 7 && (
              <span
                className="text-[11px] font-bold truncate px-1 leading-none pointer-events-none select-none"
                style={{ color: s.darkText ? '#1e293b' : '#ffffff' }}
              >
                {s.label}
              </span>
            )}

            {/* hover tooltip */}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-20
                            bg-slate-900 dark:bg-slate-800 border border-slate-600
                            rounded-lg shadow-xl px-3 py-1.5 text-xs whitespace-nowrap
                            opacity-0 group-hover:opacity-100 pointer-events-none
                            transition-opacity duration-150">
              <span className="font-semibold text-white">{s.label}</span>
              <span className="text-slate-300 mx-1.5">·</span>
              <span className="text-slate-200">{s.pct.toFixed(1)}%</span>
              <span className="text-slate-400 ml-1.5">
                {s.value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 图例 */}
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 mt-2.5">
        {segments.map(s => (
          <div key={s.label} className="flex items-center gap-1.5">
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
              style={{ backgroundColor: s.bg }}
            />
            <span className="text-xs text-slate-200 font-bold">{s.label}</span>
            <span className="text-xs text-slate-300 font-semibold">{s.pct.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Portfolio() {
  const [orderSymbol, setOrderSymbol] = useState('')
  const [refreshMsg, setRefreshMsg] = useState<'ok' | 'error' | null>(null)
  const [spinning, setSpinning] = useState(false)

  const { data: balance, isError: balanceErr } = useQuery({
    queryKey: ['balance'],
    queryFn: getBalance,
    refetchInterval: 60_000,
    retry: false,
  })

  const { data: positions, isError: posErr, refetch: refetchPositions } = useQuery({
    queryKey: ['positions'],
    queryFn: getPositions,
    refetchInterval: 60_000,
    retry: false,
  })

  const { data: orders = [] } = useQuery({
    queryKey: ['orders', orderSymbol],
    queryFn: () => getOrders(orderSymbol || undefined, 100),
    refetchInterval: 30_000,
  })

  const posSymbols = (positions ?? []).map((p: any) => p.symbol).filter(Boolean)
  const { data: earningsDates } = useQuery<Record<string, string | null>>({
    queryKey: ['earnings', posSymbols.join(',')],
    queryFn: () => posSymbols.length ? getEarningsDates(posSymbols) : Promise.resolve({}),
    enabled: posSymbols.length > 0,
    staleTime: 12 * 60 * 60_000,
  })

  const ibOffline = balanceErr && posErr

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold text-white">持仓总览</h1>

      {ibOffline && (
        <div className="bg-slate-700 border border-slate-600 rounded-lg px-4 py-2 text-sm text-slate-300">
          IB Gateway 未连接，账户余额/持仓不可用。订单历史和净值记录仍正常显示。
        </div>
      )}

      {/* 余额卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCard label="净值" value={fmt(balance?.net_liquidation)} />
        <StatCard label="现金" value={fmt(balance?.total_cash)} />
        <StatCard
          label="浮动盈亏"
          value={fmt(balance?.unrealized_pnl)}
          color={(balance?.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard
          label="实现盈亏"
          value={fmt(balance?.realized_pnl)}
          color={(balance?.realized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <StatCard label="购买力" value={fmt(balance?.buying_power)} />
      </div>

      {/* 业绩复盘 */}
      <PerformanceSection />

      {/* 持仓表格 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300 flex items-center justify-between">
          <span>当前持仓 {positions ? `（${positions.length} 只）` : ''}</span>
          <div className="flex items-center gap-2">
            {refreshMsg && (
              <span className={`text-xs ${refreshMsg === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
                {refreshMsg === 'ok' ? '已刷新' : 'IB 未连接'}
              </span>
            )}
            <button
              onClick={async () => {
                setSpinning(true)
                setRefreshMsg(null)
                try {
                  // 强制断线重连以获取 IB 最新持仓（绕过缓存）
                  await refreshPositions()
                  await refetchPositions()
                  setRefreshMsg('ok')
                } catch {
                  setRefreshMsg('error')
                } finally {
                  setSpinning(false)
                  setTimeout(() => setRefreshMsg(null), 2500)
                }
              }}
              disabled={spinning}
              className="flex items-center gap-1 text-xs text-slate-400 hover:text-white disabled:opacity-40 transition-colors"
            >
              <span className={spinning ? 'animate-spin inline-block' : 'inline-block'}>↻</span>
              {spinning ? '刷新中...' : '刷新'}
            </button>
          </div>
        </div>
        {!positions || positions.length === 0 ? (
          <div className="px-4 py-6 text-slate-500 text-sm text-center">
            {ibOffline ? '需要 IB Gateway 连接' : '当前无持仓'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs border-b border-slate-700">
                  {['股票', '买入日', '数量', '均价', '现价', '市值', '浮盈', '浮盈%', '财报'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p: any) => (
                  <tr key={p.symbol} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-4 py-2 font-mono text-white">{p.symbol}</td>
                    <td className="px-4 py-2 text-slate-400">{p.entry_date ?? '-'}</td>
                    <td className="px-4 py-2">{p.qty}</td>
                    <td className="px-4 py-2 font-mono">${p.avg_cost.toFixed(2)}</td>
                    <td className="px-4 py-2 font-mono">${p.market_price.toFixed(2)}</td>
                    <td className="px-4 py-2">{fmt(p.market_value)}</td>
                    <td className={`px-4 py-2 font-mono ${p.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {fmt(p.unrealized_pnl)}
                    </td>
                    <td className={`px-4 py-2 font-mono font-medium ${p.unrealized_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {p.unrealized_pnl_pct >= 0 ? '+' : ''}{(p.unrealized_pnl_pct * 100).toFixed(2)}%
                    </td>
                    <td className="px-4 py-2 font-mono text-xs">
                      {(() => {
                        const d = earningsDates?.[p.symbol]
                        if (!d) return <span className="text-slate-500">-</span>
                        const days = Math.round((new Date(d).getTime() - Date.now()) / 86400_000)
                        const color = days <= 2 ? 'text-red-400 font-semibold' : days <= 7 ? 'text-yellow-400' : 'text-slate-400'
                        return <span className={color} title={`${days}天后`}>{d.slice(5)}</span>
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {positions && positions.length > 0 && (
          <AllocationBar positions={positions} balance={balance} />
        )}
      </div>

      {/* 订单历史 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
          <div className="text-sm font-medium text-slate-300">订单历史</div>
          <input
            className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white placeholder-slate-400 w-24 focus:outline-none focus:border-blue-500"
            placeholder="过滤股票"
            value={orderSymbol}
            onChange={e => setOrderSymbol(e.target.value.toUpperCase())}
          />
        </div>
        {orders.length === 0 ? (
          <div className="px-4 py-6 text-slate-500 text-sm text-center">暂无订单记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-slate-400 text-xs border-b border-slate-700">
                  {['时间', '股票', '方向', '类型', '数量', '价格', '成交价', '状态'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {orders.map((o: any) => {
                  const filled = o.status === 'Filled' || o.status === 'PartialFill'
                  const dim = !filled ? 'opacity-40' : ''
                  return (
                  <tr key={o.id} className={`border-b border-slate-700/50 hover:bg-slate-700/30 text-xs ${dim}`}>
                    <td className="px-4 py-2 text-slate-400 font-mono">{o.created_at}</td>
                    <td className={`px-4 py-2 font-mono ${filled ? 'text-white' : 'text-slate-400'}`}>{o.symbol}</td>
                    <td className={`px-4 py-2 font-semibold ${filled ? (o.action === 'BUY' ? 'text-green-400' : 'text-red-400') : 'text-slate-500'}`}>
                      {o.action}
                    </td>
                    <td className="px-4 py-2 text-slate-400">{o.order_type}</td>
                    <td className="px-4 py-2">{o.quantity}</td>
                    <td className="px-4 py-2">{o.price ? `$${o.price.toFixed(2)}` : '市价'}</td>
                    <td className="px-4 py-2">{o.filled_price ? `$${o.filled_price.toFixed(2)}` : '-'}</td>
                    <td className={`px-4 py-2 ${filled ? 'text-slate-300' : 'text-slate-500'}`}>{o.status}</td>
                  </tr>
                )})}

              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
