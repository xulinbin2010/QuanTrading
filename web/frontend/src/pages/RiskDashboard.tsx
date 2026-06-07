import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getRiskThermometer } from '../api/client'
import SymbolLink from '../components/SymbolLink'

const AX = '#94a3b8'   // slate-400

export default function RiskDashboard() {
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['risk-thermometer'],
    queryFn: () => getRiskThermometer(false),
    refetchInterval: 5 * 60_000,
  })

  if (isLoading || !data) return <div className="text-slate-400 text-sm p-4">加载风险温度计…</div>

  const t = data
  const vix = t.vix_term || {}
  const corr = t.correlation || {}

  const band =
    t.color === 'red'    ? 'bg-red-900/40 border-red-600 text-red-200'
  : t.color === 'yellow' ? 'bg-amber-900/40 border-amber-600 text-amber-200'
  :                        'bg-emerald-900/40 border-emerald-600 text-emerald-200'
  const dot =
    t.color === 'red' ? 'bg-red-500' : t.color === 'yellow' ? 'bg-amber-500' : 'bg-emerald-500'
  const levelText = t.level === 'high' ? '高风险 · 红灯' : t.level === 'mid' ? '中等 · 黄灯' : '低 · 绿灯'

  // ── VIX 期限结构比值曲线 ──
  const vixOption = vix.available && {
    backgroundColor: 'transparent',
    grid: { left: 44, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: (vix.history || []).map((h: any) => h.date),
      axisLabel: { color: AX, fontSize: 10, showMaxLabel: true },
      axisLine: { lineStyle: { color: '#475569' } },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: AX, fontSize: 10 },
      splitLine: { lineStyle: { color: '#33415555' } },
    },
    series: [{
      type: 'line', smooth: true, showSymbol: false,
      data: (vix.history || []).map((h: any) => h.ratio),
      lineStyle: { color: '#38bdf8', width: 2 },
      areaStyle: { color: 'rgba(56,189,248,0.08)' },
      markLine: {
        symbol: 'none', silent: true,
        data: [
          { yAxis: 1.0, lineStyle: { color: '#ef4444', type: 'dashed' }, label: { formatter: '倒挂 1.0', color: '#ef4444', fontSize: 10 } },
          { yAxis: 0.95, lineStyle: { color: '#f59e0b', type: 'dashed' }, label: { formatter: '警惕 0.95', color: '#f59e0b', fontSize: 10 } },
        ],
      },
    }],
  }

  // ── 相关性热力图 ──
  const syms: string[] = corr.symbols || []
  const heatData: any[] = []
  if (corr.available) {
    for (let i = 0; i < syms.length; i++)
      for (let j = 0; j < syms.length; j++)
        heatData.push([j, i, (corr.matrix?.[i]?.[j]) ?? 0])
  }
  const heatOption = corr.available && {
    backgroundColor: 'transparent',
    grid: { left: 52, right: 16, top: 16, bottom: 44 },
    tooltip: {
      formatter: (p: any) => `${syms[p.data[1]]} × ${syms[p.data[0]]}<br/>相关 ${p.data[2]}`,
    },
    xAxis: { type: 'category', data: syms, axisLabel: { color: AX, fontSize: 10, rotate: 45 }, splitArea: { show: true } },
    yAxis: { type: 'category', data: syms, axisLabel: { color: AX, fontSize: 10 }, splitArea: { show: true } },
    visualMap: {
      min: 0, max: 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
      inRange: { color: ['#1e3a5f', '#facc15', '#ef4444'] },
      textStyle: { color: AX, fontSize: 10 }, itemHeight: 60,
    },
    series: [{
      type: 'heatmap', data: heatData,
      label: { show: true, fontSize: 9, color: '#e2e8f0', formatter: (p: any) => p.data[2].toFixed(2) },
    }],
  }

  return (
    <div className="space-y-4">
      <div className="text-[11px] text-slate-500 bg-slate-900/40 border border-slate-700/50 rounded px-2.5 py-1.5 leading-relaxed">
        <span className="text-slate-400">风险温度计</span>：合成多个独立的「减仓预警」信号（区别于只'停买'的 SPY/VIX 熔断）。
        多个信号<span className="text-slate-300">共振</span>才升级，避免单点误报。当前 v1 含 ① VIX 期限结构 ② 组合相关性。
      </div>

      {/* 温度总览 */}
      <div className={`rounded-lg border px-4 py-3 ${band}`}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <span className={`w-3 h-3 rounded-full ${dot} animate-pulse`} />
            <span className="text-lg font-semibold">{levelText}</span>
            <span className="text-sm opacity-80">风险分 {t.score} / {t.max_score}</span>
          </div>
          <button onClick={() => refetch()} disabled={isFetching}
            className="px-2.5 py-1 text-xs rounded bg-slate-700/60 text-slate-200 hover:bg-slate-600 disabled:opacity-50">
            {isFetching ? '刷新中…' : '刷新'}
          </button>
        </div>
        <div className="mt-1.5 text-sm">{t.advice}</div>
        <div className="mt-1 text-[10px] opacity-60">更新于 {t.updated_at}（30 分钟缓存，每 5 分钟自动拉取）</div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* VIX 期限结构 */}
        <div className="bg-slate-900/40 border border-slate-700/50 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-200">① VIX 期限结构（VIX / VIX3M）</h3>
            {vix.available && (
              <span className={`text-xs px-2 py-0.5 rounded ${
                vix.score === 2 ? 'bg-red-900/50 text-red-300' : vix.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {vix.label}
              </span>
            )}
          </div>
          {vix.available ? (
            <>
              <div className="flex items-baseline gap-4 mb-1">
                <span className="text-2xl font-bold text-sky-300">{vix.ratio}</span>
                <span className="text-xs text-slate-400">VIX {vix.vix} · VIX3M {vix.vix3m}</span>
              </div>
              <ReactECharts option={vixOption} style={{ height: 200 }} notMerge />
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                比值 ≥ 1 = 近月恐慌超过远月（倒挂）→ 短期 risk-off；0.95~1 走平需警惕；&lt;0.95 正常。
              </p>
            </>
          ) : <div className="text-xs text-slate-500 py-8 text-center">VIX 数据不可用：{vix.error}</div>}
        </div>

        {/* 组合相关性 */}
        <div className="bg-slate-900/40 border border-slate-700/50 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-200">② 组合相关性 / 有效持仓数</h3>
            {corr.available && (
              <span className={`text-xs px-2 py-0.5 rounded ${
                corr.score === 2 ? 'bg-red-900/50 text-red-300' : corr.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {corr.label}
              </span>
            )}
          </div>
          {corr.available ? (
            <>
              <div className="flex items-center gap-4 mb-1 flex-wrap text-xs">
                <span className="text-slate-400">平均相关 <b className="text-slate-200 text-base">{corr.avg_corr}</b></span>
                <span className="text-slate-400">最大 <b className="text-slate-200">{corr.max_corr}</b></span>
                <span className="text-slate-400">有效持仓 <b className="text-amber-300 text-base">{corr.enb_corr}</b> / {corr.n} 只</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-400">
                  {corr.source === 'ib' ? '真实持仓' : corr.source === 'ai_universe' ? 'AI关注池(IB未连)' : corr.source}
                </span>
              </div>
              <div className="text-[11px] text-slate-500 mb-1">
                持仓：{syms.map((s, i) => (<span key={s}><SymbolLink symbol={s} />{i < syms.length - 1 ? '、' : ''}</span>))}
              </div>
              <ReactECharts option={heatOption} style={{ height: 240 }} notMerge />
              <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                {corr.n} 只票实际只相当于 <b className="text-amber-400">{corr.enb_corr}</b> 个独立仓位 —— 相关性越高，分散越「假」，一起跌的风险越大。
              </p>
            </>
          ) : <div className="text-xs text-slate-500 py-8 text-center">相关性不可用：{corr.error}</div>}
        </div>
      </div>
    </div>
  )
}
