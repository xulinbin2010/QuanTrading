/**
 * 待确认出场面板（半自动止损）。
 * auto_trader 触发 -15% 硬止损 / EMA21 两日破位后不再直接卖出，改挂待确认记录；
 * 本面板展示触发原因 + Claude 出场情报（个股新闻/龙头动向/系统性恐慌判断），
 * 人工点「确认卖出」（盘中 MKT/DAY，盘外 LMT/OPG）或「保留持仓」（当日不再提醒）。
 * 灾难硬止损（默认 -25%）不经过本面板，自动卖出兜底。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getExits, decideExit, refreshExitIntel, getStockNews, type PendingExit } from '../api/client'
import SymbolLink from './SymbolLink'

const RULE_LABEL: Record<string, string> = {
  hard_stop: '硬止损',
  ema_exit: 'EMA21两日破位',
  disaster: '灾难止损',
}
const STATUS_LABEL: Record<string, string> = {
  sold: '已确认卖出', kept: '已保留', expired: '已自动作废', auto_sold: '灾难止损自动卖出',
}

function fmtRet(v?: number) {
  return typeof v === 'number' ? `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%` : '—'
}
function fmtTs(s?: string | null) {
  return s ? String(s).slice(0, 16).replace('T', ' ') : '—'
}
function parseIntel(row: PendingExit): { text: string; as_of?: string } | null {
  if (!row.intel_json) return null
  try { return JSON.parse(row.intel_json) } catch { return null }
}

function ExitCard({ row }: { row: PendingExit }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState<'sell' | 'keep' | 'intel' | null>(null)
  const [msg, setMsg] = useState('')
  const intel = parseIntel(row)

  // 零成本兜底：yfinance/SEC 新闻标题（不经 LLM），Claude 情报没生成时也有信息可看
  const { data: newsData } = useQuery({
    queryKey: ['stock-news', row.symbol],
    queryFn: () => getStockNews(row.symbol),
    staleTime: 2 * 60 * 60_000,
    retry: false,
  })
  const news: any[] = (newsData?.news ?? []).slice(0, 4)

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['exits'] })
    queryClient.invalidateQueries({ queryKey: ['exits-badge'] })
    queryClient.invalidateQueries({ queryKey: ['orders'] })
    queryClient.invalidateQueries({ queryKey: ['positions'] })
  }

  const doDecide = async (action: 'sell' | 'keep') => {
    if (action === 'sell' &&
        !confirm(`确认卖出 ${row.symbol} × ${row.qty} 股？\n（盘中市价单 / 盘外 OPG 限价单）`)) return
    setBusy(action)
    setMsg('')
    try {
      await decideExit(row.id, action)
      refresh()
    } catch (e: any) {
      setMsg(e.response?.data?.detail || `${action === 'sell' ? '下单' : '操作'}失败`)
    } finally {
      setBusy(null)
    }
  }

  const doIntel = async () => {
    setBusy('intel')
    setMsg('')
    try {
      await refreshExitIntel(row.id)
      refresh()
    } catch (e: any) {
      setMsg(e.response?.data?.detail || '情报生成失败')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="border border-red-500/40 bg-slate-800 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-3">
        <SymbolLink symbol={row.symbol} className="text-base font-bold text-white hover:text-blue-400 cursor-pointer" />
        <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/40">
          {RULE_LABEL[row.rule ?? ''] ?? row.rule}
        </span>
        <span className="text-sm font-mono text-slate-300">
          均价 ${row.avg_cost?.toFixed(2) ?? '—'} → 触发价 ${row.trigger_price?.toFixed(2) ?? '—'}
        </span>
        <span className={`text-sm font-mono font-semibold ${(row.ret ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
          {fmtRet(row.ret)}
        </span>
        <span className="text-xs text-slate-500">触发 {fmtTs(row.updated_at)}</span>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => doDecide('sell')} disabled={busy !== null}
            className="px-3 py-1 rounded text-sm bg-red-600 text-white hover:bg-red-500 disabled:opacity-50">
            {busy === 'sell' ? '下单中…' : '确认卖出'}
          </button>
          <button onClick={() => doDecide('keep')} disabled={busy !== null}
            className="px-3 py-1 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 disabled:opacity-50">
            {busy === 'keep' ? '…' : '保留持仓'}
          </button>
        </div>
      </div>
      <div className="mt-1 text-sm text-slate-400">{row.reason}</div>

      {/* Claude 出场情报 */}
      <div className="mt-3 rounded bg-slate-900/60 border border-slate-700 p-3">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold text-blue-300">Claude 出场情报</span>
          {intel?.as_of && <span className="text-xs text-slate-500">as-of {intel.as_of}</span>}
          <button onClick={doIntel} disabled={busy !== null}
            className="ml-auto px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300 hover:bg-slate-600 disabled:opacity-50">
            {busy === 'intel' ? '检索中（约1-3分钟）…' : intel ? '重新拉取' : '拉取情报'}
          </button>
        </div>
        {intel ? (
          <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">{intel.text}</div>
        ) : (
          <div className="text-sm text-slate-500">
            暂无情报。点「拉取情报」用 Claude 联网检索该股新闻、板块龙头动向，辅助判断是个股利空还是被外围带崩
            （走本机 Claude 订阅，无 API 费用）。
          </div>
        )}
      </div>

      {/* 近期新闻标题（yfinance/SEC，零成本，与 Claude 情报互为补充） */}
      {news.length > 0 && (
        <div className="mt-2 space-y-0.5">
          {news.map((n, i) => (
            <a key={i} href={n.url} target="_blank" rel="noopener noreferrer"
               className="flex items-start gap-2 text-sm text-slate-400 hover:text-slate-200">
              <span className="text-xs text-slate-500 shrink-0 w-20 pt-0.5">{n.date}</span>
              <span className="leading-snug">{n.title}</span>
            </a>
          ))}
        </div>
      )}
      {msg && <div className="mt-2 text-sm text-red-400">{msg}</div>}
    </div>
  )
}

export default function PendingExitsPanel() {
  const { data } = useQuery({
    queryKey: ['exits'],
    queryFn: getExits,
    refetchInterval: 60_000,
    retry: false,
  })
  const [showRecent, setShowRecent] = useState(false)
  const pending = data?.pending ?? []
  const recent = data?.recent ?? []
  if (!pending.length && !recent.length) return null

  return (
    <div className="space-y-3">
      {pending.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-red-300">
            ⚠ 待确认出场（{pending.length}）— 止损触发，等你决定卖出或保留
          </span>
          <span className="text-xs text-slate-500">未确认默认保留；灾难线（约-25%）会自动卖出兜底</span>
        </div>
      )}
      {pending.map(row => <ExitCard key={row.id} row={row} />)}

      {recent.length > 0 && (
        <div>
          <button onClick={() => setShowRecent(s => !s)}
            className="text-xs text-slate-500 hover:text-slate-300">
            {showRecent ? '▾' : '▸'} 最近出场决策记录（{recent.length}）
          </button>
          {showRecent && (
            <div className="mt-1 space-y-0.5">
              {recent.map(r => (
                <div key={r.id} className="text-xs text-slate-500 font-mono">
                  {fmtTs(r.decided_at || r.updated_at)}  {r.symbol}  {RULE_LABEL[r.rule ?? ''] ?? r.rule}  {fmtRet(r.ret)}
                  {'  → '}
                  <span className={r.status === 'sold' || r.status === 'auto_sold' ? 'text-red-400' : 'text-slate-400'}>
                    {STATUS_LABEL[r.status] ?? r.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
