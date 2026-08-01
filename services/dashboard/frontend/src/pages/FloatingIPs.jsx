import { useEffect, useState, useRef } from 'react'
import { Plus, Trash2, ChevronDown, ArrowLeftRight } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function FipActions({ fip, onAssociate, onDisassociate, onDelete }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const isAssoc = !!(fip.port_id || fip.fixed_ip_address)
  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => isAssoc ? onDisassociate(fip) : onAssociate(fip)}>
        {isAssoc ? 'Disassociate' : 'Associate'}
      </button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          <button className="dropdown-item danger" onClick={() => { setOpen(false); onDelete(fip.id) }}>Release Floating IP</button>
        </div>
      )}
    </div>
  )
}

export default function FloatingIPsPage() {
  const [fips, setFips]       = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [showAssoc, setShowAssoc]   = useState(null)
  const [networks, setNetworks]     = useState([])
  const [newFipNet, setNewFipNet]   = useState('')
  const [assocPort, setAssocPort]   = useState('')
  const [selected, setSelected]     = useState([])
  const toast = useToast()

  const load = () => {
    setLoading(true)
    Promise.all([api.getFloatingIPs(), api.getNetworks()])
      .then(([f, n]) => { setFips(f?.items || []); setNetworks(n?.items || []) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const createFip = async () => {
    if (!newFipNet) return
    try { await api.createFloatingIP({ network: newFipNet }); toast('Floating IP allocated', 'success'); setShowCreate(false); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteFip = async (id) => {
    if (!confirm('Release this floating IP?')) return
    try { await api.deleteFloatingIP(id); toast('Floating IP released', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const releaseSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Release ${selected.length} floating IP(s)?`)) return
    for (const id of selected) { try { await api.deleteFloatingIP(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} IP(s) released`, 'success'); load()
  }

  const associate = async () => {
    if (!assocPort || !showAssoc) return
    try {
      await api.associateFloatingIP(showAssoc.id, { port: assocPort })
      toast('Floating IP associated', 'success'); setShowAssoc(null); load()
    } catch (e) { toast(e.message, 'error') }
  }

  const disassociate = async (fip) => {
    try { await api.disassociateFloatingIP(fip.id); toast('Disassociated', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === fips.length ? [] : fips.map(x=>x.id))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Floating IPs</h1><p className="page-subtitle">{fips.length} floating IPs allocated</p></div>
        <div className="page-actions">
          <button id="allocate-fip-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Allocate IP To Project
          </button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={releaseSelected}>
              <Trash2 size={14} /> Release Floating IPs ({selected.length})
            </button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter floating IPs…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          fips.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><ArrowLeftRight size={40}/></div>
              <h3>No floating IPs</h3>
              <p>Allocate a floating IP from a public network pool</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Allocate IP To Project</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length === fips.length && fips.length > 0} /></th>
                    <th>IP Address</th>
                    <th>Description</th>
                    <th>DNS Name</th>
                    <th>DNS Domain</th>
                    <th>Mapped Fixed IP Address</th>
                    <th>Pool</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {fips.map(f => (
                    <tr key={f.id} className={selected.includes(f.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(f.id)} onChange={() => toggleSelect(f.id)} /></td>
                      <td><span className="mono name-cell">{f.floating_ip_address || f.floating_ip || '—'}</span></td>
                      <td className="text-muted">—</td>
                      <td className="text-muted">—</td>
                      <td className="text-muted">—</td>
                      <td><span className="mono">{f.fixed_ip_address || f.fixed_ip || '—'}</span></td>
                      <td className="text-muted">{f.floating_network_id ? f.floating_network_id.slice(0,8)+'…' : '—'}</td>
                      <td><span className={`status-badge ${(f.status === 'ACTIVE' || f.port_id) ? 'status-active' : 'status-inactive'}`}>{f.status || (f.port_id ? 'ACTIVE' : 'DOWN')}</span></td>
                      <td><FipActions fip={f} onAssociate={(fip) => { setShowAssoc(fip); setAssocPort('') }} onDisassociate={disassociate} onDelete={deleteFip} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {/* Allocate Modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowCreate(false)}>
          <div className="modal">
            <div className="modal-header">
              <span className="modal-title">Allocate Floating IP</span>
              <button className="modal-close btn" onClick={() => setShowCreate(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">Pool (External Network) *</label>
                <select id="fip-network" className="form-select" value={newFipNet} onChange={e => setNewFipNet(e.target.value)}>
                  <option value="">-- Select Pool --</option>
                  {networks.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
                </select>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              <button id="fip-allocate-submit" className="btn btn-primary" onClick={createFip}><Plus size={14} /> Allocate IP</button>
            </div>
          </div>
        </div>
      )}

      {/* Associate Modal */}
      {showAssoc && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowAssoc(null)}>
          <div className="modal">
            <div className="modal-header">
              <span className="modal-title">Manage Floating IP Associations</span>
              <button className="modal-close btn" onClick={() => setShowAssoc(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="alert alert-info">
                IP: <strong className="mono">{showAssoc.floating_ip_address || showAssoc.floating_ip}</strong>
              </div>
              <div className="form-group">
                <label className="form-label">Port or Server Name *</label>
                <input id="fip-assoc-port" className="form-input" placeholder="server-name or port-id" value={assocPort} onChange={e => setAssocPort(e.target.value)} />
                <p className="form-hint">Enter the server name or port name to associate</p>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowAssoc(null)}>Cancel</button>
              <button id="fip-assoc-submit" className="btn btn-primary" onClick={associate}>Associate</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
