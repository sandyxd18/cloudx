import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth, useToast } from '../context.jsx'

export default function LoginPage() {
  const [form, setForm] = useState({ username: '', password: '', project: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const { login } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await api.login({
        username: form.username,
        password: form.password,
      })
      // auth-api returns: { token, user: {...}, project: {...} }
      login(data.token, {
        username:     data.user?.username     || form.username,
        user_id:      data.user?.id           || '',
        role:         data.user?.role         || 'member',
        project_id:   data.project?.id        || data.user?.project_id || '',
        project_name: data.project?.name      || '',
      })
      toast('Login berhasil!', 'success')
      navigate('/')
    } catch (err) {
      setError(err.message || 'Login gagal')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-bg" />
      <div className="login-card">
        <div className="login-logo">
          <div className="login-logo-icon">☁</div>
          <h1>cloudx</h1>
          <p>Private Cloud Management</p>
        </div>

        <p className="login-form-title">Sign in to your account</p>

        {error && <div className="alert alert-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Username</label>
            <input
              id="login-username"
              className="form-input"
              placeholder="admin"
              value={form.username}
              onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Password</label>
            <input
              id="login-password"
              type="password"
              className="form-input"
              placeholder="••••••••"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Project <span style={{color:'var(--text-muted)',fontWeight:400}}>(optional)</span></label>
            <input
              id="login-project"
              className="form-input"
              placeholder="default"
              value={form.project}
              onChange={e => setForm(f => ({ ...f, project: e.target.value }))}
            />
          </div>
          <button id="login-submit" type="submit" className="btn btn-primary w-full" disabled={loading}
            style={{ justifyContent: 'center', marginTop: 8 }}>
            {loading ? <span className="spinner" style={{width:14,height:14}} /> : null}
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        <p style={{ textAlign: 'center', marginTop: 24, fontSize: 12, color: 'var(--text-muted)' }}>
          cloudx Private Cloud Dashboard
        </p>
      </div>
    </div>
  )
}
