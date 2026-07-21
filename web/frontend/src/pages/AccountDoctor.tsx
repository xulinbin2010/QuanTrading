import { useState, useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { diagnoseAccount, getAccountDoctorLatest } from '../api/client'
import {
  DEFAULT_KRW_RATE,
  parseAccountBlock,
  parsePositionsBlock,
  type DoctorAccount as Account,
  type DoctorPosition as Pos,
} from '../utils/accountDoctorParser'

const SEV = {
  crit: { dot: 'bg-red-500',     box: 'border-red-800/60 bg-red-900/15',       txt: 'text-red-300' },
  warn: { dot: 'bg-amber-500',   box: 'border-amber-800/60 bg-amber-900/15',   txt: 'text-amber-300' },
  good: { dot: 'bg-emerald-500', box: 'border-emerald-800/60 bg-emerald-900/15', txt: 'text-emerald-300' },
} as const

const fmt = (v: any) => (v == null || isNaN(Number(v)) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }))

const PIE = ['#2f80b8', '#3aa0a0', '#c99a3a', '#b5654a', '#8a6fb0', '#7aa055', '#c56b8a', '#5b8bd0', '#9aa0a8']

export default function AccountDoctor() {
  const [positions, setPositions] = useState<Pos[]>([])
  const [account, setAccount] = useState<Account>({})
  const [diagnosing, setDiagnosing] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [showPaste, setShowPaste] = useState(false)
  const [posPaste, setPosPaste] = useState('')
  const [accPaste, setAccPaste] = useState('')
  const [krwRate, setKrwRate] = useState(String(DEFAULT_KRW_RATE))

  // 实时解析预览：贴完立刻看到拆成啥样，错行当场发现
  const posPreview = useMemo(() => parsePositionsBlock(posPaste, Number(krwRate) || DEFAULT_KRW_RATE), [posPaste, krwRate])
  const accPreview = useMemo(() => parseAccountBlock(accPaste), [accPaste])

  // 载入上次诊断（本地缓存），预填输入表
  useEffect(() => {
    getAccountDoctorLatest().then((r) => {
      if (r && r.positions?.length) {
        setResult(r)
        setPositions(r.positions.map((p: any) => ({
          symbol: p.symbol, name: p.name, market_value_usd: p.market_value_usd,
          theme: p.theme, leverage_factor: p.leverage_factor, is_leveraged: p.is_leveraged, currency: p.currency,
        })))
        setAccount({
          net_liq: r.account?.net_liq, maint_margin: r.account?.maint_margin,
          excess_liquidity: r.account?.excess_liquidity, settled_cash: r.account?.settled_cash,
          unrealized_pnl: r.account?.unrealized_pnl,
        })
      }
    }).catch(() => {})
  }, [])

  const upd = (i: number, k: keyof Pos, v: any) =>
    setPositions((ps) => ps.map((p, j) => (j === i ? { ...p, [k]: v } : p)))
  const addRow = () => setPositions((ps) => [...ps, { symbol: '', theme: '其它', leverage_factor: 1 }])
  const rmRow = (i: number) => setPositions((ps) => ps.filter((_, j) => j !== i))

  function applyPaste() {
    setError(null)
    const ps = posPreview
    const acc = accPreview
    const accN = Object.keys(acc).length
    if (!ps.length && !accN) { setError('两个框都没解析出内容：①框贴持仓明细，②框贴账户总览。'); return }
    if (ps.length) setPositions(ps)
    if (accN) setAccount((a) => ({ ...a, ...acc }))
    setNote(`已解析 ${ps.length} 只持仓${accN ? ` + ${accN} 个账户字段` : ''}。已知 2X/3X 产品会自动识别；请核对「主题」与未收录产品的杠杆倍数后再诊断。`)
    setShowPaste(false); setPosPaste(''); setAccPaste('')
  }

  async function runDiagnose() {
    setError(null); setDiagnosing(true)
    try {
      const clean = positions
        .filter((p) => p.symbol && p.market_value_usd)
        .map((p) => ({ ...p, leverage_factor: Number(p.leverage_factor) || 1, market_value_usd: Number(p.market_value_usd) }))
      if (!clean.length) { setError('请至少填写一只有市值的持仓'); setDiagnosing(false); return }
      const r = await diagnoseAccount(account, clean)
      setResult(r)
    } catch (e: any) {
      setError(e.response?.data?.detail || '诊断失败')
    } finally {
      setDiagnosing(false)
    }
  }

  // ── 图表 option ──
  const pieOption = result && {
    tooltip: { trigger: 'item', formatter: (p: any) => `${p.name}<br/>$${fmt(p.value)} · ${p.percent}%` },
    series: [{
      type: 'pie', radius: ['42%', '72%'], center: ['50%', '50%'],
      itemStyle: { borderColor: '#0f172a', borderWidth: 2 },
      label: { color: '#cbd5e1', fontSize: 11, formatter: '{b} {d}%' },
      data: result.positions.map((p: any, i: number) => ({
        name: p.symbol, value: p.market_value_usd, itemStyle: { color: PIE[i % PIE.length] },
      })),
    }],
  }
  const barOption = result && {
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p: any) => `${p[0].name}<br/>敞口 ${p[0].value}% 净值` },
    xAxis: { type: 'value', axisLabel: { color: '#94a3b8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    yAxis: { type: 'category', data: result.themes.map((t: any) => t.theme), axisLabel: { color: '#cbd5e1' }, inverse: true },
    series: [{
      type: 'bar', barWidth: '58%',
      data: result.themes.map((t: any) => ({
        value: t.exposure_pct,
        itemStyle: { color: t.exposure_pct >= 100 ? '#dc4a4a' : t.exposure_pct >= 50 ? '#c99a3a' : '#2f80b8', borderRadius: [0, 4, 4, 0] },
      })),
      markLine: {
        symbol: 'none', label: { color: '#f87171', formatter: '100% 净值', fontSize: 10 },
        lineStyle: { color: '#dc4a4a', type: 'dashed' }, data: [{ xAxis: 100 }],
      },
    }],
  }

  const acc = result?.account
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-white">实盘诊断 <span className="text-slate-400 font-normal">🩺 桌面医生</span></h1>
        <p className="text-sm text-slate-400 mt-1">正式账户（不接 API）：手动填表或「📋 粘贴文本」录入持仓与保证金，诊断集中度、杠杆与爆仓风险。录入的持仓同时供「情报中心」做事件覆盖。数据全程本地，不外传。</p>
      </div>

      {note &&<div className="text-sm text-emerald-300 bg-emerald-900/15 border border-emerald-800/50 rounded-lg px-3 py-2">{note}</div>}
      {error && <div className="text-sm text-red-300 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">{error}</div>}

      {/* 输入表 */}
      <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium text-slate-200">持仓（可编辑核对）</div>
          <div className="flex items-center gap-3">
            <button onClick={() => setShowPaste((v) => !v)} className="text-xs text-blue-400 hover:text-blue-300">📋 粘贴文本</button>
            <button onClick={addRow} className="text-xs text-blue-400 hover:text-blue-300">+ 加一行</button>
          </div>
        </div>

        {showPaste && (
          <div className="bg-slate-900/60 border border-slate-700 rounded-lg p-3 space-y-4">
            <div className="text-xs text-slate-400 leading-relaxed">
              直接把券商导出的两块文本分别贴进下面两个框（多行折行、混币种、带 <span className="font-mono text-slate-300">—</span> 占位都能认）。纯本地解析，不走 API。解析器会按表头自动选择：有「最后价」时用 <span className="font-mono text-slate-300">持仓量 × 最后价</span> 反算；只有「市场价值」时直接读取该列。
            </div>

            {/* ① 持仓明细 */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="text-xs font-medium text-slate-300">① 持仓明细</div>
                <label className="text-[11px] text-slate-500 flex items-center gap-1">
                  KRW→USD 汇率
                  <input value={krwRate} onChange={(e) => setKrwRate(e.target.value)} inputMode="decimal"
                    className="w-16 bg-slate-800 border border-slate-600 rounded px-1.5 py-0.5 text-slate-200 text-[11px] font-mono text-right focus:outline-none focus:border-blue-500" />
                </label>
              </div>
              <textarea
                value={posPaste} onChange={(e) => setPosPaste(e.target.value)} rows={6}
                placeholder={'产品 持仓 最后价 变动% 成本基础 市场价值 平均价格 …\nMU  美光科技股份有限公司\n40  976.63  -5.39%  40,477  39,065.20  1011.93  —  -1,410\n000660 SK 海力士株式会社\n8  2424000  +10.84%  2070万  19,392,000  2590553.40 …'}
                className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-slate-200 text-sm font-mono focus:outline-none focus:border-blue-500 resize-y placeholder:text-slate-600"
              />
              {posPreview.length > 0 && (
                <div className="text-[11px] text-slate-400 bg-slate-800/60 rounded px-2 py-1.5">
                  解析出 <b className="text-emerald-300">{posPreview.length}</b> 只：
                  {posPreview.map((p) => (
                    <span key={p.symbol} className="inline-block mr-2 font-mono">
                      {p.symbol} ${fmt(p.market_value_usd)}
                      {Number(p.leverage_factor) > 1 && <span className="text-blue-300">·{p.leverage_factor}X</span>}
                      {p.currency === 'KRW' ? <span className="text-amber-400">·KRW折</span> : ''}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* ② 账户总览 */}
            <div className="space-y-1.5">
              <div className="text-xs font-medium text-slate-300">② 账户总览</div>
              <textarea
                value={accPaste} onChange={(e) => setAccPaste(e.target.value)} rows={5}
                placeholder={'账户\nU17851538\nUSD\n77,547.06\n已结算现金\n-11,376.92\n维持保证金\n53,504.08\n剩余流动性\n24,030.19'}
                className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-slate-200 text-sm font-mono focus:outline-none focus:border-blue-500 resize-y placeholder:text-slate-600"
              />
              {Object.keys(accPreview).length > 0 && (
                <div className="text-[11px] text-slate-400 bg-slate-800/60 rounded px-2 py-1.5 space-x-2 font-mono">
                  {([['net_liq', '净值'], ['settled_cash', '现金'], ['unrealized_pnl', '未实现'], ['maint_margin', '维持'], ['excess_liquidity', '剩余流动']] as const)
                    .filter(([k]) => (accPreview as any)[k] != null)
                    .map(([k, lbl]) => <span key={k}>{lbl} <b className="text-emerald-300">${fmt((accPreview as any)[k])}</b></span>)}
                </div>
              )}
            </div>

            <div className="flex gap-2 justify-end">
              <button onClick={() => { setShowPaste(false); setPosPaste(''); setAccPaste('') }} className="text-xs text-slate-400 hover:text-slate-200 px-3 py-1.5">取消</button>
              <button onClick={applyPaste} className="text-xs bg-blue-600 hover:bg-blue-700 text-white rounded px-4 py-1.5 font-medium">解析填入</button>
            </div>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700">
                <th className="text-left font-normal py-1.5 pr-2">代码</th>
                <th className="text-left font-normal pr-2">名称</th>
                <th className="text-right font-normal pr-2">市值(USD)</th>
                <th className="text-left font-normal pr-2 pl-2">主题</th>
                <th className="text-center font-normal pr-2">杠杆×</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i} className="border-b border-slate-800">
                  <td className="py-1 pr-2"><input value={p.symbol} onChange={(e) => upd(i, 'symbol', e.target.value)} className="w-20 bg-slate-700/60 rounded px-2 py-1 text-white text-sm font-mono focus:outline-none focus:border-blue-500 border border-transparent" /></td>
                  <td className="pr-2"><input value={p.name || ''} onChange={(e) => upd(i, 'name', e.target.value)} className="w-24 bg-slate-700/60 rounded px-2 py-1 text-slate-200 text-sm focus:outline-none border border-transparent focus:border-blue-500" /></td>
                  <td className="pr-2"><input value={p.market_value_usd ?? ''} onChange={(e) => upd(i, 'market_value_usd', e.target.value)} inputMode="decimal" className="w-24 bg-slate-700/60 rounded px-2 py-1 text-white text-sm font-mono text-right focus:outline-none border border-transparent focus:border-blue-500" /></td>
                  <td className="pr-2 pl-2"><input value={p.theme || ''} onChange={(e) => upd(i, 'theme', e.target.value)} className="w-24 bg-slate-700/60 rounded px-2 py-1 text-slate-200 text-sm focus:outline-none border border-transparent focus:border-blue-500" /></td>
                  <td className="text-center pr-2"><input value={p.leverage_factor ?? 1} onChange={(e) => { const v = e.target.value; upd(i, 'leverage_factor', v); upd(i, 'is_leveraged', Number(v) > 1) }} inputMode="decimal" className="w-12 bg-slate-700/60 rounded px-2 py-1 text-white text-sm font-mono text-center focus:outline-none border border-transparent focus:border-blue-500" /></td>
                  <td className="text-right"><button onClick={() => rmRow(i)} className="text-slate-500 hover:text-red-400 text-sm px-1">✕</button></td>
                </tr>
              ))}
              {!positions.length && <tr><td colSpan={6} className="text-center text-slate-500 text-sm py-6">点「📋 粘贴文本」贴券商导出，或「+ 加一行」手填</td></tr>}
            </tbody>
          </table>
        </div>

        {/* 账户/保证金字段 */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-2 border-t border-slate-700">
          {([
            ['net_liq', '净清算价值'], ['maint_margin', '维持保证金'], ['excess_liquidity', '剩余流动性'],
            ['settled_cash', '已结算现金'], ['unrealized_pnl', '未实现盈亏'],
          ] as const).map(([k, label]) => (
            <label key={k} className="block">
              <span className="text-[11px] text-slate-500">{label}</span>
              <input value={(account as any)[k] ?? ''} onChange={(e) => setAccount((a) => ({ ...a, [k]: e.target.value }))} inputMode="decimal"
                className="w-full mt-0.5 bg-slate-700/60 rounded px-2 py-1 text-white text-sm font-mono focus:outline-none border border-transparent focus:border-blue-500" />
            </label>
          ))}
        </div>

        <button onClick={runDiagnose} disabled={diagnosing}
          className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-semibold text-sm transition-colors disabled:opacity-40">
          {diagnosing ? '诊断中…' : '开始诊断'}
        </button>
      </div>

      {/* ── 诊断结果 ── */}
      {result && (
        <div className="space-y-6">
          {/* 指标条 */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { k: '净清算价值', v: '$' + fmt(acc.net_liq), sub: acc.settled_cash != null ? `现金 $${fmt(acc.settled_cash)}` : '' },
              { k: '维持保证金', v: '$' + fmt(acc.maint_margin), sub: acc.maint_rate != null ? `维持率 ${acc.maint_rate}%` : '', tone: 'warn' },
              { k: '剩余流动性', v: '$' + fmt(acc.excess_liquidity), sub: '归零=强平', tone: acc.excess_liquidity != null && acc.excess_liquidity < 0.2 * acc.net_liq ? 'crit' : 'good' },
              { k: '总经济敞口', v: acc.total_exposure_pct + '%', sub: '$' + fmt(acc.total_exposure), tone: acc.total_exposure_pct >= 100 ? 'crit' : 'good' },
            ].map((s: any) => (
              <div key={s.k} className="bg-slate-800 border border-slate-700 rounded-xl p-3.5">
                <div className="text-xs text-slate-400">{s.k}</div>
                <div className={`text-xl font-bold font-mono mt-1 ${s.tone === 'crit' ? 'text-red-400' : s.tone === 'warn' ? 'text-amber-400' : 'text-white'}`}>{s.v}</div>
                {s.sub && <div className="text-[11px] text-slate-500 mt-0.5">{s.sub}</div>}
              </div>
            ))}
          </div>

          {/* 风险清单 */}
          <div>
            <div className="text-sm font-medium text-slate-200 mb-2">诊断结论</div>
            <div className="space-y-2">
              {result.findings.map((f: any, i: number) => {
                const s = SEV[f.severity as keyof typeof SEV] || SEV.warn
                return (
                  <div key={i} className={`flex gap-3 items-start border rounded-lg px-4 py-3 ${s.box}`}>
                    <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${s.dot}`}></span>
                    <div>
                      <div className={`text-sm font-semibold ${s.txt}`}>{f.title}</div>
                      <div className="text-[13px] text-slate-300/90 mt-0.5 leading-relaxed">{f.detail}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* 图表 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
              <div className="text-sm font-medium text-slate-200 mb-1">持仓占比</div>
              <ReactECharts option={pieOption} style={{ height: 300 }} />
            </div>
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
              <div className="text-sm font-medium text-slate-200 mb-1">各主题经济敞口 vs 净值</div>
              <ReactECharts option={barOption} style={{ height: 300 }} />
            </div>
          </div>

          {/* 压力测试 */}
          {result.stress && (
            <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
              <div className="text-sm font-medium text-slate-200 mb-1">压力测试 · 主导主题「{result.stress.theme}」下行</div>
              <p className="text-xs text-slate-500 mb-3">
                {result.stress.trigger_shock != null
                  ? <>恒定维持率下约 <b className="text-red-400">−{result.stress.trigger_shock}%</b> 触发强制平仓；券商急跌中会上调维持率，真实更早。</>
                  : <>缺保证金字段，仅估算账户回撤（填入维持保证金/剩余流动性可算追缴线）。</>}
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[440px]">
                  <thead>
                    <tr className="text-xs text-slate-500 border-b border-slate-700">
                      <th className="text-left font-normal py-1.5">{result.stress.theme} 下行</th>
                      <th className="text-right font-normal">净清算</th>
                      <th className="text-right font-normal">账户跌幅</th>
                      <th className="text-right font-normal">剩余流动性</th>
                      <th className="text-right font-normal pl-3">状态</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {result.stress.rows.map((r: any) => {
                      const s = SEV[r.status as keyof typeof SEV] || SEV.good
                      return (
                        <tr key={r.shock} className="border-b border-slate-800">
                          <td className="py-2">−{r.shock}%</td>
                          <td className="text-right">${fmt(r.net_liq)}</td>
                          <td className="text-right text-red-400">{r.drawdown_pct}%</td>
                          <td className="text-right">{r.excess_liquidity != null ? '$' + fmt(r.excess_liquidity) : '—'}</td>
                          <td className="text-right pl-3"><span className={`inline-block w-2 h-2 rounded-full ${s.dot}`}></span></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="text-xs text-slate-500 leading-relaxed">
            假设：2x 每日 ETF 回撤按每倍杠杆 0.875 系数（2x≈1.75×底层，源于实测下行凸性）；主导主题 beta 1.0、其余 0.5；维持率取输入快照隐含值恒定。以上为情景推演、非投资建议，真实追缴线以券商实时数据为准。{acc && result.as_of ? ` · 诊断于 ${result.as_of}` : ''}
          </div>
        </div>
      )}
    </div>
  )
}
