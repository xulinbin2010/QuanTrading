import { useState } from 'react'
import SymbolLink from '../components/SymbolLink'
import { AI_COMPANY_META, AI_CHAIN_LAYERS } from '../data/aiCompanyMeta'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import {
  scanAITracker, getAIUniverse,
  addAISymbol, removeAISymbol,
  approveAIPending, rejectAIPending,
  getAIMomentum, analyzeAISymbol,
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

function GroupBadge({ label, color }: { label: string; color: string }) {
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded font-medium"
      style={{ background: color + '22', color }}>
      {label}
    </span>
  )
}

// ── 主页面 ─────────────────────────────────────────────────────

export default function AITracker() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'tracker' | 'momentum'>('tracker')
  const [showAddTool, setShowAddTool] = useState(false)
  const [forcing, setForcing] = useState(false)
  const [addForm, setAddForm] = useState<{ symbol: string; group: string } | null>(null)
  const [analyzeInput, setAnalyzeInput] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeResult, setAnalyzeResult] = useState<any | null>(null)
  const [analyzeError, setAnalyzeError] = useState<string | null>(null)
  const [analyzeGroupOverride, setAnalyzeGroupOverride] = useState<string>('')

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

  const runAnalyze = () => {
    const sym = analyzeInput.trim().toUpperCase()
    if (!sym) return
    setAnalyzing(true)
    setAnalyzeError(null)
    setAnalyzeResult(null)
    analyzeAISymbol(sym)
      .then((d: any) => {
        setAnalyzeResult(d)
        setAnalyzeGroupOverride(d.suggest_group || '')
        setAnalyzing(false)
      })
      .catch((e: any) => {
        setAnalyzeError(e?.response?.data?.detail || e?.message || '分析失败')
        setAnalyzing(false)
      })
  }

  const confirmAddAnalyzed = () => {
    if (!analyzeResult) return
    const group = analyzeGroupOverride || analyzeResult.suggest_group
    if (!group) return
    addMutation.mutate(
      { group, symbol: analyzeResult.symbol },
      { onSuccess: () => { setAnalyzeResult(null); setAnalyzeInput(''); setAnalyzeGroupOverride('') } },
    )
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

  // 子主题 label/color 直接取自 universe（ai_universe.json），不依赖 scan，图谱秒开
  const groups: Record<string, { label: string; color: string }> = universe?.groups
    ? Object.fromEntries(
        Object.entries(universe.groups as Record<string, any>).map(([k, v]) => [
          k, { label: v.label, color: v.color ?? '#94a3b8' }
        ])
      )
    : {}

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
          { key: 'tracker',  label: '产业图谱' },
          { key: 'momentum', label: '动能轮动' },
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

      {/* ── Tab: 追踪清单（AI 硬件产业链图谱）─────────────────── */}
      {tab === 'tracker' && (
        <div className="space-y-4">
          {/* 说明 + 工具条 */}
          <div className="flex items-start gap-2 flex-wrap">
            <div className="text-[11px] text-slate-500 bg-slate-900/40 border border-slate-700/50 rounded px-2.5 py-1.5 leading-relaxed flex-1 min-w-[280px]">
              <span className="text-slate-400">AI 硬件产业链图谱</span>：上下游分层 + 子主题分区，每张卡=一家公司主营。点代码看 K 线；评分/动量见「<span className="text-slate-300">动能轮动</span>」。卡片悬停可移除，子主题旁 <span className="text-slate-400">＋</span> 添加。
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button onClick={() => setShowAddTool(v => !v)}
                className={`px-3 py-1.5 text-xs rounded transition-colors ${showAddTool ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
                ➕ 手动加入
              </button>
            </div>
          </div>

          {/* 手动加入折叠面板（输入代码→识别行业→选组加入） */}
          {showAddTool && (() => {
            const ar = analyzeResult
            const canAdd = ar && !ar.already_in_group && (analyzeGroupOverride || ar.suggest_group)
            return (
              <div className="bg-slate-800 rounded-lg p-3 border border-slate-700">
                <div className="flex gap-2">
                  <input value={analyzeInput} autoFocus
                    onChange={e => setAnalyzeInput(e.target.value.toUpperCase())}
                    onKeyDown={e => { if (e.key === 'Enter') runAnalyze() }}
                    placeholder="输入代码，自动识别行业并推荐分组，如 NVTS"
                    className="flex-1 max-w-[300px] bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500" />
                  <button onClick={runAnalyze} disabled={analyzing || !analyzeInput.trim()}
                    className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white transition-colors">
                    {analyzing ? '分析中…' : '分析'}
                  </button>
                </div>
                {analyzeError && <div className="mt-2 text-xs text-red-400">{analyzeError}</div>}
                {ar && (
                  <div className="mt-3 bg-slate-700/40 border border-slate-600 rounded p-3 space-y-2">
                    <div className="flex items-baseline gap-3 flex-wrap">
                      <span className="text-base font-semibold text-white">{ar.symbol}</span>
                      <span className="text-xs text-slate-400">{ar.industry || '—'}</span>
                      <span className="text-xs text-slate-300 font-mono">市值 {fmt(ar.market_cap_b)}</span>
                      <span className="text-xs text-slate-300 font-mono">营收 {ar.revenue_growth != null ? pct(ar.revenue_growth, 0) : '—'}</span>
                    </div>
                    <div className="text-xs text-slate-300">{ar.reason}</div>
                    {ar.already_in_group ? (
                      <div className="text-xs text-amber-400">已在池中</div>
                    ) : (
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-slate-400">加入分组：</span>
                        <select value={analyzeGroupOverride} onChange={e => setAnalyzeGroupOverride(e.target.value)}
                          className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none">
                          <option value="">— 选择分组 —</option>
                          {Object.entries(groups).map(([gk, gv]) => (
                            <option key={gk} value={gk}>{gv.label}{gk === ar.suggest_group ? '（推荐）' : ''}</option>
                          ))}
                        </select>
                        <button onClick={confirmAddAnalyzed} disabled={!canAdd || addMutation.isPending}
                          className="px-3 py-1 text-xs bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 rounded text-white transition-colors">加入</button>
                        <button onClick={() => { setAnalyzeResult(null); setAnalyzeError(null) }}
                          className="px-2 py-1 text-xs text-slate-400 hover:text-slate-200">取消</button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })()}

          {/* 产业链图谱（含管理：卡片悬停✕移除 / 子主题＋添加） */}
          {AI_CHAIN_LAYERS.map((layer, li) => (
            <div key={layer.title}>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-sm font-semibold text-slate-200">{layer.title}</span>
                <span className="text-[11px] text-slate-500">— {layer.flow}</span>
              </div>
              <div className="space-y-3 pl-3 border-l-2 border-slate-700/60">
                {layer.groups.map(gk => {
                  const gnode: any = universe?.groups?.[gk]
                  if (!gnode) return null
                  const color = gnode.color ?? '#94a3b8'
                  const syms: string[] = gnode.symbols ?? []
                  return (
                    <div key={gk}>
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <span className="w-2 h-2 rounded-full" style={{ background: color }} />
                        <span className="text-xs font-medium text-slate-300">{gnode.label}</span>
                        <span className="text-[10px] text-slate-600">{syms.length}</span>
                        <button onClick={() => setAddForm({ group: gk, symbol: '' })} title="添加到本组"
                          className="text-slate-600 hover:text-blue-400 text-sm leading-none ml-0.5">＋</button>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-2">
                        {syms.map(sym => {
                          const meta = AI_COMPANY_META[sym]
                          return (
                            <div key={sym}
                              className="group relative bg-slate-800/70 border border-slate-700/60 rounded-lg px-2.5 py-2 hover:border-slate-500 hover:bg-slate-800 transition-colors"
                              style={{ borderLeftColor: color, borderLeftWidth: 3 }}>
                              <button onClick={() => removeMutation.mutate(sym)} title="从池中移除"
                                className="absolute top-1 right-1 text-slate-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-xs leading-none">✕</button>
                              <div className="flex items-baseline gap-1.5">
                                <SymbolLink symbol={sym} className="font-semibold text-white text-sm" />
                                {meta?.name && <span className="text-[11px] text-slate-400 truncate">{meta.name}</span>}
                              </div>
                              <div className="text-[10px] text-slate-500 mt-0.5 leading-snug truncate" title={meta?.desc || ''}>
                                {meta?.desc || '—'}
                              </div>
                            </div>
                          )
                        })}
                        {/* 添加输入卡片 */}
                        {addForm?.group === gk && (
                          <div className="bg-slate-700/30 border border-blue-500 rounded-lg p-2 flex flex-col gap-1">
                            <input autoFocus value={addForm.symbol}
                              onChange={e => setAddForm({ ...addForm, symbol: e.target.value.toUpperCase() })}
                              onKeyDown={e => {
                                if (e.key === 'Enter' && addForm.symbol) addMutation.mutate({ group: gk, symbol: addForm.symbol })
                                if (e.key === 'Escape') setAddForm(null)
                              }}
                              placeholder="TICKER"
                              className="bg-slate-700 border border-slate-600 rounded px-2 py-0.5 text-xs text-white focus:outline-none focus:border-blue-500 font-mono" />
                            <div className="flex gap-1">
                              <button onClick={() => addForm.symbol && addMutation.mutate({ group: gk, symbol: addForm.symbol })}
                                className="flex-1 text-xs py-0.5 bg-blue-600 hover:bg-blue-500 rounded text-white">添加</button>
                              <button onClick={() => setAddForm(null)}
                                className="text-xs px-2 text-slate-500 hover:text-slate-300">取消</button>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
              {li < AI_CHAIN_LAYERS.length - 1 && (
                <div className="flex justify-center mt-3 text-slate-600 text-sm leading-none">↓</div>
              )}
            </div>
          ))}

          {/* 待定区（灰色暂存）——放在图谱最下方 */}
          {pending.length > 0 && (
            <div className="bg-slate-800/40 rounded-lg p-4 border border-dashed border-slate-600 mt-2">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <span className="text-sm font-medium text-slate-400">⏳ 待定（{pending.length}）</span>
                <span className="text-xs text-slate-600">灰色暂存，不在图谱内；选分组「加入」后进优先池，「✕」移除</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2">
                {(pending as any[]).map((p: any) => (
                  <div key={p.symbol} className="relative bg-slate-900/40 border border-slate-700/60 rounded-md p-2 opacity-70 hover:opacity-100 transition-opacity">
                    <button onClick={() => rejectMutation.mutate(p.symbol)} title="移除待定"
                      className="absolute top-1 right-1 text-slate-600 hover:text-red-400 text-xs leading-none">✕</button>
                    <SymbolLink symbol={p.symbol} className="font-semibold text-slate-300 text-sm" />
                    <div className="text-[10px] text-slate-500 mt-0.5 truncate" title={p.name || ''}>{p.name || '—'}</div>
                    {p.note && <div className="text-[10px] text-slate-600 mt-0.5 truncate" title={p.note}>{p.note}</div>}
                    <div className="flex gap-1 mt-1.5">
                      <select defaultValue={p.suggest_group} id={`pend-grp-${p.symbol}`}
                        className="flex-1 min-w-0 bg-slate-700 border border-slate-600 rounded px-1 py-0.5 text-[10px] text-slate-200 focus:outline-none">
                        {Object.entries(groups).map(([gk, gv]) => (
                          <option key={gk} value={gk}>{gv.label}</option>
                        ))}
                      </select>
                      <button onClick={() => {
                          const sel = document.getElementById(`pend-grp-${p.symbol}`) as HTMLSelectElement
                          approveMutation.mutate({ symbol: p.symbol, group: sel?.value || p.suggest_group })
                        }}
                        className="px-1.5 py-0.5 text-[10px] bg-emerald-700 hover:bg-emerald-600 rounded text-white shrink-0">加入</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Tab: 动能轮动 ─────────────────────────────────────── */}
      {tab === 'momentum' && <MomentumTab />}


      {/* 说明 */}
      <div className="text-xs text-slate-600 space-y-0.5">
        <div>· 产业图谱按上下游分层展示，公司业务为人工标注（data/aiCompanyMeta.ts）；增删即时写入 ai_universe.json（auto_trader 优先池）</div>
        <div>· 「手动加入」识别行业并归组；待定区（图谱最下方）选分组「加入」转正；评分/动量数据见「动能轮动」</div>
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
      <div className="text-[11px] text-slate-500 bg-slate-900/40 border border-slate-700/50 rounded px-2.5 py-1.5 leading-relaxed">
        <span className="text-slate-400">研究/择时视图</span>：AI 篮子内短线动能 + 资金流复合分排名（与「A 股动能扫描」同方法）。<span className="text-slate-300">独立打分，不接入实盘下单</span>——auto_trader 用的是 RS 动量（见因子看板），与此处 composite 是两套口径。
      </div>
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
              <SymbolLink key={s} symbol={s}
                className={`px-2 py-0.5 text-xs rounded font-mono ${
                  holdingsSet.has(s) ? 'bg-emerald-700 text-emerald-100' : 'bg-blue-700 text-blue-100'}`}>
                {s}{holdingsSet.has(s) && ' ✓'}
              </SymbolLink>
            ))}
          </div>
          {(sellSuggest.length > 0 || buySuggest.length > 0) && holdingsSet.size > 0 && (
            <div className="text-[11px] text-slate-400 space-y-0.5 pt-1 border-t border-slate-700">
              {sellSuggest.length > 0 && (
                <div>卖出：<span className="text-red-400 font-mono inline-flex gap-1">
                  {sellSuggest.map(s => <SymbolLink key={s} symbol={s} className="text-red-400" />)}
                </span></div>
              )}
              {buySuggest.length > 0 && (
                <div>换入：<span className="text-emerald-400 font-mono inline-flex gap-1">
                  {buySuggest.map(s => <SymbolLink key={s} symbol={s} className="text-emerald-400" />)}
                </span></div>
              )}
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
                    <SymbolLink symbol={r.symbol} className="font-medium text-white" />
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
  const tone = rs5 >= 0 ? 'pos' : 'neg'
  return (
    <button onClick={onClick}
      className={`group-card group-card-${tone} text-left rounded-lg border p-2.5 transition-colors ${
        active ? 'border-blue-500' : 'border-slate-700 hover:border-slate-500'}`}
      style={{ background: bg }}>
      <div className="flex items-center gap-1.5 mb-1">
        <span className="w-2 h-2 rounded-full" style={{ background: g.color }} />
        <span className="gc-label text-[11px] text-slate-200 font-medium truncate">{g.label}</span>
      </div>
      <div className={`gc-rs font-mono text-sm font-bold ${rs5 >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
        {pctFmt(g.median_rs_5d)}
      </div>
      <div className="gc-ad text-[10px] text-slate-400 mt-0.5">
        涨 <span className="gc-up text-emerald-300">{g.advance}</span> / 跌 <span className="gc-dn text-red-300">{g.decline}</span>
      </div>
      <div className="flex items-center gap-1 mt-1">
        <span className={`gc-flow text-[10px] px-1 rounded ${
          g.flow_signal === 'inflow' ? 'bg-emerald-900/60 text-emerald-300'
          : g.flow_signal === 'outflow' ? 'bg-red-900/60 text-red-300'
          : 'bg-slate-700 text-slate-400'}`}>
          {g.flow_signal === 'inflow' ? '净流入' : g.flow_signal === 'outflow' ? '净流出' : '中性'}
        </span>
        <span className="gc-leaders text-[10px] text-slate-400 truncate inline-flex gap-1">
          {g.leaders.slice(0, 2).map(l => (
            <SymbolLink key={l.symbol} symbol={l.symbol} className="text-slate-400" />
          ))}
        </span>
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
