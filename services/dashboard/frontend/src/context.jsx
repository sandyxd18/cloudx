import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import { api } from './api.js'

const ToastCtx = createContext(null)
const AuthCtx  = createContext(null)

// ── Toast provider ────────────────────────────────────────────────
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback((msg, type = 'info') => {
    const id = Date.now()
    setToasts(t => [...t, { id, msg, type }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3500)
  }, [])

  const icons = { success: '✓', error: '✕', info: 'ℹ' }

  return (
    <ToastCtx.Provider value={addToast}>
      {children}
      <div className="toast-container">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`}>
            <span>{icons[t.type] || 'ℹ'}</span>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export const useToast = () => useContext(ToastCtx)

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
   * Falls back to a toast if the endpoint isn't available.
   */
  const switchProject = useCallback(async (projectId) => {
    try {
      const data = await api.switchProject(projectId)
      if (data?.user) setUser(data.user)
      // Re-check session to get fresh user context with new project
      await checkSession()
    } catch (e) {
      // If endpoint doesn't exist yet, just log
      console.warn('switchProject failed:', e.message)
    }
  }, [checkSession])

  return (
    <AuthCtx.Provider value={{ user, loading, login, logout, checkSession, projects, switchProject }}>
      {loading ? null : children}
    </AuthCtx.Provider>
  )
}

export const useAuth = () => useContext(AuthCtx)
