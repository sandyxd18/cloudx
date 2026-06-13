import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', vcpus: 1, ram_mb: 512, disk_gb: 10 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await onCreate(form)
      toast('Flavor created', 'success')
      onClose()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const f = field => e => setForm(v => ({ ...v, [field]: e.target.type === 'number' ? Number(e.target.value) : e.target.value }))

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">Create Flavor</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Name *</label>
              <input id="flavor-name" className="form-input" placeholder="m1.small" value={form.name} onChange={f('name')} required />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">vCPUs *</label>
                <input id="flavor-vcpus" type="number" min="1" className="form-input" value={form.vcpus} onChange={f('vcpus')} required />
              </div>
              <div className="form-group">
                <label className="form-label">RAM (MB) *</label>
                <input id="flavor-ram" type="number" min="128" className="form-input" value={form.ram_mb} onChange={f('ram_mb')} required />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Root Disk (GB) *</label>
              <input id="flavor-disk" type="number" min="1" className="form-input" value={form.disk_gb} onChange={f('disk_gb')} required />
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="flavor-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14} />}
              Create Flavor
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function FlavorsPage() {
  const [flavors, setFlavors] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const toast = useToast()

  const load = () => { setLoading(true); api.getFlavors().then(r => setFlavors(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const handleDelete = async (id, name) => {
    if (!confirm(`Delete flavor "${name}"?`)) return
    try { await api.deleteFlavor(id); toast('Flavor deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Flavors</h1><p className="page-subtitle">{flavors.length} flavors defined</p></div>
        <div className="page-actions">
          <button id="create-flavor-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Flavor</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          flavors.length === 0 ? <div className="empty-state"><div className="empty-state-icon">⚙️</div><h3>No flavors</h3><p>Create a flavor to define VM resources</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>vCPUs</th><th>RAM</th><th>Disk</th><th>Public</th><th></th></tr></thead>
              <tbody>
                {flavors.map(f => (
                  <tr key={f.id}>
                    <td><div className="name-cell">{f.name}</div></td>
                    <td>{f.vcpus}</td>
                    <td>{f.ram_mb >= 1024 ? `${(f.ram_mb/1024).toFixed(1)} GB` : `${f.ram_mb} MB`}</td>
                    <td>{f.disk_gb} GB</td>
                    <td><span className={`status-badge ${f.is_public ? 'status-active' : 'status-inactive'}`}>{f.is_public ? 'public' : 'private'}</span></td>
                    <td><button className="btn btn-icon btn-sm" id={`delete-flavor-${f.id}`} onClick={() => handleDelete(f.id, f.name)} title="Delete"><Trash2 size={13} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createFlavor(form); load() }} />}
    </div>
  )
}
