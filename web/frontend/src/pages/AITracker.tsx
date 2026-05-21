import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import {
  scanAITracker, getAIUniverse,
  addAISymbol, removeAISymbol,
  discoverAISymbols, approveAIPending, rejectAIPending,
  updateAIRevenue, getAIMomentum,
} from '../api/client'

// ── 工具函数 ──────────────────────────────────────────────────

function pct(v: number | null | undefined, digits = 0) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}

function fmt(v: number | null | undefined) {
  if (v == null) return '—'
  return v >= 1000 ? `$${(v / 1000).toFixed(1)}T` : `$${v.toFixed(1)}B`
}

function ScoreBadge({ score, max = 15 }: { score: number; max?: number }) {
  const pct = score / max
  const color = pct >= 0.7 ? 'bg-emerald-500' : pct >= 0.4 ? 'bg-amber-500' : 'bg-slate-600'
  return (
    <span className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold text-white ${color}`}>
      {score}
    </span>
  )
}

function GroupBadge({ label, color }: { label: string; color: string }) {
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded font-medium"
      style={{ background: color + '22', color }}>
      {label}
    </span>
  )
}

function BreakdownBar({ bd }: { bd: Record<string, number> }) {
  const items = [
    { key: 'ai_revenue', label: 'AI收入', max: 3 },
    { key: 'capex_growth', label: 'Capex', max: 2 },
    { key: 'nvda_corr', label: '联动', max: 2 },
    { key: 'rs', label: 'RS', max: 2 },
    { key: 'rev_growth', label: '营收', max: 2 },
    { key: 'news', label: '新闻', max: 1 },
    { key: 'tech', label: '技术', max: 3 },
  ]
  return (
    <div className="flex gap-1">
      {items.map(it => {
        const v = bd[it.key] ?? 0
        const filled = v > 0
        return (
          <span key={it.key} title={`${it.label}: ${v}/${it.max}`}
            className={`text-[10px] px-1 py-0.5 rounded ${filled ? 'bg-blue-700 text-blue-200' : 'bg-slate-700 text-slate-500'}`}>
            {it.label}
          </span>
        )
      })}
    </div>
  )
}

// ── 主页面 ─────────────────────────────────────────────────────

export default function AITracker() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'tracker' | 'momentum' | 'pending' | 'manage'>('tracker')
  const [groupFilter, setGroupFilter] = useState<string>('all')
  const [forcing, setForcing] = useState(false)
  const [addForm, setAddForm] = useState<{ symbol: string; group: string } | null>(null)
  const [editRevenue, setEditRevenue] = useState<{ symbol: string; ai_pct: number; note: string } | null>(null)
  const [discovering, setDiscovering] = useState(false)

  const { data: scanData, isLoading } = useQuery({
    queryKey: ['ai-tracker-scan'],
    queryFn: () => scanAITracker(false),
    staleTime: 4 * 3_600_000,
    retry: false,
  })

  const { data: universe } = useQuery({
    queryKey: ['ai-universe'],
    queryFn: getAIUniverse,
    staleTime: 60_000,
  })

  const refresh = () => {
    setForcing(true)
    scanAITracker(true)
      .then(d => { qc.setQueryData(['ai-tracker-scan'], d); setForcing(false) })
      .catch(() => setForcing(false))
  }

  const discover = () => {
    setDiscovering(true)
    discoverAISymbols(30)
      .then(() => { qc.invalidateQueries({ queryKey: ['ai-universe'] }); setDiscovering(false) })
      .catch(() => setDiscovering(false))
  }

  const removeMutation = useMutation({
    mutationFn: removeAISymbol,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-universe'] })
      qc.invalidateQueries({ queryKey: ['ai-tracker-scan'] })
    },
  })

  const addMutation = useMutation({
    mutationFn: ({ group, symbol }: { group: string; symbol: string }) => addAISymbol(group, symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-universe'] })
      qc.invalidateQueries({ queryKey: ['ai-tracker-scan'] })
      setAddForm(null)
    },
  })

  const approveMutation = useMutation({
    mutationFn: ({ symbol, group }: { symbol: string; group: string }) => approveAIPending(symbol, group),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-universe'] }),
  })

  const rejectMutation = useMutation({
    mutationFn: rejectAIPending,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-universe'] }),
  })

  const revenueMutation = useMutation({
    mutationFn: ({ symbol, ai_pct, note }: { symbol: string; ai_pct: number; note: string }) =>
      updateAIRevenue(symbol, ai_pct, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-tracker-scan'] })
      setEditRevenue(null)
    },
  })

  const groups: Record<string, { label: string; color: string }> = scanData?.groups
    ? Object.fromEntries(
        Object.entries(scanData.groups as Record<string, string>).map(([k, label]) => [
          k, { label, color: (scanData.group_colors as Record<string, string>)[k] ?? '#94a3b8' }
        ])
      )
    : {}

  const rows: any[] = scanData?.rows ?? []
  const filteredRows = rows.filter(r => {
    if (groupFilter !== 'all' && r.group !== groupFilter) return false
    return true
  })
  const pending: any[] = universe?.pending_review ?? []

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-200">AI 基建追踪器</h1>
          <p className="text-xs text-slate-500 mt-0.5">GPU/算力 · 数据中心/网络 · 电力/冷却 产业链评分监控</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {scanData?.last_updated && <span>更新于 {scanData.last_updated.slice(0, 16)}</span>}
          <button onClick={refresh} disabled={forcing || isLoading}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-300 transition-colors">
            {forcing ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {/* Tab 导航 */}
      <div className="flex gap-0 border-b border-slate-700">
        {[
          { key: 'tracker',  label: `追踪清单 (${rows.length})` },
          { key: 'momentum', label: '动能轮动' },
          { key: 'pending',  label: `待审核 (${pending.length})` },
          { key: 'manage',   label: '管理股票池' },
        ].map(t => (
          <button key={t.key} onClick={() => setTab(t.key as any)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab === t.key
                ? 'border-blue-500 text-white font-medium'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab: 追踪清单 ─────────────────────────────────────── */}
      {tab === 'tracker' && (
        <div className="space-y-3">
          {/* 过滤栏：子主题 + 市值范围 */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => setGroupFilter('all')}
                className={`px-3 py-1 text-xs rounded transition-colors ${
                  groupFilter === 'all' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
                全部
              </button>
              {Object.entries(groups).map(([gk, gv]) => (
                <button key={gk} onClick={() => setGroupFilter(gk)}
                  className={`px-3 py-1 text-xs rounded transition-colors ${
                    groupFilter === gk ? 'text-white' : 'text-slate-300 hover:opacity-80'}`}
                  style={groupFilter === gk ? { background: gv.color } : { background: gv.color + '33' }}>
                  {gv.label}
                </button>
              ))}
            </div>
            <div className="ml-auto text-xs text-slate-500">
              共 {filteredRows.length} 只
            </div>
          </div>

          {/* 评分说明 */}
          <div className="text-xs text-slate-600 flex gap-3">
            <span>评分 /15：</span>
            <span>AI收入(3) Capex增速(2) NVDA联动(2) RS动量(2) 营收增速(2) AI新闻(1) 技术信号(3：突破+量能+趋势)</span>
          </div>

          {isLoading ? (
            <div className="text-center py-12 text-slate-500 text-sm">加载中，首次约需 1-2 分钟…</div>
          ) : (
            <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-400 border-b border-slate-700">
                    <th className="text-left px-4 py-2.5 font-medium">标的</th>
                    <th className="text-center px-3 py-2.5 font-medium">评分/15</th>
                    <th className="text-left px-3 py-2.5 font-medium">维度</th>
                    <th className="text-center px-3 py-2.5 font-medium">信号</th>
                    <th className="text-right px-3 py-2.5 font-medium">AI营收</th>
                    <th className="text-right px-3 py-2.5 font-medium">Capex增速</th>
                    <th className="text-right px-3 py-2.5 font-medium">NVDA相关</th>
                    <th className="text-right px-3 py-2.5 font-medium">RS vs SPY</th>
                    <th className="text-right px-3 py-2.5 font-medium">营收增速</th>
                    <th className="text-right px-3 py-2.5 font-medium">市值</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((r: any) => (
                    <tr key={r.symbol} className="border-b border-slate-700/50 hover:bg-slate-750 transition-colors">
                      <td className="px-4 py-2">
                        <div className="font-medium text-white">{r.symbol}</div>
                        <GroupBadge label={r.group_label} color={r.group_color} />
                      </td>
                      <td className="px-3 py-2 text-center">
                        <ScoreBadge score={r.score} />
                      </td>
                      <td className="px-3 py-2">
                        <BreakdownBar bd={r.breakdown ?? {}} />
                      </td>
                      {/* 技术信号列 */}
                      <td className="px-3 py-2 text-center">
                        <div className="flex items-center justify-center gap-1">
                          {r.signal === 1 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-200 font-semibold">买</span>
                          )}
                          {r.signal === -1 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-800 text-red-300 font-semibold">警</span>
                          )}
                          {r.breakout && <span title="价格突破" className="text-yellow-400 text-xs">⚡</span>}
                          {r.vol_surge && <span title="成交量放量" className="text-blue-400 text-xs">▲</span>}
                          {r.uptrend  && <span title="趋势向上"   className="text-emerald-400 text-xs">↑</span>}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          title="点击编辑 AI 营收占比"
                          onClick={() => setEditRevenue({ symbol: r.symbol, ai_pct: r.ai_revenue_pct ?? 0, note: r.ai_revenue_note ?? '' })}
                          className={`font-mono text-xs hover:underline ${r.ai_revenue_pct ? 'text-emerald-400' : 'text-slate-500'}`}>
                          {r.ai_revenue_pct != null ? pct(r.ai_revenue_pct, 0) : '待录入'}
                        </button>
                      </td>
                      <td className={`px-3 py-2 text-right font-mono text-xs ${(r.capex_growth ?? 0) > 0 ? 'text-emerald-400' : 'text-slate-400'}`}>
                        {r.capex_growth != null ? pct(r.capex_growth, 0) : '—'}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono text-xs ${(r.nvda_corr ?? 0) > 0.6 ? 'text-blue-400' : 'text-slate-400'}`}>
                        {r.nvda_corr != null ? r.nvda_corr.toFixed(2) : '—'}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono text-xs ${(r.rs_score ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {r.rs_score != null ? pct(r.rs_score, 1) : '—'}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono text-xs ${(r.revenue_growth ?? 0) > 0 ? 'text-emerald-400' : 'text-slate-400'}`}>
                        {r.revenue_growth != null ? pct(r.revenue_growth, 0) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-slate-400">
                        {fmt(r.market_cap_b)}
                      </td>
                    </tr>
                  ))}
                  {filteredRows.length === 0 && (
                    <tr><td colSpan={9} className="text-center py-8 text-slate-500 text-sm">暂无数据，点击刷新获取</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Tab: 动能轮动 ─────────────────────────────────────── */}
      {tab === 'momentum' && <MomentumTab />}

      {/* ── Tab: 待审核 ───────────────────────────────────────── */}
      {tab === 'pending' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-500">自动发现的候选标的，审核后加入正式追踪清单</p>
            <button onClick={discover} disabled={discovering}
              className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-300 transition-colors">
              {discovering ? '发现中…' : '自动发现'}
            </button>
          </div>

          {pending.length === 0 ? (
            <div className="text-center py-8 text-slate-500 text-sm">无待审核标的，点击「自动发现」扫描 S&P500/NDX</div>
          ) : (
            <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-400 border-b border-slate-700">
                    <th className="text-left px-4 py-2.5 font-medium">标的</th>
                    <th className="text-left px-3 py-2.5 font-medium">行业</th>
                    <th className="text-right px-3 py-2.5 font-medium">市值</th>
                    <th className="text-right px-3 py-2.5 font-medium">营收增速</th>
                    <th className="text-left px-3 py-2.5 font-medium">建议分组</th>
                    <th className="text-center px-3 py-2.5 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {pending.map((p: any) => (
                    <tr key={p.symbol} className="border-b border-slate-700/50">
                      <td className="px-4 py-2 font-medium text-white">{p.symbol}</td>
                      <td className="px-3 py-2 text-xs text-slate-400">{p.industry || p.sector || '—'}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-slate-400">{fmt(p.market_cap_b)}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs text-slate-400">
                        {p.revenue_growth != null ? pct(p.revenue_growth, 0) : '—'}
                      </td>
                      <td className="px-3 py-2">
                        <select
                          defaultValue={p.suggest_group}
                          id={`group-select-${p.symbol}`}
                          className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none">
                          {Object.entries(groups).map(([gk, gv]) => (
                            <option key={gk} value={gk}>{gv.label}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <div className="flex gap-2 justify-center">
                          <button
                            onClick={() => {
                              const sel = document.getElementById(`group-select-${p.symbol}`) as HTMLSelectElement
                              approveMutation.mutate({ symbol: p.symbol, group: sel?.value || p.suggest_group })
                            }}
                            className="px-2 py-1 text-xs bg-emerald-700 hover:bg-emerald-600 rounded text-white transition-colors">
                            加入
                          </button>
                          <button
                            onClick={() => rejectMutation.mutate(p.symbol)}
                            className="px-2 py-1 text-xs bg-slate-600 hover:bg-slate-500 rounded text-slate-300 transition-colors">
                            忽略
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Tab: 管理股票池 ────────────────────────────────────── */}
      {tab === 'manage' && (
        <div className="space-y-4">
          {Object.entries(groups).map(([gk, gv]) => {
            const syms: string[] = universe?.groups?.[gk]?.symbols ?? []
            return (
              <div key={gk} className="bg-slate-800 rounded-lg p-4 border border-slate-700">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: gv.color }} />
                    <span className="text-sm font-medium text-white">{gv.label}</span>
                    <span className="text-xs text-slate-500">({syms.length} 只)</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {syms.map(sym => (
                    <span key={sym} className="inline-flex items-center gap-1 px-2 py-1 bg-slate-700 rounded text-xs text-slate-200">
                      {sym}
                      <button onClick={() => removeMutation.mutate(sym)}
                        className="text-slate-500 hover:text-red-400 transition-colors ml-1">✕</button>
                    </span>
                  ))}
                  {/* 添加按钮 */}
                  {addForm?.group === gk ? (
                    <span className="inline-flex items-center gap-1">
                      <input autoFocus value={addForm.symbol}
                        onChange={e => setAddForm({ ...addForm, symbol: e.target.value.toUpperCase() })}
                        onKeyDown={e => {
                          if (e.key === 'Enter' && addForm.symbol) addMutation.mutate({ group: gk, symbol: addForm.symbol })
                          if (e.key === 'Escape') setAddForm(null)
                        }}
                        placeholder="TICKER"
                        className="w-20 bg-slate-700 border border-blue-500 rounded px-2 py-1 text-xs text-white focus:outline-none" />
                      <button onClick={() => addForm.symbol && addMutation.mutate({ group: gk, symbol: addForm.symbol })}
                        className="text-xs px-1.5 py-1 bg-blue-600 hover:bg-blue-500 rounded text-white">+</button>
                      <button onClick={() => setAddForm(null)} className="text-xs text-slate-500 hover:text-slate-300">✕</button>
                    </span>
                  ) : (
                    <button onClick={() => setAddForm({ group: gk, symbol: '' })}
                      className="px-2 py-1 text-xs rounded border border-dashed border-slate-600 text-slate-500 hover:border-blue-500 hover:text-blue-400 transition-colors">
                      + 添加
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── AI 营收占比编辑弹窗 ────────────────────────────────── */}
      {editRevenue && (
        <>
          <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setEditRevenue(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-slate-800 border border-slate-600 rounded-xl shadow-2xl p-5 w-72 space-y-3">
              <div className="text-sm font-medium text-white">{editRevenue.symbol} — AI 营收占比</div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">AI 营收占比 %</label>
                <input type="number" min={0} max={100} step={1}
                  value={Math.round(editRevenue.ai_pct * 100)}
                  onChange={e => setEditRevenue({ ...editRevenue, ai_pct: Number(e.target.value) / 100 })}
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">备注（数据来源）</label>
                <input value={editRevenue.note}
                  onChange={e => setEditRevenue({ ...editRevenue, note: e.target.value })}
                  placeholder="如：Data Center ~87% FY2025"
                  className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => revenueMutation.mutate(editRevenue)}
                  className="flex-1 py-1.5 bg-blue-600 hover:bg-blue-500 rounded text-white text-sm transition-colors">
                  保存
                </button>
                <button onClick={() => setEditRevenue(null)}
                  className="px-4 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-300 text-sm transition-colors">
                  取消
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* 说明 */}
      <div className="text-xs text-slate-600 space-y-0.5">
        <div>· AI营收占比需手动维护（点击数值编辑），数据来源：公司财报/业绩会</div>
        <div>· Capex增速/NVDA相关性/RS动量为实时计算，首次加载约需 1-2 分钟</div>
        <div>· 技术信号（⚡突破 ▲量能 ↑趋势）来自 RSMomentum，"买"=满足5个条件，"警"=量价背离</div>
        <div>· 市场扫描器 "AI产业链" 模式只跑 $10B–$500B（本追踪器含超大市值全覆盖）</div>
        <div>· 自动发现扫描 S&P500+NDX+Russell2000，过滤 $10B–$500B AI 相关标的</div>
      </div>
    </div>
  )
}


// ═══════════════════════════════════════════════════════════════
// 动能轮动 Tab
// ═══════════════════════════════════════════════════════════════

type MomentumRow = {
  symbol: string; group: string; group_label: string; group_color: string
  close: number
  mom_3d: number | null; mom_5d: number | null; mom_10d: number | null
  rs_3d: number | null; rs_5d: number | null; rs_10d: number | null
  rs_vs_group_5d: number | null
  vol_ratio: number | null
  obv_slope: number | null; up_vol_ratio: number | null; flow_score: number
  accel: boolean
  composite: number; rank: number
  z_mom_5d: number; z_mom_3d: number; z_rs_group: number; z_vol_ratio: number
}

type GroupSummary = {
  key: string; label: string; color: string; count: number
  median_mom_5d: number | null; median_rs_5d: number | null
  advance: number; decline: number
  flow_score: number; flow_signal: 'inflow' | 'neutral' | 'outflow'
  leaders: { symbol: string; composite: number }[]
}

type BasketFlow = {
  dates: string[]
  ad_daily: number[]; ad_cumulative: number[]
  money_flow_b: number[]; money_flow_cum_b: number[]
  advance_today: number; decline_today: number
  advance_5d: number; money_flow_5d_b: number
}

function MomentumTab() {
  const qc = useQueryClient()
  const [window, setWindow] = useState<'3d' | '5d' | '10d'>('5d')
  const [groupFilter, setGroupFilter] = useState<string>('all')
  const [holdings, setHoldings] = useState<string>('')   // 逗号分隔的持仓
  const [forcing, setForcing] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['ai-momentum'],
    queryFn: () => getAIMomentum(false),
    staleTime: 30 * 60_000,
    retry: false,
  })

  const refresh = () => {
    setForcing(true)
    getAIMomentum(true)
      .then(d => { qc.setQueryData(['ai-momentum'], d); setForcing(false) })
      .catch(() => setForcing(false))
  }

  if (isLoading) {
    return <div className="text-center py-12 text-slate-500 text-sm">动能数据计算中…</div>
  }

  const rows: MomentumRow[] = data?.rows ?? []
  const groups: GroupSummary[] = data?.groups ?? []
  const basket: BasketFlow | null = data?.basket ?? null
  const top4: string[] = data?.top4 ?? []

  const holdingsSet = new Set(
    holdings.split(/[,\s，]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
  )
  // 选窗口对应的相对 SPY 字段，并据此重新排序（5d 用复合分，其他用窗口动能）
  const rsField: keyof MomentumRow = window === '3d' ? 'rs_3d' : window === '5d' ? 'rs_5d' : 'rs_10d'
  const momField: keyof MomentumRow = window === '3d' ? 'mom_3d' : window === '5d' ? 'mom_5d' : 'mom_10d'
  const sortKey: keyof MomentumRow = window === '5d' ? 'composite' : momField
  const filteredRows = rows
    .filter(r => groupFilter === 'all' || r.group === groupFilter)
    .slice()
    .sort((a, b) => ((b[sortKey] as number) ?? -999) - ((a[sortKey] as number) ?? -999))

  const windowLabel = window === '3d' ? '3 日' : window === '5d' ? '5 日' : '10 日'

  // Top-4 vs 持仓对比
  const sellSuggest = Array.from(holdingsSet).filter(s => !top4.includes(s))
  const buySuggest = top4.filter(s => !holdingsSet.has(s))

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1">
          {(['3d', '5d', '10d'] as const).map(w => (
            <button key={w} onClick={() => setWindow(w)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                window === w ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              {w === '3d' ? '3 日' : w === '5d' ? '5 日' : '10 日'}
            </button>
          ))}
        </div>
        <div className="flex gap-1 ml-2 flex-wrap">
          <button onClick={() => setGroupFilter('all')}
            className={`px-2.5 py-1 text-xs rounded transition-colors ${
              groupFilter === 'all' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
            全部
          </button>
          {groups.map(g => (
            <button key={g.key} onClick={() => setGroupFilter(g.key)}
              className={`px-2.5 py-1 text-xs rounded transition-colors ${
                groupFilter === g.key ? 'text-white' : 'text-slate-300 hover:opacity-80'}`}
              style={groupFilter === g.key ? { background: g.color } : { background: g.color + '33' }}>
              {g.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-slate-500">
          {data?.last_updated && <span>更新于 {String(data.last_updated).slice(11, 16)}</span>}
          <button onClick={refresh} disabled={forcing}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-300">
            {forcing ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {/* 篮子资金流面板 */}
      {basket && <BasketFlowPanel basket={basket} />}

      {/* 子组热力 */}
      <div className="grid grid-cols-4 lg:grid-cols-8 gap-2">
        {groups.map(g => (
          <GroupCard key={g.key} g={g} window={window} active={groupFilter === g.key}
            onClick={() => setGroupFilter(groupFilter === g.key ? 'all' : g.key)} />
        ))}
      </div>

      {/* 持仓输入 + Top-4 建议 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2 bg-slate-800 rounded-lg border border-slate-700 p-3">
          <div className="text-xs text-slate-400 mb-1">当前持仓（逗号分隔，用于换股建议）</div>
          <input value={holdings} onChange={e => setHoldings(e.target.value)}
            placeholder="如：NVDA, AVGO, AMD"
            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
        </div>
        <div className="bg-slate-800 rounded-lg border border-slate-700 p-3 space-y-1.5">
          <div className="text-xs text-slate-400">Top-4 推荐</div>
          <div className="flex flex-wrap gap-1.5">
            {top4.map(s => (
              <span key={s} className={`px-2 py-0.5 text-xs rounded font-mono ${
                holdingsSet.has(s) ? 'bg-emerald-700 text-emerald-100' : 'bg-blue-700 text-blue-100'}`}>
                {s}{holdingsSet.has(s) && ' ✓'}
              </span>
            ))}
          </div>
          {(sellSuggest.length > 0 || buySuggest.length > 0) && holdingsSet.size > 0 && (
            <div className="text-[11px] text-slate-400 space-y-0.5 pt-1 border-t border-slate-700">
              {sellSuggest.length > 0 && <div>卖出：<span className="text-red-400 font-mono">{sellSuggest.join(', ')}</span></div>}
              {buySuggest.length > 0  && <div>换入：<span className="text-emerald-400 font-mono">{buySuggest.join(', ')}</span></div>}
            </div>
          )}
        </div>
      </div>

      {/* 个股排行表 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
        <div className="px-3 py-1.5 text-[11px] text-slate-500 border-b border-slate-700">
          当前窗口：<span className="text-blue-300 font-medium">{windowLabel}</span>
          {window === '5d'
            ? ' — 按复合分排序（5 日为复合分主权重）'
            : ` — 按 ${windowLabel}动能 降序排序（复合分仍为 5 日基准，供横向对比）`}
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-slate-400 border-b border-slate-700">
              <th className="text-left  px-3 py-2 font-medium">#</th>
              <th className="text-left  px-3 py-2 font-medium">标的</th>
              <th className="text-center px-2 py-2 font-medium">复合分</th>
              <th className={`text-right px-2 py-2 font-medium ${window === '3d'  ? 'bg-blue-900/40 text-blue-200' : ''}`}>3 日</th>
              <th className={`text-right px-2 py-2 font-medium ${window === '5d'  ? 'bg-blue-900/40 text-blue-200' : ''}`}>5 日</th>
              <th className={`text-right px-2 py-2 font-medium ${window === '10d' ? 'bg-blue-900/40 text-blue-200' : ''}`}>10 日</th>
              <th className="text-right px-2 py-2 font-medium">vs SPY ({windowLabel})</th>
              <th className="text-right px-2 py-2 font-medium">vs 组中位 (5d)</th>
              <th className="text-right px-2 py-2 font-medium">量比</th>
              <th className="text-center px-2 py-2 font-medium">加速</th>
              <th className="text-right px-2 py-2 font-medium">资金流</th>
              <th className="text-center px-2 py-2 font-medium">标签</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((r, idx) => {
              const isHold = holdingsSet.has(r.symbol)
              const isTop = top4.includes(r.symbol)
              const rsVs = r[rsField] as number | null
              return (
                <tr key={r.symbol} className={`border-b border-slate-700/50 hover:bg-slate-750 ${isHold ? 'bg-slate-750/40' : ''}`}>
                  <td className="px-3 py-1.5 text-slate-500 text-xs">{idx + 1}</td>
                  <td className="px-3 py-1.5">
                    <div className="font-medium text-white">{r.symbol}</div>
                    <GroupBadge label={r.group_label} color={r.group_color} />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <CompositeBadge score={r.composite} />
                  </td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_3d)}  ${window === '3d'  ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_3d)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_5d)}  ${window === '5d'  ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_5d)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_10d)} ${window === '10d' ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_10d)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(rsVs)}`}>{pctFmt(rsVs)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.rs_vs_group_5d)}`}>{pctFmt(r.rs_vs_group_5d)}</td>
                  <td className={`px-2 py-1.5 text-right font-mono text-xs ${(r.vol_ratio ?? 0) > 1.5 ? 'text-amber-400' : 'text-slate-400'}`}>
                    {r.vol_ratio != null ? r.vol_ratio.toFixed(2) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-center text-xs">
                    {r.accel ? <span className="text-yellow-400" title="动能加速">▲</span> : <span className="text-slate-700">·</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <FlowBar score={r.flow_score} />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {isTop && <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-700 text-blue-100 font-semibold mr-1">TOP4</span>}
                    {isHold && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-100 font-semibold">持有</span>}
                  </td>
                </tr>
              )
            })}
            {filteredRows.length === 0 && (
              <tr><td colSpan={12} className="text-center py-8 text-slate-500 text-sm">暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 说明 */}
      <div className="text-xs text-slate-600 space-y-0.5">
        <div>· 复合分 = 0.35×5日相对SPY + 0.20×3日相对SPY + 0.20×子组内排名 + 0.15×量比 + 0.10×资金流 (z-score 归一化到 0-10)</div>
        <div>· 加速 ▲：3日日均收益 &gt; 5日日均收益，说明动能在加快</div>
        <div>· 资金流分：OBV 5日斜率（标准化）+ 上涨日量/下跌日量比，绿=资金净流入、红=净流出</div>
        <div>· A/D 线：篮子内每日"上涨家数 - 下跌家数"累计；金额加权资金流：每日 sign(Δ价) × 价 × 量 求和</div>
        <div>· 30 分钟缓存，刷新强制重算</div>
      </div>
    </div>
  )
}


function CompositeBadge({ score }: { score: number }) {
  const color = score >= 7 ? 'bg-emerald-500' : score >= 5 ? 'bg-amber-500' : 'bg-slate-600'
  return (
    <span className={`inline-flex items-center justify-center w-9 h-7 rounded text-xs font-bold text-white ${color}`}>
      {score.toFixed(1)}
    </span>
  )
}

function FlowBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(10, score)) / 10
  const color = score >= 6 ? '#10b981' : score >= 4 ? '#64748b' : '#ef4444'
  return (
    <div className="inline-flex items-center gap-1.5">
      <div className="w-12 h-1.5 rounded-full bg-slate-700 overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct * 100}%`, background: color }} />
      </div>
      <span className="font-mono text-[11px] text-slate-300 w-7 text-right">{score.toFixed(1)}</span>
    </div>
  )
}

function pctFmt(v: number | null | undefined, digits = 1) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}

function pctColor(v: number | null | undefined): string {
  if (v == null) return 'text-slate-500'
  if (v > 0.02) return 'text-emerald-400'
  if (v > 0)    return 'text-emerald-500/80'
  if (v < -0.02) return 'text-red-400'
  return 'text-red-500/80'
}

function GroupCard({ g, window, active, onClick }: { g: GroupSummary; window: '3d' | '5d' | '10d'; active: boolean; onClick: () => void }) {
  const rs5 = g.median_rs_5d ?? 0
  const intensity = Math.max(-1, Math.min(1, rs5 / 0.05))   // ±5% 满色
  const bg = intensity > 0
    ? `rgba(16, 185, 129, ${0.15 + Math.abs(intensity) * 0.35})`
    : `rgba(239, 68, 68, ${0.15 + Math.abs(intensity) * 0.35})`
  return (
    <button onClick={onClick}
      className={`text-left rounded-lg border p-2.5 transition-colors ${
        active ? 'border-blue-500' : 'border-slate-700 hover:border-slate-500'}`}
      style={{ background: bg }}>
      <div className="flex items-center gap-1.5 mb-1">
        <span className="w-2 h-2 rounded-full" style={{ background: g.color }} />
        <span className="text-[11px] text-slate-200 font-medium truncate">{g.label}</span>
      </div>
      <div className={`font-mono text-sm font-bold ${rs5 >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
        {pctFmt(g.median_rs_5d)}
      </div>
      <div className="text-[10px] text-slate-400 mt-0.5">
        涨 <span className="text-emerald-300">{g.advance}</span> / 跌 <span className="text-red-300">{g.decline}</span>
      </div>
      <div className="flex items-center gap-1 mt-1">
        <span className={`text-[10px] px-1 rounded ${
          g.flow_signal === 'inflow' ? 'bg-emerald-900/60 text-emerald-300'
          : g.flow_signal === 'outflow' ? 'bg-red-900/60 text-red-300'
          : 'bg-slate-700 text-slate-400'}`}>
          {g.flow_signal === 'inflow' ? '净流入' : g.flow_signal === 'outflow' ? '净流出' : '中性'}
        </span>
        <span className="text-[10px] text-slate-400 truncate">{g.leaders.slice(0, 2).map(l => l.symbol).join(' ')}</span>
        {/* 抑制 unused 警告 */}
        {window === '5d' && null}
      </div>
    </button>
  )
}

function BasketFlowPanel({ basket }: { basket: BasketFlow }) {
  const adOption = {
    grid: { left: 30, right: 10, top: 10, bottom: 18 },
    xAxis: { type: 'category', data: basket.dates, axisLabel: { fontSize: 9, color: '#64748b' }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9, color: '#64748b' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    tooltip: { trigger: 'axis' },
    series: [{
      type: 'line', data: basket.ad_cumulative, smooth: true,
      lineStyle: { color: '#3b82f6', width: 2 },
      areaStyle: { color: 'rgba(59,130,246,0.15)' },
      symbol: 'none',
    }],
  }

  const flowOption = {
    grid: { left: 35, right: 10, top: 10, bottom: 18 },
    xAxis: { type: 'category', data: basket.dates, axisLabel: { fontSize: 9, color: '#64748b' }, axisLine: { lineStyle: { color: '#334155' } } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9, color: '#64748b', formatter: '{value}B' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v?.toFixed?.(2) ?? v}B` },
    series: [{
      type: 'bar', data: basket.money_flow_b,
      itemStyle: { color: (p: any) => (p.value >= 0 ? '#10b981' : '#ef4444') },
    }],
  }

  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-xs text-slate-400">A/D 线（涨跌家数累计）</div>
          <div className="text-xs">
            今日 <span className="text-emerald-400 font-mono">{basket.advance_today}</span>
            <span className="text-slate-600 mx-1">/</span>
            <span className="text-red-400 font-mono">{basket.decline_today}</span>
            <span className="text-slate-600 mx-2">|</span>
            5日净 <span className={`font-mono ${basket.advance_5d >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {basket.advance_5d >= 0 ? '+' : ''}{basket.advance_5d}
            </span>
          </div>
        </div>
        <ReactECharts option={adOption} style={{ height: 110 }} />
      </div>
      <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-xs text-slate-400">金额加权资金流（每日 sign×价×量）</div>
          <div className="text-xs">
            5日累计 <span className={`font-mono ${basket.money_flow_5d_b >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {basket.money_flow_5d_b >= 0 ? '+' : ''}{basket.money_flow_5d_b.toFixed(1)}B
            </span>
          </div>
        </div>
        <ReactECharts option={flowOption} style={{ height: 110 }} />
      </div>
    </div>
  )
}
