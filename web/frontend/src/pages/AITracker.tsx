import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  scanAITracker, getAIUniverse,
  addAISymbol, removeAISymbol,
  discoverAISymbols, approveAIPending, rejectAIPending,
  updateAIRevenue,
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

function ScoreBadge({ score, max = 12 }: { score: number; max?: number }) {
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
  const [tab, setTab] = useState<'tracker' | 'pending' | 'manage'>('tracker')
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
  const filteredRows = groupFilter === 'all' ? rows : rows.filter(r => r.group === groupFilter)
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
          { key: 'tracker', label: `追踪清单 (${rows.length})` },
          { key: 'pending', label: `待审核 (${pending.length})` },
          { key: 'manage',  label: '管理股票池' },
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
          {/* 子主题过滤 */}
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

          {/* 评分说明 */}
          <div className="text-xs text-slate-600 flex gap-3">
            <span>评分 /12：</span>
            <span>AI收入(3) Capex增速(2) NVDA联动(2) RS动量(2) 营收增速(2) AI新闻(1)</span>
          </div>

          {isLoading ? (
            <div className="text-center py-12 text-slate-500 text-sm">加载中，首次约需 1-2 分钟…</div>
          ) : (
            <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-400 border-b border-slate-700">
                    <th className="text-left px-4 py-2.5 font-medium">标的</th>
                    <th className="text-center px-3 py-2.5 font-medium">评分</th>
                    <th className="text-left px-3 py-2.5 font-medium">维度</th>
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
        <div>· 新闻评分基于近 30 天已缓存新闻中 AI 关键词命中数</div>
      </div>
    </div>
  )
}
