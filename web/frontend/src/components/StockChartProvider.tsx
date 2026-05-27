/**
 * 全局股票详情弹窗 Provider。
 * 用法：
 *   1. App.tsx 最外层包 <StockChartProvider>
 *   2. 任何组件调 const { openChart } = useStockChart(); openChart('NVDA')
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import StockChartModal from './StockChartModal'

type Ctx = {
  openChart: (symbol: string) => void
  closeChart: () => void
}

const StockChartContext = createContext<Ctx>({
  openChart: () => {},
  closeChart: () => {},
})

export const useStockChart = () => useContext(StockChartContext)

export function StockChartProvider({ children }: { children: ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null)

  const openChart  = useCallback((sym: string) => setSymbol((sym || '').toUpperCase()), [])
  const closeChart = useCallback(() => setSymbol(null), [])

  // ESC 关闭
  useEffect(() => {
    if (!symbol) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeChart() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [symbol, closeChart])

  return (
    <StockChartContext.Provider value={{ openChart, closeChart }}>
      {children}
      {symbol && <StockChartModal symbol={symbol} onClose={closeChart} />}
    </StockChartContext.Provider>
  )
}
