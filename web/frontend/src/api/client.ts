import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120_000,
})

export default api

// ── 持仓总览 ──────────────────────────────────────────────

export const getIBStatus   = () => api.get('/portfolio/ib-status').then(r => r.data)
export const getBalance    = () => api.get('/portfolio/balance').then(r => r.data)
export const getPositions        = (account?: string | null) => api.get('/portfolio/positions', { params: account ? { account } : {} }).then(r => r.data)
export const refreshPositions    = (account?: string | null) => api.get('/portfolio/positions', { params: { refresh: true, ...(account ? { account } : {}) } }).then(r => r.data)
export const getOrders     = (symbol?: string, limit = 50) =>
  api.get('/portfolio/orders', { params: { symbol, limit } }).then(r => r.data)
export const getAccountHistory = (limit = 90) =>
  api.get('/portfolio/account-history', { params: { limit } }).then(r => r.data)
export const getPerformance = (days = 30) =>
  api.get('/portfolio/performance', { params: { days } }).then(r => r.data)
export const getSignals = (universe = 'sp500+ndx') =>
  api.get('/portfolio/signals', { params: { universe } }).then(r => r.data)

// ── 因子看板 ──────────────────────────────────────────────

export const getUniverses  = () => api.get('/factors/universes').then(r => r.data)
export const getTickers    = (universe: string) =>
  api.get('/factors/tickers', { params: { universe } }).then(r => r.data)
export const scanFactors   = (universe: string, top = 50, force = false) =>
  api.get('/factors/scan', { params: { universe, top, force } }).then(r => r.data)
export const getStockDetail = (symbol: string, days = 120) =>
  api.get(`/factors/stock/${symbol}`, { params: { days } }).then(r => r.data)
export const getStockNews = (symbol: string) =>
  api.get(`/factors/stock/${symbol}/news`).then(r => r.data)
export const getInsiderData    = () => api.get('/factors/insider').then(r => r.data)
export const getEarningsDates  = (symbols: string[]) =>
  api.get('/factors/earnings', { params: { symbols: symbols.join(',') } }).then(r => r.data as Record<string, string | null>)

export const clearFactorCache = (universe?: string) =>
  api.delete('/factors/cache', { params: { universe } }).then(r => r.data)
export const getFactorRegistry = () =>
  api.get('/factors/registry').then(r => r.data)
export const updateFactor = (key: string, body: { enabled?: boolean; params?: Record<string, any> }) =>
  api.put(`/factors/registry/${key}`, body).then(r => r.data)
export const previewFactorSignals = (universe: string, factors: string[], top = 100) =>
  api.post('/factors/preview', { universe, factors, top }).then(r => r.data)
export const checkTrailStops = (positions: { symbol: string; avg_cost: number }[]) =>
  api.post('/factors/trail-stop-check', positions).then(r => r.data)

// ── 策略回测 ──────────────────────────────────────────────

export interface BacktestParams {
  period?: string
  start?: string
  end?: string
  universe: string
  top_n: number
  min_cap_b?: number
  max_cap_b?: number
  deny_industries?: string[]
  daily?: boolean
  factors?: string[]
  factor_params?: Record<string, Record<string, any>>
}

export const runBacktest      = (params: BacktestParams) =>
  api.post('/backtest/run', params).then(r => r.data)
export const getBacktestStatus = (taskId: string) =>
  api.get(`/backtest/status/${taskId}`).then(r => r.data)
export const getBacktestResult = (taskId: string) =>
  api.get(`/backtest/result/${taskId}`).then(r => r.data)
export const getBacktestHistory = () =>
  api.get('/backtest/history').then(r => r.data)
export const getVixAnalysis = (params: {
  threshold?: number; start?: string; end?: string; symbol?: string; mode?: string
}) => api.get('/backtest/vix', { params }).then(r => r.data)

export const listFactorCombos = () =>
  api.get('/backtest/combos').then(r => r.data as FactorCombo[])
export const saveFactorCombo = (name: string, factors: string[], factor_params?: Record<string, any>) =>
  api.post('/backtest/combos', { name, factors, factor_params: factor_params ?? {} }).then(r => r.data as FactorCombo)
export const deleteFactorCombo = (id: string) =>
  api.delete(`/backtest/combos/${id}`).then(r => r.data)

export interface FactorCombo {
  id: string
  name: string
  builtin: boolean
  factors: string[]
  factor_params: Record<string, any>
  description?: string
  created_at?: string
}

export const runWalkForward = (params: {
  train_months: number; test_months: number
  total_start: string; total_end?: string
  universe: string; top_n: number
}) => api.post('/backtest/walk-forward', params).then(r => r.data)

// ── 自选股 Watchlist ──────────────────────────────────────

export const getWatchlist   = () => api.get('/watchlist/').then(r => r.data)
export const addToWatchlist = (symbol: string) =>
  api.post('/watchlist/', { symbol }).then(r => r.data)
export const removeFromWatchlist = (symbol: string) =>
  api.delete(`/watchlist/${symbol}`).then(r => r.data)

// ── 因子优化器 ────────────────────────────────────────────

export const runOptimizer        = (params: object) =>
  api.post('/optimizer/run', params).then(r => r.data)
export const getOptimizerStatus  = (taskId: string) =>
  api.get(`/optimizer/status/${taskId}`).then(r => r.data)
export const getOptimizerResult  = (taskId: string) =>
  api.get(`/optimizer/result/${taskId}`).then(r => r.data)
export const getOptimizerHistory = () =>
  api.get('/optimizer/history').then(r => r.data)

// ── 系统配置 ──────────────────────────────────────────────

export const getConfig    = () => api.get('/config').then(r => r.data)
export const updateConfig = (params: { key: string; value: string }[]) =>
  api.put('/config', { params }).then(r => r.data)
export const reloadConfig = () => api.post('/config/reload').then(r => r.data)
export const updateIBConnection = (params: {
  IB_HOST: string; IB_PORT: number; IB_CLIENT_ID: number; IB_TIMEOUT: number
}) => api.put('/config/connection/ib', params).then(r => r.data)

// ── 10x 猎手 / Screener ──────────────────────────────────

export const getFiveBaggerScreener = (force = false) =>
  api.get('/screener/fivebagger', { params: { force } }).then(r => r.data)

export const listNarrativeWatchlist = () =>
  api.get('/screener/narrative').then(r => r.data as any[])

export interface NarrativeEntryBody {
  symbol: string
  old_category?: string
  new_narrative?: string
  thesis_notes?: string
  target_price?: number | null
}
export const upsertNarrativeEntry = (body: NarrativeEntryBody) =>
  api.post('/screener/narrative', body).then(r => r.data)

export const deleteNarrativeEntry = (id: number) =>
  api.delete(`/screener/narrative/${id}`).then(r => r.data)

// ── 任务调度 ──────────────────────────────────────────────

export const getSchedulerTasks  = () => api.get('/scheduler/tasks').then(r => r.data)
export const upsertSchedulerTask = (body: object) =>
  api.post('/scheduler/tasks', body).then(r => r.data)
export const deleteSchedulerTask = (taskId: string) =>
  api.delete(`/scheduler/tasks/${taskId}`).then(r => r.data)
export const runTaskNow         = (taskId: string) =>
  api.post(`/scheduler/tasks/${taskId}/run-now`).then(r => r.data)
export const getTaskRuns        = (taskId?: string, limit = 50) =>
  api.get('/scheduler/runs', { params: { task_id: taskId, limit } }).then(r => r.data)
export const getRunLog          = (runId: number) =>
  api.get(`/scheduler/runs/${runId}/log`).then(r => r.data)
export const deleteTaskRun      = (runId: number) =>
  api.delete(`/scheduler/runs/${runId}`).then(r => r.data)
export const getCronPreview     = (expr: string, count = 5) =>
  api.get('/scheduler/cron-preview', { params: { expr, count } }).then(r => r.data)
