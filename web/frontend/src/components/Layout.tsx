import { NavLink, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getIBStatus } from '../api/client'
import { useEffect, useState } from 'react'

const NAV = [
  { to: '/',          label: '持仓总览', icon: '📊' },
  { to: '/factors',   label: '因子看板', icon: '🔬' },
  { to: '/scanner',   label: '市场扫描', icon: '🔭' },
  { to: '/backtest',  label: '策略回测', icon: '📈' },
  { to: '/scheduler', label: '任务调度', icon: '⏰' },
  { to: '/config',    label: '系统配置', icon: '⚙️'  },
]

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
          <div className="text-sm font-bold text-white">QuanTrading</div>
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
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white'
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
              <span
                className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-red-500'}`}
              />
              <span className={connected ? 'text-green-400' : 'text-slate-500'}>
                {connected ? `IB ${ibStatus?.account ?? ''}` : 'IB 离线'}
              </span>
            </div>
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
