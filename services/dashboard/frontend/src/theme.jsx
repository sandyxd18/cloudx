/**
 * theme.jsx — Cloudscape visual mode (dark/light) provider.
 *
 * Applies Cloudscape's global `applyMode` and persists the choice in
 * localStorage so the dashboard remembers the operator's preference.
 * Defaults to dark mode (matching the AWS Console dark theme).
 */
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { applyMode, Mode } from '@cloudscape-design/global-styles'

const STORAGE_KEY = 'cloudx:visual-mode'
const ThemeCtx = createContext(null)

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored === 'light' ? Mode.Light : Mode.Dark
  })

  useEffect(() => {
    applyMode(mode)
    localStorage.setItem(STORAGE_KEY, mode === Mode.Light ? 'light' : 'dark')
  }, [mode])

  const value = useMemo(() => ({
    mode,
    isDark: mode === Mode.Dark,
    toggleMode: () => setMode(m => (m === Mode.Dark ? Mode.Light : Mode.Dark)),
  }), [mode])

  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>
}

export const useTheme = () => useContext(ThemeCtx)
