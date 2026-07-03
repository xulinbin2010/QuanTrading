/**
 * A 股半自动调仓面板：系统出目标持仓 → 与本地台账 diff 出调仓清单 →
 * 你手动在券商 App 下单 → 回填成交。不接券商，台账独立存 data/astock_trade.db。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import SymbolLink from './SymbolLink'
import LeaderBadge from './LeaderBadge'
import {
  getAStockTradeSettings, updateAStockTradeSettings, getAStockHoldings,
  setAStockPosition, deleteAStockPosition, genAStockPlan, getAStockPlan,
  confirmAStockFill, setAStockOrderStatus,
} from '../api/client'

const STRATEGIES = [
  ['sector_rotation', '板块轮动（选最强2板块的龙头·集中押主线）'],
  ['momentum_filtered', '动能+EMA21（全市场前N·须站上EMA21趋势过滤）'],
  ['momentum', '纯动能（全市场composite前N·不看板块）'],
  ['quality_momentum', '质量动能（动能×低PE加权·偏估值防高估）'],
] as const

const yuan = (v: number | null | undefined) =>
  v == null ? '—' : '¥' + v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
const px = (v: number | null | undefined) => v == null ? '—' : v.toFixed(2)
const pct = (v: number | null | undefined) =>
  v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'

export default function AStockRebalancePanel() {
  const qc = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['astock-trade-settings'], queryFn: getAStockTradeSettings, staleTime: 60_000 })
  const { data: holdings } = useQuery({ queryKey: ['astock-holdings'], queryFn: getAStockHoldings, staleTime: 60_000 })
  const { data: plan } = useQuery({ queryKey: ['astock-plan'], queryFn: () => getAStockPlan(), staleTime: 60_000 })

  const [capital, setCapital] = useState('')
  const [topN, setTopN] = useState('')
  const [strategy, setStrategy] = useState('')
  const [generating, setGenerating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [genResult, setGenResult] = useState<any>(null)

  const [fillId, setFillId] = useState<number | null>(null)
  const [fillQty, setFillQty] = useState('')
  const [fillPrice, setFillPrice] = useState('')

  const [addCode, setAddCode] = useState('')
  const [addQty, setAddQty] = useState('')
  const [addCost, setAddCost] = useState('')

  const curCapital = capital !== '' ? capital : String(settings?.capital ?? 70000)
  const curTopN = topN !== '' ? topN : String(settings?.top_n ?? 5)
  const curStrategy = strategy !== '' ? strategy : (settings?.strategy ?? 'sector_rotation')
  const perPos = Number(curCapital) / Math.max(Number(curTopN) || 1, 1)

  const refreshPlan = async () => qc.setQueryData(['astock-plan'], await getAStockPlan())

  const saveSettings = async () => {
    setBusy(true); setMsg('')
    try {
      await updateAStockTradeSettings({ capital: Number(curCapital), top_n: Number(curTopN), strategy: curStrategy })
      await qc.invalidateQueries({ queryKey: ['astock-trade-settings'] })
      setCapital(''); setTopN(''); setStrategy('')
      setMsg('✓ 设置已保存')
    } finally { setBusy(false) }
  }

  const generate = async () => {
    setGenerating(true); setMsg('')
    try {
      // 生成前先把当前输入框的设置写库,免去「必须先点保存」的反直觉步骤
      await updateAStockTradeSettings({ capital: Number(curCapital), top_n: Number(curTopN), strategy: curStrategy })
      await qc.invalidateQueries({ queryKey: ['astock-trade-settings'] })
      const r = await genAStockPlan(false)
      setGenResult(r)
      if (r.scanning) setMsg('⏳ ' + (r.message || '动能扫描进行中，请稍后重试'))
      else {
        await refreshPlan()
        await qc.invalidateQueries({ queryKey: ['astock-holdings'] })
        setMsg(`✓ 已生成 ${r.plan_date} 调仓清单`)
      }
    } catch (e: any) {
      setMsg('✗ 生成失败: ' + (e?.response?.data?.detail || e?.message))
    } finally { setGenerating(false) }
  }

  const openFill = (o: any) => { setFillId(o.id); setFillQty(String(o.target_qty)); setFillPrice(o.ref_price != null ? String(o.ref_price) : '') }
  const doFill = async () => {
    if (fillId == null) return
    setBusy(true)
    try {
      await confirmAStockFill({ order_id: fillId, filled_qty: Number(fillQty), filled_price: Number(fillPrice) })
      setFillId(null); setFillQty(''); setFillPrice('')
      await refreshPlan()
      await qc.invalidateQueries({ queryKey: ['astock-holdings'] })
    } catch (e: any) {
      setMsg('✗ 回填失败: ' + (e?.response?.data?.detail || e?.message))
    } finally { setBusy(false) }
  }

  const skipOrder = async (id: number) => { await setAStockOrderStatus(id, 'skipped'); await refreshPlan() }

  const addPosition = async () => {
    if (addCode.length < 6 || !addQty || !addCost) return
    setBusy(true)
    try {
      await setAStockPosition({ code: addCode, qty: Number(addQty), avg_cost: Number(addCost) })
      setAddCode(''); setAddQty(''); setAddCost('')
      await qc.invalidateQueries({ queryKey: ['astock-holdings'] })
    } finally { setBusy(false) }
  }

  const delPosition = async (code: string) => {
    if (!confirm(`从台账移除 ${code}？`)) return
    await deleteAStockPosition(code)
    await qc.invalidateQueries({ queryKey: ['astock-holdings'] })
  }

  const orders: any[] = plan?.orders ?? []
  const positions: any[] = holdings?.positions ?? []
  const pendingCount = orders.filter(o => o.status === 'pending').length

  return (
    <div className="space-y-4">
      {/* 设置 + 生成 */}
      <div className="bg-slate-800/60 rounded-lg border border-slate-700 p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-slate-400">
            <div className="mb-1">总资金（元）</div>
            <input value={curCapital} onChange={e => setCapital(e.target.value.replace(/\D/g, ''))}
              className="w-28 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none" />
          </label>
          <label className="text-xs text-slate-400">
            <div className="mb-1">持仓数</div>
            <input value={curTopN} onChange={e => setTopN(e.target.value.replace(/\D/g, '').slice(0, 2))}
              className="w-16 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none" />
          </label>
          <label className="text-xs text-slate-400">
            <div className="mb-1">策略</div>
            <select value={curStrategy} onChange={e => setStrategy(e.target.value)}
              className="px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 focus:border-blue-500 outline-none">
              {STRATEGIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
          <button onClick={saveSettings} disabled={busy}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-sm text-slate-300 transition-colors">
            保存设置
          </button>
          <button onClick={generate} disabled={generating}
            className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-sm text-white font-medium transition-colors">
            {generating ? '生成中…' : '🛒 生成本周调仓'}
          </button>
          {msg && <span className="text-xs text-slate-300">{msg}</span>}
        </div>
        <p className="text-xs text-slate-500">
          单仓预算 ≈ {yuan(perPos)}（资金÷持仓数），A 股整百股；买不起 1 手的高价股会跳过（skipped）。生成清单 = 策略目标持仓 与 当前台账 diff。
        </p>
      </div>

      {/* 本次目标持仓 */}
      {genResult?.targets?.length > 0 && (
        <div className="bg-slate-800/40 rounded-lg border border-slate-700 p-3">
          <div className="text-xs text-slate-400 mb-2">🎯 本次目标持仓（{genResult.strategy} · 前 {genResult.top_n}）</div>
          <div className="flex flex-wrap gap-2">
            {genResult.targets.map((t: any) => (
              <span key={t.code} className="inline-flex items-center gap-1.5 px-2 py-1 bg-slate-900 rounded text-xs">
                <SymbolLink symbol={t.code} market="a" className="font-mono text-white" /><LeaderBadge code={t.code} />
                <span className="text-slate-300">{t.name}</span>
                <span className="text-slate-500">{t.subcat_label || t.group_label}</span>
                <span className="text-emerald-400">分{t.composite}</span>
                <span className="text-slate-400">¥{px(t.close)}</span>
                {t.substitute && <span className="text-amber-400" title="原最强候选买不起1手，此为次优替补">替补</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 调仓清单 */}
      <div className="bg-slate-800/60 rounded-lg border border-slate-700 overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-200">
            📋 调仓清单 {plan?.plan_date && <span className="text-slate-500 font-normal">· {plan.plan_date}</span>}
          </h3>
          {pendingCount > 0 && <span className="text-xs text-amber-400">{pendingCount} 笔待执行</span>}
        </div>
        {orders.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-500">暂无调仓清单，点「生成本周调仓」</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700">
                <th className="px-3 py-2 text-left">方向</th>
                <th className="px-3 py-2 text-left">代码</th>
                <th className="px-3 py-2 text-left">名称</th>
                <th className="px-3 py-2 text-right">目标股数</th>
                <th className="px-3 py-2 text-right">参考价</th>
                <th className="px-3 py-2 text-right">预算/市值</th>
                <th className="px-3 py-2 text-left">原因</th>
                <th className="px-3 py-2 text-left">状态</th>
                <th className="px-3 py-2 text-left">操作</th>
              </tr>
            </thead>
            <tbody>
              {orders.map(o => (
                <FragmentRow key={o.id} o={o} fillId={fillId} fillQty={fillQty} fillPrice={fillPrice}
                  setFillQty={setFillQty} setFillPrice={setFillPrice}
                  openFill={openFill} doFill={doFill} skipOrder={skipOrder} cancelFill={() => setFillId(null)} busy={busy} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 当前持仓台账 */}
      <div className="bg-slate-800/60 rounded-lg border border-slate-700 overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-200">💼 当前持仓台账</h3>
          {holdings && (
            <span className="text-xs text-slate-400">
              成本 {yuan(holdings.total_cost)} · 市值 {yuan(holdings.total_market_value)} ·
              <span className={`ml-1 ${(holdings.total_pnl ?? 0) >= 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                浮盈 {holdings.total_pnl == null ? '—' : yuan(holdings.total_pnl)}
              </span>
            </span>
          )}
        </div>
        {positions.length === 0 ? (
          <div className="px-4 py-6 text-center text-sm text-slate-500">台账为空。下方录入你现有的 A 股持仓，系统才能算出调仓 diff。</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700">
                <th className="px-3 py-2 text-left">代码</th>
                <th className="px-3 py-2 text-left">名称</th>
                <th className="px-3 py-2 text-right">股数</th>
                <th className="px-3 py-2 text-right">成本价</th>
                <th className="px-3 py-2 text-right">现价</th>
                <th className="px-3 py-2 text-right">市值</th>
                <th className="px-3 py-2 text-right">浮盈</th>
                <th className="px-3 py-2 text-right">浮盈%</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.code} className="border-b border-slate-700/50 hover:bg-slate-750">
                  <td className="px-3 py-1.5 whitespace-nowrap"><SymbolLink symbol={p.code} market="a" className="font-mono text-white" /><LeaderBadge code={p.code} /></td>
                  <td className="px-3 py-1.5 text-slate-300">{p.name}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-slate-300">{p.qty}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-slate-400">{px(p.avg_cost)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-slate-300">{px(p.cur_price)}</td>
                  <td className="px-3 py-1.5 text-right font-mono text-slate-300">{yuan(p.market_value)}</td>
                  <td className={`px-3 py-1.5 text-right font-mono ${(p.pnl ?? 0) >= 0 ? 'text-red-400' : 'text-emerald-400'}`}>{p.pnl == null ? '—' : yuan(p.pnl)}</td>
                  <td className={`px-3 py-1.5 text-right font-mono ${(p.pnl_pct ?? 0) >= 0 ? 'text-red-400' : 'text-emerald-400'}`}>{pct(p.pnl_pct)}</td>
                  <td className="px-3 py-1.5 text-right">
                    <button onClick={() => delPosition(p.code)} className="text-xs text-slate-500 hover:text-red-400">移除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {/* 手动录入 */}
        <div className="px-4 py-3 border-t border-slate-700 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">➕ 录入持仓</span>
          <input value={addCode} onChange={e => setAddCode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6位代码"
            className="w-24 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none" />
          <input value={addQty} onChange={e => setAddQty(e.target.value.replace(/\D/g, ''))} placeholder="股数"
            className="w-20 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none" />
          <input value={addCost} onChange={e => setAddCost(e.target.value.replace(/[^\d.]/g, ''))} placeholder="成本价"
            className="w-24 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none" />
          <button onClick={addPosition} disabled={busy || addCode.length < 6 || !addQty || !addCost}
            className="px-3 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-sm text-slate-300">添加</button>
        </div>
      </div>

      {/* 说明 */}
      <div className="text-sm text-slate-400 space-y-1">
        <div>· <span className="text-slate-300">半自动</span>：系统出目标持仓 → 你在券商 App 手动下单 → 回填成交。不接券商，台账独立存 data/astock_trade.db。</div>
        <div>· 调仓清单 = 策略选出的目标持仓 与 当前台账 diff：<span className="text-emerald-400">买入</span>=新进目标，<span className="text-red-400">卖出</span>=掉出目标，已在目标内的持仓维持不动。</div>
        <div>· <span className="text-amber-300">回填是闭环关键</span>：手动下单后务必填实际成交价/量，否则下次 diff 基于错误持仓。</div>
        <div>· 参考价为最新收盘，仅供下单参考；A 股 T+1，当日买入次日才可卖。</div>
        <div>· <span className="text-amber-300">候选替补</span>：策略选出的高价票若单仓预算买不起 1 手，自动用下一个买得起的高分候选(优先同板块次龙头)替补，凑满持仓数；被替补的原目标在清单里标「已替补」。</div>
      </div>
    </div>
  )
}

function FragmentRow({ o, fillId, fillQty, fillPrice, setFillQty, setFillPrice, openFill, doFill, skipOrder, cancelFill, busy }: any) {
  const isBuy = o.side === 'BUY'
  const statusBadge: Record<string, string> = {
    pending: 'text-amber-400', filled: 'text-emerald-400', skipped: 'text-slate-500', canceled: 'text-slate-500',
  }
  const statusText: Record<string, string> = {
    pending: '待执行', filled: '已成交', skipped: '已跳过', canceled: '已取消',
  }
  return (
    <>
      <tr className="border-b border-slate-700/50 hover:bg-slate-750">
        <td className="px-3 py-1.5">
          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${isBuy ? 'bg-emerald-900/50 text-emerald-300' : 'bg-red-900/50 text-red-300'}`}>
            {isBuy ? '买入' : '卖出'}
          </span>
        </td>
        <td className="px-3 py-1.5 whitespace-nowrap"><SymbolLink symbol={o.code} market="a" className="font-mono text-white" /><LeaderBadge code={o.code} /></td>
        <td className="px-3 py-1.5 text-slate-300">{o.name}</td>
        <td className="px-3 py-1.5 text-right font-mono text-slate-300">{o.target_qty || '—'}</td>
        <td className="px-3 py-1.5 text-right font-mono text-slate-400">{px(o.ref_price)}</td>
        <td className="px-3 py-1.5 text-right font-mono text-slate-400">{yuan(o.budget)}</td>
        <td className="px-3 py-1.5 text-xs text-slate-500">{o.reason}</td>
        <td className={`px-3 py-1.5 text-xs ${statusBadge[o.status] || ''}`}>
          {statusText[o.status] || o.status}
          {o.status === 'filled' && o.filled_qty != null && (
            <span className="text-slate-500 ml-1">{o.filled_qty}@{px(o.filled_price)}</span>
          )}
        </td>
        <td className="px-3 py-1.5">
          {o.status === 'pending' && (
            <div className="flex gap-2 text-xs">
              <button onClick={() => openFill(o)} className="text-blue-400 hover:text-blue-300">回填成交</button>
              <button onClick={() => skipOrder(o.id)} className="text-slate-500 hover:text-slate-300">放弃</button>
            </div>
          )}
        </td>
      </tr>
      {fillId === o.id && (
        <tr className="bg-slate-900/60">
          <td colSpan={9} className="px-3 py-2">
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span>回填 {o.name} 实际成交：</span>
              <input value={fillQty} onChange={(e: any) => setFillQty(e.target.value.replace(/\D/g, ''))} placeholder="成交股数"
                className="w-24 px-2 py-1 bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono outline-none focus:border-blue-500" />
              <input value={fillPrice} onChange={(e: any) => setFillPrice(e.target.value.replace(/[^\d.]/g, ''))} placeholder="成交价"
                className="w-24 px-2 py-1 bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono outline-none focus:border-blue-500" />
              <button onClick={doFill} disabled={busy || !fillQty || !fillPrice}
                className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded text-white">确认</button>
              <button onClick={cancelFill} className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-300">取消</button>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
