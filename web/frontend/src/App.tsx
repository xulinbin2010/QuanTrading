import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/Layout'
import Portfolio from './pages/Portfolio'
import FactorDashboard from './pages/FactorDashboard'
import MarketScan from './pages/MarketScan'
import Backtest from './pages/Backtest'
import Scheduler from './pages/Scheduler'
import Config from './pages/Config'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Portfolio />} />
            <Route path="factors" element={<FactorDashboard />} />
            <Route path="scanner" element={<MarketScan />} />
            <Route path="backtest" element={<Backtest />} />
            <Route path="scheduler" element={<Scheduler />} />
            <Route path="config"    element={<Config />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
