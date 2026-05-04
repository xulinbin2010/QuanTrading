import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createContext, useContext, useState } from 'react'
import Layout from './components/Layout'
import Portfolio from './pages/Portfolio'
import FactorDashboard from './pages/FactorDashboard'
import MarketScan from './pages/MarketScan'
import StockAnalysis from './pages/StockAnalysis'
import Optimizer from './pages/Optimizer'
import Backtest from './pages/Backtest'
import Comparison from './pages/Comparison'
import AITracker from './pages/AITracker'
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
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Portfolio />} />
              <Route path="factors" element={<FactorDashboard />} />
              <Route path="scanner"   element={<MarketScan />} />
              <Route path="analysis"  element={<StockAnalysis />} />
              <Route path="optimizer" element={<Optimizer />} />
              <Route path="backtest"    element={<Backtest />} />
              <Route path="comparison"  element={<Comparison />} />
              <Route path="ai"          element={<AITracker />} />
              <Route path="scheduler" element={<Scheduler />} />
              <Route path="config"    element={<Config />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </AccountContext.Provider>
  )
}
