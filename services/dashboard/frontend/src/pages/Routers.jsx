import { useEffect, useState } from 'react'
import { Plus, Trash2, Globe } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateRouterModal({ onClose, onCreate }) {
  const [name, setName] = useState('')
  const [gwNet, setGwNet] = useState('')
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  useEffect(() => { api.getNetworks().then(r => setNetworks(r?.items || [])) }, [])

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate({ name, external_gateway_network: gwNet || undefined }); toast('Router created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Router</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group"><label className="form-label">Router Name *</label><input id="router-name" className="form-input" placeholder="my-router" value={name} onChange={e => setName(e.target.value)} required /></div>
            <div className="form-group">
              <label className="form-label">External Gateway Network</label>
              <select id="router-gw" className="form-select" value={gwNet} onChange={e => setGwNet(e.target.value)}>
                <option value="">None</option>
                {networks.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
              </select>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="router-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create Router</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function AddInterfaceModal({ router, onClose, onAdd }) {
  const [subnet, setSubnet] = useState('')
  const [subnets, setSubnets] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  useEffect(() => { api.getSubnets().then(r => setSubnets(r?.items || [])) }, [])

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onAdd({ subnet }); toast('Interface added', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Add Interface: {router.name}</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Subnet *</label>
              <select id="iface-subnet" className="form-select" value={subnet} onChange={e => setSubnet(e.target.value)} required>
                <option value="">-- Select Subnet --</option>
                {subnets.map(s => <option key={s.id} value={s.name}>{s.name} ({s.cidr})</option>)}
              </select>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="iface-add-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Add Interface</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function RoutersPage() {
  const [routers, setRouters] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [addIface, setAddIface] = useState(null)
  const toast = useToast()

  const load = () => { setLoading(true); api.getRouters().then(r => setRouters(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteRouter = async (id, name) => {
    if (!confirm(`Delete router "${name}"?`)) return
    try { await api.deleteRouter(id); toast('Router deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Routers</h1><p className="page-subtitle">{routers.length} routers</p></div>
        <div className="page-actions">
          <button id="create-router-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Router</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          routers.length === 0 ? <div className="empty-state"><div className="empty-state-icon">🔀</div><h3>No routers</h3><p>Create a router to connect networks</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Status</th><th>External Gateway</th><th>Interfaces</th><th></th></tr></thead>
              <tbody>
                {routers.map(r => (
                  <tr key={r.id}>
                    <td><div className="name-cell">{r.name}</div></td>
                    <td><span className={`status-badge status-${r.status === 'ACTIVE' ? 'active' : 'inactive'}`}>{r.status || 'active'}</span></td>
                    <td className="mono text-muted">{r.external_gateway_network || '—'}</td>
                    <td className="text-muted">{(r.interfaces || []).length} ports</td>
                    <td>
                      <div className="table-actions">
                        <button className="btn btn-icon btn-sm" title="Add interface" onClick={() => setAddIface(r)}><Globe size={13} /></button>
                        <button className="btn btn-icon btn-sm" id={`delete-router-${r.id}`} onClick={() => deleteRouter(r.id, r.name)}><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>
      {showCreate && <CreateRouterModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createRouter(form); load() }} />}
      {addIface && <AddInterfaceModal router={addIface} onClose={() => setAddIface(null)} onAdd={async (form) => { await api.addRouterInterface(addIface.id, form); load() }} />}
    </div>
  )
}
