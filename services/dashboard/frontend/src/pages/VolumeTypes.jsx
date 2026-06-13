/**
 * VolumeTypes.jsx — Admin page for managing AWS EBS-style volume types
 *
 * Volume types are performance profiles (gp2, gp3, io1, io2, sc1, st1, etc.)
 * Each type references a backend (lvm | ceph) and carries informational
 * performance specs (disk_type, max_iops, max_throughput).
 *
 * No volume types are auto-created — admin must add them before volumes
 * can be created by users.
 */

import { useEffect, useState } from 'react'
import { Plus, Trash2, Info } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

// ─── Constants ────────────────────────────────────────────────────────────────

const DISK_TYPES = [
  { value: 'nvme_ssd', label: 'NVMe SSD',  desc: 'Highest IOPS — provisioned (io1, io2)' },
  { value: 'sas_ssd',  label: 'SAS SSD',   desc: 'High throughput SSD (gp3)' },
  { value: 'sata_ssd', label: 'SATA SSD',  desc: 'General purpose SSD (gp2)' },
  { value: 'sas_hdd',  label: 'SAS HDD',   desc: 'Throughput HDD (st1)' },
  { value: 'sata_hdd', label: 'SATA HDD',  desc: 'Cold HDD (sc1)' },
]

const DISK_TYPE_COLOR = {
  nvme_ssd: { fg: '#7c3aed', bg: '#7c3aed18', br: '#7c3aed40' },
  sas_ssd:  { fg: '#0ea5e9', bg: '#0ea5e918', br: '#0ea5e940' },
  sata_ssd: { fg: '#06b6d4', bg: '#06b6d418', br: '#06b6d440' },
  sas_hdd:  { fg: '#f59e0b', bg: '#f59e0b18', br: '#f59e0b40' },
  sata_hdd: { fg: '#6b7280', bg: '#6b728018', br: '#6b728040' },
}

const BACKEND_COLOR = {
  lvm:  { fg: '#06b6d4', bg: '#06b6d418', br: '#06b6d440' },
  ceph: { fg: '#8b5cf6', bg: '#8b5cf618', br: '#8b5cf640' },
}

// AWS EBS equivalents as quick-fill presets
const PRESETS = [
  { name: 'gp2',  disk_type: 'sata_ssd', backend: 'lvm',  max_iops: 3000,  max_throughput: 125,  description: 'General Purpose SSD (SATA)' },
  { name: 'gp3',  disk_type: 'sas_ssd',  backend: 'lvm',  max_iops: 16000, max_throughput: 1000, description: 'General Purpose SSD (SAS) — throughput optimized' },
  { name: 'io1',  disk_type: 'nvme_ssd', backend: 'lvm',  max_iops: 64000, max_throughput: 4000, description: 'Provisioned IOPS SSD NVMe (io1)' },
  { name: 'io2',  disk_type: 'nvme_ssd', backend: 'ceph', max_iops: 64000, max_throughput: 4000, description: 'Provisioned IOPS SSD NVMe (io2) — Ceph RBD' },
  { name: 'st1',  disk_type: 'sas_hdd',  backend: 'lvm',  max_iops: 500,   max_throughput: 500,  description: 'Throughput Optimized HDD (SAS)' },
  { name: 'sc1',  disk_type: 'sata_hdd', backend: 'lvm',  max_iops: 250,   max_throughput: 250,  description: 'Cold HDD (SATA) — lowest cost' },
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

function Badge({ color, children }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
      background: color.bg, color: color.fg, border: `1px solid ${color.br}`,
      letterSpacing: '0.04em',
    }}>{children}</span>
  )
}

function fmtIops(v) {
  if (!v) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(0)}K` : `${v}`
}

// ─── Create Modal ─────────────────────────────────────────────────────────────

const EMPTY_FORM = { name: '', disk_type: 'sata_ssd', backend: 'lvm', max_iops: '', max_throughput: '', description: '', is_public: true }

function CreateTypeModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const f = field => e => {
    const val = e.target.type === 'checkbox' ? e.target.checked
               : e.target.type === 'number'  ? (e.target.value === '' ? '' : Number(e.target.value))
               : e.target.value
    setForm(v => ({ ...v, [field]: val }))
  }

  const applyPreset = (preset) => setForm(p => ({ ...p, ...preset }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      const payload = {
        ...form,
        max_iops:       form.max_iops       === '' ? null : Number(form.max_iops),
        max_throughput: form.max_throughput === '' ? null : Number(form.max_throughput),
      }
      await onCreate(payload)
      toast(`Volume type '${form.name}' created`, 'success')
      onClose()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const selectedDisk = DISK_TYPES.find(d => d.value === form.disk_type)
  const diskColor = DISK_TYPE_COLOR[form.disk_type] || DISK_TYPE_COLOR.sata_ssd
  const backendColor = BACKEND_COLOR[form.backend] || BACKEND_COLOR.lvm

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <span className="modal-title">Create Volume Type</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}

            {/* Presets */}
            <div style={{ marginBottom: 16 }}>
              <div className="form-label" style={{ marginBottom: 6 }}>Quick Presets (AWS EBS equivalents)</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {PRESETS.map(p => (
                  <button key={p.name} type="button" className="btn btn-secondary btn-sm"
                    style={{ fontWeight: 700, fontFamily: 'monospace', fontSize: 12 }}
                    onClick={() => applyPreset(p)}>
                    {p.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="form-row">
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label">Type Name *</label>
                <input id="vt-name" className="form-input" placeholder="gp2, io2, custom-nvme …"
                  value={form.name} onChange={f('name')} required style={{ fontFamily: 'monospace' }} />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label">Backend *</label>
                <select id="vt-backend" className="form-select" value={form.backend} onChange={f('backend')}>
                  <option value="lvm">LVM (block device)</option>
                  <option value="ceph">Ceph RBD (network disk)</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Disk Type *</label>
              <select id="vt-disk-type" className="form-select" value={form.disk_type} onChange={f('disk_type')}>
                {DISK_TYPES.map(d => (
                  <option key={d.value} value={d.value}>{d.label} — {d.desc}</option>
                ))}
              </select>
            </div>

            {/* Preview badges */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <Badge color={diskColor}>{selectedDisk?.label || form.disk_type}</Badge>
              <Badge color={backendColor}>{form.backend.toUpperCase()}</Badge>
            </div>

            <div className="form-row">
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label">Max IOPS <span className="text-muted">(informational)</span></label>
                <input id="vt-max-iops" type="number" min="1" className="form-input"
                  placeholder="e.g. 3000" value={form.max_iops} onChange={f('max_iops')} />
              </div>
              <div className="form-group" style={{ flex: 1 }}>
                <label className="form-label">Max Throughput MB/s <span className="text-muted">(informational)</span></label>
                <input id="vt-max-tp" type="number" min="1" className="form-input"
                  placeholder="e.g. 125" value={form.max_throughput} onChange={f('max_throughput')} />
              </div>
            </div>

            <div className="form-group">
              <label className="form-label">Description</label>
              <input id="vt-description" className="form-input" placeholder="General Purpose SSD"
                value={form.description} onChange={f('description')} />
            </div>

            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}>
              <input type="checkbox" checked={form.is_public} onChange={f('is_public')} />
              Public (visible to all projects)
            </label>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="vt-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
              <Plus size={14} /> Create Type
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function VolumeTypesPage() {
  const [types, setTypes] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    api.getVolumeTypes().then(r => setTypes(r?.items || [])).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const deleteType = async (ref, name) => {
    if (!confirm(`Delete volume type "${name}"?`)) return
    try { await api.deleteVolumeType(ref); toast(`Type '${name}' deleted`, 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Volume Types</h1>
          <p className="page-subtitle">
            {types.length === 0
              ? 'No types configured — volumes cannot be created until at least one type exists'
              : `${types.length} type${types.length !== 1 ? 's' : ''} available`}
          </p>
        </div>
        <div className="page-actions">
          <button id="refresh-vtypes" className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
          <button id="create-vtype-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Add Type
          </button>
        </div>
      </div>

      {/* Info box */}
      <div className="card" style={{ marginBottom: 16, padding: '12px 16px', background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <Info size={16} style={{ color: 'var(--color-primary)', marginTop: 2, flexShrink: 0 }} />
          <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-secondary)' }}>
            Volume types are <strong>AWS EBS-style performance profiles</strong>. Each type combines a disk tier hint
            (NVMe/SAS/SATA) with a storage backend (LVM or Ceph RBD).
            The IOPS and throughput values are <em>informational only</em> — they are not enforced by the hypervisor.
            <br />
            <strong>Admin must add at least one type</strong> before users can create volumes.
          </div>
        </div>
      </div>

      {types.length === 0 && !loading && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">📊</div>
            <h3>No volume types yet</h3>
            <p>Add your first volume type to allow users to create volumes.</p>
            <div style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
              {PRESETS.map(p => (
                <code key={p.name} style={{ fontSize: 11, background: 'var(--bg-tertiary)', padding: '3px 8px', borderRadius: 4 }}>
                  {p.name}
                </code>
              ))}
            </div>
          </div>
        </div>
      )}

      {types.length > 0 && (
        <div className="card">
          {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Disk Type</th>
                    <th>Backend</th>
                    <th>Max IOPS</th>
                    <th>Max Throughput</th>
                    <th>Description</th>
                    <th>Public</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {types.map(t => {
                    const dc = DISK_TYPE_COLOR[t.disk_type] || DISK_TYPE_COLOR.sata_ssd
                    const bc = BACKEND_COLOR[t.backend] || BACKEND_COLOR.lvm
                    const diskLabel = DISK_TYPES.find(d => d.value === t.disk_type)?.label || t.disk_type
                    return (
                      <tr key={t.id}>
                        <td>
                          <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 14 }}>{t.name}</span>
                        </td>
                        <td><Badge color={dc}>{diskLabel}</Badge></td>
                        <td><Badge color={bc}>{t.backend.toUpperCase()}</Badge></td>
                        <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                          {t.max_iops ? <strong>{t.max_iops.toLocaleString()}</strong> : <span className="text-muted">—</span>}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                          {t.max_throughput ? <strong>{t.max_throughput} MB/s</strong> : <span className="text-muted">—</span>}
                        </td>
                        <td className="text-muted" style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {t.description || '—'}
                        </td>
                        <td>
                          <span className={`status-badge status-${t.is_public ? 'available' : 'inactive'}`}>
                            {t.is_public ? 'public' : 'private'}
                          </span>
                        </td>
                        <td>
                          <div className="table-actions">
                            <button className="btn btn-icon btn-sm" id={`delete-vt-${t.id}`}
                              title="Delete (fails if volumes use this type)"
                              onClick={() => deleteType(t.id, t.name)}>
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          }
        </div>
      )}

      {showCreate && (
        <CreateTypeModal
          onClose={() => setShowCreate(false)}
          onCreate={async (data) => { await api.createVolumeType(data); load() }}
        />
      )}
    </div>
  )
}
