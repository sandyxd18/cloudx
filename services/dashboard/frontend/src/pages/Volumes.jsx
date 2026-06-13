import { useEffect, useState } from 'react'
import { Plus, Trash2, Expand, Unlink } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function statusClass(s) {
  const m = { available: 'available', 'in-use': 'in-use', error: 'error', creating: 'building' }
  return 'status-badge status-' + (m[s?.toLowerCase()] || 'inactive')
}

const DISK_TYPE_LABEL = {
  sata_ssd: 'SATA SSD',
  sas_ssd:  'SAS SSD',
  nvme_ssd: 'NVMe SSD',
  sata_hdd: 'SATA HDD',
  sas_hdd:  'SAS HDD',
}

const DISK_TYPE_COLOR = {
  nvme_ssd: 'var(--color-primary)',
  sas_ssd:  '#8b5cf6',
  sata_ssd: '#06b6d4',
  sas_hdd:  '#f59e0b',
  sata_hdd: '#6b7280',
}

function DiskTypeBadge({ diskType }) {
  if (!diskType) return null
  return (
    <span style={{
      fontSize: 10,
      fontWeight: 700,
      padding: '1px 6px',
      borderRadius: 4,
      background: DISK_TYPE_COLOR[diskType] + '22',
      color: DISK_TYPE_COLOR[diskType],
      border: `1px solid ${DISK_TYPE_COLOR[diskType]}44`,
      letterSpacing: '0.05em',
    }}>
      {DISK_TYPE_LABEL[diskType] || diskType}
    </span>
  )
}

function CreateVolumeModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', size_gb: 10, volume_type: '' })
  const [types, setTypes] = useState([])
  const [typesLoaded, setTypesLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => {
    api.getVolumeTypes()
      .then(r => setTypes(r?.items || []))
      .catch(() => setTypes([]))
      .finally(() => setTypesLoaded(true))
  }, [])

  const f = field => e => setForm(v => ({ ...v, [field]: e.target.type === 'number' ? Number(e.target.value) : e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (typesLoaded && types.length === 0) {
      setError('No volume types configured. Ask an admin to create one first (e.g. cloudx volume-type create gp2 --backend lvm --disk-type sata_ssd).')
      return
    }
    setLoading(true); setError('')
    try {
      const payload = { name: form.name, size_gb: form.size_gb }
      if (form.volume_type) payload.volume_type = form.volume_type
      await onCreate(payload)
      toast('Volume created', 'success')
      onClose()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Volume</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            {typesLoaded && types.length === 0 && (
              <div className="alert alert-error" style={{ marginBottom: 12 }}>
                ⚠️ <strong>No volume types configured.</strong> An admin must create at least one volume type before volumes can be created.
              </div>
            )}
            <div className="form-group"><label className="form-label">Volume Name *</label><input id="vol-name" className="form-input" placeholder="my-volume" value={form.name} onChange={f('name')} required /></div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">Size (GB) *</label><input id="vol-size" type="number" min="1" className="form-input" value={form.size_gb} onChange={f('size_gb')} required /></div>
              <div className="form-group">
                <label className="form-label">Volume Type</label>
                <select id="vol-type" className="form-select" value={form.volume_type} onChange={f('volume_type')} disabled={types.length === 0}>
                  <option value="">{types.length === 0 ? '— no types available —' : '— auto-select —'}</option>
                  {types.map(t => (
                    <option key={t.id} value={t.name}>
                      {t.name} · {DISK_TYPE_LABEL[t.disk_type] || t.disk_type} · {t.backend.toUpperCase()}
                      {t.max_iops ? ` · ${t.max_iops.toLocaleString()} IOPS` : ''}
                    </option>
                  ))}
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
            <div className="alert alert-info">Current size: <strong>{volume.size_gb} GB</strong> · Backend: <strong>{volume.backend}</strong></div>
            <div className="form-group"><label className="form-label">New Size (GB) *</label>
              <input id="vol-extend-size" type="number" min={volume.size_gb + 1} className="form-input" value={size} onChange={e => setSize(e.target.value)} required />
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="vol-extend-submit" type="submit" className="btn btn-primary" disabled={loading}><Expand size={14} /> Extend</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function VolumesPage() {
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [extendVol, setExtendVol]   = useState(null)
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

  const totalGB = volumes.reduce((a, v) => a + (v.size_gb || 0), 0)

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Volumes</h1><p className="page-subtitle">{volumes.length} volumes · {totalGB} GB total</p></div>
        <div className="page-actions">
          <button id="refresh-volumes" className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
          <button id="create-volume-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Volume</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          volumes.length === 0 ? <div className="empty-state"><div className="empty-state-icon">💾</div><h3>No volumes</h3><p>Create a volume to get persistent block storage</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Size</th><th>Status</th><th>Type</th><th>Backend</th><th>Attached To</th><th></th></tr></thead>
              <tbody>
                {volumes.map(v => (
                  <tr key={v.id}>
                    <td>
                      <div className="name-cell">{v.name}</div>
                      <div className="mono text-muted" style={{ fontSize: 10 }}>{v.id?.slice(0, 16)}…</div>
                    </td>
                    <td>{v.size_gb} GB</td>
                    <td><span className={statusClass(v.status)}>{v.status}</span></td>
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{v.volume_type || '—'}</span>
                      </div>
                    </td>
                    <td>
                      <span style={{
                        fontSize: 11,
                        fontWeight: 600,
                        padding: '2px 7px',
                        borderRadius: 4,
                        background: v.backend === 'ceph' ? '#8b5cf622' : '#06b6d422',
                        color: v.backend === 'ceph' ? '#8b5cf6' : '#06b6d4',
                        border: `1px solid ${v.backend === 'ceph' ? '#8b5cf644' : '#06b6d444'}`,
                      }}>
                        {v.backend?.toUpperCase() || 'LVM'}
                      </span>
                    </td>
                    <td className="text-muted">{v.attached_to_server_id ? v.attached_to_server_id.slice(0, 8) + '…' : '—'}</td>
                    <td>
                      <div className="table-actions">
                        <button className="btn btn-icon btn-sm" title="Extend" onClick={() => setExtendVol(v)}><Expand size={13} /></button>
                        {v.status === 'in-use' && <button className="btn btn-icon btn-sm" title="Detach" onClick={() => detachVolume(v.id)}><Unlink size={13} /></button>}
                        <button className="btn btn-icon btn-sm" id={`delete-vol-${v.id}`} onClick={() => deleteVolume(v.id, v.name)}><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
      {showCreate && <CreateVolumeModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createVolume(form); load() }} />}
      {extendVol  && <ExtendModal volume={extendVol} onClose={() => setExtendVol(null)} onExtend={async (id, sz) => { await api.extendVolume(id, { new_size_gb: sz }); load() }} />}
    </div>
  )
}
