/**
 * AI 产业链热力图（流程图布局：板块=紧凑块，左→右=上游→下游，连线=供应关系）。
 * 设计（用户逐步拍板）：
 *   - 每个子主题一个紧凑块（非全宽长条），按 NODE_POS 手工排布成整体流程图：
 *     列1 设备/材料 → 列2 芯片(GPU/存储) → 列3 整机与网络 → 列4 算力运营，配套电力在底部
 *     该排布下 CHAIN_EDGES 全部连线零交叉，供应树状结构一眼可见
 *   - 连线：SVG 贝塞尔，箭头指向需方，中点标签注明供什么（制造设备·材料 / HBM·SSD / 供电·散热…）
 *     锚点按 DOM 实时测量（ResizeObserver），自动选块的左右/上下侧，多条线沿边均匀错开
 *   - 个股 = 统一小色块（颜色=所选窗口涨跌，绿涨红跌），组内市值降序龙头在前；
 *     区块右上角 = 板块温度（市值加权平均涨跌）；市值/评分/指数成分在悬停 tooltip
 */
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AI_COMPANY_META } from '../data/aiCompanyMeta'
import { useStockChart } from './StockChartProvider'

type Period = '1d' | '3d' | '5d' | '10d'

const PERIODS: { key: Period; label: string; field: string; clamp: number }[] = [
  { key: '1d',  label: '1天',  field: 'mom_1d',  clamp: 0.15 },
  { key: '3d',  label: '3天',  field: 'mom_3d',  clamp: 0.15 },
  { key: '5d',  label: '5天',  field: 'mom_5d',  clamp: 0.15 },
  { key: '10d', label: '10天', field: 'mom_10d', clamp: 0.15 },
]

// 流程图节点排布（CSS grid：5 列主流程 + 底部配套行）。新增分组若不在此表，落到底部备用区。
const NODE_POS: Record<string, { col: string; row: string; center?: boolean }> = {
  semicon_equip:    { col: '1',     row: '2 / 5', center: true },  // 根节点：设备材料喂代工厂
  foundry_osat:     { col: '2',     row: '2 / 5', center: true },  // 代工/封测：分叉喂三类芯片
  gpu_compute:      { col: '3',     row: '2' },
  memory_storage:   { col: '3',     row: '3' },
  analog_power:     { col: '3',     row: '4' },
  ai_networking:    { col: '4',     row: '2' },
  datacenter_infra: { col: '4',     row: '3 / 5' },
  ai_infra_build:   { col: '5',     row: '2 / 5', center: true },  // 汇聚节点
  power_cooling:    { col: '3 / 5', row: '5' },                    // 配套：底部横跨，向上供给
}

// 列标题（第 1 行）：左→右 = 上游→下游
const COL_TITLES = ['设备/材料', '晶圆制造/封测', '芯片设计', '整机与网络', '算力运营']

// 供应关系（from 给 to 供货）
const CHAIN_EDGES: { from: string; to: string; label: string }[] = [
  { from: 'semicon_equip',    to: 'foundry_osat',     label: '制造设备·材料' },
  { from: 'foundry_osat',     to: 'gpu_compute',      label: '代工·封测' },
  { from: 'foundry_osat',     to: 'memory_storage',   label: '代工·封测' },
  { from: 'foundry_osat',     to: 'analog_power',     label: '代工·封测' },
  { from: 'gpu_compute',      to: 'ai_networking',    label: '交换/DSP 芯片' },
  { from: 'gpu_compute',      to: 'datacenter_infra', label: 'GPU/CPU' },
  { from: 'memory_storage',   to: 'datacenter_infra', label: 'HBM·SSD' },
  { from: 'analog_power',     to: 'datacenter_infra', label: '供电·模拟芯片' },
  { from: 'ai_networking',    to: 'ai_infra_build',   label: '光模块·交换机' },
  { from: 'datacenter_infra', to: 'ai_infra_build',   label: '服务器整机' },
  { from: 'power_cooling',    to: 'datacenter_infra', label: '液冷·电源' },
  { from: 'power_cooling',    to: 'ai_infra_build',   label: '供电·散热' },
]

// ── 发散色阶（绿涨红跌，与全系统一致）──
// 每臂 3 个停靠点（色相+明度双通道递进）+ 平方根 t：小幅涨跌(±1~2%)也拉得开层次，
// 不再是「中性灰→纯色」两点线性插值那种只剩四五档的效果。
// 深浅两套都遵循同一强度方向：涨跌幅越大，颜色越鲜艳、越醒目。
// 主题差异只体现在近零中性色和低强度起点；高强度端点保持一致。
type RGB = [number, number, number]
const RAMPS: Record<'dark' | 'light', { neutral: RGB; na: string; up: RGB[]; down: RGB[] }> = {
  dark: {
    neutral: [51, 65, 85],      // slate-700
    na: '#1e293b',
    // 越涨/越跌 → 越鲜艳饱和（Finviz 惯例），绝不能到粉彩——「跌得重反而粉红」是反直觉的
    up:   [[6, 78, 59],   [22, 163, 74],  [48, 204, 90]],   // 墨绿 → green-600 → 鲜绿
    down: [[127, 29, 29], [185, 28, 28],  [246, 53, 56]],   // 暗酒红 → red-700 → 正红
  },
  light: {
    neutral: [229, 234, 241],   // 近零淡出，贴近白卡片
    na: '#eef1f6',
    up:   [[167, 243, 208], [22, 163, 74], [48, 204, 90]],    // 淡绿 → green-600 → 鲜绿
    down: [[254, 202, 202], [185, 28, 28], [246, 53, 56]],    // 淡红 → red-700 → 正红
  },
}

function lerpRgb(a: RGB, b: RGB, t: number): RGB {
  return [0, 1, 2].map(i => Math.round(a[i] + (b[i] - a[i]) * t)) as RGB
}
// 多停靠点分段插值：t∈[0,1] 走 [neutral, s0, s1, s2]
function rampAt(neutral: RGB, stops: RGB[], t: number): RGB {
  const pts = [neutral, ...stops]
  const x = t * (pts.length - 1)
  const i = Math.min(Math.floor(x), pts.length - 2)
  return lerpRgb(pts[i], pts[i + 1], x - i)
}
function rgbFor(mom: number | null | undefined, clamp: number, dark: boolean): RGB | null {
  if (mom == null || !isFinite(mom)) return null
  const t = Math.sqrt(Math.min(Math.abs(mom) / clamp, 1))  // 平方根：放大近零分辨率
  const r = RAMPS[dark ? 'dark' : 'light']
  return rampAt(r.neutral, mom >= 0 ? r.up : r.down, t)
}
function colorFor(mom: number | null | undefined, clamp: number, dark: boolean): string {
  const c = rgbFor(mom, clamp, dark)
  return c ? `rgb(${c[0]},${c[1]},${c[2]})` : RAMPS[dark ? 'dark' : 'light'].na
}
// 按底色亮度选文字颜色：亮底深字、暗底白字（两种主题通吃）
function inkFor(mom: number | null | undefined, clamp: number, dark: boolean): string {
  const c = rgbFor(mom, clamp, dark)
  if (!c) return dark ? '#94a3b8' : '#64748b'
  const y = 0.2126 * (c[0] / 255) ** 2.2 + 0.7152 * (c[1] / 255) ** 2.2 + 0.0722 * (c[2] / 255) ** 2.2
  return y > 0.35 ? '#0f172a' : '#ffffff'
}
// 主题感知：html.dark ↔ html.day/night（Layout 挂在 <html> 上，MutationObserver 跟随切换）
function useIsDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useLayoutEffect(() => {
    const el = document.documentElement
    const ob = new MutationObserver(() => setDark(el.classList.contains('dark')))
    ob.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => ob.disconnect()
  }, [])
  return dark
}
function fmtPct(v: number | null | undefined, digits = 1) {
  if (v == null || !isFinite(v)) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}
function fmtCapB(b: number | null | undefined) {
  if (b == null) return '—'
  return b >= 1000 ? `$${(b / 1000).toFixed(2)}T` : `$${b.toFixed(0)}B`
}

type Side = 'left' | 'right' | 'top' | 'bottom'
type EdgeDraw = { path: string; color: string; label: string; mx: number; my: number }

function sidesOf(f: DOMRect, t: DOMRect): [Side, Side] {
  const dx = (t.left + t.right) / 2 - (f.left + f.right) / 2
  const dy = (t.top + t.bottom) / 2 - (f.top + f.bottom) / 2
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ['right', 'left'] : ['left', 'right']
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
}
function anchorPt(r: DOMRect, side: Side, frac: number, cRect: DOMRect) {
  const x = side === 'left' ? r.left : side === 'right' ? r.right : r.left + r.width * frac
  const y = side === 'top' ? r.top : side === 'bottom' ? r.bottom : r.top + r.height * frac
  return { x: x - cRect.left, y: y - cRect.top }
}

export default function AIChainHeatmap({ rows, universe, idxMem }: {
  rows: any[]
  universe: any
  idxMem?: { sp500?: string[]; ndx?: string[] }
}) {
  const [period, setPeriod] = useState<Period>('3d')
  const { openChart } = useStockChart()
  const isDark = useIsDark()

  const p = PERIODS.find(x => x.key === period)!
  const sp500Set = useMemo(() => new Set(idxMem?.sp500 ?? []), [idxMem])
  const ndxSet = useMemo(() => new Set(idxMem?.ndx ?? []), [idxMem])
  const rowMap = useMemo(() => {
    const m: Record<string, any> = {}
    for (const r of rows ?? []) m[r.symbol] = r
    return m
  }, [rows])
  const tpMap: Record<string, boolean> = universe?.trade_priority ?? {}
  const groups: Record<string, any> = universe?.groups ?? {}

  const visibleGroups = useMemo(
    () => Object.entries<any>(groups).filter(([, g]) => !g.hidden),
    [groups])
  const placedKeys = new Set(Object.keys(NODE_POS))
  const fallbackGroups = visibleGroups.filter(([gk]) => !placedKeys.has(gk))

  // 组内按市值降序（龙头在前）；板块温度 = 市值加权平均涨跌
  const groupData = useMemo(() => {
    const out: Record<string, { syms: string[]; wavg: number | null }> = {}
    for (const [gk, g] of visibleGroups) {
      const syms = [...(g.symbols ?? [])].sort((a: string, b: string) =>
        (rowMap[b]?.market_cap_b ?? 0) - (rowMap[a]?.market_cap_b ?? 0))
      let wSum = 0, wMomSum = 0
      for (const s of syms) {
        const r = rowMap[s]
        const mom = r?.[p.field]
        const cap = r?.market_cap_b
        if (mom != null && cap != null && cap > 0) { wSum += cap; wMomSum += cap * mom }
      }
      out[gk] = { syms, wavg: wSum > 0 ? wMomSum / wSum : null }
    }
    return out
  }, [visibleGroups, rowMap, p.field])

  // ── 供应关系连线：实时测量区块位置画贝塞尔曲线 ────────────────────
  const containerRef = useRef<HTMLDivElement | null>(null)
  const blockRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const [edges, setEdges] = useState<EdgeDraw[]>([])
  const [svgH, setSvgH] = useState(0)

  const recompute = useCallback(() => {
    const cont = containerRef.current
    if (!cont) return
    const cRect = cont.getBoundingClientRect()
    setSvgH(cont.scrollHeight)
    const active = CHAIN_EDGES.filter(e => blockRefs.current[e.from] && blockRefs.current[e.to])
    // 先算每条边在供/需两侧的朝向，再把同块同侧的多条线沿边均匀错开
    const withSides = active.map(e => {
      const f = blockRefs.current[e.from]!.getBoundingClientRect()
      const t = blockRefs.current[e.to]!.getBoundingClientRect()
      const [sf, st] = sidesOf(f, t)
      return { e, f, t, sf, st }
    })
    const sideBuckets: Record<string, string[]> = {}
    for (const w of withSides) {
      const k = `${w.e.from}->${w.e.to}`
      ;(sideBuckets[`${w.e.from}:${w.sf}`] ??= []).push(k)
      ;(sideBuckets[`${w.e.to}:${w.st}`] ??= []).push(k)
    }
    const frac = (node: string, side: Side, key: string) => {
      const arr = sideBuckets[`${node}:${side}`]
      return (arr.indexOf(key) + 1) / (arr.length + 1)
    }
    const list: EdgeDraw[] = []
    for (const { e, f, t, sf, st } of withSides) {
      const k = `${e.from}->${e.to}`
      const a = anchorPt(f, sf, frac(e.from, sf, k), cRect)
      const b = anchorPt(t, st, frac(e.to, st, k), cRect)
      const horizontal = sf === 'left' || sf === 'right'
      const path = horizontal
        ? `M ${a.x} ${a.y} C ${(a.x + b.x) / 2} ${a.y}, ${(a.x + b.x) / 2} ${b.y}, ${b.x} ${b.y}`
        : `M ${a.x} ${a.y} C ${a.x} ${(a.y + b.y) / 2}, ${b.x} ${(a.y + b.y) / 2}, ${b.x} ${b.y}`
      list.push({
        path,
        color: groups[e.from]?.color ?? '#64748b',
        label: e.label,
        mx: (a.x + b.x) / 2,
        my: (a.y + b.y) / 2,
      })
    }
    setEdges(list)
  }, [groups])

  useLayoutEffect(() => {
    recompute()
    const ro = new ResizeObserver(() => recompute())
    if (containerRef.current) ro.observe(containerRef.current)
    window.addEventListener('resize', recompute)
    return () => { ro.disconnect(); window.removeEventListener('resize', recompute) }
  }, [recompute, rows, universe])

  const tileTitle = (sym: string) => {
    const r = rowMap[sym] ?? {}
    const meta = (AI_COMPANY_META as any)[sym]
    const badges = [
      sp500Set.has(sym) ? 'S&P500' : null,
      ndxSet.has(sym) ? 'Nasdaq100' : null,
      (tpMap[sym] ?? true) ? 'AI优先池' : '仅观察',
    ].filter(Boolean).join(' · ')
    return [
      `${sym}${meta?.name ? ' ' + meta.name : ''}`,
      meta?.desc ?? '',
      `市值 ${fmtCapB(r.market_cap_b)} · 现价 ${r.price != null ? '$' + Number(r.price).toFixed(2) : '—'} · AI评分 ${r.score ?? '—'}`,
      PERIODS.map(pp => `${pp.label} ${fmtPct(r[pp.field])}`).join(' · '),
      badges,
      '点击查看 K 线',
    ].filter(Boolean).join('\n')
  }

  const GroupBlock = ({ gk }: { gk: string }) => {
    const g = groups[gk]
    const gd = groupData[gk]
    if (!g || !gd) return null
    const pos = NODE_POS[gk]
    return (
      <div ref={el => { blockRefs.current[gk] = el }}
        className="border border-slate-700 rounded-lg p-2 bg-slate-900/60"
        style={{
          borderTopColor: g.color ?? '#94a3b8', borderTopWidth: 3,
          gridColumn: pos?.col, gridRow: pos?.row,
          alignSelf: pos?.center ? 'center' : 'start',
          zIndex: 2,
        }}>
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-xs font-medium text-slate-300">{g.label}</span>
          <span className="text-[10px] text-slate-600">{gd.syms.length}只</span>
          <span className="ml-auto text-[11px] font-mono font-bold px-1.5 py-0.5 rounded"
            style={{ background: colorFor(gd.wavg, p.clamp, isDark), color: inkFor(gd.wavg, p.clamp, isDark) }}
            title="板块温度：市值加权平均涨跌">
            {fmtPct(gd.wavg)}
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {gd.syms.map(sym => {
            const mom = rowMap[sym]?.[p.field]
            const ink = inkFor(mom, p.clamp, isDark)
            return (
              <button key={sym} onClick={() => openChart(sym)} title={tileTitle(sym)}
                className="w-[76px] px-1 py-1 rounded text-center leading-tight transition-shadow hover:ring-2 hover:ring-blue-400/60 cursor-pointer"
                style={{ background: colorFor(mom, p.clamp, isDark), color: ink }}>
                <div className="text-[12px] font-bold truncate">{sym}</div>
                <div className="text-[11px] font-mono" style={{ opacity: 0.85 }}>{fmtPct(mom)}</div>
              </button>
            )
          })}
        </div>
      </div>
    )
  }

  // 图例：按真实映射（含平方根）采样成连续渐变条
  const legendGrad = useMemo(() => {
    const n = 32
    const pts = Array.from({ length: n + 1 }, (_, i) => {
      const mom = ((i / n) * 2 - 1) * p.clamp
      return `${colorFor(mom, p.clamp, isDark)} ${((i / n) * 100).toFixed(1)}%`
    })
    return `linear-gradient(to right, ${pts.join(',')})`
  }, [p, isDark])
  const edgeColors = [...new Set(edges.map(e => e.color))]

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-3">
      {/* 工具条：窗口切换 + 色阶图例 */}
      <div className="flex items-center gap-3 flex-wrap mb-3">
        <div className="flex gap-1">
          {PERIODS.map(pp => (
            <button key={pp.key} onClick={() => setPeriod(pp.key)}
              className={`px-3 py-1 text-sm rounded transition-colors ${period === pp.key
                ? 'bg-blue-600 text-white font-medium'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              {pp.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 ml-auto text-xs text-slate-400">
          <span className="font-mono">-{(p.clamp * 100).toFixed(0)}%</span>
          <span className="relative w-40 h-3.5 rounded-sm overflow-hidden" style={{ background: legendGrad }}>
            <span className="absolute left-1/2 top-0 bottom-0 w-px bg-slate-500/50" title="0%" />
          </span>
          <span className="font-mono">+{(p.clamp * 100).toFixed(0)}%</span>
        </div>
      </div>

      {/* 流程图：4 列主流程 + 底部配套；窄屏容器内横向滚动 */}
      <div className="overflow-x-auto">
        <div ref={containerRef} className="relative" style={{ minWidth: 1240 }}>
          <svg className="absolute left-0 top-0 w-full pointer-events-none" style={{ height: svgH, zIndex: 3 }}>
            <defs>
              {edgeColors.map(c => (
                <marker key={c} id={`arw-${c.replace(/[^a-zA-Z0-9]/g, '')}`}
                  viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
                </marker>
              ))}
            </defs>
            {edges.map((e, i) => (
              <path key={i} d={e.path} stroke={e.color} strokeWidth={1.8} fill="none" opacity={0.7}
                markerEnd={`url(#arw-${e.color.replace(/[^a-zA-Z0-9]/g, '')})`} />
            ))}
          </svg>
          {/* 关系标签（线中点小药丸） */}
          {edges.map((e, i) => (
            <div key={i}
              className="absolute -translate-x-1/2 -translate-y-1/2 text-[10px] px-1.5 py-0.5 rounded-full bg-slate-900/95 border pointer-events-none whitespace-nowrap"
              style={{ left: e.mx, top: e.my, zIndex: 4, borderColor: e.color, color: e.color }}>
              {e.label}
            </div>
          ))}

          <div className="grid" style={{
            gridTemplateColumns: 'repeat(5, minmax(200px, 1fr))',
            columnGap: 64, rowGap: 44,
          }}>
            {/* 第 1 行：列标题（上游→下游） */}
            {COL_TITLES.map((t, i) => (
              <div key={t} className="text-sm font-semibold text-slate-200 text-center"
                style={{ gridColumn: i + 1, gridRow: 1 }}>
                {t}{i < COL_TITLES.length - 1 && <span className="text-slate-600 ml-3">→</span>}
              </div>
            ))}
            {/* 板块块（NODE_POS 定位） */}
            {Object.keys(NODE_POS).filter(gk => groups[gk] && !groups[gk].hidden).map(gk => (
              <GroupBlock key={gk} gk={gk} />
            ))}
          </div>

          {/* 未在流程图排布的新分组（备用区，避免加组后丢失） */}
          {fallbackGroups.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-4">
              {fallbackGroups.map(([gk]) => <GroupBlock key={gk} gk={gk} />)}
            </div>
          )}
        </div>
      </div>

      {/* 说明 */}
      <div className="text-sm text-slate-400 space-y-1 mt-3">
        <div>· 左→右 = 上游→下游：设备材料 → 晶圆制造/封测 → 芯片设计（算力/存储/模拟电源）→ 整机与网络 → 算力运营；<span className="text-slate-300">电力/冷却在底部向上供给</span>。连线箭头指向需方，标签注明供什么。</div>
        <div>· 每块右上角为<span className="text-slate-300">板块温度</span>（市值加权平均涨跌）；色块颜色 = 所选窗口涨跌（±{(p.clamp * 100).toFixed(0)}% 封顶，平方根色阶——小幅涨跌也拉得开深浅），组内按市值降序、龙头在前。</div>
        <div>· 点色块看 K 线；市值/评分/S&P·Nasdaq 成分在悬停提示。增删股票请到「清单管理」tab。</div>
      </div>
    </div>
  )
}
