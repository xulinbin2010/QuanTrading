/**
 * 盘前扫描模态框（纯实时数据，无 LLM）。
 *   - 「盘前扫描」tab：实时宏观快照 + 持仓/watchlist 逐只盘前报价(价/涨跌/昨收/20d高低)
 *   - 「清单配置」tab：核心持仓 / 短线 / watchlist 三组可编辑清单，存后端 JSON；核心持仓可从 IB 导入
 * 数字全部由后端实时拉取(yfinance, 带 as-of ET 时间戳)。
 * 注：模块2/4(隔夜新闻/行动分析)需接 LLM，当前未启用；后端 /briefing 端点保留，充值后可一键恢复。
 */
import { useEffect, useState } from 'react'
import {
  getPremarketConfig, savePremarketConfig, getPremarketScan,
  getPositions, getCoreCards, generateCoreCards, type PremarketConfig,
} from '../api/client'

const EMPTY: PremarketConfig = { core: [], swing: [], watchlist: [] }

type Col = { key: string; label: string; w?: string; ph?: string }
const COLS: Record<keyof PremarketConfig, Col[]> = {
  core: [
    { key: 'ticker', label: '代码', w: 'w-20', ph: 'NVDA' },
    { key: 'cost', label: '成本价', w: 'w-20', ph: '120' },
    { key: 'weight', label: '仓位%', w: 'w-16', ph: '25%' },
    { key: 'thesis', label: '持有逻辑', ph: 'AI capex 主线' },
    { key: 'invalidation', label: '失效条件', ph: 'HBM 报价连续两季下跌' },
    { key: 'catalysts', label: '催化剂日历', ph: '12/18 财报；CES 1/6' },
  ],
  swing: [
    { key: 'ticker', label: '代码', w: 'w-20', ph: 'PLTR' },
    { key: 'entry', label: '进场价', w: 'w-20', ph: '38' },
    { key: 'stop', label: '止损位', w: 'w-20', ph: '34' },
    { key: 'reason', label: '交易理由', ph: '突破回踩' },
  ],
  watchlist: [
    { key: 'ticker', label: '代码', w: 'w-20', ph: 'MU' },
    { key: 'trigger', label: '触发条件', ph: '站上 EMA21 放量' },
    { key: 'reason', label: '想买的理由', ph: '存储周期反转' },
  ],
}
const GROUP_LABEL: Record<keyof PremarketConfig, string> = {
  core: '核心持仓', swing: '短线仓位', watchlist: 'Watchlist',
}
// 扫描表里每组附带的定性列
const EXTRA: Record<keyof PremarketConfig, Col[]> = {
  core: [{ key: 'cost', label: '成本' }, { key: 'weight', label: '仓位' }, { key: 'thesis', label: '逻辑' }],
  swing: [{ key: 'entry', label: '进场' }, { key: 'stop', label: '止损' }, { key: 'reason', label: '理由' }],
  watchlist: [{ key: 'trigger', label: '触发' }, { key: 'reason', label: '理由' }],
}

function pct(v: any) {
  return typeof v === 'number' ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—'
}
function chgCls(v: any) {
  if (typeof v !== 'number') return 'text-slate-400'
  return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-slate-300'
}

// 论点检查档位 → 徽章配色
const VERDICT_CLS: Record<string, string> = {
  '强化':     'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  '中性':     'bg-slate-600/40 text-slate-300 border-slate-500/40',
  '削弱':     'bg-amber-500/20 text-amber-300 border-amber-500/40',
  '失效预警': 'bg-red-500/20 text-red-300 border-red-500/40',
}

export default function PremarketBriefingModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'scan' | 'cards' | 'config'>('scan')
  const [cfg, setCfg] = useState<PremarketConfig>(EMPTY)
  const [saved, setSaved] = useState('')

  const [scan, setScan] = useState<any>(null)
  const [scanLoading, setScanLoading] = useState(false)

  const [cards, setCards] = useState<any>(null)
  const [cardsBusy, setCardsBusy] = useState(false)
  const [cardsMsg, setCardsMsg] = useState('')

  useEffect(() => {
    getPremarketConfig().then(c => setCfg({ ...EMPTY, ...c })).catch(() => {})
    getCoreCards().then(d => { if (d?.cards?.length) setCards(d) }).catch(() => {})
    refreshScan()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const genCards = () => {
    setCardsBusy(true)
    setCardsMsg('')
    generateCoreCards()
      .then(setCards)
      .catch(e => setCardsMsg(e.response?.data?.detail || '生成失败（网络/API key/额度？）'))
      .finally(() => setCardsBusy(false))
  }

  const refreshScan = () => {
    setScanLoading(true)
    getPremarketScan().then(setScan).catch(() => {}).finally(() => setScanLoading(false))
  }

  const setRow = (grp: keyof PremarketConfig, i: number, k: string, v: string) =>
    setCfg(c => ({ ...c, [grp]: c[grp].map((r, ri) => ri === i ? { ...r, [k]: v } : r) }))
  const addRow = (grp: keyof PremarketConfig) =>
    setCfg(c => ({ ...c, [grp]: [...c[grp], { ticker: '' } as any] }))
  const delRow = (grp: keyof PremarketConfig, i: number) =>
    setCfg(c => ({ ...c, [grp]: c[grp].filter((_, ri) => ri !== i) }))

  const save = () => {
    savePremarketConfig(cfg).then(c => {
      setCfg({ ...EMPTY, ...c }); setSaved('已保存 ✓'); setTimeout(() => setSaved(''), 2000)
      refreshScan()  // 清单变了，刷新扫描
    }).catch(() => { setSaved('保存失败'); setTimeout(() => setSaved(''), 2000) })
  }

  const importIB = () => {
    getPositions().then((res: any) => {
      const list = Array.isArray(res) ? res : (res?.positions ?? [])
      const rows = list
        .filter((p: any) => (p.sec_type ?? p.secType ?? 'STK') === 'STK')
        .map((p: any) => ({
          ticker: String(p.symbol ?? p.ticker ?? '').toUpperCase(),
          cost: String(p.avg_cost ?? p.avgCost ?? p.cost ?? ''), weight: '', thesis: '',
        }))
        .filter((r: any) => r.ticker)
      if (rows.length) { setCfg(c => ({ ...c, core: rows })); setSaved(`已从 IB 导入 ${rows.length} 只（记得保存）`); setTimeout(() => setSaved(''), 3000) }
      else { setSaved('IB 未连接或无股票持仓'); setTimeout(() => setSaved(''), 3000) }
    }).catch(() => { setSaved('IB 导入失败（未连接？）'); setTimeout(() => setSaved(''), 3000) })
  }

  const quoteOf = (ticker: string) => (scan?.quotes?.quotes ?? {})[(ticker || '').toUpperCase()] ?? null

  const ScanGroup = ({ grp }: { grp: keyof PremarketConfig }) => {
    const rows: any[] = scan?.config?.[grp] ?? []
    const extra = EXTRA[grp]
    return (
      <div className="mb-4">
        <div className="text-sm font-semibold text-slate-200 mb-1">{GROUP_LABEL[grp]}</div>
        {!rows.length ? (
          <div className="text-xs text-slate-600">（清单为空，去「清单配置」添加）</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm whitespace-nowrap">
              <thead>
                <tr className="text-xs text-slate-400 border-b border-slate-700">
                  <th className="text-left px-2 py-1 font-medium">代码</th>
                  <th className="text-right px-2 py-1 font-medium">pre-mkt</th>
                  <th className="text-right px-2 py-1 font-medium">涨跌</th>
                  <th className="text-right px-2 py-1 font-medium">昨收</th>
                  <th className="text-right px-2 py-1 font-medium">20d 高/低</th>
                  {extra.map(c => <th key={c.key} className="text-left px-2 py-1 font-medium">{c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const q = quoteOf(r.ticker)
                  return (
                    <tr key={i} className="border-b border-slate-800">
                      <td className="px-2 py-1 font-mono font-medium text-slate-100">{(r.ticker || '').toUpperCase()}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-200">{q?.last ?? '—'}</td>
                      <td className={`px-2 py-1 text-right font-mono ${chgCls(q?.change_pct)}`}>{pct(q?.change_pct)}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-500">{q?.prev_close ?? '—'}</td>
                      <td className="px-2 py-1 text-right font-mono text-slate-500">
                        {q?.high_20d != null ? `${q.high_20d}/${q.low_20d}` : '—'}
                      </td>
                      {extra.map(c => <td key={c.key} className="px-2 py-1 text-slate-400 max-w-[180px] truncate" title={r[c.key]}>{r[c.key] || '—'}</td>)}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl"
        onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-700">
          <div className="text-base font-bold text-white">📋 盘前扫描（实时）</div>
          <div className="flex gap-1">
            {(['scan', 'cards', 'config'] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-3 py-1 text-sm rounded ${tab === t ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
                {t === 'scan' ? '盘前扫描' : t === 'cards' ? '核心票情报卡' : '清单配置'}
              </button>
            ))}
          </div>
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-white text-xl leading-none">×</button>
        </div>

        <div className="overflow-y-auto p-5">
          {tab === 'scan' && (
            <>
              <div className="flex items-center gap-2 mb-2">
                <div className="text-sm font-semibold text-slate-200">实时宏观快照</div>
                {scan?.snapshot && <span className="text-xs text-slate-500">as-of {scan.snapshot.as_of} · yfinance</span>}
                <button onClick={refreshScan} disabled={scanLoading}
                  className="ml-auto px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300 hover:bg-slate-600 disabled:opacity-50">
                  {scanLoading ? '刷新中…' : '刷新行情'}
                </button>
              </div>
              <div className="overflow-x-auto mb-4">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-slate-400 border-b border-slate-700">
                      <th className="text-left px-2 py-1 font-medium">指标</th>
                      <th className="text-right px-2 py-1 font-medium">现价</th>
                      <th className="text-right px-2 py-1 font-medium">隔夜涨跌</th>
                      <th className="text-right px-2 py-1 font-medium">昨结/昨收</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(scan?.snapshot?.rows ?? []).map((r: any) => (
                      <tr key={r.symbol} className="border-b border-slate-800">
                        <td className="px-2 py-1 text-slate-300">{r.label}</td>
                        <td className="px-2 py-1 text-right font-mono text-slate-200">{r.last ?? '—'}</td>
                        <td className={`px-2 py-1 text-right font-mono ${chgCls(r.change_pct)}`}>{pct(r.change_pct)}</td>
                        <td className="px-2 py-1 text-right font-mono text-slate-500">{r.prev_close ?? '—'}</td>
                      </tr>
                    ))}
                    {!scan && <tr><td colSpan={4} className="px-2 py-3 text-center text-slate-500">加载中…</td></tr>}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center gap-2 mb-2">
                <div className="text-sm font-semibold text-slate-200">个股盘前扫描</div>
                {scan?.quotes && <span className="text-xs text-slate-500">as-of {scan.quotes.as_of} · yfinance</span>}
              </div>
              <ScanGroup grp="core" />
              <ScanGroup grp="swing" />
              <ScanGroup grp="watchlist" />

              <div className="text-sm text-slate-400 space-y-1 mt-3">
                <div>· Pre-market 报价流动性稀薄，可能与开盘价显著偏离，仅供参考。</div>
                <div>· 隔夜新闻 / 财报 / 行动建议的大盘简报（模块 2/4）未启用；核心票联网情报默认走本机 Codex 登录态，无 API 费用。</div>
              </div>
            </>
          )}
          {tab === 'cards' && (
            <>
              <div className="flex items-center gap-2 mb-3">
                <div className="text-sm font-semibold text-slate-200">核心票每日情报卡</div>
                {cards?.as_of && <span className="text-xs text-slate-500">生成于 {cards.as_of}</span>}
                <button onClick={genCards} disabled={cardsBusy}
                  className="ml-auto px-3 py-1 rounded text-sm bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50">
                  {cardsBusy ? '联网检索中（约 2-5 分钟）…' : cards ? '重新生成' : '生成情报卡'}
                </button>
              </div>
              {cardsMsg && <div className="mb-3 text-sm text-red-400">{cardsMsg}</div>}
              {!cards?.cards?.length && !cardsBusy && !cardsMsg && (
                <div className="text-sm text-slate-500 space-y-1">
                  <div>还没有情报卡。先到「清单配置」把核心持仓（含持有逻辑 / 失效条件 / 催化剂日历）填好并保存，再点「生成情报卡」。</div>
                  <div>每张卡：隔夜要闻 · 产业链/同行动向 · 华尔街评级 · 催化剂倒计时 + 对照你的论点给出「强化 / 中性 / 削弱 / 失效预警」检查结论。</div>
                  <div>也可在「任务调度」开启 core_intel_cards 任务，每天美东 8:15 盘前自动生成（默认走本机 Codex 登录态，无 API 费用）。</div>
                </div>
              )}
              <div className="space-y-3">
                {(cards?.cards ?? []).map((c: any) => (
                  <div key={c.ticker} className="border border-slate-700 bg-slate-800/60 rounded-lg p-4">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-base font-bold font-mono text-white">{c.ticker}</span>
                      {c.verdict && (
                        <span className={`text-xs px-1.5 py-0.5 rounded border ${VERDICT_CLS[c.verdict] ?? VERDICT_CLS['中性']}`}>
                          论点检查：{c.verdict}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">{c.text}</div>
                  </div>
                ))}
              </div>
              {cards?.cards?.length > 0 && (
                <div className="text-sm text-slate-400 space-y-1 mt-4">
                  <div>· 情报由 AI 联网检索生成，来源以卡内标注为准；「论点检查」对照的是你在清单配置里手填的持有逻辑与失效条件。</div>
                  <div>· 出现「削弱 / 失效预警」时建议人工核实原始新闻源后再决策，不要只凭卡片行动。</div>
                </div>
              )}
            </>
          )}
          {tab === 'config' && (
            <>
              <div className="flex items-center gap-3 mb-3">
                <button onClick={save} className="px-3 py-1 rounded text-sm bg-blue-600 text-white hover:bg-blue-500">保存清单</button>
                <button onClick={importIB} className="px-3 py-1 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600">从 IB 导入核心持仓</button>
                {saved && <span className="text-xs text-emerald-400">{saved}</span>}
              </div>
              {(['core', 'swing', 'watchlist'] as const).map(grp => (
                <div key={grp} className="mb-4">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="text-sm font-semibold text-slate-200">{GROUP_LABEL[grp]}</div>
                    <button onClick={() => addRow(grp)} className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 hover:bg-slate-600">+ 添加</button>
                  </div>
                  <div className="space-y-1">
                    {cfg[grp].map((row, i) => (
                      <div key={i} className="flex gap-1.5 items-center">
                        {COLS[grp].map(col => (
                          <input key={col.key} value={(row as any)[col.key] ?? ''} placeholder={col.ph}
                            onChange={e => setRow(grp, i, col.key, e.target.value)}
                            className={`${col.w ?? 'flex-1'} px-2 py-1 text-sm rounded bg-slate-800 border border-slate-700 text-slate-200 placeholder:text-slate-600 focus:border-blue-500 outline-none`} />
                        ))}
                        <button onClick={() => delRow(grp, i)} className="text-slate-500 hover:text-red-400 px-1">×</button>
                      </div>
                    ))}
                    {!cfg[grp].length && <div className="text-xs text-slate-600">（空，点「+ 添加」）</div>}
                  </div>
                </div>
              ))}
              <div className="text-sm text-slate-400 space-y-1 mt-3">
                <div>· 核心持仓可点「从 IB 导入」自动拉 ticker + 成本（需 IB 已连接），再补持有逻辑/仓位%。</div>
                <div>· 「失效条件」写清楚什么情况下承认看错（如：HBM 报价连续两季下跌）、「催化剂日历」写日期+事件；这两栏是「核心票情报卡」做论点检查的依据，写得越具体，AI 的检查越有用。</div>
                <div>· 保存后「盘前扫描」tab 会自动按新清单刷新逐只报价。</div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
