import { useEffect, useState } from 'react'
import { Plus, Trash2, Key, Copy, Eye, EyeOff, RefreshCw } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

// ─── Create Project Modal ────────────────────────────────────────────────────
function CreateProjectModal({ onClose, onCreate }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate({ name, description: desc }); toast('Project created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Project</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group"><label className="form-label">Project Name *</label><input id="project-name" className="form-input" placeholder="my-project" value={name} onChange={e => setName(e.target.value)} required /></div>
            <div className="form-group"><label className="form-label">Description</label><input id="project-desc" className="form-input" placeholder="Optional description" value={desc} onChange={e => setDesc(e.target.value)} /></div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="project-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Create User Modal ───────────────────────────────────────────────────────
function CreateUserModal({ onClose, onCreate, projects }) {
  const [form, setForm] = useState({ username: '', password: '', email: '', role: 'member', project_id: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  // Auto-select first project if available
  useEffect(() => {
    if (projects.length > 0 && !form.project_id) {
      setForm(v => ({ ...v, project_id: projects[0].id }))
    }
  }, [projects])

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    if (!form.project_id) { setError('Please select a project'); setLoading(false); return }
    try { await onCreate(form); toast('User created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create User</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-row">
              <div className="form-group"><label className="form-label">Username *</label><input id="user-username" className="form-input" placeholder="john.doe" value={form.username} onChange={f('username')} required /></div>
              <div className="form-group"><label className="form-label">Email *</label><input id="user-email" className="form-input" type="email" placeholder="john@example.com" value={form.email} onChange={f('email')} required /></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">Password *</label><input id="user-password" type="password" className="form-input" placeholder="••••••••" value={form.password} onChange={f('password')} required /></div>
              <div className="form-group">
                <label className="form-label">Role</label>
                <select id="user-role" className="form-select" value={form.role} onChange={f('role')}>
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                </select>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Project *</label>
              <select id="user-project" className="form-select" value={form.project_id} onChange={f('project_id')} required>
                <option value="">— select project —</option>
                {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="user-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create User</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Generate Access Key Modal ───────────────────────────────────────────────
function GenerateKeyModal({ onClose, onGenerate }) {
  const [name, setName] = useState('default')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [showSecret, setShowSecret] = useState(false)
  const [copied, setCopied] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      const data = await onGenerate({ name })
      setResult(data)
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const copyToClipboard = async (text, field) => {
    await navigator.clipboard.writeText(text)
    setCopied(field)
    setTimeout(() => setCopied(''), 2000)
    toast(`${field} copied to clipboard`, 'success')
  }

  if (result) {
    return (
      <div className="modal-overlay">
        <div className="modal" style={{ maxWidth: 520 }}>
          <div className="modal-header">
            <span className="modal-title">🔑 Access Key Generated</span>
          </div>
          <div className="modal-body">
            <div className="alert alert-error" style={{ marginBottom: 16 }}>
              ⚠️ Save your <strong>Secret Access Key</strong> now — it will not be shown again.
            </div>
            <div className="form-group">
              <label className="form-label">Access Key ID</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input className="form-input" readOnly value={result.access_key_id} style={{ fontFamily: 'monospace', fontSize: 13 }} />
                <button className="btn btn-secondary" onClick={() => copyToClipboard(result.access_key_id, 'Access Key ID')} title="Copy">
                  {copied === 'Access Key ID' ? '✓' : <Copy size={14} />}
                </button>
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Secret Access Key</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="form-input"
                  readOnly
                  type={showSecret ? 'text' : 'password'}
                  value={result.secret_access_key}
                  style={{ fontFamily: 'monospace', fontSize: 13 }}
                />
                <button className="btn btn-secondary" onClick={() => setShowSecret(s => !s)} title="Toggle visibility">
                  {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
                <button className="btn btn-secondary" onClick={() => copyToClipboard(result.secret_access_key, 'Secret Access Key')} title="Copy">
                  {copied === 'Secret Access Key' ? '✓' : <Copy size={14} />}
                </button>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn btn-primary" onClick={onClose}>Done</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Generate Access Key</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Key Name</label>
              <input id="key-name" className="form-input" placeholder="default" value={name} onChange={e => setName(e.target.value)} />
              <span className="form-hint">A friendly label for this key (e.g. "cli", "ci-pipeline")</span>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="key-generate-submit" type="submit" className="btn btn-primary" disabled={loading}><Key size={14} /> Generate Key</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Main Admin Page ─────────────────────────────────────────────────────────
export default function AdminPage() {
  const [projects, setProjects] = useState([])
  const [users, setUsers]       = useState([])
  const [accessKeys, setAccessKeys] = useState([])
  const [loading, setLoading]   = useState(true)
  const [tab, setTab]           = useState('projects')
  const [showCreateProject, setShowCreateProject] = useState(false)
  const [showCreateUser, setShowCreateUser]       = useState(false)
  const [showGenerateKey, setShowGenerateKey]     = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.allSettled([api.getProjects(), api.getUsers(), api.getAccessKeys()])
      .then(([p, u, k]) => {
        if (p.status === 'fulfilled') setProjects(p.value?.items || [])
        if (u.status === 'fulfilled') setUsers(u.value?.items || [])
        if (k.status === 'fulfilled') setAccessKeys(k.value?.items || [])
      })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const deleteProject = async (id, name) => {
    if (!confirm(`Delete project "${name}"?`)) return
    try { await api.deleteProject(id); toast('Project deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }
  const deleteUser = async (id, name) => {
    if (!confirm(`Delete user "${name}"?`)) return
    try { await api.deleteUser(id); toast('User deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }
  const revokeKey = async (keyId, keyName) => {
    if (!confirm(`Revoke access key "${keyName}"?`)) return
    try { await api.deleteAccessKey(keyId); toast('Access key revoked', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Administration</h1><p className="page-subtitle">Manage projects, users and access keys</p></div>
      </div>

      <div className="tabs">
        <button className={`tab-btn ${tab === 'projects' ? 'active' : ''}`} onClick={() => setTab('projects')}>Projects ({projects.length})</button>
        <button className={`tab-btn ${tab === 'users' ? 'active' : ''}`} onClick={() => setTab('users')}>Users ({users.length})</button>
        <button className={`tab-btn ${tab === 'keys' ? 'active' : ''}`} onClick={() => setTab('keys')}>Access Keys ({accessKeys.length})</button>
      </div>

      {/* ── Projects ── */}
      {tab === 'projects' && (
        <>
          <div className="page-header" style={{ marginBottom: 16 }}>
            <div />
            <button id="create-project-btn" className="btn btn-primary" onClick={() => setShowCreateProject(true)}><Plus size={14} /> Create Project</button>
          </div>
          <div className="card">
            {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
              projects.length === 0 ? <div className="empty-state"><div className="empty-state-icon">📁</div><h3>No projects</h3></div> :
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Name</th><th>Description</th><th>Status</th><th></th></tr></thead>
                  <tbody>
                    {projects.map(p => (
                      <tr key={p.id}>
                        <td><div className="name-cell">{p.name}</div><div className="mono text-muted" style={{fontSize:10}}>{p.id?.slice(0,16)}…</div></td>
                        <td className="text-muted">{p.description || '—'}</td>
                        <td><span className={`status-badge ${p.enabled !== false ? 'status-active' : 'status-inactive'}`}>{p.enabled !== false ? 'enabled' : 'disabled'}</span></td>
                        <td><button className="btn btn-icon btn-sm" id={`delete-project-${p.id}`} onClick={() => deleteProject(p.id, p.name)}><Trash2 size={13} /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
          </div>
        </>
      )}

      {/* ── Users ── */}
      {tab === 'users' && (
        <>
          <div className="page-header" style={{ marginBottom: 16 }}>
            <div />
            <button id="create-user-btn" className="btn btn-primary" onClick={() => setShowCreateUser(true)}><Plus size={14} /> Create User</button>
          </div>
          <div className="card">
            {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
              users.length === 0 ? <div className="empty-state"><div className="empty-state-icon">👤</div><h3>No users</h3></div> :
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Username</th><th>Email</th><th>Project</th><th>Role</th><th>Status</th><th></th></tr></thead>
                  <tbody>
                    {users.map(u => {
                      const proj = projects.find(p => p.id === u.project_id)
                      return (
                        <tr key={u.id}>
                          <td><div className="name-cell">{u.username}</div></td>
                          <td className="text-muted">{u.email || '—'}</td>
                          <td className="text-muted mono" style={{fontSize:12}}>{proj?.name || u.project_id?.slice(0,8) + '…'}</td>
                          <td><span className={`status-badge ${u.role === 'admin' ? 'status-paused' : 'status-active'}`}>{u.role}</span></td>
                          <td><span className={`status-badge ${u.enabled !== false ? 'status-active' : 'status-inactive'}`}>{u.enabled !== false ? 'active' : 'disabled'}</span></td>
                          <td><button className="btn btn-icon btn-sm" id={`delete-user-${u.id}`} onClick={() => deleteUser(u.id, u.username)}><Trash2 size={13} /></button></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>}
          </div>
        </>
      )}

      {/* ── Access Keys ── */}
      {tab === 'keys' && (
        <>
          <div className="page-header" style={{ marginBottom: 16 }}>
            <div>
              <p className="page-subtitle" style={{marginTop:0}}>
                Access keys authenticate CLI and API requests without a password. Generate one for each user or CI/CD pipeline.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-secondary" onClick={load}><RefreshCw size={14} /></button>
              <button id="generate-key-btn" className="btn btn-primary" onClick={() => setShowGenerateKey(true)}><Key size={14} /> Generate Access Key</button>
            </div>
          </div>
          <div className="card">
            {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
              accessKeys.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-icon">🔑</div>
                  <h3>No access keys</h3>
                  <p className="text-muted">Generate an access key to authenticate the CLI without a password.</p>
                </div>
              ) :
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Name</th><th>Access Key ID</th><th>Owner</th><th>Status</th><th>Created</th><th></th></tr></thead>
                  <tbody>
                    {accessKeys.map(k => {
                      const user = users.find(u => u.id === k.user_id)
                      return (
                        <tr key={k.id}>
                          <td><div className="name-cell">{k.name || 'default'}</div></td>
                          <td><span className="mono" style={{ fontSize: 12 }}>{k.access_key_id}</span></td>
                          <td className="text-muted">{user?.username || k.user_id?.slice(0,8) + '…'}</td>
                          <td><span className={`status-badge ${k.enabled ? 'status-active' : 'status-inactive'}`}>{k.enabled ? 'active' : 'disabled'}</span></td>
                          <td className="text-muted" style={{fontSize:12}}>{new Date(k.created_at).toLocaleDateString()}</td>
                          <td><button className="btn btn-icon btn-sm btn-danger" id={`revoke-key-${k.id}`} onClick={() => revokeKey(k.id, k.name)} title="Revoke"><Trash2 size={13} /></button></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>}
          </div>
        </>
      )}

      {showCreateProject && <CreateProjectModal onClose={() => setShowCreateProject(false)} onCreate={async (b) => { await api.createProject(b); load() }} />}
      {showCreateUser    && <CreateUserModal    onClose={() => setShowCreateUser(false)}    onCreate={async (b) => { await api.createUser(b);    load() }} projects={projects} />}
      {showGenerateKey   && <GenerateKeyModal   onClose={() => { setShowGenerateKey(false); load() }} onGenerate={(b) => api.createAccessKey(b)} />}
    </div>
  )
}
