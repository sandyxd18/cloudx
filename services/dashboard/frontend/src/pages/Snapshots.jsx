import { useEffect, useState, useRef } from 'react'
import { Plus, Trash2, ChevronDown, Camera } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateSnapshotModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ volume: '', name: '', description: '' })
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  useEffect(() => { api.getVolumes().then(r => setVolumes(r?.items || [])) }, [])
  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    const vol = volumes.find(v => v.name === form.volume || v.id === form.volume)
    if (!vol) { setError('Volume not found'); setLoading(false); return }
    try { await onCreate(vol.id, { name: form.name, description: form.description }); toast('Snapshot created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Volume Snapshot</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Source Volume *</label>
              <select id="snap-volume" className="form-select" value={form.volume} onChange={f('volume')} required>
                <option value="">-- Select Volume --</option>
                {volumes.map(v => <option key={v.id} value={v.name}>{v.name} ({v.size_gb || v.size} GiB)</option>)}
              </select>
            </div>
            <div className="form-group"><label className="form-label">Snapshot Name *</label><input id="snap-name" className="form-input" placeholder="snap-2026-01-01" value={form.name} onChange={f('name')} required /></div>
            <div className="form-group"><label className="form-label">Description</label><input id="snap-desc" className="form-input" placeholder="Optional description" value={form.description} onChange={f('description')} /></div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="snap-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create Snapshot</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function SnapshotActions({ snap, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => toast && null}>Create Volume</button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}><ChevronDown size={12}/></button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item" onClick={() => { setOpen(false) }}>Edit Snapshot</button>
          <div className="dropdown-sep" />
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(snap.id, snap.name) }}>Delete Volume Snapshot</button>
        </div>
      )}
    </div>
  )
}

export default function SnapshotsPage() {
  const [snaps, setSnaps]     = useState([])
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.all([api.getSnapshots(), api.getVolumes()])
      .then(([s, v]) => { setSnaps(s?.items || []); setVolumes(v?.items || []) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const deleteSnap = async (id, name) => {
    if (!confirm(`Delete snapshot "${name}"?`)) return
    try { await api.deleteSnapshot(id); toast('Snapshot deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} snapshot(s)?`)) return
    for (const id of selected) { try { await api.deleteSnapshot(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} snapshot(s) deleted`, 'success'); load()
  }

  const getVolumeName = (volId) => {
    const vol = volumes.find(v => v.id === volId)
    return vol?.name || volId?.slice(0,8)+'…' || '—'
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === snaps.length ? [] : snaps.map(x=>x.id))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Volume Snapshots</h1><p className="page-subtitle">{snaps.length} snapshots</p></div>
        <div className="page-actions">
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Volume Snapshots ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter snapshots…" />
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Snapshot</button>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner"/></div> :
          snaps.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Camera size={40}/></div>
              <h3>No snapshots</h3>
              <p>Create a snapshot to back up a volume at a point in time</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Snapshot</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length===snaps.length && snaps.length>0}/></th>
                    <th>Name</th>
                    <th>Description</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Group Snapshot</th>
                    <th>Volume Name</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {snaps.map(s => (
                    <tr key={s.id} className={selected.includes(s.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(s.id)} onChange={() => toggleSelect(s.id)}/></td>
                      <td>
                        <div className="name-cell">{s.name || '—'}</div>
                        <div className="mono text-muted" style={{fontSize:10}}>{s.id?.slice(0,16)}…</div>
                      </td>
                      <td className="text-muted">{s.description || '—'}</td>
                      <td>{s.size_gb || s.size || 0} GiB</td>
                      <td><span className={`status-badge status-${s.status==='available'?'available':'building'}`}>{s.status}</span></td>
                      <td className="text-muted">—</td>
                      <td className="text-muted">{getVolumeName(s.volume_id)}</td>
                      <td><SnapshotActions snap={s} onDelete={deleteSnap}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      {showCreate && <CreateSnapshotModal onClose={() => setShowCreate(false)} onCreate={async (volId, form) => { await api.createSnapshot(volId, form); load() }} />}
    </div>
  )
}
