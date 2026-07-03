/**
 * 盘前简报全局 Provider：右下角浮动按钮，点一下弹出/隐藏模态框（可切换显示隐藏）。
 * App.tsx 最外层包一层即可，无需在页面里放入口；ESC 关闭。
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import PremarketBriefingModal from './PremarketBriefingModal'

type Ctx = { open: () => void; close: () => void; toggle: () => void }
const PremarketContext = createContext<Ctx>({ open: () => {}, close: () => {}, toggle: () => {} })
export const usePremarketBriefing = () => useContext(PremarketContext)

export function PremarketBriefingProvider({ children }: { children: ReactNode }) {
  const [show, setShow] = useState(false)
  const open = useCallback(() => setShow(true), [])
  const close = useCallback(() => setShow(false), [])
  const toggle = useCallback(() => setShow(s => !s), [])

  useEffect(() => {
    if (!show) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShow(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [show])

  return (
    <PremarketContext.Provider value={{ open, close, toggle }}>
      {children}
      {/* 浮动开关：始终可见，切换显隐 */}
      <button
        onClick={toggle}
        title="盘前扫描（实时宏观快照 + 个股盘前报价）"
        className="fixed bottom-5 right-5 z-[55] flex items-center gap-1.5 px-3.5 py-2 rounded-full shadow-lg
                   bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors">
        <span>📋</span><span className="hidden sm:inline">盘前扫描</span>
      </button>
      {show && <PremarketBriefingModal onClose={close} />}
    </PremarketContext.Provider>
  )
}
