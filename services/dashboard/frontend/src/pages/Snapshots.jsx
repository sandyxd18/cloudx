import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function fmtSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

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
        <div className="modal-header"><span className="modal-title">Create Snapshot</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Source Volume *</label>
              <select id="snap-volume" className="form-select" value={form.volume} onChange={f('volume')} required>
                <option value="">-- Select Volume --</option>
                {volumes.map(v => <option key={v.id} value={v.name}>{v.name} ({v.size_gb}GB)</option>)}
              </select>
            </div>
            <div className="form-group"><label className="form-label">Snapshot Name *</label><input id="snap-name" className="form-input" placeholder="snap-2026-06-01" value={form.name} onChange={f('name')} required /></div>
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

export default function SnapshotsPage() {
  const [snaps, setSnaps]     = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const toast = useToast()

  const load = () => { setLoading(true); api.getSnapshots().then(r => setSnaps(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteSnap = async (id, name) => {
    if (!confirm(`Delete snapshot "${name}"?`)) return
    try { await api.deleteSnapshot(id); toast('Snapshot deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Snapshots</h1><p className="page-subtitle">{snaps.length} snapshots</p></div>
        <div className="page-actions">
          <button id="create-snapshot-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Snapshot</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          snaps.length === 0 ? <div className="empty-state"><div className="empty-state-icon">📸</div><h3>No snapshots</h3><p>Create a snapshot to back up a volume</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Volume</th><th>Size</th><th>Status</th><th>Created</th><th></th></tr></thead>
              <tbody>
                {snaps.map(s => (
                  <tr key={s.id}>
                    <td><div className="name-cell">{s.name}</div></td>
                    <td className="text-muted">{s.volume_name || s.volume_id || '—'}</td>
                    <td>{s.size_gb ? s.size_gb + ' GB' : fmtSize(s.size)}</td>
                    <td><span className={`status-badge status-${s.status === 'available' ? 'available' : 'building'}`}>{s.status}</span></td>
                    <td className="text-muted">{s.created_at ? new Date(s.created_at).toLocaleDateString() : '—'}</td>
                    <td><button className="btn btn-icon btn-sm" id={`delete-snap-${s.id}`} onClick={() => deleteSnap(s.id, s.name)}><Trash2 size={13} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
      {showCreate && <CreateSnapshotModal onClose={() => setShowCreate(false)} onCreate={async (volId, form) => { await api.createSnapshot(volId, form); load() }} />}
    </div>
  )
}
