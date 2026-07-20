import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getRiskThermometer } from '../api/client'
import SymbolLink from './SymbolLink'

const AX = '#94a3b8'   // slate-400

/** 风险温度计：减仓预警块（VIX 期限结构 + 组合相关性），嵌在持仓总览页内 */
export default function RiskThermometer() {
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['risk-thermometer'],
    queryFn: () => getRiskThermometer(false),
    refetchInterval: 5 * 60_000,
  })

  if (isLoading || !data)
    return (
      <div className="bg-slate-800 rounded-lg border border-slate-700 px-4 py-3 text-slate-400 text-sm">
        风险温度计加载中…
      </div>
    )

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

  const syms: string[] = corr.symbols || []
  const isPortfolioProxy = corr.source !== 'ib'
  const heatData: any[] = []
  if (corr.available && !isPortfolioProxy) {
    for (let i = 0; i < syms.length; i++)
      for (let j = 0; j < syms.length; j++)
        heatData.push([j, i, (corr.matrix?.[i]?.[j]) ?? 0])
  }
  const heatOption = corr.available && !isPortfolioProxy && {
    backgroundColor: 'transparent',
    grid: { left: 52, right: 16, top: 16, bottom: 44 },
    tooltip: { formatter: (p: any) => `${syms[p.data[1]]} × ${syms[p.data[0]]}<br/>相关 ${p.data[2]}` },
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

  const breadth = t.breadth || {}
  const leadership = t.leadership || {}
  const pct = (x: number) => `${Math.round(x * 100)}%`

  const bdOption = breadth.available && {
    backgroundColor: 'transparent',
    grid: { left: 40, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => pct(v) },
    xAxis: {
      type: 'category',
      data: (breadth.history || []).map((h: any) => h.date),
      axisLabel: { color: AX, fontSize: 10, showMaxLabel: true },
      axisLine: { lineStyle: { color: '#475569' } },
    },
    yAxis: {
      type: 'value', min: 0, max: 1,
      axisLabel: { color: AX, fontSize: 10, formatter: (v: number) => pct(v) },
      splitLine: { lineStyle: { color: '#33415555' } },
    },
    series: [{
      type: 'line', smooth: true, showSymbol: false,
      data: (breadth.history || []).map((h: any) => h.ma50),
      lineStyle: { color: '#34d399', width: 2 },
      areaStyle: { color: 'rgba(52,211,153,0.08)' },
      markLine: {
        symbol: 'none', silent: true,
        data: [
          { yAxis: 0.6, lineStyle: { color: '#f59e0b', type: 'dashed' }, label: { formatter: '转弱 60%', color: '#f59e0b', fontSize: 10 } },
          { yAxis: 0.4, lineStyle: { color: '#ef4444', type: 'dashed' }, label: { formatter: '走弱 40%', color: '#ef4444', fontSize: 10 } },
        ],
      },
    }],
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 overflow-hidden">
      {/* 温度总览条 */}
      <div className={`px-4 py-2.5 border-b ${band} flex items-center justify-between flex-wrap gap-2`}>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium opacity-70">🌡️ 风险温度计</span>
          <span className={`w-2.5 h-2.5 rounded-full ${dot} animate-pulse`} />
          <span className="text-base font-semibold">{levelText}</span>
          <span className="text-xs opacity-80">风险分 {t.score}/{t.max_score}</span>
          <span className="text-xs opacity-90">· {t.advice}</span>
        </div>
        <button onClick={() => refetch()} disabled={isFetching}
          className="px-2 py-0.5 text-[11px] rounded bg-slate-700/60 text-slate-200 hover:bg-slate-600 disabled:opacity-50">
          {isFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-px bg-slate-700/40">
        {/* VIX 期限结构 */}
        <div className="bg-slate-800 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <h3 className="text-xs font-semibold text-slate-300">① VIX 期限结构（VIX / VIX3M）</h3>
            {vix.available && (
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                vix.score === 2 ? 'bg-red-900/50 text-red-300' : vix.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {vix.label}
              </span>
            )}
          </div>
          {vix.available ? (
            <>
              <div className="flex items-baseline gap-3 mb-1">
                <span className="text-xl font-bold text-sky-300">{vix.ratio}</span>
                <span className="text-[11px] text-slate-400">VIX {vix.vix} · VIX3M {vix.vix3m}</span>
              </div>
              <ReactECharts option={vixOption} style={{ height: 170 }} notMerge />
              <p className="text-[10px] text-slate-500 mt-1 leading-relaxed">
                ≥1 倒挂 = 近月恐慌超远月 → risk-off；0.95~1 走平警惕；&lt;0.95 正常。
              </p>
            </>
          ) : <div className="text-xs text-slate-500 py-6 text-center">VIX 数据不可用：{vix.error}</div>}
        </div>

        {/* 组合相关性 */}
        <div className="bg-slate-800 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <h3 className="text-xs font-semibold text-slate-300">
              {isPortfolioProxy ? '② AI 关注池相关性代理（IB 未连接）' : '② 组合相关性 / 有效持仓数'}
            </h3>
            {corr.available && (
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                corr.score === 2 ? 'bg-red-900/50 text-red-300' : corr.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {corr.label}
              </span>
            )}
          </div>
          {corr.available ? (
            <>
              <div className="flex items-center gap-3 mb-1 flex-wrap text-[11px]">
                <span className="text-slate-400">平均 <b className="text-slate-200 text-sm">{corr.avg_corr}</b></span>
                <span className="text-slate-400">最大 <b className="text-slate-200">{corr.max_corr}</b></span>
                <span className="text-slate-400">有效持仓 <b className="text-amber-300 text-sm">{corr.enb_corr}</b>/{corr.n}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-400">
                  {corr.source === 'ib' ? '真实持仓' : corr.source === 'ai_universe' ? 'AI关注池(IB未连)' : corr.source}
                </span>
              </div>
              {isPortfolioProxy ? (
                <div className="rounded-lg border border-yellow-800/50 bg-yellow-950/20 px-3 py-3 mt-3 text-sm text-yellow-300 leading-relaxed">
                  当前数值来自 {corr.n} 只 AI 关注股，只反映板块内部同步程度。它不代表真实持仓，
                  不进入统一风险灯号，也不据此生成减仓建议；连接 IB 后才显示实际持仓热力图。
                </div>
              ) : (
                <>
                  <div className="text-[10px] text-slate-500 mb-1">
                    {syms.map((s, i) => (<span key={s}><SymbolLink symbol={s} />{i < syms.length - 1 ? '、' : ''}</span>))}
                  </div>
                  <ReactECharts option={heatOption} style={{ height: 200 }} notMerge />
                  <p className="text-[10px] text-slate-500 mt-1 leading-relaxed">
                    {corr.n} 只实际只相当于 <b className="text-amber-400">{corr.enb_corr}</b> 个独立仓位 —— 相关越高分散越假。
                  </p>
                </>
              )}
            </>
          ) : <div className="text-xs text-slate-500 py-6 text-center">相关性不可用：{corr.error}</div>}
        </div>

        {/* 板块广度 */}
        <div className="bg-slate-800 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <h3 className="text-xs font-semibold text-slate-300">③ 板块广度（站上均线占比）</h3>
            {breadth.available && (
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                breadth.score === 2 ? 'bg-red-900/50 text-red-300' : breadth.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {breadth.label}
              </span>
            )}
          </div>
          {breadth.available ? (
            <>
              <div className="flex items-center gap-3 mb-1 flex-wrap text-[11px]">
                <span className="text-slate-400">&gt;MA50 <b className="text-emerald-300 text-sm">{pct(breadth.above_ma50)}</b>
                  <span className="text-slate-500"> (5日前 {pct(breadth.above_ma50_prev5)})</span></span>
                <span className="text-slate-400">&gt;MA20 <b className="text-slate-200">{pct(breadth.above_ma20)}</b>
                  <span className="text-slate-500"> (5日前 {pct(breadth.above_ma20_prev5)})</span></span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700/60 text-slate-400">AI池 {breadth.n} 只</span>
              </div>
              <ReactECharts option={bdOption} style={{ height: 170 }} notMerge />
              <p className="text-[10px] text-slate-500 mt-1 leading-relaxed">
                板块内站上 MA50 的占比，越低=内部越烂。从高位快速回落=顶部派发预警（即便指数还没跌）。
              </p>
            </>
          ) : <div className="text-xs text-slate-500 py-6 text-center">广度不可用：{breadth.error}</div>}
        </div>

        {/* 龙头 RS 掉头 */}
        <div className="bg-slate-800 p-3">
          <div className="flex items-center justify-between mb-1.5">
            <h3 className="text-xs font-semibold text-slate-300">④ 龙头 RS 掉头（领涨股转弱）</h3>
            {leadership.available && (
              <span className={`text-[11px] px-2 py-0.5 rounded ${
                leadership.score === 2 ? 'bg-red-900/50 text-red-300' : leadership.score === 1 ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>
                {leadership.label}
              </span>
            )}
          </div>
          {leadership.available ? (
            <>
              <div className="flex items-baseline gap-3 mb-2">
                <span className="text-xl font-bold text-amber-300">{pct(leadership.frac)}</span>
                <span className="text-[11px] text-slate-400">
                  龙头 {leadership.n_leaders} 只中 <b className="text-slate-200">{leadership.rolled_over}</b> 只近 10 日跑输 SPY
                </span>
              </div>
              <div className="text-[11px] text-slate-400 mb-1">掉头的龙头：</div>
              <div className="flex flex-wrap gap-1.5">
                {(leadership.rolled_symbols || []).length
                  ? (leadership.rolled_symbols || []).map((s: string) => (
                      <span key={s} className="text-[11px] px-1.5 py-0.5 rounded bg-red-900/30 text-red-300 border border-red-800/40">
                        <SymbolLink symbol={s} />
                      </span>
                    ))
                  : <span className="text-[11px] text-slate-500">无 —— 龙头仍在领涨</span>}
              </div>
              <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
                龙头=63 日相对 SPY 最强的前 1/3。最强的票开始跑输 = 资金撤离领涨股，常领先大盘见顶。
              </p>
            </>
          ) : <div className="text-xs text-slate-500 py-6 text-center">龙头 RS 不可用：{leadership.error}</div>}
        </div>
      </div>
    </div>
  )
}
