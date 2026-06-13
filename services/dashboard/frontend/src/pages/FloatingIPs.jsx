import { useEffect, useState } from 'react'
import { Plus, Trash2, Link, Unlink } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

export default function FloatingIPsPage() {
  const [fips, setFips]       = useState([])
  const [ports, setPorts]     = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [showAssoc, setShowAssoc]   = useState(null)
  const [newFipNet, setNewFipNet]   = useState('')
  const [networks, setNetworks]     = useState([])
  const [assocPort, setAssocPort]   = useState('')
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

  const associate = async () => {
    if (!assocPort || !showAssoc) return
    try {
      await api.associateFloatingIP(showAssoc.id, { port: assocPort })
      toast('Floating IP associated', 'success'); setShowAssoc(null); load()
    } catch (e) { toast(e.message, 'error') }
  }

  const disassociate = async (fip) => {
    try { await api.disassociateFloatingIP(fip.id); toast('Floating IP disassociated', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Floating IPs</h1><p className="page-subtitle">{fips.length} floating IPs allocated</p></div>
        <div className="page-actions">
          <button id="allocate-fip-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Allocate IP</button>
        </div>
      </div>
      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          fips.length === 0 ? <div className="empty-state"><div className="empty-state-icon">🌍</div><h3>No floating IPs</h3><p>Allocate a floating IP from a public network</p></div> :
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Floating IP</th><th>Fixed IP</th><th>Status</th><th>Network</th><th></th></tr></thead>
              <tbody>
                {fips.map(f => (
                  <tr key={f.id}>
                    <td><span className="mono name-cell">{f.floating_ip_address || f.ip || '—'}</span></td>
                    <td><span className="mono">{f.fixed_ip_address || f.fixed_ip || '—'}</span></td>
                    <td><span className={`status-badge ${f.status?.toLowerCase() === 'active' || f.port_id ? 'status-active' : 'status-inactive'}`}>{f.status || (f.port_id ? 'associated' : 'available')}</span></td>
                    <td className="text-muted">{f.network_name || f.network || '—'}</td>
                    <td>
                      <div className="table-actions">
                        {f.port_id
                          ? <button className="btn btn-icon btn-sm" title="Disassociate" onClick={() => disassociate(f)}><Unlink size={13} /></button>
                          : <button className="btn btn-icon btn-sm" title="Associate" onClick={() => { setShowAssoc(f); setAssocPort('') }}><Link size={13} /></button>
                        }
                        <button className="btn btn-icon btn-sm" id={`delete-fip-${f.id}`} onClick={() => deleteFip(f.id)}><Trash2 size={13} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </div>

      {showCreate && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowCreate(false)}>
          <div className="modal">
            <div className="modal-header">
              <span className="modal-title">Allocate Floating IP</span>
              <button className="modal-close btn" onClick={() => setShowCreate(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">External Network *</label>
                <select id="fip-network" className="form-select" value={newFipNet} onChange={e => setNewFipNet(e.target.value)}>
                  <option value="">-- Select Network --</option>
                  {networks.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
                </select>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
              <button id="fip-allocate-submit" className="btn btn-primary" onClick={createFip}><Plus size={14} /> Allocate</button>
            </div>
          </div>
        </div>
      )}

      {showAssoc && (
        <div className="modal-overlay" onClick={e => e.target === e.currentTarget && setShowAssoc(null)}>
          <div className="modal">
            <div className="modal-header">
              <span className="modal-title">Associate Floating IP</span>
              <button className="modal-close btn" onClick={() => setShowAssoc(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="alert alert-info">
                Floating IP: <strong className="mono">{showAssoc.floating_ip_address || showAssoc.ip}</strong>
              </div>
              <div className="form-group">
                <label className="form-label">Server / Port Name *</label>
                <input id="fip-assoc-port" className="form-input" placeholder="server name or port ID" value={assocPort} onChange={e => setAssocPort(e.target.value)} />
                <p className="form-hint">Enter the server name or port ID to associate with</p>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowAssoc(null)}>Cancel</button>
              <button id="fip-assoc-submit" className="btn btn-primary" onClick={associate}><Link size={14} /> Associate</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
