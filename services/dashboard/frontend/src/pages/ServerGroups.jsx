import { useEffect, useState } from 'react'
import { Plus, Trash2, Layers } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateSGModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', policy: 'anti-affinity' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate(form); toast('Server group created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Server Group</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group"><label className="form-label">Name *</label><input id="sg-name" className="form-input" placeholder="my-server-group" value={form.name} onChange={e => setForm(v=>({...v,name:e.target.value}))} required /></div>
            <div className="form-group">
              <label className="form-label">Policy</label>
              <select id="sg-policy" className="form-select" value={form.policy} onChange={e => setForm(v=>({...v,policy:e.target.value}))}>
                <option value="anti-affinity">Anti-Affinity</option>
                <option value="affinity">Affinity</option>
                <option value="soft-anti-affinity">Soft Anti-Affinity</option>
                <option value="soft-affinity">Soft Affinity</option>
              </select>
              <p className="form-hint">Anti-affinity: place instances on different hosts</p>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="servergroup-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14}/> Create Server Group</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ServerGroupsPage() {
  const [groups, setGroups]   = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => { setLoading(true); api.getServerGroups().then(r => setGroups(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteGroup = async (id, name) => {
    if (!confirm(`Delete server group "${name}"?`)) return
    try { await api.deleteServerGroup(id); toast('Server group deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} server group(s)?`)) return
    for (const id of selected) { try { await api.deleteServerGroup(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} group(s) deleted`, 'success'); load()
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === groups.length ? [] : groups.map(x=>x.id))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Server Groups</h1><p className="page-subtitle">{groups.length} server groups</p></div>
        <div className="page-actions">
          <button id="create-sg-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Server Group</button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Server Groups ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter server groups…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner"/></div> :
          groups.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Layers size={40}/></div>
              <h3>No server groups</h3>
              <p>Create server groups to control instance placement policies</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Server Group</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length===groups.length && groups.length>0}/></th>
                    <th>Name</th>
                    <th>ID</th>
                    <th>Policy</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map(g => (
                    <tr key={g.id} className={selected.includes(g.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggleSelect(g.id)}/></td>
                      <td><div className="name-cell">{g.name}</div></td>
                      <td><span className="mono" style={{fontSize:11}}>{g.id?.slice(0,16)}…</span></td>
                      <td><span className={`status-badge ${g.policy==='anti-affinity'||g.policy==='soft-anti-affinity'?'status-building':'status-active'}`}>{g.policy}</span></td>
                      <td>
                        <button className="btn btn-icon btn-sm" id={`delete-group-${g.id}`} onClick={() => deleteGroup(g.id, g.name)} title="Delete Server Group"><Trash2 size={13}/></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      {showCreate && <CreateSGModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createServerGroup(form); load() }} />}
    </div>
  )
}
