import { useEffect, useState, useRef } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { Plus, Trash2, ChevronDown, Database } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'


function statusClass(s) {
  const m = { available: 'available', 'in-use': 'in-use', error: 'error', creating: 'building', extending: 'building', deleting: 'building' }
  return 'status-badge status-' + (m[s?.toLowerCase()] || 'inactive')
}

function CreateVolumeModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', size_gb: 10, volume_type: '', description: '' })
  const [types, setTypes] = useState([])
  const [typesLoaded, setTypesLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api.getVolumeTypes().then(r => setTypes(r?.items || [])).catch(() => setTypes([])).finally(() => setTypesLoaded(true))
  }, [])

  const f = field => e => setForm(v => ({ ...v, [field]: e.target.type === 'number' ? Number(e.target.value) : e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (typesLoaded && types.length === 0) { setError('No volume types configured.'); return }
    setLoading(true); setError('')
    try {
      const payload = { name: form.name, size_gb: form.size_gb }
      if (form.volume_type) payload.volume_type = form.volume_type
      await onCreate(payload); toast('Volume created', 'success'); onClose()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header"><span className="modal-title">Create Volume</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            {typesLoaded && types.length === 0 && (
              <div className="alert alert-error">⚠️ <strong>No volume types configured.</strong> An admin must create a volume type first.</div>
            )}
            <div className="form-group"><label className="form-label">Volume Name *</label><input id="vol-name" className="form-input" placeholder="my-volume" value={form.name} onChange={f('name')} required /></div>
            <div className="form-group"><label className="form-label">Description</label><input className="form-input" placeholder="Optional description" value={form.description} onChange={f('description')} /></div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">Size (GiB) *</label><input id="vol-size" type="number" min="1" className="form-input" value={form.size_gb} onChange={f('size_gb')} required /></div>
              <div className="form-group">
                <label className="form-label">Volume Type</label>
                <select id="vol-type" className="form-select" value={form.volume_type} onChange={f('volume_type')} disabled={types.length === 0}>
                  <option value="">{types.length === 0 ? '— no types available —' : '— auto-select —'}</option>
                  {types.map(t => <option key={t.id} value={t.name}>{t.name} · {t.disk_type} · {t.backend?.toUpperCase()}</option>)}
                </select>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="vol-create-submit" type="submit" className="btn btn-primary" disabled={loading || (typesLoaded && types.length === 0)}><Plus size={14} /> Create Volume</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ExtendModal({ volume, onClose, onExtend }) {
  const [size, setSize] = useState(volume.size_gb + 10)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onExtend(volume.id, Number(size)); toast('Volume extended', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Extend Volume: {volume.name}</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="alert alert-info">Current size: <strong>{volume.size_gb || volume.size} GiB</strong></div>
            <div className="form-group"><label className="form-label">New Size (GiB) *</label>
              <input id="vol-extend-size" type="number" min={(volume.size_gb || volume.size) + 1} className="form-input" value={size} onChange={e => setSize(e.target.value)} required />
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="vol-extend-submit" type="submit" className="btn btn-primary" disabled={loading}>Extend Volume</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function VolumeActions({ volume, onExtend, onDelete, onDetach, onCreateSnapshot }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => onExtend(volume)}>Edit Volume</button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}><ChevronDown size={12} /></button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item" onClick={() => { setOpen(false); onExtend(volume) }}>Extend Volume</button>
          {volume.status === 'in-use' && (
            <button className="dropdown-item" onClick={() => { setOpen(false); onDetach(volume.id) }}>Manage Attachment</button>
          )}
          <button className="dropdown-item" onClick={() => { setOpen(false); onCreateSnapshot(volume) }}>Create Snapshot</button>
          <div className="dropdown-sep" />
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(volume.id, volume.name) }}>Delete Volume</button>
        </div>
      )}
    </div>
  )
}

export default function VolumesPage() {
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [extendVol, setExtendVol]   = useState(null)
  const [selected, setSelected]     = useState([])
  const toast = useToast()

  const load = () => { setLoading(true); api.getVolumes().then(r => setVolumes(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteVolume = async (id, name) => {
    if (!confirm(`Delete volume "${name}"?`)) return
    try { await api.deleteVolume(id); toast('Volume deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const detachVolume = async (id) => {
    try { await api.detachVolume(id); toast('Volume detached', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const createSnapshot = (volume) => {
    toast('Use CLI: cloudx snapshot create --volume ' + volume.name, 'info')
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} volume(s)?`)) return
    for (const id of selected) { try { await api.deleteVolume(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} volume(s) deleted`, 'success'); load()
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === volumes.length ? [] : volumes.map(x=>x.id))

  const totalGB = volumes.reduce((a, v) => a + (v.size_gb || v.size || 0), 0)

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Volumes</h1><p className="page-subtitle">{volumes.length} volumes · {totalGB} GiB total</p></div>
        <div className="page-actions">
          <button id="create-volume-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Volume</button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Volumes ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter volumes…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          volumes.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Database size={40}/></div>
              <h3>No volumes</h3>
              <p>Create a volume to get persistent block storage</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Volume</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length===volumes.length && volumes.length>0} /></th>
                    <th>Name</th>
                    <th>Description</th>
                    <th>Size</th>
                    <th>Status</th>
                    <th>Group</th>
                    <th>Type</th>
                    <th>Attached To</th>
                    <th>Availability Zone</th>
                    <th>Bootable</th>
                    <th>Encrypted</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {volumes.map(v => (
                    <tr key={v.id} className={selected.includes(v.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(v.id)} onChange={() => toggleSelect(v.id)} /></td>
                      <td>
                        <RouterLink
                          to={`/volumes/${v.id}`}
                          style={{ textDecoration: 'none', color: 'inherit' }}
                        >
                          <div className="name-cell" style={{ color: '#7eaadb', cursor: 'pointer' }}>{v.name || '—'}</div>
                          <div className="mono text-muted" style={{fontSize:10}}>{v.id?.slice(0,16)}…</div>
                        </RouterLink>
                      </td>
                      <td className="text-muted">{v.description || '—'}</td>
                      <td>{v.size_gb || v.size || 0} GiB</td>
                      <td><span className={statusClass(v.status)}>{v.status}</span></td>
                      <td className="text-muted">—</td>
                      <td>{v.type || v.volume_type || v.backend || '—'}</td>
                      <td className="text-muted mono" style={{fontSize:11}}>
                        {v.attached_to_server_id ? v.attached_to_server_id.slice(0,8)+'…' : '—'}
                      </td>
                      <td className="text-muted">{v.availability_zone || 'nova'}</td>
                      <td>
                        <span className={`status-badge ${v.bootable ? 'status-active' : 'status-inactive'}`}>
                          {v.bootable ? 'Yes' : 'No'}
                        </span>
                      </td>
                      <td><span className="status-badge status-inactive">No</span></td>
                      <td><VolumeActions volume={v} onExtend={setExtendVol} onDelete={deleteVolume} onDetach={detachVolume} onCreateSnapshot={createSnapshot} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {showCreate && <CreateVolumeModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createVolume(form); load() }} />}
      {extendVol  && <ExtendModal volume={extendVol} onClose={() => setExtendVol(null)} onExtend={async (id, sz) => { await api.extendVolume(id, { new_size_gb: sz }); load() }} />}
    </div>
  )
}
