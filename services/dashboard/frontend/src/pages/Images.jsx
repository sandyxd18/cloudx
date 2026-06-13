import { useEffect, useState } from 'react'
import { Trash2, Eye, EyeOff, RotateCcw } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function fmtSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function statusClass(s) {
  const m = { ACTIVE: 'active', deactivated: 'shutoff', error: 'error' }
  return 'status-badge status-' + (m[s?.toLowerCase()] || 'inactive')
}

export default function ImagesPage() {
  const [images, setImages]   = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch]   = useState('')
  const [visFilter, setVisFilter] = useState('')
  const toast = useToast()

  const load = () => { setLoading(true); api.getImages().then(r => setImages(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteImage = async (id, name) => {
    if (!confirm(`Delete image "${name}"?`)) return
    try { await api.deleteImage(id); toast('Image deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
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

  const filtered = images.filter(i =>
    i.name?.toLowerCase().includes(search.toLowerCase()) &&
    (!visFilter || i.visibility === visFilter)
  )

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Images</h1><p className="page-subtitle">{images.length} images total · Upload via CLI: cloudx image upload</p></div>
        <div className="page-actions">
          <button id="refresh-images" className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{ color: 'var(--text-muted)' }}>🔍</span>
          <input placeholder="Search images…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select id="vis-filter" className="filter-select" value={visFilter} onChange={e => setVisFilter(e.target.value)}>
          <option value="">All visibility</option>
          <option value="public">Public</option>
          <option value="private">Private</option>
        </select>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          filtered.length === 0 ? <div className="empty-state"><div className="empty-state-icon">💿</div><h3>No images found</h3><p>Upload images using: cloudx image upload --name NAME --file FILE</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Format</th><th>Size</th><th>Status</th><th>Visibility</th><th>Backend</th><th></th></tr></thead>
              <tbody>
                {filtered.map(i => (
                  <tr key={i.id}>
                    <td>
                      <div className="name-cell">{i.name}</div>
                      <div className="mono text-muted" style={{ fontSize: 10 }}>{i.id?.slice(0, 16)}…</div>
                    </td>
                    <td><span className="mono">{i.disk_format || '—'}</span></td>
                    <td>{fmtSize(i.size)}</td>
                    <td><span className={statusClass(i.status)}>{i.status}</span></td>
                    <td>
                      <span className={`status-badge ${i.visibility === 'public' ? 'status-active' : 'status-inactive'}`}>
                        {i.visibility || 'private'}
                      </span>
                    </td>
                    <td className="text-muted">{i.storage_backend || 'file'}</td>
                    <td>
                      <div className="table-actions">
                        <button className="btn btn-icon btn-sm" title={i.visibility === 'public' ? 'Make private' : 'Make public'} onClick={() => toggleVisibility(i)}>
                          {i.visibility === 'public' ? <EyeOff size={13} /> : <Eye size={13} />}
                        </button>
                        <button className="btn btn-icon btn-sm" title={i.status === 'ACTIVE' ? 'Deactivate' : 'Reactivate'} onClick={() => toggleActive(i)}>
                          <RotateCcw size={13} />
                        </button>
                        <button className="btn btn-icon btn-sm" id={`delete-image-${i.id}`} onClick={() => deleteImage(i.id, i.name)}><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
    </div>
  )
}
