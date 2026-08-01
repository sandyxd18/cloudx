import { useEffect, useState, useRef, useCallback } from 'react'
import { Trash2, ChevronDown, HardDrive, Upload, X, Image as ImageIcon } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function fmtSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function statusClass(s) {
  const m = { active: 'active', deactivated: 'shutoff', error: 'error', queued: 'building', saving: 'building' }
  return 'status-badge status-' + (m[s?.toLowerCase()] || 'inactive')
}

function ImageActions({ image, onDelete, onToggleActive, onToggleVisibility }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => null}>Launch</button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}><ChevronDown size={12}/></button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item" onClick={() => { setOpen(false); onToggleVisibility(image) }}>
            {image.visibility === 'public' ? 'Make Private' : 'Make Public'}
          </button>
          <button className="dropdown-item" onClick={() => { setOpen(false); onToggleActive(image) }}>
            {image.status === 'ACTIVE' ? 'Deactivate Image' : 'Reactivate Image'}
          </button>
          <div className="dropdown-sep" />
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(image.id, image.name) }}>Delete Image</button>
        </div>
      )}
    </div>
  )
}

// ── Upload Image Modal ─────────────────────────────────────────────────────────
function UploadModal({ onClose, onSuccess }) {
  const [name, setName]             = useState('')
  const [diskFormat, setDiskFormat] = useState('qcow2')
  const [visibility, setVisibility] = useState('public')
  const [file, setFile]             = useState(null)
  const [dragging, setDragging]     = useState(false)
  const [progress, setProgress]     = useState(null) // null = idle, 0-100 = uploading, -1 = done
  const [error, setError]           = useState('')
  const fileRef = useRef()
  const toast = useToast()

  const handleFile = (f) => {
    setFile(f)
    if (!name) setName(f.name.replace(/\.(qcow2|img|raw|vmdk|iso)$/i, ''))
    const ext = f.name.split('.').pop().toLowerCase()
    if (['qcow2', 'img', 'raw', 'vmdk', 'iso'].includes(ext)) setDiskFormat(ext === 'img' ? 'raw' : ext)
  }

  const onDrop = useCallback(e => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [name])

  const onDragOver = e => { e.preventDefault(); setDragging(true) }
  const onDragLeave = () => setDragging(false)

  const handleUpload = async () => {
    if (!file) { setError('Please select a file.'); return }
    if (!name.trim()) { setError('Name is required.'); return }
    setError('')
    setProgress(0)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name.trim())
    fd.append('disk_format', diskFormat)
    fd.append('container_format', 'bare')
    fd.append('visibility', visibility)
    try {
      await api.uploadImageWithProgress(fd, pct => setProgress(pct))
      setProgress(-1)
      toast('Image uploaded successfully!', 'success')
      onSuccess()
      setTimeout(onClose, 800)
    } catch (e) {
      setError(e.message)
      setProgress(null)
    }
  }

  const uploading = progress !== null && progress >= 0 && progress < 100

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && !uploading && onClose()}>
      <div className="modal" style={{ maxWidth: 520 }}>
        <div className="modal-header">
          <span className="modal-title"><Upload size={15} /> Upload Image</span>
          {!uploading && <button className="modal-close btn" onClick={onClose}><X size={14}/></button>}
        </div>
        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Drop Zone */}
          <div
            className={`upload-dropzone${dragging ? ' dragging' : ''}${file ? ' has-file' : ''}`}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onClick={() => !uploading && fileRef.current?.click()}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".qcow2,.img,.raw,.vmdk,.iso"
              style={{ display: 'none' }}
              onChange={e => e.target.files[0] && handleFile(e.target.files[0])}
            />
            {file ? (
              <div style={{ textAlign: 'center' }}>
                <ImageIcon size={28} style={{ color: 'var(--accent)', marginBottom: 8 }} />
                <div style={{ fontWeight: 600, fontSize: 13 }}>{file.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{fmtSize(file.size)}</div>
              </div>
            ) : (
              <div style={{ textAlign: 'center' }}>
                <Upload size={28} style={{ color: 'var(--text-muted)', marginBottom: 8 }} />
                <div style={{ fontWeight: 600, fontSize: 13 }}>Drag & drop image file</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>or click to browse • .qcow2 .img .raw .vmdk .iso</div>
              </div>
            )}
          </div>

          {/* Progress bar */}
          {progress !== null && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {progress === -1 ? '✓ Upload complete' : `Uploading… ${progress}%`}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>{progress === -1 ? '' : `${progress}%`}</span>
              </div>
              <div className="resource-bar-track">
                <div
                  className="resource-bar-fill"
                  style={{
                    width: `${progress === -1 ? 100 : progress}%`,
                    background: progress === -1 ? '#10b981' : 'var(--accent)',
                    transition: 'width 0.3s ease',
                  }}
                />
              </div>
            </div>
          )}

          {/* Fields */}
          <div className="form-group">
            <label className="form-label">Image Name *</label>
            <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. ubuntu-22.04" disabled={uploading} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Disk Format</label>
              <select className="form-input" value={diskFormat} onChange={e => setDiskFormat(e.target.value)} disabled={uploading}>
                <option value="qcow2">QCOW2</option>
                <option value="raw">RAW</option>
                <option value="vmdk">VMDK</option>
                <option value="iso">ISO</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Visibility</label>
              <select className="form-input" value={visibility} onChange={e => setVisibility(e.target.value)} disabled={uploading}>
                <option value="public">Public</option>
                <option value="private">Private</option>
              </select>
            </div>
          </div>

          {error && <div className="alert alert-danger" style={{ fontSize: 13 }}>⚠ {error}</div>}
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose} disabled={uploading}>Cancel</button>
          <button className="btn btn-primary" onClick={handleUpload} disabled={uploading || !file}>
            {uploading ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Uploading…</> : <><Upload size={14} /> Upload Image</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function ImagesPage() {
  const [images, setImages]   = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch]   = useState('')
  const [visFilter, setVisFilter] = useState('')
  const [selected, setSelected] = useState([])
  const [showUpload, setShowUpload] = useState(false)
  const toast = useToast()

  const load = () => { setLoading(true); api.getImages().then(r => setImages(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteImage = async (id, name) => {
    if (!confirm(`Delete image "${name}"?`)) return
    try { await api.deleteImage(id); toast('Image deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} image(s)?`)) return
    for (const id of selected) { try { await api.deleteImage(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} image(s) deleted`, 'success'); load()
  }

  const toggleActive = async (img) => {
    try {
      if (img.status === 'ACTIVE') { await api.deactivateImage(img.id); toast('Image deactivated', 'success') }
      else { await api.reactivateImage(img.id); toast('Image reactivated', 'success') }
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  const toggleVisibility = async (img) => {
    const newVis = img.visibility === 'public' ? 'private' : 'public'
    try { await api.updateImage(img.id, { visibility: newVis }); toast(`Visibility set to ${newVis}`, 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === filtered.length ? [] : filtered.map(x=>x.id))

  const filtered = images.filter(i =>
    i.name?.toLowerCase().includes(search.toLowerCase()) &&
    (!visFilter || i.visibility === visFilter)
  )

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Images</h1><p className="page-subtitle">{images.length} images total</p></div>
        <div className="page-actions">
          <button id="refresh-images" className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Images ({selected.length})</button>
          )}
          <button id="upload-image-btn" className="btn btn-primary" onClick={() => setShowUpload(true)}>
            <Upload size={14}/> Upload Image
          </button>
        </div>
      </div>

      <div className="toolbar">
        <div className="toolbar-left">
          <div className="search-box">
            <span style={{color:'var(--text-muted)'}}>🔍</span>
            <input placeholder="Filter images…" value={search} onChange={e => setSearch(e.target.value)} />
          </div>
          <select id="vis-filter" className="filter-select" value={visFilter} onChange={e => setVisFilter(e.target.value)}>
            <option value="">All Visibility</option>
            <option value="public">Public</option>
            <option value="private">Private</option>
          </select>
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner"/></div> :
          filtered.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><HardDrive size={40}/></div>
              <h3>No images found</h3>
              <p>Upload an image using the button above or via CLI: <code>cloudx image upload --name NAME --file FILE</code></p>
              <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={() => setShowUpload(true)}>
                <Upload size={14}/> Upload Image
              </button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length===filtered.length && filtered.length>0}/></th>
                    <th>Owner</th>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Visibility</th>
                    <th>Protected</th>
                    <th>Disk Format</th>
                    <th>Size</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(i => (
                    <tr key={i.id} className={selected.includes(i.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(i.id)} onChange={() => toggleSelect(i.id)}/></td>
                      <td className="text-muted" style={{fontSize:12}}>{i.owner || i.project_id || '—'}</td>
                      <td>
                        <div className="name-cell">{i.name}</div>
                        <div className="mono text-muted" style={{fontSize:10}}>{i.id?.slice(0,16)}…</div>
                      </td>
                      <td className="text-muted">{i.container_format || '—'}</td>
                      <td><span className={statusClass(i.status)}>{i.status}</span></td>
                      <td><span className={`status-badge ${i.visibility==='public'?'status-active':'status-inactive'}`}>{i.visibility||'private'}</span></td>
                      <td><span className="status-badge status-inactive">No</span></td>
                      <td><span className="mono">{i.disk_format || '—'}</span></td>
                      <td>{fmtSize(i.size)}</td>
                      <td><ImageActions image={i} onDelete={deleteImage} onToggleActive={toggleActive} onToggleVisibility={toggleVisibility}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} onSuccess={load} />}
    </div>
  )
}
