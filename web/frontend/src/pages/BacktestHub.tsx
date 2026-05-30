import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import Backtest from './Backtest'
import SingleBacktest from './SingleBacktest'
import Comparison from './Comparison'
import AStockMomentumBacktest from './AStockMomentumBacktest'

type TabKey = 'portfolio' | 'single' | 'compare' | 'astock'

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'portfolio', label: '策略回测', icon: '📈' },
  { key: 'single',    label: '单股回测', icon: '🪙' },
  { key: 'compare',   label: '收益对比', icon: '⚖️' },
  { key: 'astock',    label: 'A股动能轮动', icon: '🇨🇳' },
]

const VALID: TabKey[] = ['portfolio', 'single', 'compare', 'astock']

export default function BacktestHub() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') as TabKey | null
  const tab: TabKey = raw && VALID.includes(raw) ? raw : 'portfolio'

  // lazy-mount：首次切到某 tab 才挂载，挂载后保留以保住其内部状态
  const [mounted, setMounted] = useState<Set<TabKey>>(() => new Set([tab]))
  useEffect(() => {
    if (!mounted.has(tab)) setMounted(prev => new Set([...prev, tab]))
  }, [tab, mounted])

  const setTab = (k: TabKey) => {
    const next = new URLSearchParams(params)
    if (k === 'portfolio') next.delete('tab')
    else next.set('tab', k)
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-3">
      {/* Tab 栏 */}
      <div className="flex gap-0 border-b border-slate-700">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab === t.key
                ? 'border-blue-500 text-white font-medium'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            <span className="mr-1.5">{t.icon}</span>{t.label}
          </button>
        ))}
      </div>

      {/* Tab 内容：lazy-mount + keep mounted（保留各自的本地状态） */}
      {mounted.has('portfolio') && <div hidden={tab !== 'portfolio'}><Backtest /></div>}
      {mounted.has('single')    && <div hidden={tab !== 'single'}>   <SingleBacktest /></div>}
      {mounted.has('compare')   && <div hidden={tab !== 'compare'}>  <Comparison /></div>}
      {mounted.has('astock')    && <div hidden={tab !== 'astock'}>   <AStockMomentumBacktest /></div>}
    </div>
  )
}
