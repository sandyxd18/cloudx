import { useEffect, useState } from 'react'
import { Plus, Trash2, Copy, Key } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateKeyPairModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', public_key: '', type: 'ssh' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate(form); toast('Key pair imported', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Import Public Key</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Key Pair Name *</label>
              <input id="keypair-name" className="form-input" placeholder="my-key" value={form.name} onChange={e => setForm(v=>({...v,name:e.target.value}))} required />
            </div>
            <div className="form-group">
              <label className="form-label">Key Type</label>
              <select className="form-select" value={form.type} onChange={e => setForm(v=>({...v,type:e.target.value}))}>
                <option value="ssh">SSH Key</option>
                <option value="x509">X.509 Certificate</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Public Key *</label>
              <textarea
                id="keypair-pubkey" className="form-textarea"
                placeholder="ssh-rsa AAAA..."
                style={{ height: 120, fontFamily: 'monospace', fontSize: 12 }}
                value={form.public_key}
                onChange={e => setForm(v=>({...v,public_key:e.target.value}))}
                required
              />
              <p className="form-hint">Paste your SSH public key content (id_rsa.pub or id_ed25519.pub)</p>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="keypair-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14} />}
              Import Key Pair
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function KeypairsPage() {
  const [keypairs, setKeypairs] = useState([])
  const [loading, setLoading]   = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => { setLoading(true); api.getKeypairs().then(r => setKeypairs(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const handleDelete = async (name) => {
    if (!confirm(`Delete key pair "${name}"?`)) return
    try { await api.deleteKeypair(name); toast('Key pair deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} key pair(s)?`)) return
    for (const name of selected) { try { await api.deleteKeypair(name) } catch(e) {} }
    setSelected([]); toast(`${selected.length} key pair(s) deleted`, 'success'); load()
  }

  const copyKey = (key) => { navigator.clipboard.writeText(key); toast('Public key copied!', 'success') }

  const toggleSelect = name => setSelected(s => s.includes(name) ? s.filter(x=>x!==name) : [...s, name])
  const toggleAll = () => setSelected(s => s.length === keypairs.length ? [] : keypairs.map(x=>x.name))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Key Pairs</h1><p className="page-subtitle">{keypairs.length} key pairs</p></div>
        <div className="page-actions">
          <button id="create-keypair-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Import Public Key
          </button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Key Pairs ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter key pairs…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          keypairs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Key size={40}/></div>
              <h3>No key pairs</h3>
              <p>Import an SSH public key to use with instances</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Import Public Key</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length===keypairs.length && keypairs.length>0}/></th>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Fingerprint</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {keypairs.map(k => (
                    <tr key={k.name} className={selected.includes(k.name) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(k.name)} onChange={() => toggleSelect(k.name)}/></td>
                      <td><div className="name-cell">{k.name}</div></td>
                      <td><span className="status-badge status-inactive">{k.key_type || 'ssh'}</span></td>
                      <td><span className="mono" style={{fontSize:11}}>{k.fingerprint || '—'}</span></td>
                      <td>
                        <div className="table-actions">
                          {k.public_key && (
                            <button className="btn btn-icon btn-sm" onClick={() => copyKey(k.public_key)} title="Copy public key"><Copy size={13}/></button>
                          )}
                          <button className="btn btn-icon btn-sm" id={`delete-keypair-${k.name}`} onClick={() => handleDelete(k.name)} title="Delete Key Pair"><Trash2 size={13}/></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
      {showCreate && <CreateKeyPairModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createKeypair(form); load() }} />}
    </div>
  )
}
