import { useEffect, useState } from 'react'
import { Plus, Trash2, Shield } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function CreateSGModal({ onClose, onCreate }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try { await onCreate({ name, description: desc }); toast('Security group created', 'success'); onClose() }
    catch (err) { setError(err.message) } finally { setLoading(false) }
  }
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Create Security Group</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group"><label className="form-label">Name *</label><input id="sg-name" className="form-input" placeholder="my-sg" value={name} onChange={e => setName(e.target.value)} required /></div>
            <div className="form-group"><label className="form-label">Description</label><input id="sg-desc" className="form-input" placeholder="My security group" value={desc} onChange={e => setDesc(e.target.value)} /></div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="sg-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create Security Group</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ManageRulesModal({ sg, onClose, onAddRule, onDeleteRule }) {
  const [form, setForm] = useState({ direction: 'ingress', protocol: 'tcp', port_min: '', port_max: '', remote_ip_prefix: '0.0.0.0/0' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await onAddRule({
        direction: form.direction,
        protocol: form.protocol || 'any',
        port_range_min: form.port_min ? Number(form.port_min) : null,
        port_range_max: form.port_max ? Number(form.port_max) : null,
        remote_ip_prefix: form.remote_ip_prefix || null,
      })
      setForm({ direction: 'ingress', protocol: 'tcp', port_min: '', port_max: '', remote_ip_prefix: '0.0.0.0/0' })
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header"><span className="modal-title">Manage Rules — {sg.name}</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <div className="modal-body">
          {/* Existing rules */}
          <div style={{marginBottom:20}}>
            <div style={{fontWeight:600,marginBottom:8,fontSize:13}}>Existing Rules</div>
            <table className="table">
              <thead><tr><th>Direction</th><th>Ether Type</th><th>IP Protocol</th><th>Port Range</th><th>Remote IP Prefix</th><th></th></tr></thead>
              <tbody>
                {(sg.rules || []).map(r => (
                  <tr key={r.id}>
                    <td><span className={`status-badge ${r.direction==='ingress'?'status-active':'status-building'}`}>{r.direction}</span></td>
                    <td className="mono">{r.ethertype || 'IPv4'}</td>
                    <td className="mono">{r.protocol || 'any'}</td>
                    <td className="mono">{r.port_range_min != null ? `${r.port_range_min} – ${r.port_range_max}` : 'Any'}</td>
                    <td className="mono">{r.remote_ip_prefix || 'any'}</td>
                    <td><button className="btn btn-icon btn-sm" onClick={() => onDeleteRule(r.id)}><Trash2 size={12}/></button></td>
                  </tr>
                ))}
                {(sg.rules||[]).length===0 && <tr><td colSpan={6} style={{textAlign:'center',color:'var(--text-muted)',padding:16}}>No rules</td></tr>}
              </tbody>
            </table>
          </div>
          {/* Add rule form */}
          <div style={{fontWeight:600,marginBottom:8,fontSize:13}}>Add Rule</div>
          {error && <div className="alert alert-error">{error}</div>}
          <form onSubmit={handleSubmit}>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Direction</label>
                <select id="rule-direction" className="form-select" value={form.direction} onChange={f('direction')}>
                  <option value="ingress">Ingress</option><option value="egress">Egress</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Protocol</label>
                <select id="rule-protocol" className="form-select" value={form.protocol} onChange={f('protocol')}>
                  <option value="tcp">TCP</option><option value="udp">UDP</option>
                  <option value="icmp">ICMP</option><option value="">Any</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Port Min</label>
                <input id="rule-port-min" type="number" min="1" max="65535" className="form-input" placeholder="22" value={form.port_min} onChange={f('port_min')} />
              </div>
              <div className="form-group">
                <label className="form-label">Port Max</label>
                <input id="rule-port-max" type="number" min="1" max="65535" className="form-input" placeholder="22" value={form.port_max} onChange={f('port_max')} />
              </div>
            </div>
            <div className="form-group">
              <label className="form-label">Remote IP Prefix</label>
              <input id="rule-cidr" className="form-input" placeholder="0.0.0.0/0" value={form.remote_ip_prefix} onChange={f('remote_ip_prefix')} />
            </div>
            <div style={{display:'flex',justifyContent:'flex-end',gap:8,marginTop:8}}>
              <button type="button" className="btn btn-secondary" onClick={onClose}>Close</button>
              <button id="rule-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14}/> Add Rule</button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}

export default function SecurityGroupsPage() {
  const [sgs, setSgs]         = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [manageRules, setManageRules] = useState(null)
  const [selected, setSelected] = useState([])
  const toast = useToast()

  const load = () => {
    setLoading(true)
    // Load each SG with its rules
    api.getSecurityGroups().then(async r => {
      const groups = r?.items || []
      const withRules = await Promise.all(groups.map(async sg => {
        try {
          const detail = await api.getSecurityGroup(sg.id)
          return { ...sg, rules: detail?.security_group_rules || detail?.rules || [] }
        } catch { return { ...sg, rules: [] } }
      }))
      setSgs(withRules)
    }).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const deleteSg = async (id, name) => {
    if (!confirm(`Delete security group "${name}"?`)) return
    try { await api.deleteSecurityGroup(id); toast('Security group deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} security group(s)?`)) return
    for (const id of selected) { try { await api.deleteSecurityGroup(id) } catch(e) {} }
    setSelected([]); toast(`${selected.length} group(s) deleted`, 'success'); load()
  }

  const addRule = async (sgId, ruleData) => {
    await api.createSecurityGroupRule(sgId, ruleData)
    toast('Rule added', 'success')
    load()
    const updated = await api.getSecurityGroup(sgId)
    setManageRules(prev => ({ ...prev, rules: updated?.security_group_rules || updated?.rules || [] }))
  }

  const deleteRule = async (ruleId, sgId) => {
    await api.deleteSecurityGroupRule(ruleId)
    toast('Rule deleted', 'success')
    const updated = await api.getSecurityGroup(sgId)
    setManageRules(prev => ({ ...prev, rules: updated?.security_group_rules || updated?.rules || [] }))
    load()
  }

  const toggleSelect = id => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === sgs.length ? [] : sgs.map(x=>x.id))

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Security Groups</h1><p className="page-subtitle">{sgs.length} security groups</p></div>
        <div className="page-actions">
          <button id="create-sg-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Security Group</button>
          {selected.length > 0 && (
            <button className="btn btn-danger" onClick={deleteSelected}><Trash2 size={14}/> Delete Security Groups ({selected.length})</button>
          )}
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{color:'var(--text-muted)'}}>🔍</span>
          <input placeholder="Filter security groups…" />
        </div>
      </div>

      <div className="card">
        {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
          sgs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><Shield size={40}/></div>
              <h3>No security groups</h3>
              <p>Create a security group to manage access rules</p>
              <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Create Security Group</button>
            </div>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{width:32}}><input type="checkbox" onChange={toggleAll} checked={selected.length === sgs.length && sgs.length > 0} /></th>
                    <th>Name</th>
                    <th>Security Group ID</th>
                    <th>Description</th>
                    <th>Shared</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sgs.map(sg => (
                    <tr key={sg.id} className={selected.includes(sg.id) ? 'row-selected' : ''}>
                      <td><input type="checkbox" checked={selected.includes(sg.id)} onChange={() => toggleSelect(sg.id)} /></td>
                      <td>
                        <div className="name-cell">{sg.name}</div>
                        <div style={{fontSize:11,color:'var(--text-muted)'}}>{(sg.rules||[]).length} rules</div>
                      </td>
                      <td><span className="mono" style={{fontSize:11}}>{sg.id?.slice(0,16)}…</span></td>
                      <td className="text-muted">{sg.description || '—'}</td>
                      <td><span className="status-badge status-inactive">No</span></td>
                      <td>
                        <button className="btn btn-sm btn-hz-primary" onClick={() => setManageRules(sg)}>
                          Manage Rules
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {showCreate && <CreateSGModal onClose={() => setShowCreate(false)} onCreate={async (b) => { await api.createSecurityGroup(b); load() }} />}
      {manageRules && (
        <ManageRulesModal
          sg={manageRules}
          onClose={() => setManageRules(null)}
          onAddRule={async (b) => await addRule(manageRules.id, b)}
          onDeleteRule={async (rid) => await deleteRule(rid, manageRules.id)}
        />
      )}
    </div>
  )
}
