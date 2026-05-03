import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getIBStatus } from '../api/client'
import { useEffect, useState } from 'react'
import { useAccount } from '../App'

const NAV = [
  { to: '/',          label: '持仓总览', icon: '📊' },
  { to: '/factors',   label: '因子看板', icon: '🔬' },
  { to: '/scanner',   label: '市场扫描', icon: '🔭' },
  { to: '/optimizer', label: '因子优化', icon: '⚗️'  },
  { to: '/backtest',  label: '策略回测', icon: '📈' },
  { to: '/comparison',label: '收益对比', icon: '⚖️'  },
  { to: '/analysis',  label: '自选分析', icon: '🎯' },
  { to: '/scheduler', label: '任务调度', icon: '⏰' },
  { to: '/config',    label: '系统配置', icon: '⚙️'  },
]

type Theme = 'light' | 'dark' | 'system'

/** 8:00–20:00 本地时间视为白天 */
function isDayTime() {
  const h = new Date().getHours()
  return h >= 8 && h < 20
}

function useTheme(): [Theme, (t: Theme) => void] {
  const VALID: Theme[] = ['light', 'dark', 'system']
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem('theme') as Theme
    return VALID.includes(saved) ? saved : 'dark'
  })

  const applyTheme = (t: Theme) => {
    const html = document.documentElement
    html.classList.remove('day', 'night', 'dark')
    if (t === 'dark') {
      html.classList.add('dark')
    } else if (t === 'light') {
      // 浅色：按时间段切换白天灰调/夜晚白调
      html.classList.add(isDayTime() ? 'day' : 'night')
    } else {
      // 跟随系统
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      html.classList.add(prefersDark ? 'dark' : (isDayTime() ? 'day' : 'night'))
    }
  }

  useEffect(() => {
    applyTheme(theme)
    if (theme === 'dark') return
    // 浅色/跟随系统：每分钟检查时间段
    const id = setInterval(() => applyTheme(theme), 60_000)
    if (theme !== 'system') return () => clearInterval(id)
    // 跟随系统：同时监听系统主题变化
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onMqChange = () => applyTheme('system')
    mq.addEventListener('change', onMqChange)
    return () => { clearInterval(id); mq.removeEventListener('change', onMqChange) }
  }, [theme])

  const setTheme = (t: Theme) => {
    localStorage.setItem('theme', t)
    setThemeState(t)
  }

  return [theme, setTheme]
}

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
  { key: 'system', icon: <IconMonitor />, label: '跟随系统' },
]

function ThemeToggle() {
  const [theme, setTheme] = useTheme()
  const [open, setOpen] = useState(false)
  const current = THEMES.find(t => t.key === theme)!

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(s => !s)}
        title={`当前主题：${current.label}`}
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
          <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden min-w-[100px]">
            {THEMES.map(t => (
              <button
                key={t.key}
                onClick={() => { setTheme(t.key); setOpen(false) }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors
                  ${theme === t.key
                    ? 'bg-blue-600 text-white font-medium'
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

function IBAccountSelector() {
  const { data: ibStatus } = useQuery({
    queryKey: ['ib-status'],
    queryFn: getIBStatus,
    refetchInterval: 30_000,
    retry: false,
  })
  const { selectedAccount, setSelectedAccount } = useAccount()
  const [open, setOpen] = useState(false)

  const connected: boolean = ibStatus?.connected ?? false
  const accounts: string[] = ibStatus?.accounts ?? []
  const isLive: boolean = ibStatus?.is_live ?? false

  // 连接后若还没选账号，自动选第一个
  useEffect(() => {
    if (connected && accounts.length > 0 && !selectedAccount) {
      setSelectedAccount(accounts[0])
    }
  }, [connected, accounts.join(',')])

  const display = selectedAccount ?? accounts[0] ?? null

  if (!connected) {
    return (
      <div className="flex items-center gap-1.5 text-xs">
        <span className="w-2 h-2 rounded-full bg-red-500" />
        <span className="text-slate-500">IB 离线</span>
      </div>
    )
  }

  return (
    <div className="relative">
      <button
        onClick={() => accounts.length > 1 && setOpen(s => !s)}
        className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded border transition-colors
          ${isLive
            ? 'border-green-700 text-green-300 hover:border-green-500'
            : 'border-yellow-700 text-yellow-300 hover:border-yellow-500'}
          ${accounts.length <= 1 ? 'cursor-default' : 'cursor-pointer'}`}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
        <span className={`font-semibold ${isLive ? 'text-green-300' : 'text-yellow-300'}`}>
          {isLive ? '实盘' : '模拟'}
        </span>
        <span className="text-slate-500 mx-0.5">·</span>
        <span className="font-mono">{display}</span>
        {accounts.length > 1 && (
          <svg className="w-3 h-3 text-slate-400 ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </button>

      {open && accounts.length > 1 && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden min-w-[160px]">
            <div className="px-3 py-1.5 text-[11px] text-slate-500 border-b border-slate-700">选择账号</div>
            {accounts.map(acc => (
              <button
                key={acc}
                onClick={() => { setSelectedAccount(acc); setOpen(false) }}
                className={`w-full flex items-center justify-between px-3 py-2 text-xs transition-colors
                  ${selectedAccount === acc
                    ? 'bg-blue-600 text-white font-medium'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white'}`}
              >
                <span className="font-mono">{acc}</span>
                {selectedAccount === acc && <span className="text-blue-200 ml-2">✓</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export default function Layout() {
  return (
    <div className="flex h-screen bg-slate-900 text-slate-200 overflow-hidden">
      {/* 侧边栏 */}
      <aside className="w-48 flex-shrink-0 bg-slate-800 flex flex-col">
        <div className="px-4 py-4">
          <div className="flex items-center gap-2.5">
            <img src="/logo.png" alt="logo" className="w-8 h-8 rounded-lg object-contain flex-shrink-0" />
            <div>
              <div className="text-sm font-bold text-slate-200">QuanTrading</div>
              <div className="text-xs text-slate-400 mt-0.5">个人量化交易平台</div>
            </div>
          </div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2 px-4 py-2.5 text-sm transition-colors border-l-[4px] ${
                  isActive
                    ? 'border-blue-500 text-white font-medium'
                    : 'border-transparent text-slate-400 hover:bg-slate-700 hover:text-slate-200'
                }`
              }
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 text-xs text-slate-500">
          v1.0
        </div>
      </aside>

      {/* 主内容区 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 顶栏 */}
        <header className="h-12 bg-slate-800 flex items-center justify-between px-5 flex-shrink-0">
          <div />
          <div className="flex items-center gap-4">
            <ETClock />
            <IBAccountSelector />
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
