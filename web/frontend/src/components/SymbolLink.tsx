/**
 * 可点击的股票代码徽章。点击调起全局 StockChartModal。
 * 用法：<SymbolLink symbol="NVDA" />  或  <SymbolLink symbol="NVDA" className="text-base font-bold" />
 */
import { useStockChart } from './StockChartProvider'
import type { ReactNode } from 'react'

type Props = {
  symbol: string
  market?: 'us' | 'a'   // 'a' = A 股，走 akshare 数据接口；默认美股
  className?: string
  children?: ReactNode  // 自定义显示内容（不传则显示 symbol 本身）
  title?: string
}

export default function SymbolLink({ symbol, market = 'us', className = '', children, title }: Props) {
  const { openChart } = useStockChart()
  return (
    <button
      type="button"
      onClick={e => { e.stopPropagation(); openChart(symbol, market) }}
      title={title ?? `点击查看 ${symbol} K 线`}
      className={`hover:underline hover:text-blue-400 cursor-pointer transition-colors ${className}`}
    >
      {children ?? symbol}
    </button>
  )
}
