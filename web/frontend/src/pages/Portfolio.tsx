import { useQuery } from '@tanstack/react-query'
import { getOrders, getBalance, getPositions, refreshPositions, getEarningsDates, getPerformance } from '../api/client'
import ReactECharts from 'echarts-for-react'
import { useState } from 'react'
import { useAccount } from '../App'

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

  // 用 net_liquidation 作分母：IB 实时净值，总和恒等于 100%
  // market_value 各持仓占 netLiq 的比例；Cash = netLiq 减去所有持仓市值的余量
  const netLiq = balance?.net_liquidation
  if (!netLiq || netLiq <= 0) return null

  // ── 期权价差智能合并 ──────────────────────────────────────────────────────
  // 策略：同底层+方向+到期日的期权中，将每个空头与最近行权价的多头配对成价差；
  //       未配对的多头单独显示。这样 C260(多)+C325(多)+C340(空) → C325/340价差 + C260单腿
  type BarItem = { label: string; value: number }
  interface OptLeg { strike: number; mv: number; qty: number; symbol: string }
  const optGroups: Record<string, OptLeg[]> = {}
  const barItems: BarItem[] = []

  for (const p of (positions ?? [])) {
    const mv: number = p.market_value ?? 0
    const parts: string[] = (p.symbol as string).trim().split(/\s+/)
    if (parts.length === 3) {
      const underlying = parts[0]
      const right = parts[1][0]
      const strike = parseFloat(parts[1].slice(1))
      const expiry = parts[2]
      const key = `${underlying}_${right}_${expiry}`
      if (!optGroups[key]) optGroups[key] = []
      optGroups[key].push({ strike, mv, qty: p.qty ?? 0, symbol: p.symbol })
    } else {
      barItems.push({ label: p.symbol, value: mv })
    }
  }

  for (const legs of Object.values(optGroups)) {
    const longs  = legs.filter(l => l.qty > 0).sort((a, b) => a.strike - b.strike)
    const shorts = legs.filter(l => l.qty < 0).sort((a, b) => a.strike - b.strike)
    const paired = new Set<number>()   // longs 中已配对的索引

    // 配对规则：① 数量绝对值相同（+2/-2 才是价差），② 数量相同时取行权价最近的
    for (const s of shorts) {
      const absQty = Math.abs(s.qty)
      let bestIdx = -1, bestDist = Infinity
      longs.forEach((lng, i) => {
        if (paired.has(i)) return
        if (lng.qty !== absQty) return          // 数量必须匹配
        const d = Math.abs(lng.strike - s.strike)
        if (d < bestDist) { bestDist = d; bestIdx = i }
      })
      // 若无精确数量匹配（非标准价差），fallback 到任意未配对多头（按最近行权价）
      if (bestIdx < 0) {
        longs.forEach((lng, i) => {
          if (paired.has(i)) return
          const d = Math.abs(lng.strike - s.strike)
          if (d < bestDist) { bestDist = d; bestIdx = i }
        })
      }
      if (bestIdx >= 0) {
        paired.add(bestIdx)
        const lng = longs[bestIdx]
        const netMv = lng.mv + s.mv
        if (netMv > 0) {
          const [lo, hi] = [lng.strike, s.strike].sort((a, b) => a - b)
          const sym0Parts = lng.symbol.split(' ')
          barItems.push({ label: `${sym0Parts[0]} ${sym0Parts[1][0]}${lo}/${hi}`, value: netMv })
        }
      }
    }

    // 未配对的多头单独显示
    longs.forEach((lng, i) => {
      if (!paired.has(i) && lng.mv > 0) barItems.push({ label: lng.symbol, value: lng.mv })
    })
  }

  const positiveMv = barItems.reduce((s, x) => s + Math.max(x.value, 0), 0)
  const cashPct = Math.max((netLiq - positiveMv) / netLiq * 100, 0)

  let colorIdx = 0
  const segments: { label: string; value: number; pct: number; bg: string; darkText: boolean }[] = [
    ...barItems.filter(x => x.value > 0).map(x => {
      const [bg, darkText] = SEGMENT_PALETTE[colorIdx++ % SEGMENT_PALETTE.length]
      return { label: x.label, value: x.value, pct: (x.value / netLiq) * 100, bg, darkText }
    }),
    {
      label: 'Cash',
      value: netLiq - positiveMv,
      pct: cashPct,
      bg: '#94a3b8',
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
            {/* 段内标签：宽度 >= 4% 显示，太窄时截断显示前3字符 */}
            {s.pct >= 4 && (
              <span
                className="text-[11px] font-bold leading-none pointer-events-none select-none overflow-hidden"
                style={{
                  color: s.darkText ? '#1e293b' : '#ffffff',
                  maxWidth: '90%',
                  display: 'block',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  padding: '0 2px',
                }}
              >
                {s.pct < 7 ? s.label.slice(0, 4) : s.label}
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

  const { data: balance, isError: balanceErr, refetch: refetchBalance } = useQuery({
    queryKey: ['balance'],
    queryFn: getBalance,
    refetchInterval: 60_000,
    retry: false,
  })

  const { selectedAccount } = useAccount()

  const { data: positions, isError: posErr, refetch: refetchPositions } = useQuery({
    queryKey: ['positions', selectedAccount],
    queryFn: () => getPositions(selectedAccount),
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
      <div>
        <h1 className="text-lg font-semibold text-white">持仓总览</h1>
      </div>

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
            {positions && positions.length > 0 && (
              <button
                onClick={() => {
                  const headers = ['股票', '买入日', '数量', '均价', '现价', '市值', '浮盈', '浮盈%']
                  const rows = positions.map((p: any) => [
                    p.symbol,
                    p.entry_date ?? '',
                    p.qty,
                    p.avg_cost.toFixed(2),
                    p.market_price.toFixed(2),
                    p.market_value.toFixed(2),
                    p.unrealized_pnl.toFixed(2),
                    (p.unrealized_pnl_pct * 100).toFixed(2) + '%',
                  ])
                  const csv = [headers, ...rows].map(r => r.join(',')).join('\n')
                  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
                  const url = URL.createObjectURL(blob)
                  const a = document.createElement('a')
                  a.href = url
                  a.download = `positions_${new Date().toISOString().slice(0, 10)}.csv`
                  a.click()
                  URL.revokeObjectURL(url)
                }}
                className="flex items-center gap-1 text-xs text-slate-400 hover:text-white transition-colors"
              >
                ↓ 导出
              </button>
            )}
            <button
              onClick={async () => {
                setSpinning(true)
                setRefreshMsg(null)
                try {
                  // 强制断线重连以获取 IB 最新持仓（绕过缓存）
                  await refreshPositions(selectedAccount)
                  await Promise.all([refetchPositions(), refetchBalance()])
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
