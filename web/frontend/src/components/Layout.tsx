import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getIBStatus } from '../api/client'
import { useEffect, useState } from 'react'

const NAV = [
  { to: '/',          label: '持仓总览', icon: '📊' },
  { to: '/factors',   label: '因子看板', icon: '🔬' },
  { to: '/scanner',   label: '市场扫描', icon: '🔭' },
  { to: '/optimizer', label: '因子优化', icon: '⚗️'  },
  { to: '/backtest',  label: '策略回测', icon: '📈' },
  { to: '/scheduler', label: '任务调度', icon: '⏰' },
  { to: '/config',    label: '系统配置', icon: '⚙️'  },
]

type Theme = 'light' | 'dark' | 'system'

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() => {
    return (localStorage.getItem('theme') as Theme) ?? 'dark'
  })

  const applyTheme = (t: Theme) => {
    const html = document.documentElement
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const isDark = t === 'dark' || (t === 'system' && prefersDark)
    html.classList.toggle('dark', isDark)
    html.classList.toggle('light', !isDark)
  }

  useEffect(() => {
    applyTheme(theme)
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => applyTheme('system')
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [theme])

  const setTheme = (t: Theme) => {
    localStorage.setItem('theme', t)
    setThemeState(t)
  }

  return [theme, setTheme]
}

// 主题图标
function IconSun() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="4" />
      <path strokeLinecap="round" d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
    </svg>
  )
}
function IconMoon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
    </svg>
  )
}
function IconMonitor() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path strokeLinecap="round" d="M8 21h8M12 17v4" />
    </svg>
  )
}

const THEMES: { key: Theme; icon: React.ReactNode; label: string }[] = [
  { key: 'light',  icon: <IconSun />,     label: '浅色' },
  { key: 'dark',   icon: <IconMoon />,    label: '深色' },
  { key: 'system', icon: <IconMonitor />, label: '系统' },
]

function ThemeToggle() {
  const [theme, setTheme] = useTheme()
  const [open, setOpen] = useState(false)
  const current = THEMES.find(t => t.key === theme)!

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(s => !s)}
        title={`当前：${current.label}`}
        className="flex items-center gap-1.5 px-2 py-1 rounded border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-400 text-xs transition-colors"
      >
        {current.icon}
        <span>{current.label}</span>
        <svg className="w-3 h-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden min-w-[90px]">
            {THEMES.map(t => (
              <button
                key={t.key}
                onClick={() => { setTheme(t.key); setOpen(false) }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors
                  ${theme === t.key
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white'}`}
              >
                {t.icon}
                <span>{t.label}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ETClock() {
  const [time, setTime] = useState('')
  useEffect(() => {
    const tick = () => {
      const now = new Date().toLocaleString('zh-CN', {
        timeZone: 'America/New_York',
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
      })
      setTime('ET ' + now)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-xs text-slate-400 font-mono">{time}</span>
}

export default function Layout() {
  const { data: ibStatus } = useQuery({
    queryKey: ['ib-status'],
    queryFn: getIBStatus,
    refetchInterval: 30_000,
    retry: false,
  })

  const connected = ibStatus?.connected ?? false

  return (
    <div className="flex h-screen bg-slate-900 text-slate-200 overflow-hidden">
      {/* 侧边栏 */}
      <aside className="w-48 flex-shrink-0 bg-slate-800 border-r border-slate-700 flex flex-col">
        <div className="px-4 py-5 border-b border-slate-700">
          <div className="text-sm font-bold text-slate-200">QuanTrading</div>
          <div className="text-xs text-slate-400 mt-0.5">量化交易平台</div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2 px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-slate-200'
                }`
              }
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-slate-700 text-xs text-slate-500">
          v1.0
        </div>
      </aside>

      {/* 主内容区 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 顶栏 */}
        <header className="h-12 bg-slate-800 border-b border-slate-700 flex items-center justify-between px-5 flex-shrink-0">
          <div />
          <div className="flex items-center gap-4">
            <ETClock />
            <div className="flex items-center gap-1.5 text-xs">
              <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-red-500'}`} />
              <span className={connected ? 'text-green-400' : 'text-slate-500'}>
                {connected ? `IB ${ibStatus?.account ?? ''}` : 'IB 离线'}
              </span>
            </div>
            <ThemeToggle />
          </div>
        </header>

        {/* 页面内容 */}
        <main className="flex-1 overflow-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
