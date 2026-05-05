import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getSchedulerTasks, getTaskRuns, runTaskNow,
  upsertSchedulerTask, getRunLog, getCronPreview, deleteTaskRun,
} from '../api/client'

function cronToHuman(expr: string): string {
  try {
    const [mn, hr, , , dw] = expr.split(' ')
    const dayMap: Record<string, string> = { '1-5': '周一至五', '2-6': '周二至六', '*': '每天' }
    const days = dayMap[dw] ?? `周${dw}`
    return `${days} ${hr.padStart(2, '0')}:${mn.padStart(2, '0')} 北京`
  } catch {
    return expr
  }
}

function StatusDot({ status }: { status?: string }) {
  if (status === 'success') return <span className="text-green-400">●</span>
  if (status === 'failed') return <span className="text-red-400">●</span>
  if (status === 'running') return <span className="text-yellow-400 animate-pulse">●</span>
  return <span className="text-slate-600">●</span>
}

function LogModal({ runId, isRunning, onClose }: { runId: number; isRunning?: boolean; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['run-log', runId],
    queryFn: () => getRunLog(runId),
    refetchInterval: isRunning ? 3000 : false,   // 运行中每 3 秒自动刷新
  })

  // 日志更新时自动滚到底部
  const logRef = useRef<HTMLPreElement>(null)
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [data?.log])

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-[720px] max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700">
          <div className="flex items-center gap-2">
            <span className="text-white font-medium text-sm">执行日志 #{runId}</span>
            {isRunning && (
              <span className="flex items-center gap-1 text-xs text-yellow-400">
                <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
                运行中
              </span>
            )}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {isLoading ? (
            <div className="text-slate-400 text-sm">加载中...</div>
          ) : (
            <pre ref={logRef} className="text-xs text-slate-300 font-mono whitespace-pre-wrap leading-5 max-h-full overflow-auto">
              {data?.log || (isRunning ? '任务启动中，等待输出...' : '（无日志）')}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Scheduler() {
  const [selectedLog, setSelectedLog] = useState<{ id: number; running: boolean } | null>(null)
  const [editTask, setEditTask] = useState<any | null>(null)
  const [cronTimes, setCronTimes] = useState<string[]>([])
  const [cronError, setCronError] = useState<string>('')
  // 记录每个 task_id 是否刚被手动触发（乐观显示 running 状态）
  const [localRunning, setLocalRunning] = useState<Set<string>>(new Set())
  const queryClient = useQueryClient()

  // cron 预览：输入后 600ms debounce 调用后端
  useEffect(() => {
    const expr = editTask?.cron_expr?.trim()
    if (!expr) { setCronTimes([]); setCronError(''); return }
    const timer = setTimeout(async () => {
      try {
        const res = await getCronPreview(expr)
        setCronTimes(res.times ?? [])
        setCronError(res.error ?? '')
      } catch {
        setCronTimes([])
        setCronError('解析失败')
      }
    }, 600)
    return () => clearTimeout(timer)
  }, [editTask?.cron_expr])

  const { data: tasks = [], isLoading: tasksLoading, isError: tasksError } = useQuery({
    queryKey: ['scheduler-tasks'],
    queryFn: getSchedulerTasks,
    refetchInterval: (query) => {
      const data = query.state.data as any[] | undefined
      const hasRunning = data?.some(t => t.last_run?.status === 'running') || localRunning.size > 0
      return hasRunning ? 3_000 : 15_000
    },
    retry: 1,
  })

  const { data: runs = [], isLoading: runsLoading } = useQuery({
    queryKey: ['task-runs'],
    queryFn: () => getTaskRuns(undefined, 50),
    refetchInterval: (query) => {
      const data = query.state.data as any[] | undefined
      const hasRunning = data?.some(r => r.status === 'running') || localRunning.size > 0
      return hasRunning ? 3_000 : 10_000
    },
    retry: 1,
  })

  const toggleMutation = useMutation({
    mutationFn: (task: any) => upsertSchedulerTask({ ...task, enabled: !task.enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['scheduler-tasks'] }),
  })

  const runNowMutation = useMutation({
    mutationFn: (taskId: string) => {
      setLocalRunning(prev => new Set(prev).add(taskId))
      return runTaskNow(taskId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['task-runs'] })
      queryClient.invalidateQueries({ queryKey: ['scheduler-tasks'] })
      // 不在此处删除 localRunning，等 runs 数据刷回后由 useEffect 接管，避免闪回
    },
    onError: (_err, taskId) => {
      setLocalRunning(prev => { const s = new Set(prev); s.delete(taskId); return s })
    },
  })

  // 当 runs 数据中已出现该任务记录（running / 完成），解除乐观标记
  useEffect(() => {
    if (localRunning.size === 0) return
    const knownIds = new Set((runs as any[]).map((r: any) => r.task_id))
    setLocalRunning(prev => {
      const next = new Set(prev)
      prev.forEach(id => { if (knownIds.has(id)) next.delete(id) })
      return next.size === prev.size ? prev : next
    })
  }, [runs])

  const runningTaskIds = new Set([
    ...(runs as any[]).filter(r => r.status === 'running').map(r => r.task_id),
    ...Array.from(localRunning),
  ])

  const deleteRunMutation = useMutation({
    mutationFn: (runId: number) => deleteTaskRun(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['task-runs'] }),
  })

  const saveMutation = useMutation({
    mutationFn: (body: any) => upsertSchedulerTask(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scheduler-tasks'] })
      setEditTask(null)
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-white">任务调度</h1>
        <button
          onClick={() => setEditTask({ task_id: '', name: '', command: '', cron_expr: '0 14 * * 1-5', enabled: true, _new: true })}
          className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors"
        >
          + 新建任务
        </button>
      </div>

      {/* 加载 / 错误状态 */}
      {tasksError && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg px-4 py-3 text-sm text-red-300">
          调度器数据加载失败，请检查后端服务和数据库连接是否正常。
        </div>
      )}

      {/* 任务卡片网格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {tasksLoading ? (
          // 骨架屏
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-slate-800 rounded-lg border border-slate-700 p-4 animate-pulse">
              <div className="flex items-start justify-between mb-3">
                <div className="space-y-1.5">
                  <div className="h-4 w-36 bg-slate-700 rounded" />
                  <div className="h-3 w-20 bg-slate-700/60 rounded" />
                </div>
                <div className="h-5 w-9 bg-slate-700 rounded-full" />
              </div>
              <div className="space-y-2 mb-3">
                <div className="h-6 bg-slate-700/50 rounded" />
                <div className="h-3 w-48 bg-slate-700/40 rounded" />
              </div>
              <div className="flex gap-2">
                <div className="h-6 w-20 bg-slate-700 rounded" />
                <div className="h-6 w-12 bg-slate-700 rounded" />
              </div>
            </div>
          ))
        ) : tasks.length === 0 ? (
          <div className="col-span-2 text-center py-10 text-slate-500 text-sm">暂无任务，点击「+ 新建任务」添加</div>
        ) : tasks.map((task: any) => (
          <div key={task.task_id} className="bg-slate-800 rounded-lg border border-slate-700 p-4">
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="text-white font-medium text-sm">{task.name}</div>
                <div className="text-xs text-slate-400 mt-0.5 font-mono">{task.task_id}</div>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  className="sr-only peer"
                  checked={task.enabled}
                  onChange={() => toggleMutation.mutate(task)}
                />
                <div className="w-9 h-5 bg-slate-600 peer-checked:bg-blue-600 rounded-full peer transition-colors after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-4" />
              </label>
            </div>

            <div className="space-y-1.5 text-xs text-slate-400 mb-3">
              <div className="font-mono text-slate-300 bg-slate-700/50 rounded px-2 py-1 truncate">{task.command}</div>
              <div className="flex justify-between">
                <span>⏱ {cronToHuman(task.cron_expr)}</span>
                {task.next_run && <span className="text-slate-500">下次：{task.next_run}</span>}
              </div>
              {task.last_run && (
                <div className="flex items-center gap-1">
                  <StatusDot status={task.last_run.status} />
                  <span>上次：{task.last_run.started_at}</span>
                  <span className={task.last_run.status === 'success' ? 'text-green-400' : task.last_run.status === 'failed' ? 'text-red-400' : 'text-yellow-400'}>
                    ({task.last_run.status})
                  </span>
                </div>
              )}
            </div>

            <div className="flex gap-2">
              {(() => {
                const isPending = runNowMutation.isPending && runNowMutation.variables === task.task_id
                const isRunning = runningTaskIds.has(task.task_id)
                return (
                  <button
                    onClick={() => runNowMutation.mutate(task.task_id)}
                    disabled={isPending || isRunning}
                    className="px-2.5 py-1 text-xs rounded transition-all active:scale-95 disabled:cursor-not-allowed
                      bg-slate-700 hover:bg-slate-600 active:bg-slate-500 text-slate-300
                      disabled:opacity-60"
                  >
                    {isPending ? (
                      <span className="flex items-center gap-1">
                        <span className="inline-block w-2.5 h-2.5 border border-slate-400 border-t-transparent rounded-full animate-spin" />
                        触发中
                      </span>
                    ) : isRunning ? (
                      <span className="flex items-center gap-1 text-yellow-400">
                        <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
                        运行中
                      </span>
                    ) : (
                      '▶ 立即执行'
                    )}
                  </button>
                )
              })()}
              <button
                onClick={() => setEditTask({ ...task })}
                className="px-2.5 py-1 text-xs border border-slate-600 text-slate-400 hover:text-white rounded transition-colors"
              >
                编辑
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* 执行历史 */}
      <div className="bg-slate-800 rounded-lg border border-slate-700">
        <div className="px-4 py-3 border-b border-slate-700 text-sm font-medium text-slate-300">
          最近执行记录
        </div>
        {runsLoading ? (
          <div className="px-4 py-8 text-center text-slate-500 text-sm animate-pulse">加载执行记录中...</div>
        ) : runs.length === 0 ? (
          <div className="px-4 py-8 text-center text-slate-500 text-sm">暂无执行记录</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-700">
                  {['任务', '开始时间', '耗时', '状态', '操作'].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.map((r: any) => (
                  <tr key={r.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                    <td className="px-4 py-2 font-medium text-slate-300">{r.task_name}</td>
                    <td className="px-4 py-2 text-slate-400 font-mono">{r.started_at}</td>
                    <td className="px-4 py-2 text-slate-400">
                      {r.duration_s != null ? `${r.duration_s}s` : r.status === 'running' ? '运行中' : '-'}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`flex items-center gap-1 ${
                        r.status === 'success' ? 'text-green-400' :
                        r.status === 'failed' ? 'text-red-400' :
                        r.status === 'running' ? 'text-yellow-400' : 'text-slate-400'
                      }`}>
                        <StatusDot status={r.status} />
                        {r.status}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-3">
                        <button
                          onClick={() => setSelectedLog({ id: r.id, running: r.status === 'running' })}
                          className="text-blue-400 hover:text-blue-300"
                        >
                          查看日志
                        </button>
                        <button
                          onClick={() => deleteRunMutation.mutate(r.id)}
                          disabled={deleteRunMutation.isPending}
                          className="text-red-500 hover:text-red-400 disabled:opacity-40"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 日志弹窗 */}
      {selectedLog !== null && (
        <LogModal
          runId={selectedLog.id}
          isRunning={selectedLog.running}
          onClose={() => setSelectedLog(null)}
        />
      )}

      {/* 编辑任务弹窗 */}
      {editTask && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-xl border border-slate-700 w-[480px]">
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-700">
              <div className="text-white font-medium text-sm">{editTask._new ? '新建任务' : '编辑任务'}</div>
              <button onClick={() => setEditTask(null)} className="text-slate-400 hover:text-white">✕</button>
            </div>
            <div className="p-5 space-y-3">
              {[
                { label: 'Task ID（唯一标识）', key: 'task_id', disabled: !editTask._new },
                { label: '任务名称', key: 'name' },
                { label: '执行命令', key: 'command' },
              ].map(({ label, key, disabled }) => (
                <div key={key}>
                  <label className="block text-xs text-slate-400 mb-1">{label}</label>
                  <input
                    disabled={disabled}
                    className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500 disabled:opacity-50"
                    value={editTask[key] ?? ''}
                    onChange={e => setEditTask((t: any) => ({ ...t, [key]: e.target.value }))}
                  />
                </div>
              ))}
              {/* Cron 表达式（北京时间）+ 预览 */}
              <div>
                <label className="block text-xs text-slate-400 mb-1">Cron 表达式（北京时间）</label>
                <input
                  placeholder="0 22 * * 1-5"
                  className="w-full bg-slate-700 border border-slate-600 rounded px-2 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500"
                  value={editTask.cron_expr ?? ''}
                  onChange={e => setEditTask((t: any) => ({ ...t, cron_expr: e.target.value }))}
                />
                {cronError && (
                  <div className="mt-1.5 text-xs text-red-400">{cronError}</div>
                )}
                {cronTimes.length > 0 && (
                  <div className="mt-1.5 space-y-0.5">
                    <div className="text-xs text-slate-500">未来5次执行时间（北京）：</div>
                    {cronTimes.map((t, i) => (
                      <div key={i} className="text-xs font-mono text-slate-400 pl-2">
                        {i + 1}. {t}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-blue-500"
                  checked={editTask.enabled}
                  onChange={e => setEditTask((t: any) => ({ ...t, enabled: e.target.checked }))}
                />
                启用
              </label>
            </div>
            <div className="px-5 py-3 border-t border-slate-700 flex justify-end gap-2">
              <button
                onClick={() => setEditTask(null)}
                className="px-4 py-1.5 text-sm border border-slate-600 text-slate-400 hover:text-white rounded"
              >
                取消
              </button>
              <button
                onClick={() => {
                  const { _new, ...body } = editTask
                  saveMutation.mutate(body)
                }}
                disabled={saveMutation.isPending}
                className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded"
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
