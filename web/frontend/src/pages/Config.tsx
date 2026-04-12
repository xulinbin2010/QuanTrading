import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getConfig, updateConfig, reloadConfig, updateIBConnection } from '../api/client'

// 分组顺序
const CATEGORY_ORDER = ['风控', '止损', '熔断', '策略', '过滤', '内幕']

const CATEGORY_LABELS: Record<string, { label: string; desc: string; color: string }> = {
  风控: { label: '风控参数', desc: '仓位管理与止损规则', color: 'text-blue-400 border-blue-800' },
  止损: { label: '移动止损', desc: '追踪止盈止损触发条件', color: 'text-purple-400 border-purple-800' },
  熔断: { label: 'SPY 熔断', desc: '大盘急跌时暂停买入', color: 'text-orange-400 border-orange-800' },
  策略: { label: '策略参数', desc: 'RS 动量策略信号参数', color: 'text-cyan-400 border-cyan-800' },
  过滤: { label: '股票过滤', desc: '市值范围与行业黑名单', color: 'text-green-400 border-green-800' },
  内幕: { label: '内幕数据', desc: '内幕买入因子观察参数', color: 'text-yellow-400 border-yellow-800' },
}

type Param = {
  key: string
  value: string
  default: number | string
  type: string
  category: string
  description: string
}

type ConnParam = {
  group: string
  key: string
  value: string
  readonly: boolean
}

function MySQLCard({ params }: { params: ConnParam[] }) {
  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
      <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">🗄 MySQL 数据库</div>
      <div className="grid grid-cols-2 gap-2">
        {params.map(p => (
          <div key={p.key} className="flex items-center justify-between text-sm py-1">
            <span className="text-slate-400 font-mono text-xs">{p.key}</span>
            <span className="text-slate-300 font-mono text-xs bg-slate-700/50 px-2 py-0.5 rounded">{p.value}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 text-xs text-slate-500">修改请编辑项目根目录的 <code className="bg-slate-700 px-1 rounded">.env</code> 文件</div>
    </div>
  )
}

function IBGatewayCard({ params }: { params: ConnParam[] }) {
  const queryClient = useQueryClient()

  const init = () => ({
    IB_HOST:      params.find(p => p.key === 'IB_HOST')?.value      ?? '127.0.0.1',
    IB_PORT:      params.find(p => p.key === 'IB_PORT')?.value      ?? '4002',
    IB_CLIENT_ID: params.find(p => p.key === 'IB_CLIENT_ID')?.value ?? '1',
    IB_TIMEOUT:   params.find(p => p.key === 'IB_TIMEOUT')?.value   ?? '60',
  })

  const [fields, setFields] = useState(init)
  const [saved, setSaved] = useState<'ok' | 'err' | null>(null)

  // 远端数据刷新后同步本地
  useEffect(() => { setFields(init()) }, [params.map(p => p.value).join()])

  const isDirty = JSON.stringify(fields) !== JSON.stringify(init())

  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: () => updateIBConnection({
      IB_HOST:      fields.IB_HOST,
      IB_PORT:      parseInt(fields.IB_PORT),
      IB_CLIENT_ID: parseInt(fields.IB_CLIENT_ID),
      IB_TIMEOUT:   parseInt(fields.IB_TIMEOUT),
    }),
    onSuccess: () => {
      setSaved('ok')
      queryClient.invalidateQueries({ queryKey: ['config'] })
      setTimeout(() => setSaved(null), 2500)
    },
    onError: () => {
      setSaved('err')
      setTimeout(() => setSaved(null), 3000)
    },
  })

  const set = (k: string, v: string) => setFields(f => ({ ...f, [k]: v }))

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-600 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-semibold text-slate-300 uppercase tracking-wider">📡 IB Gateway</div>
        {saved === 'ok' && <span className="text-xs text-green-400">已保存，连接已重置</span>}
        {saved === 'err' && <span className="text-xs text-red-400">保存失败</span>}
      </div>

      <div className="space-y-2.5">
        {/* HOST */}
        <div className="flex items-center justify-between gap-3">
          <span className="text-slate-400 font-mono text-xs w-28 flex-shrink-0">IB_HOST</span>
          <input
            className="flex-1 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-blue-500"
            value={fields.IB_HOST}
            onChange={e => set('IB_HOST', e.target.value)}
          />
        </div>

        {/* PORT — 带 tips */}
        <div className="flex items-center justify-between gap-3">
          <span className="text-slate-400 font-mono text-xs w-28 flex-shrink-0">IB_PORT</span>
          <div className="flex items-center gap-2 flex-1">
            <input
              className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-blue-500"
              value={fields.IB_PORT}
              onChange={e => set('IB_PORT', e.target.value)}
            />
            <div className="flex gap-1.5 text-xs">
              <button
                onClick={() => set('IB_PORT', '4002')}
                className={`px-2 py-0.5 rounded border transition-colors ${
                  fields.IB_PORT === '4002'
                    ? 'bg-yellow-600/30 border-yellow-600 text-yellow-300'
                    : 'border-slate-600 text-slate-500 hover:text-slate-300'
                }`}
              >
                4002 模拟
              </button>
              <button
                onClick={() => set('IB_PORT', '4001')}
                className={`px-2 py-0.5 rounded border transition-colors ${
                  fields.IB_PORT === '4001'
                    ? 'bg-green-600/30 border-green-600 text-green-300'
                    : 'border-slate-600 text-slate-500 hover:text-slate-300'
                }`}
              >
                4001 实盘
              </button>
            </div>
          </div>
        </div>

        {/* CLIENT_ID */}
        <div className="flex items-center justify-between gap-3">
          <span className="text-slate-400 font-mono text-xs w-28 flex-shrink-0">IB_CLIENT_ID</span>
          <input
            className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-blue-500"
            value={fields.IB_CLIENT_ID}
            onChange={e => set('IB_CLIENT_ID', e.target.value)}
          />
        </div>

        {/* TIMEOUT */}
        <div className="flex items-center justify-between gap-3">
          <span className="text-slate-400 font-mono text-xs w-28 flex-shrink-0">IB_TIMEOUT</span>
          <div className="flex items-center gap-2">
            <input
              className="w-20 bg-slate-700 border border-slate-600 rounded px-2 py-1 text-xs font-mono text-white focus:outline-none focus:border-blue-500"
              value={fields.IB_TIMEOUT}
              onChange={e => set('IB_TIMEOUT', e.target.value)}
            />
            <span className="text-xs text-slate-500">秒</span>
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className="text-xs text-slate-500">保存后自动写入 <code className="bg-slate-700 px-1 rounded">.env</code> 并重置连接</div>
        <button
          onClick={() => save()}
          disabled={saving || !isDirty}
          className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded font-medium transition-colors"
        >
          {saving ? '保存中...' : '保存并重连'}
        </button>
      </div>
    </div>
  )
}

export default function Config() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['config'],
    queryFn: getConfig,
  })

  // 本地编辑状态
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)

  // 当远端数据到来时，初始化编辑状态
  useEffect(() => {
    if (data?.strategy) {
      const init: Record<string, string> = {}
      data.strategy.forEach((p: Param) => { init[p.key] = String(p.value) })
      setEdits(init)
      setDirty(false)
    }
  }, [data])

  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: () => {
      const params = Object.entries(edits).map(([key, value]) => ({ key, value }))
      return updateConfig(params)
    },
    onSuccess: () => {
      setDirty(false)
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
  })

  const { mutate: doReload, isPending: reloading } = useMutation({
    mutationFn: reloadConfig,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['config'] }),
  })

  const handleChange = (key: string, value: string) => {
    setEdits(e => ({ ...e, [key]: value }))
    setDirty(true)
  }

  if (isLoading) {
    return <div className="text-slate-400 text-sm py-12 text-center">加载配置中...</div>
  }

  // 分组策略参数
  const grouped: Record<string, Param[]> = {}
  ;(data?.strategy ?? []).forEach((p: Param) => {
    if (!grouped[p.category]) grouped[p.category] = []
    grouped[p.category].push(p)
  })

  // 连接参数按 group 分
  const connByGroup: Record<string, ConnParam[]> = {}
  ;(data?.connection ?? []).forEach((p: ConnParam) => {
    if (!connByGroup[p.group]) connByGroup[p.group] = []
    connByGroup[p.group].push(p)
  })

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-white">系统配置</h1>
          <p className="text-xs text-slate-400 mt-0.5">策略参数存储于数据库，实时生效；连接参数从 .env 文件读取</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => doReload()}
            disabled={reloading}
            className="px-3 py-1.5 text-xs border border-slate-600 text-slate-400 hover:text-white rounded transition-colors disabled:opacity-50"
          >
            {reloading ? '重载中...' : '↺ 从 DB 重载'}
          </button>
          {dirty && (
            <button
              onClick={() => save()}
              disabled={saving}
              className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded font-medium transition-colors disabled:opacity-50"
            >
              {saving ? '保存中...' : '保存修改'}
            </button>
          )}
        </div>
      </div>

      {dirty && (
        <div className="bg-blue-900/30 border border-blue-800 rounded-lg px-4 py-2 text-sm text-blue-300">
          有未保存的修改，点击「保存修改」后立即生效（无需重启）。
        </div>
      )}

      {/* 策略参数（分组） */}
      <div className="max-w-2xl">
        <div className="text-sm font-medium text-slate-300 mb-3">策略参数（可编辑）</div>
        <div className="space-y-4">
          {CATEGORY_ORDER.filter(cat => grouped[cat]).map(cat => {
            const meta = CATEGORY_LABELS[cat] ?? { label: cat, desc: '', color: 'text-slate-400 border-slate-700' }
            return (
              <div key={cat} className={`bg-slate-800 rounded-lg border ${meta.color.split(' ')[1]} p-4`}>
                <div className={`text-xs font-semibold uppercase tracking-wider mb-1 ${meta.color.split(' ')[0]}`}>
                  {meta.label}
                </div>
                <div className="text-xs text-slate-500 mb-3">{meta.desc}</div>
                <div className="space-y-2">
                  {grouped[cat].map((p: Param) => (
                    <div key={p.key} className="grid grid-cols-[1fr_auto] items-center gap-3">
                      {/* 参数名 + 描述（含默认值） */}
                      <div className="min-w-0">
                        <span className="text-xs font-mono text-slate-300">{p.key}</span>
                        <span className="text-xs text-slate-500 ml-2">
                          {p.description}（默认 {String(p.default)}）
                        </span>
                      </div>
                      {/* 输入框 */}
                      <input
                        className={`w-24 bg-slate-700 border rounded px-2 py-1 text-sm font-mono text-white text-right focus:outline-none focus:border-blue-500 transition-colors ${
                          edits[p.key] !== String(p.value) ? 'border-blue-500/70' : 'border-slate-600'
                        }`}
                        value={edits[p.key] ?? String(p.value)}
                        onChange={e => handleChange(p.key, e.target.value)}
                        title={`默认值: ${p.default}`}
                      />
                    </div>
                  ))}
                  {/* 风控组：显示派生的保留现金比例 */}
                  {cat === '风控' && (() => {
                    const n = parseFloat(edits['MAX_POSITIONS'] ?? '6')
                    const p = parseFloat(edits['POSITION_PCT'] ?? '0.15')
                    const reserve = Math.max(0, 1 - n * p)
                    const warn = reserve < 0
                    return (
                      <div className="grid grid-cols-[1fr_auto] items-center gap-3 pt-1 border-t border-slate-700/60">
                        <div className="min-w-0">
                          <span className="text-xs font-mono text-slate-400">CASH_RESERVE_PCT</span>
                          <span className="text-xs text-slate-500 ml-2">= 1 − MAX_POSITIONS × POSITION_PCT（自动派生）</span>
                        </div>
                        <span className={`w-24 text-right text-sm font-mono ${warn ? 'text-red-400' : 'text-slate-400'}`}>
                          {warn ? '超出100%!' : (reserve * 100).toFixed(1) + '%'}
                        </span>
                      </div>
                    )
                  })()}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* 连接参数 */}
      <div className="max-w-2xl">
        <div className="text-sm font-medium text-slate-300 mb-3">连接参数</div>
        <div className="space-y-4">
          {connByGroup['MySQL'] && <MySQLCard params={connByGroup['MySQL']} />}
          {connByGroup['IB']    && <IBGatewayCard params={connByGroup['IB']} />}
        </div>
      </div>
    </div>
  )
}
