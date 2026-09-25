/**
 * XShellContext.jsx — global dockable panel state for XShell (terminal) and
 * XIDE (code-server). One shared bottom panel surfaces either mode; switching
 * mode preserves the live WebSocket session so terminals don't restart.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const XShellCtx = createContext(null)

// Shared storage (survives reloads) so users don't lose the toggle state.
const STORAGE_KEY_OPEN   = 'cloudx:xshell:open'
const STORAGE_KEY_HEIGHT = 'cloudx:xshell:height'
const STORAGE_KEY_MODE   = 'cloudx:xshell:mode'   // 'xshell' | 'xide'

const DEFAULT_HEIGHT = Math.floor(window.innerHeight * 0.45)
const MIN_HEIGHT     = 120
const MAX_HEIGHT     = 0.85 // fraction of viewport height
const MIN_VISIBLE    = 350  // auto-expand to default if stored value below this

export function XShellProvider({ children }) {
  const [open, setOpenState]               = useState(() => localStorage.getItem(STORAGE_KEY_OPEN) === '1')
  const [height, setHeightState]           = useState(() => {
    const stored = Number(localStorage.getItem(STORAGE_KEY_HEIGHT))
    if (Number.isFinite(stored) && stored >= MIN_HEIGHT) {
      return stored < MIN_VISIBLE ? DEFAULT_HEIGHT : stored
    }
    return DEFAULT_HEIGHT
  })
  const [mode, setModeState]               = useState(() => localStorage.getItem(STORAGE_KEY_MODE) || 'xshell')

  const setOpen = useCallback((v) => {
    setOpenState(v)
    try { localStorage.setItem(STORAGE_KEY_OPEN, v ? '1' : '0') } catch { /* ignore */ }
  }, [])

  const setHeight = useCallback((h) => {
    const clamped = Math.max(MIN_HEIGHT, Math.min(window.innerHeight * MAX_HEIGHT, h))
    setHeightState(clamped)
    try { localStorage.setItem(STORAGE_KEY_HEIGHT, String(clamped)) } catch { /* ignore */ }
  }, [])

  const setMode = useCallback((m) => {
    setModeState(m)
    try { localStorage.setItem(STORAGE_KEY_MODE, m) } catch { /* ignore */ }
  }, [])

  // Keyboard shortcut: Ctrl+` (backtick) toggles the panel, just like AWS.
  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.key === '`' && !e.altKey && !e.shiftKey && !e.metaKey) {
        // Don't trigger when an input/textarea is focused.
        const tag = (e.target?.tagName || '').toLowerCase()
        if (tag === 'input' || tag === 'textarea' || e.target?.isContentEditable) return
        e.preventDefault()
        setOpen(!open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  const value = useMemo(() => ({
    open, setOpen,
    height, setHeight,
    mode, setMode,
    minHeight: MIN_HEIGHT,
  }), [open, height, mode, setOpen, setHeight, setMode])

  return <XShellCtx.Provider value={value}>{children}</XShellCtx.Provider>
}

export const useXShell = () => useContext(XShellCtx)
