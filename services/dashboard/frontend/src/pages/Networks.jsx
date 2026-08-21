import { useEffect, useState, useRef } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import { Plus, Trash2, ChevronDown, Network, Globe } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

// ── Create Network Modal ──────────────────────────────────────────────────────
function CreateNetworkModal({ onClose, onCreated }) {
  const [tab, setTab] = useState('network')
  const [netForm, setNetForm] = useState({ name: '', external: false })
  const [subForm, setSubForm] = useState({ name: '', network: '', cidr: '', gateway_ip: '', dns: '8.8.8.8,8.8.4.4', allocation_start: '', allocation_end: '' })
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => { api.getNetworks().then(r => setNetworks(r?.items || [])) }, [])

  const handleNet = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await api.createNetwork({ name: netForm.name, external: netForm.external })
      toast('Network created', 'success'); onCreated()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const handleSub = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await api.createSubnet({
        name: subForm.name, network: subForm.network, cidr: subForm.cidr,
        gateway_ip: subForm.gateway_ip || undefined,
        allocation_start: subForm.allocation_start || undefined,
        allocation_end: subForm.allocation_end || undefined,
        dns_nameservers: subForm.dns ? subForm.dns.split(',').map(s => s.trim()) : [],
      })
      toast('Subnet created', 'success'); onCreated()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Create Network</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <div className="tabs">
            <button className={`tab-btn ${tab === 'network' ? 'active' : ''}`} onClick={() => setTab('network')}>Network</button>
            <button className={`tab-btn ${tab === 'subnet' ? 'active' : ''}`} onClick={() => setTab('subnet')}>Subnet</button>
          </div>
          {error && <div className="alert alert-error">{error}</div>}
          {tab === 'network' ? (
            <form id="network-form" onSubmit={handleNet}>
              <div className="form-group">
                <label className="form-label">Name *</label>
                <input id="network-name" className="form-input" placeholder="my-network" value={netForm.name} onChange={e => setNetForm(v=>({...v,name:e.target.value}))} required />
              </div>
              <div className="form-group" style={{display:'flex',alignItems:'center',gap:10}}>
                <input type="checkbox" id="network-external" checked={netForm.external} onChange={e => setNetForm(v=>({...v,external:e.target.checked}))} />
                <label htmlFor="network-external" className="form-label" style={{margin:0,textTransform:'none',letterSpacing:0}}>External Network</label>
              </div>
              <div className="modal-footer" style={{padding:'0',marginTop:12}}>
                <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                <button id="network-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14}/>} Create Network
                </button>
              </div>
            </form>
          ) : (
            <form id="subnet-form" onSubmit={handleSub}>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Subnet Name *</label>
                  <input id="subnet-name" className="form-input" placeholder="my-subnet" value={subForm.name} onChange={e=>setSubForm(v=>({...v,name:e.target.value}))} required />
                </div>
                <div className="form-group">
                  <label className="form-label">Network *</label>
                  <select id="subnet-network" className="form-select" value={subForm.network} onChange={e=>setSubForm(v=>({...v,network:e.target.value}))} required>
                    <option value="">-- Select Network --</option>
                    {networks.map(n=><option key={n.id} value={n.name}>{n.name}</option>)}
                  </select>
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Network Address (CIDR) *</label>
                  <input id="subnet-cidr" className="form-input" placeholder="192.168.10.0/24" value={subForm.cidr} onChange={e=>setSubForm(v=>({...v,cidr:e.target.value}))} required />
                </div>
                <div className="form-group">
                  <label className="form-label">Gateway IP</label>
                  <input id="subnet-gw" className="form-input" placeholder="192.168.10.1" value={subForm.gateway_ip} onChange={e=>setSubForm(v=>({...v,gateway_ip:e.target.value}))} />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">Allocation Start</label>
                  <input className="form-input" placeholder="192.168.10.10" value={subForm.allocation_start} onChange={e=>setSubForm(v=>({...v,allocation_start:e.target.value}))} />
                </div>
                <div className="form-group">
                  <label className="form-label">Allocation End</label>
                  <input className="form-input" placeholder="192.168.10.200" value={subForm.allocation_end} onChange={e=>setSubForm(v=>({...v,allocation_end:e.target.value}))} />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">DNS Nameservers</label>
                <input id="subnet-dns" className="form-input" placeholder="8.8.8.8,8.8.4.4" value={subForm.dns} onChange={e=>setSubForm(v=>({...v,dns:e.target.value}))} />
              </div>
              <div className="modal-footer" style={{padding:'0',marginTop:12}}>
                <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                <button id="subnet-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14}/>} Create Subnet
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Network Actions Dropdown ───────────────────────────────────────────────────
function NetworkActions({ network, subnets, onDelete, onCreateSubnet }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => onCreateSubnet(network)}>
        Edit Network
      </button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item" onClick={() => { setOpen(false); onCreateSubnet(network) }}>Create Subnet</button>
          <div className="dropdown-sep" />
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(network.id, network.name) }}>Delete Network</button>
        </div>
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function NetworksPage() {
  const [networks, setNetworks] = useState([])
  const [subnets, setSubnets]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.all([api.getNetworks(), api.getSubnets()])
      .then(([n, s]) => { setNetworks(n?.items || []); setSubnets(s?.items || []) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const deleteNetwork = async (id, name) => {
    if (!confirm(`Delete network "${name}"?`)) return
    try { await api.deleteNetwork(id); toast('Network deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} network(s)?`)) return
    for (const id of selected) { try { await api.deleteNetwork(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} network(s) deleted`, 'success'); load()
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === networks.length ? [] : networks.map(x=>x.id))

  // Get subnets for a network
  const getSubnets = (netId) => subnets.filter(s => s.network_id === netId)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Networks</h1>
          <p className="page-subtitle">{networks.length} networks · {subnets.length} subnets</p>
        </div>
        <div className="page-actions">
          <button id="create-network-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Create Network
          </button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}>
              <Trash2 size={14} /> Delete Networks ({selected.length})
            </button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter networks…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          networks.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Network size={40}/></div>
              <h3>No networks</h3>
              <p>Create a network to get started</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Network</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length === networks.length && networks.length > 0} /></th>
                    <th>Name</th>
                    <th>Subnets Associated</th>
                    <th>Shared</th>
                    <th>External</th>
                    <th>Status</th>
                    <th>Admin State</th>
                    <th>Availability Zone</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {networks.map(n => (
                    <tr key={n.id} className={selected.includes(n.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(n.id)} onChange={() => toggleSelect(n.id)} /></td>
                      <td>
                        <RouterLink
                          to={`/networks/${n.id}`}
                          style={{ textDecoration: 'none', color: 'inherit' }}
                        >
                          <div className="name-cell" style={{ color: '#7eaadb', cursor: 'pointer' }}>{n.name}</div>
                          <div className="mono text-muted" style={{fontSize:10}}>{n.id?.slice(0,16)}…</div>
                        </RouterLink>
                      </td>
                      <td>
                        {getSubnets(n.id).map(s => (
                          <div key={s.id} className="mono" style={{fontSize:12}}>{s.name} {s.cidr}</div>
                        ))}
                        {getSubnets(n.id).length === 0 && <span className="text-muted">—</span>}
                      </td>
                      <td><span className={`status-badge ${n.shared ? 'status-active' : 'status-inactive'}`}>{n.shared ? 'Yes' : 'No'}</span></td>
                      <td><span className={`status-badge ${(n.external || n['router:external'] === 'External') ? 'status-active' : 'status-inactive'}`}>{(n.external || n['router:external'] === 'External') ? 'Yes' : 'No'}</span></td>
                      <td><span className="status-badge status-active">{n.status || 'ACTIVE'}</span></td>
                      <td><span className={`status-badge ${n.admin_state_up !== false ? 'status-active' : 'status-inactive'}`}>{n.admin_state_up !== false ? 'UP' : 'DOWN'}</span></td>
                      <td className="text-muted">nova</td>
                      <td><NetworkActions network={n} subnets={subnets} onDelete={deleteNetwork} onCreateSubnet={() => setShowCreate(true)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {showCreate && <CreateNetworkModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); load() }} />}
    </div>
  )
}
