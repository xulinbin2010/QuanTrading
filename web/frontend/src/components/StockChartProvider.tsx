/**
 * 全局股票详情弹窗 Provider。
 * 用法：
 *   1. App.tsx 最外层包 <StockChartProvider>
 *   2. 任何组件调 const { openChart } = useStockChart(); openChart('NVDA')
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import StockChartModal from './StockChartModal'

type Market = 'us' | 'a'
type Ctx = {
  openChart: (symbol: string, market?: Market) => void
  closeChart: () => void
}

const StockChartContext = createContext<Ctx>({
  openChart: () => {},
  closeChart: () => {},
})

export const useStockChart = () => useContext(StockChartContext)

export function StockChartProvider({ children }: { children: ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null)
  const [market, setMarket] = useState<Market>('us')

  const openChart  = useCallback((sym: string, mkt: Market = 'us') => {
    // 美股代码转大写；A 股保持 6 位数字原样
    setSymbol(mkt === 'a' ? (sym || '').trim() : (sym || '').toUpperCase())
    setMarket(mkt)
  }, [])
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
      {symbol && <StockChartModal symbol={symbol} market={market} onClose={closeChart} />}
    </StockChartContext.Provider>
  )
}
