import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createContext, useContext, useState } from 'react'
import Layout from './components/Layout'
import { StockChartProvider } from './components/StockChartProvider'
import { PremarketBriefingProvider } from './components/PremarketBriefingProvider'
import Portfolio from './pages/Portfolio'
import MarketScan from './pages/MarketScan'
import StockAnalysis from './pages/StockAnalysis'
import Optimizer from './pages/Optimizer'
import BacktestHub from './pages/BacktestHub'
import AITracker from './pages/AITracker'
import AStockTracker from './pages/AStockTracker'
import Scheduler from './pages/Scheduler'
import Config from './pages/Config'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

type AccountCtx = { selectedAccount: string | null; setSelectedAccount: (a: string | null) => void }
export const AccountContext = createContext<AccountCtx>({ selectedAccount: null, setSelectedAccount: () => {} })
export const useAccount = () => useContext(AccountContext)

export default function App() {
  const [selectedAccount, setSelectedAccount] = useState<string | null>(null)
  return (
    <AccountContext.Provider value={{ selectedAccount, setSelectedAccount }}>
      <QueryClientProvider client={queryClient}>
        <StockChartProvider>
        <PremarketBriefingProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Portfolio />} />
              <Route path="scanner"   element={<MarketScan />} />
              <Route path="analysis"  element={<StockAnalysis />} />
              <Route path="optimizer" element={<Optimizer />} />
              <Route path="backtest"    element={<BacktestHub />} />
              {/* 旧路由保留为重定向，避免外部书签失效 */}
              <Route path="single-bt"   element={<Navigate to="/backtest?tab=single" replace />} />
              <Route path="comparison"  element={<Navigate to="/backtest?tab=compare" replace />} />
              <Route path="ai"          element={<AITracker />} />
              <Route path="astock"      element={<AStockTracker />} />
              <Route path="scheduler" element={<Scheduler />} />
              <Route path="config"    element={<Config />} />
            </Route>
          </Routes>
        </BrowserRouter>
        </PremarketBriefingProvider>
        </StockChartProvider>
      </QueryClientProvider>
    </AccountContext.Provider>
  )
}
