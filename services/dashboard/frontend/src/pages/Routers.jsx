import { useEffect, useState, useRef } from 'react'
import { Plus, Trash2, ChevronDown, Globe } from 'lucide-react'
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
              <label className="form-label">External Network</label>
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
        <div className="modal-header"><span className="modal-title">Add Interface — {router.name}</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
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

function RouterActions({ router, onDelete, onAddInterface, onSetGateway }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const hasGw = !!(router.external_network_id || router.external_gateway_network)
  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => onSetGateway(router)}>
        {hasGw ? 'Clear Gateway' : 'Set Gateway'}
      </button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item" onClick={() => { setOpen(false); onAddInterface(router) }}>Add Interface</button>
          <div className="dropdown-sep" />
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(router.id, router.name) }}>Delete Router</button>
        </div>
      )}
    </div>
  )
}

export default function RoutersPage() {
  const [routers, setRouters] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [addIface, setAddIface] = useState(null)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => { setLoading(true); api.getRouters().then(r => setRouters(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const deleteRouter = async (id, name) => {
    if (!confirm(`Delete router "${name}"?`)) return
    try { await api.deleteRouter(id); toast('Router deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} router(s)?`)) return
    for (const id of selected) { try { await api.deleteRouter(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} router(s) deleted`, 'success'); load()
  }

  const handleSetGateway = (router) => {
    toast('Use CLI: cloudx router set-gateway ' + router.name + ' <network>', 'info')
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === routers.length ? [] : routers.map(x=>x.id))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Routers</h1><p className="page-subtitle">{routers.length} routers</p></div>
        <div className="page-actions">
          <button id="create-router-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Router</button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14} /> Delete Routers ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter routers…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          routers.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Globe size={40}/></div>
              <h3>No routers</h3>
              <p>Create a router to connect networks</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Router</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length === routers.length && routers.length > 0} /></th>
                    <th>Name</th>
                    <th>Status</th>
                    <th>External Network</th>
                    <th>Admin State</th>
                    <th>Availability Zone</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {routers.map(r => (
                    <tr key={r.id} className={selected.includes(r.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(r.id)} onChange={() => toggleSelect(r.id)} /></td>
                      <td>
                        <div className="name-cell">{r.name}</div>
                        <div className="mono text-muted" style={{fontSize:10}}>{r.id?.slice(0,16)}…</div>
                      </td>
                      <td><span className={`status-badge status-${(r.status||'ACTIVE')==='ACTIVE'?'active':'inactive'}`}>{r.status || 'ACTIVE'}</span></td>
                      <td className="mono">{r.external_gateway_info?.network_id ? (r.external_gateway_network || 'external') : <span className="text-muted">—</span>}</td>
                      <td><span className={`status-badge ${r.admin_state_up !== false ? 'status-active' : 'status-inactive'}`}>{r.admin_state_up !== false ? 'UP' : 'DOWN'}</span></td>
                      <td className="text-muted">nova</td>
                      <td><RouterActions router={r} onDelete={deleteRouter} onAddInterface={setAddIface} onSetGateway={handleSetGateway} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {showCreate && <CreateRouterModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createRouter(form); load() }} />}
      {addIface   && <AddInterfaceModal router={addIface} onClose={() => setAddIface(null)} onAdd={async (form) => { await api.addRouterInterface(addIface.id, form); load() }} />}
    </div>
  )
}
