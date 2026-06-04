/**
 * A 股动能扫描页（板块强度 + 个股强度，akshare 数据源，沪深300 基准）。
 * 申万行业全市场轮动（sw）+ 自定义主题板块（theme），两个 tab 切换。
 * UI 结构复用 AITracker 的动能轮动设计。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getAStockMomentum, classifyAStock, addAStockTheme, removeAStockTheme } from '../api/client'
import SymbolLink from '../components/SymbolLink'

// ── 辅助函数 ──────────────────────────────────────────────────

function pctFmt(v: number | null | undefined, digits = 1) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}
function pctColor(v: number | null | undefined): string {
  if (v == null) return 'text-slate-500'
  if (v > 0.02) return 'text-emerald-400'
  if (v > 0) return 'text-emerald-500/80'
  if (v < -0.02) return 'text-red-400'
  return 'text-red-500/80'
}

// 30 个细分主题归并为 10 个中类（板块卡 + 过滤用；细分降为股票上的标签）。
// 仅 theme 模式生效；sw（申万行业）模式保持原行业分组不变。
const ASTOCK_CATEGORIES: { key: string; label: string; color: string; groups: string[] }[] = [
  { key: 'cat_optics',  label: '🔦 光通信/网络',   color: '#22c55e', groups: ['optical', 'ocs', 'optical_chip', 'fiber_cable', 'connector'] },
  { key: 'cat_storage', label: '💾 存储',          color: '#06b6d4', groups: ['storage'] },
  { key: 'cat_pcb',     label: '🟫 PCB/载板',      color: '#f97316', groups: ['pcb', 'ccl', 'glass_fiber', 'resin', 'copper_foil'] },
  { key: 'cat_aichip',  label: '🧠 算力/AI芯片',   color: '#ef4444', groups: ['chip_compute', 'chip_design'] },
  { key: 'cat_semimfg', label: '🏭 半导体制造',    color: '#eab308', groups: ['semi_material', 'semi_equip', 'foundry', 'packaging'] },
  { key: 'cat_analog',  label: '🔌 模拟/功率/被动', color: '#d946ef', groups: ['analog_chip', 'power_semi', 'passive'] },
  { key: 'cat_server',  label: '🖥 服务器/数据中心', color: '#3b82f6', groups: ['server', 'idc', 'compute_lease'] },
  { key: 'cat_power',   label: '⚡ 电力/供电',     color: '#84cc16', groups: ['power_supply', 'sst', 'power_grid', 'power_compute', 'gas_turbine'] },
  { key: 'cat_cooling', label: '❄️ 液冷/散热',     color: '#fb923c', groups: ['cooling'] },
  { key: 'cat_display', label: '🖼 显示面板',      color: '#8b5cf6', groups: ['display_panel'] },
]
const CAT_BY_KEY: Record<string, { key: string; label: string; color: string; groups: string[] }> =
  Object.fromEntries(ASTOCK_CATEGORIES.map(c => [c.key, c]))

// ── 辅助组件（A 股版，与 AITracker 同款）────────────────────────

function CompositeBadge({ score }: { score: number }) {
  const color = score >= 7 ? 'bg-emerald-500' : score >= 5 ? 'bg-amber-500' : 'bg-slate-600'
  return (
    <span className={`inline-flex items-center justify-center w-9 h-7 rounded text-xs font-bold text-white ${color}`}>
      {score.toFixed(1)}
    </span>
  )
}

function EmaBadge({ state }: { state: string }) {
  const cfg = state === 'strong'
    ? { label: '强', cls: 'bg-emerald-900/60 text-emerald-300', title: '站上 EMA7 + EMA21' }
    : state === 'weak'
    ? { label: '破7', cls: 'bg-amber-900/60 text-amber-300', title: '跌破 EMA7，仍站上 EMA21' }
    : { label: '破21', cls: 'bg-red-900/60 text-red-300', title: '跌破 EMA21（中期走弱）' }
  return (
    <span className={`inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[10px] font-medium ${cfg.cls}`} title={cfg.title}>
      {cfg.label}
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

function GroupCard({ g, active, onClick }: {
  g: any; active: boolean; onClick: () => void
}) {
  const rs5 = g.median_rs_5d ?? 0
  const intensity = Math.max(-1, Math.min(1, rs5 / 0.03))  // A股振幅小，±3% 满色
  const bg = intensity > 0
    ? `rgba(16,185,129,${0.15 + Math.abs(intensity) * 0.35})`
    : `rgba(239,68,68,${0.15 + Math.abs(intensity) * 0.35})`
  const tone = rs5 >= 0 ? 'pos' : 'neg'
  return (
    <button onClick={onClick}
      title={`${g.label}：板块中位 5 日相对沪深300 ${pctFmt(g.median_rs_5d)}`}
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
    </button>
  )
}

function BasketFlowPanel({ basket }: { basket: any }) {
  if (!basket?.dates?.length) return null
  const adOption = {
    backgroundColor: 'transparent',
    grid: { left: 30, right: 10, top: 10, bottom: 18 },
    xAxis: { type: 'category', data: basket.dates, axisLabel: { fontSize: 9, color: '#64748b' } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9, color: '#64748b' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    tooltip: { trigger: 'axis' },
    series: [{ type: 'line', data: basket.ad_cumulative, smooth: true,
      lineStyle: { color: '#3b82f6', width: 2 }, areaStyle: { color: 'rgba(59,130,246,0.15)' }, symbol: 'none' }],
  }
  const flowOption = {
    backgroundColor: 'transparent',
    grid: { left: 40, right: 10, top: 10, bottom: 18 },
    xAxis: { type: 'category', data: basket.dates, axisLabel: { fontSize: 9, color: '#64748b' } },
    yAxis: { type: 'value', axisLabel: { fontSize: 9, color: '#64748b', formatter: '{value}亿' }, splitLine: { lineStyle: { color: '#1e293b' } } },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v?.toFixed?.(2) ?? v}亿` },
    series: [{ type: 'bar', data: basket.money_flow_b,
      itemStyle: { color: (p: any) => p.value >= 0 ? '#10b981' : '#ef4444' } }],
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
          <div className="text-xs text-slate-400">金额加权资金流（每日，亿元）</div>
          <div className="text-xs">
            5日累计 <span className={`font-mono ${basket.money_flow_5d_b >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {basket.money_flow_5d_b >= 0 ? '+' : ''}{basket.money_flow_5d_b?.toFixed(1) ?? '—'}亿
            </span>
          </div>
        </div>
        <ReactECharts option={flowOption} style={{ height: 110 }} />
      </div>
    </div>
  )
}

// ── 主页面 ─────────────────────────────────────────────────────

type Mode = 'sw' | 'theme'

export default function AStockTracker() {
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>('theme')
  const [window, setWindow] = useState<'composite' | 'trend' | '3d' | '5d' | '10d'>('composite')
  const [groupFilter, setGroupFilter] = useState<string>('all')
  const [emaFilter, setEmaFilter] = useState<'all' | 'ema7' | 'ema21'>('ema21')
  const [priceMin, setPriceMin] = useState('')
  const [priceMax, setPriceMax] = useState('')
  const [capMin, setCapMin] = useState('')
  const [capMax, setCapMax] = useState('')
  const [minTrend, setMinTrend] = useState('')
  const [forcing, setForcing] = useState(false)
  // 添加股票面板
  const [addCode, setAddCode] = useState('')
  const [classifying, setClassifying] = useState(false)
  const [suggest, setSuggest] = useState<{ code: string; name: string; group: string | null; sw3_name: string | null } | null>(null)
  const [pickGroup, setPickGroup] = useState('')
  const [busy, setBusy] = useState(false)
  const [addMsg, setAddMsg] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['astock-momentum', mode],
    queryFn: () => getAStockMomentum(mode, false),
    staleTime: 30 * 60_000,
    // 每 5 分钟自动拉一次（force=false，命中调度保鲜的热缓存）：
    // 调度任务交易时段每 30 分钟刷新缓存，页面据此自动跟新，无需手动点刷新；
    // 盘后缓存过期时这次轮询会触发一次后台重扫，补上调度不覆盖的时段。
    // refetchIntervalInBackground 默认 false → tab 失焦时暂停，省请求。
    refetchInterval: 5 * 60_000,
    retry: false,
  })

  const refresh = () => {
    setForcing(true)
    getAStockMomentum(mode, true)
      .then(d => { qc.setQueryData(['astock-momentum', mode], d); setForcing(false) })
      .catch(() => setForcing(false))
  }

  const rows: any[] = data?.rows ?? []
  const groups: any[] = data?.groups ?? []
  const basket = data?.basket
  const benchmark = data?.benchmark
  const groupOptions: { key: string; label: string }[] = groups.map((g: any) => ({ key: g.key, label: g.label }))

  // 板块卡：theme 模式把 30 小类聚合成 10 中类；sw 模式用原申万行业组
  const isTheme = mode === 'theme'
  const groupLabelMap: Record<string, string> = Object.fromEntries(groups.map((g: any) => [g.key, g.label]))
  const displayGroups: any[] = isTheme
    ? ASTOCK_CATEGORIES.map(cat => {
        const subs = groups.filter((g: any) => cat.groups.includes(g.key))
        if (!subs.length) return null
        const catRows = rows.filter((r: any) => cat.groups.includes(r.group))
        const rsList = catRows.map((r: any) => r.rs_5d).filter((v: any) => v != null).sort((a: number, b: number) => a - b)
        return {
          key: cat.key, label: cat.label, color: cat.color,
          median_rs_5d: rsList.length ? rsList[Math.floor(rsList.length / 2)] : 0,
          advance: subs.reduce((s: number, g: any) => s + (g.advance || 0), 0),
          decline: subs.reduce((s: number, g: any) => s + (g.decline || 0), 0),
        }
      }).filter(Boolean)
    : groups
  // 当前选中（中类 key 或 sw 小类 key）下，某只股票是否命中
  const inActiveGroup = (r: any) => {
    if (groupFilter === 'all') return true
    if (isTheme) return (CAT_BY_KEY[groupFilter]?.groups ?? []).includes(r.group)
    return r.group === groupFilter
  }

  const doClassify = () => {
    const code = addCode.trim()
    if (!code) return
    setClassifying(true); setSuggest(null); setAddMsg('')
    classifyAStock(code)
      .then((r: any) => {
        setSuggest(r)
        setPickGroup(r.group ?? '')
        if (!r.group) setAddMsg('未能自动识别板块，请手动选择')
      })
      .catch(() => setAddMsg('识别失败'))
      .finally(() => setClassifying(false))
  }

  const doAdd = () => {
    if (!suggest || !pickGroup) return
    setBusy(true); setAddMsg('')
    const lbl = groupOptions.find(g => g.key === pickGroup)?.label ?? pickGroup
    addAStockTheme(suggest.code, pickGroup)
      .then(() => {
        setAddMsg(`已加入【${lbl}】，刷新中…`)
        setAddCode(''); setSuggest(null); setPickGroup('')
        return getAStockMomentum(mode, true)
      })
      .then((d: any) => { qc.setQueryData(['astock-momentum', mode], d); setAddMsg('') })
      .catch((e: any) => setAddMsg(e?.response?.data?.detail ?? '添加失败'))
      .finally(() => setBusy(false))
  }

  const doRemove = (code: string, name: string) => {
    if (!confirm(`从板块移除 ${name}（${code}）？`)) return
    setBusy(true)
    removeAStockTheme(code)
      .then(() => getAStockMomentum(mode, true))
      .then((d: any) => qc.setQueryData(['astock-momentum', mode], d))
      .catch(() => {})
      .finally(() => setBusy(false))
  }

  const numOr = (s: string, d: number) => { const n = parseFloat(s); return Number.isFinite(n) ? n : d }
  const pMin = numOr(priceMin, -Infinity), pMax = numOr(priceMax, Infinity)
  const cMin = numOr(capMin, -Infinity), cMax = numOr(capMax, Infinity)
  const tMin = numOr(minTrend, -Infinity)
  const filteredRows = rows.filter(r => {
    if (!inActiveGroup(r)) return false
    if (!(emaFilter === 'all' || (emaFilter === 'ema7' ? r.above_ema7 : r.above_ema21))) return false
    if (r.close != null && (r.close < pMin || r.close > pMax)) return false
    // 市值缺失（如申万模式部分股票）不参与过滤，避免误删
    if ((capMin !== '' || capMax !== '') && r.market_cap != null && (r.market_cap < cMin || r.market_cap > cMax)) return false
    if (minTrend !== '' && (r.trend_score == null || r.trend_score < tMin)) return false
    return true
  })
  const hiddenByEma = emaFilter === 'all' ? 0
    : rows.filter(inActiveGroup).length
      - rows.filter(r => inActiveGroup(r) && (emaFilter === 'ema7' ? r.above_ema7 : r.above_ema21)).length
  const filtersActive = priceMin !== '' || priceMax !== '' || capMin !== '' || capMax !== '' || minTrend !== ''
  const fmtCap = (v: number | null | undefined) =>
    v == null ? '—' : v >= 10000 ? (v / 10000).toFixed(2) + '万亿' : Math.round(v) + '亿'
  const rsField = window === '3d' ? 'rs_3d' : window === '10d' ? 'rs_10d' : 'rs_5d'
  const sortField = window === '3d' ? 'mom_3d' : window === '5d' ? 'mom_5d' : window === '10d' ? 'mom_10d' : window === 'trend' ? 'trend_score' : 'composite'
  const sortedRows = [...filteredRows].sort((a, b) => (b[sortField] ?? -Infinity) - (a[sortField] ?? -Infinity))

  return (
    <div className="space-y-4">
      {/* 标题行 */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-200">🇨🇳 A 股动能扫描</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            akshare 数据 · 沪深300 基准 · 申万行业轮动 + 主题板块
            {benchmark && (
              <span className="ml-2 text-slate-600">
                沪深300 3日{pctFmt(benchmark.mom_3d)} / 5日{pctFmt(benchmark.mom_5d)} / 10日{pctFmt(benchmark.mom_10d)}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {data?.last_updated && <span>更新于 {data.last_updated.slice(0, 16)}</span>}
          <button onClick={refresh} disabled={forcing || isLoading}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-300 transition-colors">
            {forcing ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {/* 模式切换 */}
      <div className="flex gap-0 border-b border-slate-700">
        {([['theme','AI 产业链（主题板块）'], ['sw','申万行业（全市场轮动）']] as const).map(([k, l]) => (
          <button key={k} onClick={() => { setMode(k); setGroupFilter('all') }}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              mode === k ? 'border-blue-500 text-white font-medium' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {/* 添加股票（主题模式专用：输入代码→自动识别板块→确认加入） */}
      {mode === 'theme' && (
        <div className="bg-slate-800/60 rounded-lg border border-slate-700 p-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400 font-medium">➕ 添加股票</span>
          <input
            value={addCode}
            onChange={e => { setAddCode(e.target.value.replace(/\D/g, '').slice(0, 6)); setSuggest(null); setAddMsg('') }}
            onKeyDown={e => { if (e.key === 'Enter') doClassify() }}
            placeholder="6位代码"
            className="w-28 px-2 py-1 text-sm bg-slate-900 border border-slate-600 rounded text-slate-200 font-mono focus:border-blue-500 outline-none"
          />
          <button onClick={doClassify} disabled={classifying || addCode.length < 6}
            className="px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white">
            {classifying ? '识别中…' : '识别'}
          </button>
          {suggest && (
            <>
              <span className="text-sm text-slate-200 font-medium">{suggest.name}</span>
              {suggest.sw3_name && <span className="text-[11px] text-slate-500">申万：{suggest.sw3_name}</span>}
              <span className="text-xs text-slate-500">归入</span>
              <select value={pickGroup} onChange={e => setPickGroup(e.target.value)}
                className="px-2 py-1 text-xs bg-slate-900 border border-slate-600 rounded text-slate-200 focus:border-blue-500 outline-none">
                <option value="">选择板块…</option>
                {groupOptions.map(g => (
                  <option key={g.key} value={g.key}>{g.label}</option>
                ))}
              </select>
              <button onClick={doAdd} disabled={busy || !pickGroup}
                className="px-3 py-1 text-xs rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white">
                {busy ? '处理中…' : '加入'}
              </button>
            </>
          )}
          {addMsg && <span className="text-xs text-amber-400">{addMsg}</span>}
        </div>
      )}

      {/* 板块热力卡 */}
      {isLoading ? (
        <div className="text-center py-12 text-slate-500 text-sm">加载中，首次约需 1-2 分钟（下载行业成分数据）…</div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-2">
            {displayGroups.map((g: any) => (
              <GroupCard key={g.key} g={g} active={groupFilter === g.key}
                onClick={() => setGroupFilter(f => f === g.key ? 'all' : g.key)} />
            ))}
          </div>

          {/* 筛选 + 窗口切换（板块筛选靠上方热力方块点击，此处不再重复列板块按钮） */}
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={() => setGroupFilter('all')}
              className={`px-3 py-1 text-xs rounded ${groupFilter === 'all' ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              全部板块
            </button>
            {groupFilter !== 'all' && (() => {
              const dg = displayGroups.find((g: any) => g.key === groupFilter)
              return (
                <span className="text-xs text-slate-400 inline-flex items-center gap-1">
                  已选
                  <span className="px-2 py-0.5 rounded text-white" style={{ background: dg?.color }}>
                    {dg?.label ?? groupFilter}
                  </span>
                </span>
              )
            })()}
            <div className="ml-auto flex items-center gap-2">
              <span className="text-xs text-slate-500">均线：</span>
              {([['ema21','站上EMA21'],['ema7','站上EMA7'],['all','全部']] as const).map(([k, l]) => (
                <button key={k} onClick={() => setEmaFilter(k)}
                  className={`px-2 py-1 text-xs rounded ${emaFilter === k ? 'bg-emerald-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
                  {l}
                </button>
              ))}
              <span className="text-xs text-slate-500 ml-2">排序：</span>
              {([['composite','综合分'],['trend','趋势分'],['3d','3日'],['5d','5日'],['10d','10日']] as const).map(([w, l]) => (
                <button key={w} onClick={() => setWindow(w)}
                  className={`px-2 py-1 text-xs rounded ${window === w ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
                  {l}
                </button>
              ))}
              <span className="text-slate-500 ml-1 text-xs">
                共 {filteredRows.length} 只{hiddenByEma > 0 && <span className="text-slate-600">（破位隐藏 {hiddenByEma}）</span>}
              </span>
            </div>
          </div>

          {/* 价格 / 市值 过滤 */}
          <div className="flex items-center gap-x-5 gap-y-2 flex-wrap bg-slate-800/40 rounded-lg border border-slate-700/60 px-3 py-2">
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-400 w-12">价格</span>
              <input value={priceMin} onChange={e => setPriceMin(e.target.value.replace(/[^\d.]/g, ''))}
                placeholder="不限" inputMode="decimal"
                className="w-16 px-1.5 py-1 text-xs text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
              <span className="text-slate-500 text-xs">–</span>
              <input value={priceMax} onChange={e => setPriceMax(e.target.value.replace(/[^\d.]/g, ''))}
                placeholder="不限" inputMode="decimal"
                className="w-16 px-1.5 py-1 text-xs text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
              <span className="text-[11px] text-slate-500">元</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-400 w-12">市值</span>
              <input value={capMin} onChange={e => setCapMin(e.target.value.replace(/[^\d.]/g, ''))}
                placeholder="不限" inputMode="decimal"
                className="w-16 px-1.5 py-1 text-xs text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
              <span className="text-slate-500 text-xs">–</span>
              <input value={capMax} onChange={e => setCapMax(e.target.value.replace(/[^\d.]/g, ''))}
                placeholder="不限" inputMode="decimal"
                className="w-16 px-1.5 py-1 text-xs text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
              <span className="text-[11px] text-slate-500">亿（流通）</span>
              <div className="flex gap-1 ml-1">
                {([['全部', '', ''], ['小盘≤300', '', '300'], ['中盘300-1500', '300', '1500'], ['大盘≥1500', '1500', '']] as const).map(([l, a, b]) => (
                  <button key={l} onClick={() => { setCapMin(a); setCapMax(b) }}
                    className={`px-1.5 py-0.5 text-[11px] rounded ${capMin === a && capMax === b ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-slate-400">趋势分 ≥</span>
              <input value={minTrend} onChange={e => setMinTrend(e.target.value.replace(/[^\d.]/g, ''))}
                placeholder="不限" inputMode="decimal"
                className="w-14 px-1.5 py-1 text-xs text-right bg-slate-900 border border-slate-600 rounded text-slate-200 outline-none focus:border-blue-500" />
              <div className="flex gap-1">
                {([['趋势股6', '6'], ['强趋势8', '8']] as const).map(([l, v]) => (
                  <button key={v} onClick={() => setMinTrend(minTrend === v ? '' : v)}
                    className={`px-1.5 py-0.5 text-[11px] rounded ${minTrend === v ? 'bg-emerald-600 text-white' : 'bg-slate-700 text-slate-400 hover:text-slate-200'}`}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            {filtersActive && (
              <button onClick={() => { setPriceMin(''); setPriceMax(''); setCapMin(''); setCapMax(''); setMinTrend('') }}
                className="text-xs text-slate-400 hover:text-slate-200 underline ml-auto">重置筛选</button>
            )}
          </div>

          {/* 个股动能表格 */}
          <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-400 border-b border-slate-700">
                  <th className="text-left px-3 py-2.5 font-medium">代码</th>
                  <th className="text-left px-3 py-2.5 font-medium">名称</th>
                  <th className="text-right px-2 py-2.5 font-medium">现价</th>
                  <th className="text-right px-2 py-2.5 font-medium">市值</th>
                  <th className="text-center px-2 py-2.5 font-medium">综合分</th>
                  <th className="text-center px-2 py-2.5 font-medium" title="板块内 composite 强度排名(短期动量+量能),不等同于机构龙头(无市值/北上/龙虎榜数据)">板块强度</th>
                  <th className="text-center px-2 py-2.5 font-medium">趋势</th>
                  <th className="text-right px-2 py-2.5 font-medium">3日</th>
                  <th className="text-right px-2 py-2.5 font-medium">5日</th>
                  <th className="text-right px-2 py-2.5 font-medium">10日</th>
                  <th className="text-right px-2 py-2.5 font-medium">vs300</th>
                  <th className="text-right px-2 py-2.5 font-medium">组内</th>
                  <th className="text-right px-2 py-2.5 font-medium">量比</th>
                  <th className="text-center px-2 py-2.5 font-medium">均线</th>
                  <th className="text-center px-2 py-2.5 font-medium">加速</th>
                  <th className="text-right px-2 py-2.5 font-medium">资金流</th>
                  {mode === 'theme' && <th className="px-2 py-2.5"></th>}
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((r: any, idx: number) => (
                  <tr key={r.symbol} className="border-b border-slate-700/50 hover:bg-slate-750 transition-colors">
                    <td className="px-3 py-1.5 text-slate-500 text-xs">{idx + 1}
                      <SymbolLink symbol={r.symbol} market="a"
                        className="ml-1.5 font-mono font-medium text-white text-sm" />
                    </td>
                    <td className="px-3 py-1.5 text-xs text-slate-400 max-w-[120px]">
                      <div className="truncate">{r.name}</div>
                      {isTheme && r.group && groupLabelMap[r.group] && (
                        <span className="inline-block mt-0.5 text-[9px] px-1 rounded bg-slate-700/60 text-slate-400 leading-tight">{groupLabelMap[r.group]}</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs text-slate-300">{r.close != null ? r.close.toFixed(2) : '—'}</td>
                    <td className="px-2 py-1.5 text-right font-mono text-xs text-slate-400">{fmtCap(r.market_cap)}</td>
                    <td className="px-2 py-1.5 text-center"><CompositeBadge score={r.composite} /></td>
                    <td className="px-2 py-1.5 text-center text-sm">
                      {r.group_rank == null ? (
                        <span className="text-slate-700 text-xs">·</span>
                      ) : r.group_rank <= 3 ? (
                        <span
                          className="text-amber-400 tracking-tight"
                          title={`板块强度第 ${r.group_rank}/${r.group_size}(composite 排名,动态)`}
                        >
                          {'★'.repeat(4 - r.group_rank)}
                        </span>
                      ) : (
                        <span
                          className="text-slate-500 font-mono text-xs"
                          title={`板块强度第 ${r.group_rank}/${r.group_size}`}
                        >
                          #{r.group_rank}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      {r.trend_score != null ? (
                        <span title={r.ema7_hold != null ? `EMA7站稳 ${Math.round(r.ema7_hold * 100)}%` : ''}>
                          <CompositeBadge score={r.trend_score} />
                        </span>
                      ) : <span className="text-slate-700">—</span>}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_3d)} ${window === '3d' ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_3d)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_5d)} ${window === '5d' ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_5d)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.mom_10d)} ${window === '10d' ? 'bg-blue-900/20' : ''}`}>{pctFmt(r.mom_10d)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r[rsField])}`}>{pctFmt(r[rsField])}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${pctColor(r.rs_vs_group_5d)}`}>{pctFmt(r.rs_vs_group_5d)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono text-xs ${(r.vol_ratio ?? 0) > 1.5 ? 'text-amber-400' : 'text-slate-400'}`}>
                      {r.vol_ratio != null ? r.vol_ratio.toFixed(2) : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      {r.ema_state ? <EmaBadge state={r.ema_state} /> : <span className="text-slate-700">·</span>}
                    </td>
                    <td className="px-2 py-1.5 text-center text-xs">
                      {r.accel ? <span className="text-yellow-400" title="动能加速">▲</span> : <span className="text-slate-700">·</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right"><FlowBar score={r.flow_score} /></td>
                    {mode === 'theme' && (
                      <td className="px-2 py-1.5 text-center">
                        <button onClick={() => doRemove(r.symbol, r.name)} disabled={busy}
                          title="从板块移除"
                          className="text-slate-600 hover:text-red-400 disabled:opacity-40 text-xs">✕</button>
                      </td>
                    )}
                  </tr>
                ))}
                {filteredRows.length === 0 && !isLoading && (
                  <tr><td colSpan={mode === 'theme' ? 16 : 15} className="text-center py-8 text-slate-500 text-sm">暂无数据，点击刷新扫描</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* A/D 线 + 资金流 */}
          {basket && <BasketFlowPanel basket={basket} />}
        </div>
      )}

      {/* 说明 */}
      <div className="text-xs text-slate-400 space-y-0.5">
        <div>· 综合分 = 0.35×5日相对沪深300 + 0.20×3日相对沪深300 + 0.20×组内排名 + 0.15×量比 + 0.10×资金流（z-score 归一 0-10）</div>
        <div>· 加速 ▲：3日日均收益 &gt; 5日日均收益</div>
        <div>· 均线：<span className="text-emerald-300">强</span>=站上EMA7+EMA21 / <span className="text-amber-300">破7</span>=跌破EMA7仍站上EMA21 / <span className="text-red-300">破21</span>=跌破EMA21中期走弱；默认隐藏破EMA21，可切「全部」查看</div>
        <div>· 趋势分（0-10）：近20日站上EMA7占比 + log价格回归R²（平滑爬升·仅上升有效），暴涨（单日&gt;9.5%/区间&gt;50%）扣分。找<span className="text-emerald-300">稳步爬升不暴涨</span>的票就按趋势分排序或筛「趋势股」</div>
        <div>· 资金流：OBV 5日斜率（标准化）+ 上涨日量/下跌日量比；A/D 线按 Parquet 本地数据计算</div>
        <div>· <span className="text-amber-400">申万行业</span>：每行业取权重最高 40 只；首次扫描较慢（下载行业成分），缓存 30 分钟</div>
      </div>
    </div>
  )
}
