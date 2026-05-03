import { useState, useCallback, useRef, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { getComparison } from '../api/client'
import DatePicker, { dateToStr } from '../components/DatePicker'

// ── 预设对比组合（内置，不可删除）─────────────────────────
const BUILTIN_PRESETS = [
  { label: 'NVDA vs NVDL (2x)', symbols: 'NVDA,NVDL,SPY' },
  { label: 'TSLA vs TSLL (2x)', symbols: 'TSLA,TSLL,SPY' },
  { label: 'QQQ vs QLD (2x)', symbols: 'QQQ,QLD' },
  { label: 'SPY vs SSO (2x)', symbols: 'SPY,SSO' },
  { label: 'AAPL vs AAPU (2x)', symbols: 'AAPL,AAPU,SPY' },
  { label: 'MU vs MUU (2x)', symbols: 'MU,MUU,SPY' },
]

// ── 自定义组合持久化（localStorage）───────────────────────
const STORAGE_KEY = 'comparison_custom_presets'
type CustomPreset = { label: string; symbols: string }

function loadCustomPresets(): CustomPreset[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') } catch { return [] }
}
function persistCustomPresets(list: CustomPreset[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
}

// ── 日期预设 ───────────────────────────────────────────────
const DATE_PRESETS = [
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: '2Y', days: 730 },
  { label: '3Y', days: 1095 },
  { label: '5Y', days: 1825 },
]

function daysAgo(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return dateToStr(d)
}

function pct(v: number, digits = 1) {
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}

function pctColor(v: number) {
  return v >= 0 ? 'text-emerald-400' : 'text-red-400'
}

// ── 收益曲线图 ─────────────────────────────────────────────
type Series = { symbol: string; color: string; data: { date: string; value: number }[] }

function ReturnChart({ series }: { series: Series[] }) {
  if (!series.length) return null

  const allDates = series[0].data.map(d => d.date)

  const echartSeries = series.map(s => ({
    name: s.symbol,
    type: 'line',
    smooth: false,
    symbol: 'none',
    data: s.data.map(d => parseFloat((d.value - 100).toFixed(2))),
    lineStyle: { color: s.color, width: 2 },
    itemStyle: { color: s.color },
  }))

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params: any[]) => {
        const date = params[0]?.axisValue ?? ''
        const lines = params.map((p: any) =>
          `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color};margin-right:5px"></span>${p.seriesName}: <b>${p.value >= 0 ? '+' : ''}${p.value}%</b>`
        )
        return `<div style="font-size:11px">${date}</div>` + lines.join('<br/>')
      },
    },
    legend: {
      data: series.map(s => s.symbol),
      textStyle: { color: '#94a3b8', fontSize: 12 },
      top: 4,
    },
    grid: { left: 60, right: 24, top: 36, bottom: 40 },
    xAxis: {
      type: 'category',
      data: allDates,
      axisLabel: {
        color: '#64748b',
        fontSize: 10,
        interval: Math.floor(allDates.length / 8),
        formatter: (v: string) => v.slice(0, 7),
      },
      axisLine: { lineStyle: { color: '#334155' } },
      axisTick: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      axisLabel: {
        color: '#64748b',
        fontSize: 10,
        formatter: (v: number) => (v >= 0 ? '+' : '') + v + '%',
      },
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLine: { show: false },
    },
    series: echartSeries,
  }

  return <ReactECharts option={option} style={{ height: 340 }} />
}

// ── 相关系数矩阵 ───────────────────────────────────────────
function CorrMatrix({ matrix }: { matrix: { symbols: string[]; values: number[][] } }) {
  const { symbols, values } = matrix
  return (
    <div>
      <div className="text-xs text-slate-400 mb-2">相关系数矩阵（日收益率）</div>
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="px-3 py-1.5 text-slate-500 text-left"></th>
            {symbols.map(s => (
              <th key={s} className="px-3 py-1.5 text-slate-400 font-medium text-center">{s}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {symbols.map((row, i) => (
            <tr key={row}>
              <td className="px-3 py-1.5 text-slate-400 font-medium">{row}</td>
              {values[i].map((v, j) => (
                <td key={j} className={`px-3 py-1.5 text-center font-mono ${
                  i === j ? 'text-slate-500' : v >= 0.8 ? 'text-emerald-400' : v <= 0.3 ? 'text-red-400' : 'text-slate-300'
                }`}>
                  {v.toFixed(3)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 主页面 ─────────────────────────────────────────────────
export default function Comparison() {
  const [symbols, setSymbols] = useState('NVDA,NVDL,SPY')
  const [startDate, setStartDate] = useState(daysAgo(365))
  const [activeDatePreset, setActiveDatePreset] = useState('1Y')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')

  // ── 自定义快捷组合 ───────────────────────────────────────
  const [customPresets, setCustomPresets] = useState<CustomPreset[]>(() => loadCustomPresets())
  const [saveMode, setSaveMode] = useState(false)
  const [saveName, setSaveName] = useState('')
  const saveInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { if (saveMode) saveInputRef.current?.focus() }, [saveMode])

  const commitSave = () => {
    const name = saveName.trim()
    if (!name) return
    const sym = symbols.trim()
    if (!sym) return
    // 同名覆盖
    const updated = [...customPresets.filter(p => p.label !== name), { label: name, symbols: sym }]
    setCustomPresets(updated)
    persistCustomPresets(updated)
    setSaveMode(false)
    setSaveName('')
  }

  const deleteCustomPreset = (label: string) => {
    const updated = customPresets.filter(p => p.label !== label)
    setCustomPresets(updated)
    persistCustomPresets(updated)
  }

  const run = useCallback(async (syms?: string, start?: string) => {
    const s = (syms ?? symbols).trim()
    const st = start ?? startDate
    if (!s) { setError('请输入至少一个股票代码'); return }

    setLoading(true)
    setError('')
    setResult(null)

    try {
      const data = await getComparison(
        s.split(',').map(x => x.trim().toUpperCase()).filter(Boolean),
        st,
      )
      if (data.error) { setError(data.error); return }
      setResult(data)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? '请求失败')
    } finally {
      setLoading(false)
    }
  }, [symbols, startDate])

  const handlePreset = (preset: { label: string; symbols: string }) => {
    setSymbols(preset.symbols)
    run(preset.symbols, startDate)
  }

  const handleDatePreset = (label: string, days: number) => {
    const d = daysAgo(days)
    setStartDate(d)
    setActiveDatePreset(label)
    run(symbols, d)
  }

  return (
    <div className="space-y-4">
      {/* 标题 */}
      <div>
        <h1 className="text-lg font-semibold text-slate-200">收益对比</h1>
        <p className="text-xs text-slate-500 mt-0.5">对比正股与 2X 杠杆 ETF 的历史累计收益曲线</p>
      </div>

      {/* 控制区 */}
      <div className="bg-slate-800 rounded-lg p-4 space-y-3 border border-slate-700">
        {/* 快捷组合 */}
        <div className="space-y-1.5">
          {/* 内置组合 */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-slate-500 shrink-0">内置</span>
            {BUILTIN_PRESETS.map(p => (
              <button
                key={p.label}
                onClick={() => handlePreset(p)}
                className="px-2.5 py-1 text-xs rounded border border-slate-600 text-slate-300 hover:border-blue-500 hover:text-blue-300 transition-colors"
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* 自定义组合 */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-slate-500 shrink-0">自定义</span>
            {customPresets.map(p => (
              <span key={p.label} className="inline-flex items-center gap-0 rounded border border-slate-600 overflow-hidden">
                <button
                  onClick={() => handlePreset(p)}
                  className="px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700 hover:text-blue-300 transition-colors"
                >
                  {p.label}
                </button>
                <button
                  onClick={() => deleteCustomPreset(p.label)}
                  title="删除"
                  className="px-1.5 py-1 text-slate-500 hover:bg-red-900/40 hover:text-red-400 transition-colors text-[10px] border-l border-slate-600"
                >
                  ✕
                </button>
              </span>
            ))}

            {/* 保存按钮 / 保存表单 */}
            {saveMode ? (
              <span className="inline-flex items-center gap-1">
                <input
                  ref={saveInputRef}
                  value={saveName}
                  onChange={e => setSaveName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') commitSave(); if (e.key === 'Escape') { setSaveMode(false); setSaveName('') } }}
                  placeholder="组合名称"
                  className="bg-slate-700 border border-blue-500 rounded px-2 py-0.5 text-xs text-white placeholder-slate-500 focus:outline-none w-28"
                />
                <button onClick={commitSave} className="text-xs px-1.5 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors">保存</button>
                <button onClick={() => { setSaveMode(false); setSaveName('') }} className="text-xs px-1.5 py-0.5 text-slate-400 hover:text-slate-200 transition-colors">取消</button>
              </span>
            ) : (
              <button
                onClick={() => setSaveMode(true)}
                className="px-2 py-1 text-xs rounded border border-dashed border-slate-600 text-slate-500 hover:border-blue-500 hover:text-blue-400 transition-colors"
              >
                + 保存当前
              </button>
            )}
          </div>
        </div>

        {/* 自定义输入 */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[260px]">
            <div className="text-xs text-slate-400 mb-1">股票代码（逗号分隔，最多 8 个）</div>
            <input
              value={symbols}
              onChange={e => setSymbols(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && run()}
              placeholder="NVDA,NVDL,SPY"
              className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />

          </div>

          <div>
            <DatePicker
              label="开始日期"
              value={startDate}
              onChange={v => { setStartDate(v); setActiveDatePreset('') }}
            />
          </div>

          <div>
            <div className="text-xs text-slate-400 mb-1">快捷区间</div>
            <div className="flex gap-1">
              {DATE_PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => handleDatePreset(p.label, p.days)}
                  className={`px-2.5 py-1.5 text-xs rounded transition-colors ${
                    activeDatePreset === p.label
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-700 border border-slate-600 text-slate-300 hover:border-slate-400'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <button
            onClick={() => run()}
            disabled={loading}
            className="px-5 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded transition-colors font-medium"
          >
            {loading ? '加载中…' : '对比'}
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-300 text-sm">{error}</div>
      )}

      {/* 无数据提示 */}
      {result?.missing?.length > 0 && (
        <div className="bg-yellow-900/20 border border-yellow-700/50 rounded-lg px-4 py-2 text-yellow-400 text-xs">
          以下代码无数据：{result.missing.join('、')}（杠杆 ETF 可能尚未上市或代码有误）
        </div>
      )}

      {/* 图表 */}
      {result?.series?.length > 0 && (
        <>
          <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-medium text-slate-300">累计收益对比</div>
              <div className="text-xs text-slate-500">
                {result.start_date} — {result.end_date}
                <span className="ml-2 text-slate-600">（基准价取所选起始日前最近交易日收盘）</span>
              </div>
            </div>
            <ReturnChart series={result.series} />
          </div>

          {/* 指标表 */}
          <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
            <div className="text-sm font-medium text-slate-300 mb-3">业绩指标对比</div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-400 border-b border-slate-700">
                    <th className="text-left py-2 pr-6 font-medium">标的</th>
                    <th className="text-right py-2 px-4 font-medium">基准日 / 价格</th>
                    <th className="text-right py-2 px-4 font-medium">总收益</th>
                    <th className="text-right py-2 px-4 font-medium">年化收益</th>
                    <th className="text-right py-2 px-4 font-medium">最大回撤</th>
                    <th className="text-right py-2 px-4 font-medium">Sharpe</th>
                    <th className="text-right py-2 px-4 font-medium">年化波动率</th>
                  </tr>
                </thead>
                <tbody>
                  {result.metrics.map((m: any, i: number) => {
                    const color = result.series[i]?.color ?? '#94a3b8'
                    return (
                      <tr key={m.symbol} className="border-b border-slate-700/50">
                        <td className="py-2 pr-6">
                          <span className="inline-block w-2.5 h-2.5 rounded-full mr-2" style={{ background: color }} />
                          <span className="font-medium text-white">{m.symbol}</span>
                        </td>
                        <td className="text-right py-2 px-4 text-slate-500 text-xs font-mono">
                          {m.base_date}<br />${m.base_price}
                        </td>
                        <td className={`text-right py-2 px-4 font-mono ${pctColor(m.total_return)}`}>
                          {pct(m.total_return)}
                        </td>
                        <td className={`text-right py-2 px-4 font-mono ${pctColor(m.annual_return)}`}>
                          {pct(m.annual_return)}
                        </td>
                        <td className="text-right py-2 px-4 font-mono text-red-400">
                          {pct(m.max_drawdown)}
                        </td>
                        <td className={`text-right py-2 px-4 font-mono ${m.sharpe >= 1 ? 'text-emerald-400' : m.sharpe >= 0 ? 'text-slate-300' : 'text-red-400'}`}>
                          {m.sharpe.toFixed(2)}
                        </td>
                        <td className="text-right py-2 px-4 font-mono text-slate-300">
                          {pct(m.volatility)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* 相关系数矩阵 */}
          {result.corr_matrix && (
            <div className="bg-slate-800 rounded-lg p-4 border border-slate-700">
              <CorrMatrix matrix={result.corr_matrix} />
            </div>
          )}

          {/* 说明 */}
          <div className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50 text-xs text-slate-500 space-y-1">
            <div>· 收益曲线以对比区间第一个共同交易日为基准（归一化为 0%），展示累计收益率变化</div>
            <div>· 杠杆 ETF 因每日再平衡（复利磨损），长期收益未必恰好是正股的 2 倍；震荡市中实际表现可能更差</div>
            <div>· 数据来源：yfinance，已做复权调整</div>
          </div>
        </>
      )}
    </div>
  )
}
