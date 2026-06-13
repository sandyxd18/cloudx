import { useEffect, useState } from 'react'
import { Plus, Trash2, ChevronDown } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateNetworkModal({ onClose, onCreated }) {
  const [tab, setTab] = useState('network')
  const [netForm, setNetForm] = useState({ name: '', provider_type: 'flat', shared: false })
  const [subForm, setSubForm] = useState({ name: '', network: '', cidr: '', gateway_ip: '', dns: '8.8.8.8,8.8.4.4' })
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  useEffect(() => { api.getNetworks().then(r => setNetworks(r?.items || [])) }, [])

  const handleNet = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await api.createNetwork({ name: netForm.name, provider_type: netForm.provider_type, shared: netForm.shared })
      toast('Network created', 'success'); onCreated()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const handleSub = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await api.createSubnet({
        name: subForm.name,
        network: subForm.network,
        cidr: subForm.cidr,
        gateway_ip: subForm.gateway_ip || undefined,
        dns_nameservers: subForm.dns ? subForm.dns.split(',').map(s => s.trim()) : [],
      })
      toast('Subnet created', 'success'); onCreated()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Create Network / Subnet</span>
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
                <input id="network-name" className="form-input" placeholder="my-network" value={netForm.name} onChange={e=>setNetForm(v=>({...v,name:e.target.value}))} required />
              </div>
              <div className="form-group">
                <label className="form-label">Provider Type</label>
                <select className="form-select" value={netForm.provider_type} onChange={e=>setNetForm(v=>({...v,provider_type:e.target.value}))}>
                  <option value="flat">flat</option>
                  <option value="vlan">vlan</option>
                  <option value="local">local</option>
                </select>
              </div>
              <div className="form-group" style={{display:'flex',alignItems:'center',gap:10}}>
                <input type="checkbox" id="network-shared" checked={netForm.shared} onChange={e=>setNetForm(v=>({...v,shared:e.target.checked}))} />
                <label htmlFor="network-shared" className="form-label" style={{margin:0,textTransform:'none',letterSpacing:0}}>Shared network</label>
              </div>
              <div className="modal-footer" style={{padding:'0',marginTop:8}}>
                <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                <button id="network-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14} />} Create Network
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
                  <label className="form-label">CIDR *</label>
                  <input id="subnet-cidr" className="form-input" placeholder="192.168.10.0/24" value={subForm.cidr} onChange={e=>setSubForm(v=>({...v,cidr:e.target.value}))} required />
                </div>
                <div className="form-group">
                  <label className="form-label">Gateway IP</label>
                  <input id="subnet-gw" className="form-input" placeholder="192.168.10.1" value={subForm.gateway_ip} onChange={e=>setSubForm(v=>({...v,gateway_ip:e.target.value}))} />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">DNS Nameservers</label>
                <input id="subnet-dns" className="form-input" placeholder="8.8.8.8,8.8.4.4" value={subForm.dns} onChange={e=>setSubForm(v=>({...v,dns:e.target.value}))} />
              </div>
              <div className="modal-footer" style={{padding:'0',marginTop:8}}>
                <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                <button id="subnet-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14} />} Create Subnet
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}

export default function NetworksPage() {
  const [networks, setNetworks] = useState([])
  const [subnets, setSubnets]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [showCreate, setShowCreate] = useState(false)
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

  const deleteSubnet = async (id, name) => {
    if (!confirm(`Delete subnet "${name}"?`)) return
    try { await api.deleteSubnet(id); toast('Subnet deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Networks</h1><p className="page-subtitle">{networks.length} networks · {subnets.length} subnets</p></div>
        <div className="page-actions">
          <button id="create-network-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create</button>
        </div>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20}}>
        <div className="card">
          <div className="card-header"><span className="card-title">Networks</span></div>
          {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
            networks.length === 0 ? <div className="empty-state"><div className="empty-state-icon">🌐</div><h3>No networks</h3></div> :
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Name</th><th>Type</th><th>Shared</th><th></th></tr></thead>
                <tbody>
                  {networks.map(n=>(
                    <tr key={n.id}>
                      <td><div className="name-cell">{n.name}</div><div className="mono text-muted" style={{fontSize:10}}>{n.id?.slice(0,16)}…</div></td>
                      <td><span className="mono">{n.provider_type || 'flat'}</span></td>
                      <td><span className={`status-badge ${n.shared ? 'status-active' : 'status-inactive'}`}>{n.shared ? 'shared' : 'private'}</span></td>
                      <td><button className="btn btn-icon btn-sm" id={`delete-net-${n.id}`} onClick={()=>deleteNetwork(n.id, n.name)}><Trash2 size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>}
        </div>
        <div className="card">
          <div className="card-header"><span className="card-title">Subnets</span></div>
          {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
            subnets.length === 0 ? <div className="empty-state"><div className="empty-state-icon">📡</div><h3>No subnets</h3></div> :
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Name</th><th>CIDR</th><th>Gateway</th><th></th></tr></thead>
                <tbody>
                  {subnets.map(s=>(
                    <tr key={s.id}>
                      <td><div className="name-cell">{s.name}</div></td>
                      <td><span className="mono">{s.cidr}</span></td>
                      <td><span className="mono">{s.gateway_ip || '—'}</span></td>
                      <td><button className="btn btn-icon btn-sm" id={`delete-subnet-${s.id}`} onClick={()=>deleteSubnet(s.id, s.name)}><Trash2 size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>}
        </div>
      </div>
      {showCreate && <CreateNetworkModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); load() }} />}
    </div>
  )
}
