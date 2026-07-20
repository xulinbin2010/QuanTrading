/**
 * 盘前简报全局 Provider：管理顶部入口打开的模态框；ESC 关闭。
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
      {show && <PremarketBriefingModal onClose={close} />}
    </PremarketContext.Provider>
  )
}
