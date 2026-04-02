import { useQuery } from '@tanstack/react-query'
import { getAccountHistory, getOrders, getBalance, getPositions } from '../api/client'
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


function EquityChart({ data }: { data: { snapshot_at: string; net_liquidation: number }[] }) {
  if (!data.length) return <div className="text-slate-500 text-sm py-8 text-center">暂无净值数据</div>
  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', formatter: (p: any) => `${p[0].name}<br/>净值: $${p[0].value.toLocaleString()}` },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: data.map(d => d.snapshot_at.slice(0, 10)),
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => '$' + (v / 1000).toFixed(0) + 'K' },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [{
      type: 'line',
      data: data.map(d => d.net_liquidation),
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#3b82f6', width: 2 },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.3)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }] } },
    }],
  }
  return <ReactECharts option={option} style={{ height: 200 }} />
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

  const { data: history = [] } = useQuery({
    queryKey: ['account-history'],
    queryFn: () => getAccountHistory(90),
    refetchInterval: 60_000,
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

      {/* 净值曲线 */}
      <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
        <div className="text-sm font-medium text-slate-300 mb-3">账户净值历史</div>
        <EquityChart data={history} />
      </div>

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
                const [result] = await Promise.all([
                  refetchPositions(),
                  new Promise(r => setTimeout(r, 600)),
                ])
                setSpinning(false)
                setRefreshMsg(result.status === 'error' ? 'error' : 'ok')
                setTimeout(() => setRefreshMsg(null), 2500)
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
                  {['股票', '买入日', '数量', '均价', '现价', '市值', '浮盈', '浮盈%'].map(h => (
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                {orders.map((o: any) => (
                  <tr key={o.id} className="border-b border-slate-700/50 hover:bg-slate-700/30 text-xs">
                    <td className="px-4 py-2 text-slate-400 font-mono">{o.created_at}</td>
                    <td className="px-4 py-2 font-mono text-white">{o.symbol}</td>
                    <td className={`px-4 py-2 font-semibold ${o.action === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                      {o.action}
                    </td>
                    <td className="px-4 py-2 text-slate-300">{o.order_type}</td>
                    <td className="px-4 py-2">{o.quantity}</td>
                    <td className="px-4 py-2">{o.price ? `$${o.price.toFixed(2)}` : '市价'}</td>
                    <td className="px-4 py-2">{o.filled_price ? `$${o.filled_price.toFixed(2)}` : '-'}</td>
                    <td className="px-4 py-2 text-slate-400">{o.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
