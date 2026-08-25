import { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react'
import { api } from './api.js'

const ToastCtx = createContext(null)
const FlashCtx = createContext([])
const AuthCtx  = createContext(null)

// ── Notification provider ─────────────────────────────────────────────────
// Keeps the legacy `useToast()(msg, type)` call signature used across pages,
// but renders through Cloudscape's <Flashbar/> in the AppLayout shell.
// Type mapping: success → success, error → error, info → info, warning → warning.
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])

  const dismiss = useCallback((id) => {
    setItems(list => list.filter(i => i.id !== id))
  }, [])

  const addToast = useCallback((msg, type = 'info') => {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`
    const flashType = type === 'error' ? 'error'
      : type === 'success' ? 'success'
      : type === 'warning' ? 'warning'
      : 'info'
    setItems(list => [...list, {
      id,
      type: flashType,
      content: msg,
      dismissible: true,
      dismissLabel: 'Dismiss message',
      onDismiss: () => dismiss(id),
    }])
    // Auto-dismiss non-error notifications, matching the previous toast UX.
    if (flashType !== 'error') {
      setTimeout(() => dismiss(id), 5000)
    }
  }, [dismiss])

  return (
    <ToastCtx.Provider value={addToast}>
      <FlashCtx.Provider value={items}>
        {children}
      </FlashCtx.Provider>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)
export const useFlashItems = () => useContext(FlashCtx)

// ── Auth provider ─────────────────────────────────────────────────
export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [projects, setProjects] = useState([])

  const checkSession = useCallback(async () => {
    try {
      const data = await api.getSessionMe()
      setUser(data)
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadProjects = useCallback(async () => {
    try {
      const data = await api.getProjects()
      setProjects(data?.items || [])
    } catch {
      setProjects([])
    }
  }, [])

  useEffect(() => {
    checkSession()
  }, [checkSession])

  useEffect(() => {
    if (user) loadProjects()
  }, [user, loadProjects])

  const login = useCallback(async (userInfo) => {
    setUser(userInfo)
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch (e) { /* ignore */ }
    setUser(null)
    setProjects([])
  }, [])

  /**
   * Switch to a different project by calling the re-issue token endpoint.
   */
  const switchProject = useCallback(async (projectId) => {
    try {
      const data = await api.switchProject(projectId)
      if (data?.user) setUser(data.user)
      await checkSession()
    } catch (e) {
      console.warn('switchProject failed:', e.message)
    }
  }, [checkSession])

  const value = useMemo(
    () => ({ user, loading, login, logout, checkSession, projects, switchProject }),
    [user, loading, login, logout, checkSession, projects, switchProject],
  )

  return (
    <AuthCtx.Provider value={value}>
      {loading ? null : children}
    </AuthCtx.Provider>
  )
}

export const useAuth = () => useContext(AuthCtx)
