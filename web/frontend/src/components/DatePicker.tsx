import { useState, useRef, useEffect } from 'react'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']
const MONTHS = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

function pad(n: number) { return String(n).padStart(2, '0') }

export function dateToStr(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function strToDate(s: string): Date | undefined {
  if (!s) return undefined
  const [y, m, d] = s.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return isNaN(dt.getTime()) ? undefined : dt
}

function CalendarPanel({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const today = new Date()
  const sel = strToDate(value)
  const init = sel ?? today
  const [view, setView] = useState<'day' | 'month' | 'year'>('day')
  const [curYear, setCurYear] = useState(init.getFullYear())
  const [curMonth, setCurMonth] = useState(init.getMonth())

  const yearBase = Math.floor(curYear / 12) * 12

  const firstDay = new Date(curYear, curMonth, 1).getDay()
  const daysInMonth = new Date(curYear, curMonth + 1, 0).getDate()
  const cells: (number | null)[] = [
    ...Array(firstDay).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  const hdr = 'flex items-center justify-between px-3 py-2 border-b border-slate-700'
  const navBtn = 'w-7 h-7 flex items-center justify-center rounded hover:bg-slate-600 text-slate-400 hover:text-white transition-colors text-sm select-none'
  const titleBtn = 'font-medium text-white hover:text-blue-400 transition-colors cursor-pointer text-sm px-1'
  const cellCls = (active: boolean, isToday: boolean) =>
    `w-8 h-7 flex items-center justify-center rounded text-xs cursor-pointer select-none transition-colors
    ${active ? 'bg-blue-600 text-white' : isToday ? 'border border-blue-500 text-blue-300 hover:bg-slate-600' : 'text-slate-300 hover:bg-slate-600'}`

  if (view === 'year') return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={() => setCurYear(yearBase - 12)}>‹</button>
        <span className="text-sm text-slate-400">{yearBase}–{yearBase + 11}</span>
        <button className={navBtn} onClick={() => setCurYear(yearBase + 12)}>›</button>
      </div>
      <div className="grid grid-cols-3 gap-1 p-3">
        {Array.from({ length: 12 }, (_, i) => yearBase + i).map(y => (
          <button key={y}
            onClick={() => { setCurYear(y); setView('month') }}
            className={`py-1.5 rounded text-sm transition-colors
              ${y === curYear ? 'bg-blue-600 text-white' : y === today.getFullYear() ? 'border border-blue-500 text-blue-300 hover:bg-slate-600' : 'text-slate-300 hover:bg-slate-600'}`}
          >{y}</button>
        ))}
      </div>
    </div>
  )

  if (view === 'month') return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={() => setCurYear(y => y - 1)}>‹</button>
        <button className={titleBtn} onClick={() => setView('year')}>{curYear}年</button>
        <button className={navBtn} onClick={() => setCurYear(y => y + 1)}>›</button>
      </div>
      <div className="grid grid-cols-3 gap-1 p-3">
        {MONTHS.map((name, i) => (
          <button key={i}
            onClick={() => { setCurMonth(i); setView('day') }}
            className={`py-1.5 rounded text-sm transition-colors
              ${i === curMonth && curYear === (sel?.getFullYear() ?? -1) ? 'bg-blue-600 text-white'
              : i === today.getMonth() && curYear === today.getFullYear() ? 'border border-blue-500 text-blue-300 hover:bg-slate-600'
              : 'text-slate-300 hover:bg-slate-600'}`}
          >{name}</button>
        ))}
      </div>
    </div>
  )

  const prevMonth = () => { if (curMonth === 0) { setCurYear(y => y - 1); setCurMonth(11) } else setCurMonth(m => m - 1) }
  const nextMonth = () => { if (curMonth === 11) { setCurYear(y => y + 1); setCurMonth(0) } else setCurMonth(m => m + 1) }

  return (
    <div className="w-56">
      <div className={hdr}>
        <button className={navBtn} onClick={prevMonth}>‹</button>
        <div className="flex gap-1">
          <button className={titleBtn} onClick={() => setView('year')}>{curYear}年</button>
          <button className={titleBtn} onClick={() => setView('month')}>{MONTHS[curMonth]}</button>
        </div>
        <button className={navBtn} onClick={nextMonth}>›</button>
      </div>
      <div className="p-2">
        <div className="grid grid-cols-7 mb-1">
          {WEEKDAYS.map(w => <div key={w} className="w-8 text-center text-xs text-slate-500 py-1">{w}</div>)}
        </div>
        <div className="grid grid-cols-7 gap-y-0.5">
          {cells.map((day, i) => {
            if (!day) return <div key={i} className="w-8 h-7" />
            const isSelected = sel?.getFullYear() === curYear && sel?.getMonth() === curMonth && sel?.getDate() === day
            const isToday = today.getFullYear() === curYear && today.getMonth() === curMonth && today.getDate() === day
            return (
              <button key={i}
                onClick={() => onChange(dateToStr(new Date(curYear, curMonth, day)))}
                className={cellCls(isSelected, isToday)}
              >{day}</button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function DatePicker({
  value,
  onChange,
  label,
}: {
  value: string
  onChange: (v: string) => void
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div className="relative" ref={ref}>
      {label && <span className="block text-xs text-slate-400 mb-1">{label}</span>}
      <button
        type="button"
        onClick={() => setOpen(s => !s)}
        className="flex items-center gap-2 bg-slate-800 border border-slate-600 rounded px-3 py-1.5 text-sm hover:border-slate-400 focus:outline-none focus:border-blue-500 transition-colors min-w-[140px]"
      >
        <svg className="w-4 h-4 text-slate-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
        <span className={value ? 'text-white' : 'text-slate-500'}>{value || '选择日期'}</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-2xl">
          <CalendarPanel value={value} onChange={v => { onChange(v); setOpen(false) }} />
          {value && (
            <div className="border-t border-slate-700 px-3 py-1.5">
              <button type="button" onClick={() => { onChange(''); setOpen(false) }}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors">清除</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
