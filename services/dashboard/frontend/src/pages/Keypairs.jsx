import { useEffect, useState } from 'react'
import { Plus, Trash2, Copy } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', public_key: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate(form); toast('Key pair created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">Import Key Pair</span>
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
              <label className="form-label">Public Key *</label>
              <textarea id="keypair-pubkey" className="form-textarea" placeholder="ssh-rsa AAAA..." style={{ height: 120, fontFamily: 'monospace', fontSize: 12 }} value={form.public_key} onChange={e => setForm(v=>({...v,public_key:e.target.value}))} required />
              <p className="form-hint">Paste your SSH public key (id_rsa.pub)</p>
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
  const toast = useToast()

  const load = () => { setLoading(true); api.getKeypairs().then(r => setKeypairs(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const handleDelete = async (name) => {
    if (!confirm(`Delete key pair "${name}"?`)) return
    try { await api.deleteKeypair(name); toast('Key pair deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const copyKey = (key) => { navigator.clipboard.writeText(key); toast('Public key copied!', 'success') }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Key Pairs</h1><p className="page-subtitle">{keypairs.length} key pairs</p></div>
        <div className="page-actions">
          <button id="create-keypair-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Import Key Pair</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          keypairs.length === 0 ? <div className="empty-state"><div className="empty-state-icon">🔑</div><h3>No key pairs</h3><p>Import an SSH public key to use with instances</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Fingerprint</th><th>Type</th><th></th></tr></thead>
              <tbody>
                {keypairs.map(k => (
                  <tr key={k.name}>
                    <td><div className="name-cell">{k.name}</div></td>
                    <td><span className="mono">{k.fingerprint || '—'}</span></td>
                    <td>{k.key_type || 'ssh'}</td>
                    <td>
                      <div className="table-actions">
                        {k.public_key && <button className="btn btn-icon btn-sm" onClick={() => copyKey(k.public_key)} title="Copy public key"><Copy size={13} /></button>}
                        <button className="btn btn-icon btn-sm" id={`delete-keypair-${k.name}`} onClick={() => handleDelete(k.name)} title="Delete"><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createKeypair(form); load() }} />}
    </div>
  )
}
