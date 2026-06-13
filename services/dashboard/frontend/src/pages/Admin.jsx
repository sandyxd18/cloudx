import { useEffect, useState } from 'react'
import { Plus, Trash2, Users } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

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

function CreateUserModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ username: '', password: '', email: '', role: 'member' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
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
              <div className="form-group"><label className="form-label">Email</label><input id="user-email" className="form-input" type="email" placeholder="john@example.com" value={form.email} onChange={f('email')} /></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">Password *</label><input id="user-password" type="password" className="form-input" placeholder="••••••••" value={form.password} onChange={f('password')} required /></div>
              <div className="form-group">
                <label className="form-label">Role</label>
                <select id="user-role" className="form-select" value={form.role} onChange={f('role')}>
                  <option value="admin">admin</option>
                  <option value="member">member</option>
                  <option value="reader">reader</option>
                </select>
              </div>
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

export default function AdminPage() {
  const [projects, setProjects] = useState([])
  const [users, setUsers]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [tab, setTab]           = useState('projects')
  const [showCreateProject, setShowCreateProject] = useState(false)
  const [showCreateUser, setShowCreateUser]       = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.allSettled([api.getProjects(), api.getUsers()])
      .then(([p, u]) => {
        if (p.status === 'fulfilled') setProjects(p.value?.items || [])
        if (u.status === 'fulfilled') setUsers(u.value?.items || [])
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

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Administration</h1><p className="page-subtitle">Manage projects and users</p></div>
      </div>

      <div className="tabs">
        <button className={`tab-btn ${tab === 'projects' ? 'active' : ''}`} onClick={() => setTab('projects')}>Projects ({projects.length})</button>
        <button className={`tab-btn ${tab === 'users' ? 'active' : ''}`} onClick={() => setTab('users')}>Users ({users.length})</button>
      </div>

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
                  <thead><tr><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
                  <tbody>
                    {users.map(u => (
                      <tr key={u.id}>
                        <td><div className="name-cell">{u.username}</div></td>
                        <td className="text-muted">{u.email || '—'}</td>
                        <td><span className={`status-badge ${u.role === 'admin' ? 'status-paused' : 'status-active'}`}>{u.role}</span></td>
                        <td><span className={`status-badge ${u.enabled !== false ? 'status-active' : 'status-inactive'}`}>{u.enabled !== false ? 'active' : 'disabled'}</span></td>
                        <td><button className="btn btn-icon btn-sm" id={`delete-user-${u.id}`} onClick={() => deleteUser(u.id, u.username)}><Trash2 size={13} /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
          </div>
        </>
      )}

      {showCreateProject && <CreateProjectModal onClose={() => setShowCreateProject(false)} onCreate={async (b) => { await api.createProject(b); load() }} />}
      {showCreateUser    && <CreateUserModal    onClose={() => setShowCreateUser(false)}    onCreate={async (b) => { await api.createUser(b);    load() }} />}
    </div>
  )
}
