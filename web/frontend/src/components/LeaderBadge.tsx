/**
 * A 股龙头地位标记(▲金 全球第一/垄断 · ◆蓝 全球前列 · ●绿 国产替代龙头)。
 * 即时 tooltip：鼠标移上去立刻显示领域说明,无原生 title 延迟。
 * tooltip 用 fixed 定位脱离表格容器(overflow-hidden / overflow-x-auto 都不会裁),
 * 并在视口底部自动翻转向上,保证最后一行也能完整看到。
 */
import { useRef, useState } from 'react'
import { ASTOCK_LEADERS } from '../data/astockLeaders'

export default function LeaderBadge({ code, className = '' }: { code: string; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const [pos, setPos] = useState<{ x: number; y: number; above: boolean } | null>(null)
  const L = ASTOCK_LEADERS[code]
  if (!L) return null
  const glyph = L.tier === '○' ? '●' : L.tier === '🏆' ? '▲' : L.tier
  const color = L.tier === '◆' ? 'text-sky-400' : L.tier === '○' ? 'text-emerald-400' : 'text-amber-400'

  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    const below = window.innerHeight - r.bottom
    const above = below < 60          // 距视口底部不足 60px 则向上显示
    setPos({ x: r.left, y: above ? r.top - 4 : r.bottom + 4, above })
  }

  return (
    <span ref={ref} onMouseEnter={show} onMouseLeave={() => setPos(null)}
      className={`inline-block align-middle ${className}`}>
      <span className={`cursor-help text-[11px] ${color}`}>{glyph}</span>
      {pos && (
        <span
          style={{ position: 'fixed', left: pos.x, top: pos.y, zIndex: 99,
                   transform: pos.above ? 'translateY(-100%)' : undefined }}
          className="pointer-events-none whitespace-nowrap rounded bg-slate-900 border border-slate-600 px-2 py-1 text-[11px] text-slate-200 shadow-lg">
          {glyph} {L.note}
        </span>
      )}
    </span>
  )
}
