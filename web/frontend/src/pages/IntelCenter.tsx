/**
 * 情报中心（MVP：持仓圈层）：原始新闻/SEC → Claude 聚簇去重 + 提取 → 结构化事件卡。
 * 缓存秒开；「刷新情报」后台跑 1-3 分钟（一次 LLM 调用，订阅 CLI 引擎零成本），期间轮询。
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getIntelEvents, refreshIntelEvents, getSocialBuzz } from '../api/client'
import SymbolLink from '../components/SymbolLink'

type IntelEvent = {
  scope?: string; type: string; symbols: string[]; direction: string; strength: string
  title: string; analysis: string; date: string; links: string[]
}

type IntelData = {
  running?: boolean; error?: string; generated_at_cn?: string; model?: string
  holdings?: string[]; events?: IntelEvent[]
}

type BuzzData = {
  as_of?: string; baseline_days: number; alerts?: string[]; rows?: BuzzRow[]
}

type RequestError = { response?: { data?: { detail?: string } }; message?: string }
const requestError = (error: unknown, fallback: string) => {
  const err = error as RequestError
  return err.response?.data?.detail || err.message || fallback
}

// 事件距今天数（本地日期粒度；无效日期返回 Infinity 归入「全部」）
function daysAgo(dateStr: string): number {
  const d = new Date(dateStr + 'T00:00:00')
  if (isNaN(d.getTime())) return Infinity
  return Math.floor((Date.now() - d.getTime()) / 86_400_000)
}
const RANGE_OPTS = [
  { key: 'overnight', label: '隔夜', maxDays: 1 },
  { key: '3d',        label: '近3天', maxDays: 3 },
  { key: 'all',       label: '全部', maxDays: Infinity },
] as const

const DIR_STYLE: Record<string, { box: string; txt: string; dot: string }> = {
  利好: { box: 'border-emerald-800/50 bg-emerald-900/10', txt: 'text-emerald-300', dot: 'bg-emerald-500' },
  利空: { box: 'border-red-800/50 bg-red-900/10',         txt: 'text-red-300',     dot: 'bg-red-500' },
  中性: { box: 'border-slate-700 bg-slate-800/40',        txt: 'text-slate-300',   dot: 'bg-slate-500' },
}

function EventCard({ e }: { e: IntelEvent }) {
  const s = DIR_STYLE[e.direction] || DIR_STYLE.中性
  return (
    <div className={`border rounded-lg px-4 py-3 ${s.box}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`w-2 h-2 rounded-full shrink-0 ${s.dot}`} />
        <span className={`text-sm font-semibold ${s.txt}`}>{e.direction}{e.strength === '强' ? '·强' : e.strength === '弱' ? '·弱' : ''}</span>
        <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700/70 text-slate-300">{e.type}</span>
        {e.symbols.map(sym => <SymbolLink key={sym} symbol={sym} className="font-mono text-xs text-blue-400 hover:text-blue-300 cursor-pointer" />)}
        <span className="ml-auto text-xs text-slate-500 font-mono">{e.date}</span>
      </div>
      <div className="text-sm font-medium text-slate-100 mt-1.5">{e.title}</div>
      <div className="text-sm text-slate-300/90 mt-1 leading-relaxed">{e.analysis}</div>
      {e.links.length > 0 && (
        <div className="flex gap-3 mt-1.5 flex-wrap">
          {e.links.slice(0, 3).map((u, j) => (
            <a key={j} href={u} target="_blank" rel="noreferrer"
              className="text-xs text-slate-500 hover:text-blue-400 truncate max-w-[280px]">
              {(() => { try { return new URL(u).hostname } catch { return u } })()} ↗
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 社区热度板块（Reddit/StockTwits 异动榜，L1 观察层）─────────────────

type BuzzRow = {
  symbol: string; tag: string; mentions: number | null; avg7: number | null
  z: number | null; rank: number | null; reddit_posts: number | null
  st_msgs: number | null; bull_pct: number | null; titles: string[]; spike: boolean
}

function zBadge(z: number | null) {
  if (z == null) return <span className="text-xs text-slate-600">基线积累中</span>
  const cls = z >= 2 ? 'bg-red-900/60 text-red-300 font-semibold'
    : z >= 1 ? 'bg-amber-900/50 text-amber-300'
    : 'bg-slate-700/60 text-slate-400'
  return <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${cls}`}>{z >= 0 ? '+' : ''}{z.toFixed(1)}σ</span>
}

function SocialBuzzSection() {
  const [collecting, setCollecting] = useState(false)
  const [buzzErr, setBuzzErr] = useState<string | null>(null)
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<BuzzData>({
    queryKey: ['social-buzz'],
    queryFn: () => getSocialBuzz(false),
    staleTime: 10 * 60_000,
    retry: false,
  })

  async function collect() {
    setCollecting(true); setBuzzErr(null)
    try {
      const fresh = await getSocialBuzz(true)
      qc.setQueryData(['social-buzz'], fresh)
    } catch (error: unknown) {
      setBuzzErr(requestError(error, '采集失败（Reddit/StockTwits 需代理可达）'))
    } finally {
      setCollecting(false)
    }
  }

  const rows: BuzzRow[] = data?.rows ?? []
  const shown = rows.filter(r => (r.mentions ?? 0) > 0 || (r.st_msgs ?? 0) > 0 || r.spike).slice(0, 30)

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-medium text-slate-200">💬 社区热度 <span className="text-slate-500 font-normal">
          Reddit/StockTwits · 异动 = 相对自身 7 日基线</span></span>
        {data?.as_of && <span className="text-xs text-slate-500">采集于 {String(data.as_of).slice(0, 16)}</span>}
        {data != null && data.baseline_days < 7 && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300/90">
            基线积累中：{data.baseline_days}/7 天，z-score 满一周后才可靠
          </span>
        )}
        <button onClick={collect} disabled={collecting}
          className="ml-auto px-3 py-1 text-xs rounded bg-slate-700 text-slate-300 hover:bg-slate-600 disabled:opacity-50 transition-colors">
          {collecting ? '采集中…（约 1 分钟）' : '⟳ 立即采集'}
        </button>
      </div>

      {buzzErr && <div className="text-sm text-red-300 bg-red-900/20 border border-red-800/50 rounded px-3 py-2">{buzzErr}</div>}

      {!!(data?.alerts?.length) && (
        <div className="text-sm text-red-300 bg-red-900/15 border border-red-800/50 rounded-lg px-3 py-2">
          ⚠ 持仓票热度异动：{(data.alerts as string[]).map(s => (
            <SymbolLink key={s} symbol={s} className="font-mono font-semibold text-red-200 mx-1" />
          ))}——去看看社区在传什么
        </div>
      )}

      {isLoading ? (
        <div className="text-sm text-slate-500 bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-6 text-center animate-pulse">
          加载中…（首次使用会现场采集一轮，约 1 分钟）
        </div>
      ) : shown.length === 0 ? (
        <div className="text-sm text-slate-500 bg-slate-800/40 border border-slate-700 rounded-lg px-4 py-6 text-center">
          暂无数据。点「⟳ 立即采集」抓一轮，或在任务调度开启 social_buzz（每日 4 次自动采集）。
        </div>
      ) : (
        <div className="overflow-x-auto bg-slate-800/40 border border-slate-700 rounded-lg">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 border-b border-slate-700">
                <th className="text-left px-3 py-2 font-medium">代码</th>
                <th className="text-right px-2 py-2 font-medium" title="相对自身 7 日均值的标准分；≥2σ 视为异动">异动 z</th>
                <th className="text-right px-2 py-2 font-medium" title="ApeWisdom 统计的 Reddit 全站 24h 提及数">24h 提及</th>
                <th className="text-right px-2 py-2 font-medium">7 日均</th>
                <th className="text-right px-2 py-2 font-medium" title="Reddit 全站热度排名">排名</th>
                <th className="text-right px-2 py-2 font-medium" title="StockTwits 近 24h 消息数">ST 消息</th>
                <th className="text-left px-2 py-2 font-medium" title="StockTwits 带标签帖子的看多占比（样本≥5 才显示）">多空</th>
                <th className="text-left px-3 py-2 font-medium">热帖样本（Reddit）</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(r => (
                <tr key={r.symbol} className={`border-b border-slate-700/40 ${r.spike ? 'bg-red-900/10' : ''}`}>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    <SymbolLink symbol={r.symbol} className="font-mono font-semibold text-white" />
                    <span className={`ml-1.5 text-[10px] px-1 rounded ${r.tag === '持仓' ? 'bg-blue-900/60 text-blue-300' : 'bg-slate-700/70 text-slate-500'}`}>{r.tag}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right">{zBadge(r.z)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-300">{r.mentions ?? '—'}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-500">{r.avg7 ?? '—'}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-500">{r.rank ? `#${r.rank}` : '—'}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-400">{r.st_msgs ?? '—'}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {r.bull_pct != null ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="w-14 h-1.5 rounded bg-red-900/60 overflow-hidden inline-block">
                          <span className="h-full bg-emerald-500 block" style={{ width: `${r.bull_pct * 100}%` }} />
                        </span>
                        <span className={`text-xs font-mono ${r.bull_pct >= 0.5 ? 'text-emerald-400' : 'text-red-400'}`}>{Math.round(r.bull_pct * 100)}%多</span>
                      </span>
                    ) : <span className="text-xs text-slate-600">—</span>}
                  </td>
                  <td className="px-3 py-1.5 text-xs text-slate-400 max-w-[360px]">
                    {r.titles.length > 0
                      ? <span className="line-clamp-1" title={r.titles.join('\n')}>{r.titles[0]}</span>
                      : <span className="text-slate-600">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function IntelCenter() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'events' | 'social'>('events')
  const [typeFilter, setTypeFilter] = useState<string>('全部')
  const [range, setRange] = useState<typeof RANGE_OPTS[number]['key']>('all')
  const [err, setErr] = useState<string | null>(null)

  const { data } = useQuery<IntelData>({
    queryKey: ['intel-events'],
    queryFn: getIntelEvents,
    refetchInterval: (q) => (q.state.data?.running ? 5_000 : false),  // 生成中每 5s 轮询
  })

  const running: boolean = !!data?.running
  const events = useMemo<IntelEvent[]>(() => data?.events ?? [], [data?.events])
  const types = useMemo(() => ['全部', ...Array.from(new Set(events.map(e => e.type)))], [events])
  const maxDays = RANGE_OPTS.find(r => r.key === range)!.maxDays
  const shown = events
    .filter(e => typeFilter === '全部' || e.type === typeFilter)
    .filter(e => maxDays === Infinity || daysAgo(e.date) <= maxDays)
  const holdEvents = shown.filter(e => e.scope !== '候选池')
  const poolEvents = shown.filter(e => e.scope === '候选池')

  async function refresh() {
    setErr(null)
    try {
      await refreshIntelEvents()
      qc.invalidateQueries({ queryKey: ['intel-events'] })
    } catch (error: unknown) {
      setErr(requestError(error, '刷新失败'))
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">情报中心 🛰</h1>
          {tab === 'events' && (
            <p className="text-sm text-slate-400 mt-1">
              对持仓票的新闻/SEC 公告做聚簇去重 + 方向研判，只留「跟你的钱有关」的事件。
              {data?.generated_at_cn && <span className="ml-2 text-slate-500">最近生成：{data.generated_at_cn}（北京）· {data.model}</span>}
            </p>
          )}
          {tab === 'social' && (
            <p className="text-sm text-slate-400 mt-1">
              Reddit/StockTwits 上 AI 池 + 持仓的提及热度与多空情绪，核心信号是相对自身 7 日基线的异动。
            </p>
          )}
        </div>
        {tab === 'events' && (
          <button onClick={refresh} disabled={running}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white text-sm font-medium transition-colors disabled:opacity-40">
            {running ? '生成中…（约 1-3 分钟）' : '🔄 刷新情报'}
          </button>
        )}
      </div>

      {/* Tab 导航 */}
      <div className="flex gap-0 border-b border-slate-700">
        {([
          { key: 'events', label: '持仓事件雷达' },
          { key: 'social', label: '社区热度' },
        ] as const).map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab === t.key
                ? 'border-blue-500 text-white font-medium'
                : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'social' ? (
        <>
          <SocialBuzzSection />
          <div className="text-sm text-slate-400 space-y-1">
            <div>· <span className="text-slate-300">社区热度</span>为纯观察层（不参与交易信号）：提及数来自 ApeWisdom（Reddit 全站聚合），情绪来自 StockTwits 用户自标多空；核心信号是 <span className="text-slate-300">z-score 异动</span>（相对自身 7 日基线），绝对热度高（如 NVDA 常年榜一）不代表信号。</div>
            <div>· 散户情绪在顶部往往最欢腾，热度飙升既可能是机会也可能是出货，需结合叙事判断；基线需积累约一周采样（调度任务 social_buzz 每日 4 次，默认关闭需手动开启）。</div>
            <div>· 热帖标题列需在 .env 配置 REDDIT_CLIENT_ID/SECRET（reddit.com/prefs/apps 免费建 script 应用）；未配置只缺标题，提及数不受影响。</div>
          </div>
        </>
      ) : (
      <>
      {err && <div className="text-sm text-red-300 bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">{err}</div>}
      {data?.error && !running && (
        <div className="text-sm text-amber-300 bg-amber-900/15 border border-amber-800/50 rounded-lg px-3 py-2">上次刷新失败：{data.error}</div>
      )}

      {/* 覆盖持仓 */}
      {!!data?.holdings?.length && (
        <div className="flex items-center gap-2 flex-wrap text-sm text-slate-400">
          覆盖持仓：
          {data.holdings.map((s: string) => (
            <SymbolLink key={s} symbol={s} className="px-2 py-0.5 rounded bg-slate-700/60 text-slate-200 font-mono text-xs hover:bg-slate-600 cursor-pointer" />
          ))}
        </div>
      )}

      {/* 过滤：时间段 + 类型 */}
      {events.length > 0 && (
        <div className="flex gap-1.5 flex-wrap items-center">
          {RANGE_OPTS.map(r => (
            <button key={r.key} onClick={() => setRange(r.key)}
              className={`px-3 py-1 text-sm rounded transition-colors ${range === r.key
                ? 'bg-blue-600 text-white font-medium'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              {r.label}
            </button>
          ))}
          <span className="w-px h-5 bg-slate-600 mx-1.5" />
          {types.map(t => (
            <button key={t} onClick={() => setTypeFilter(t)}
              className={`px-3 py-1 text-sm rounded transition-colors ${typeFilter === t
                ? 'bg-blue-600 text-white font-medium'
                : 'bg-slate-700 text-slate-300 hover:bg-slate-600'}`}>
              {t}
            </button>
          ))}
        </div>
      )}

      {/* 事件卡：持仓段 + 候选池段 */}
      {shown.length > 0 ? (
        <div className="space-y-5">
          {holdEvents.length > 0 && (
            <div className="space-y-2.5">
              <div className="text-sm font-medium text-slate-200">📌 持仓事件 <span className="text-slate-500 font-normal">({holdEvents.length})</span></div>
              {holdEvents.map((e, i) => <EventCard key={i} e={e} />)}
            </div>
          )}
          {poolEvents.length > 0 && (
            <div className="space-y-2.5">
              <div className="text-sm font-medium text-slate-200">🎯 候选池强事件 <span className="text-slate-500 font-normal">({poolEvents.length} · AI 股票池非持仓，只留重大事件)</span></div>
              {poolEvents.map((e, i) => <EventCard key={i} e={e} />)}
            </div>
          )}
        </div>
      ) : (
        <div className="text-center text-slate-500 text-sm py-16 bg-slate-800/40 border border-slate-700 rounded-xl">
          {running ? '正在拉取新闻并做事件提取，完成后自动显示…'
            : events.length > 0 ? '当前时间段/类型下没有事件，试试放宽过滤。'
            : '还没有事件卡。点右上「🔄 刷新情报」生成（首次约 1-3 分钟）。'}
        </div>
      )}

      {/* 说明 */}
      <div className="text-sm text-slate-400 space-y-1">
        <div>· 覆盖范围 = <span className="text-slate-300">实盘诊断录入的持仓 ∪ IB 模拟持仓</span>（Gateway 在线时自动合并）+ <span className="text-slate-300">AI 股票池候选圈层</span>（约 95 只，仅轻量标题、只留强事件）；杠杆/篮子 ETF 自动按底层标的检索（MUU→MU、RAM→MU/SNDK/WDC）；现金等价 ETF 与期权腿不纳入。</div>
        <div>· 原始新闻来自 yfinance + SEC EDGAR（免费，2 小时缓存）；提取分析走 Claude 订阅 CLI 引擎，一次刷新 = 一次调用，无 API 现金成本。</div>
        <div>· <span className="text-slate-300">产业链传导</span>（如海力士涨价 → 利好 MU）由模型显式标注；解读仅供参考，重大决定请核对原文链接。</div>
      </div>
      </>
      )}
    </div>
  )
}
