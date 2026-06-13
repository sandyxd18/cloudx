import { useEffect, useState } from 'react'
import { Plus, Trash2, ChevronDown, ChevronRight } from 'lucide-react'
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
            <button id="sg-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Create</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function AddRuleModal({ sg, onClose, onCreate }) {
  const [form, setForm] = useState({ direction: 'ingress', protocol: 'tcp', port_min: '', port_max: '', remote_ip_prefix: '0.0.0.0/0' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await onCreate({
        direction: form.direction,
        protocol: form.protocol || null,
        port_range_min: form.port_min ? Number(form.port_min) : null,
        port_range_max: form.port_max ? Number(form.port_max) : null,
        remote_ip_prefix: form.remote_ip_prefix || null,
      })
      onClose()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header"><span className="modal-title">Add Rule to: {sg.name}</span><button className="modal-close btn" onClick={onClose}>✕</button></div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-row">
              <div className="form-group"><label className="form-label">Direction</label>
                <select id="rule-direction" className="form-select" value={form.direction} onChange={f('direction')}>
                  <option value="ingress">Ingress</option><option value="egress">Egress</option>
                </select></div>
              <div className="form-group"><label className="form-label">Protocol</label>
                <select id="rule-protocol" className="form-select" value={form.protocol} onChange={f('protocol')}>
                  <option value="tcp">TCP</option><option value="udp">UDP</option>
                  <option value="icmp">ICMP</option><option value="">Any</option>
                </select></div>
            </div>
            <div className="form-row">
              <div className="form-group"><label className="form-label">Port Min</label><input id="rule-port-min" type="number" min="1" max="65535" className="form-input" placeholder="22" value={form.port_min} onChange={f('port_min')} /></div>
              <div className="form-group"><label className="form-label">Port Max</label><input id="rule-port-max" type="number" min="1" max="65535" className="form-input" placeholder="22" value={form.port_max} onChange={f('port_max')} /></div>
            </div>
            <div className="form-group"><label className="form-label">Remote IP Prefix</label><input id="rule-cidr" className="form-input" placeholder="0.0.0.0/0" value={form.remote_ip_prefix} onChange={f('remote_ip_prefix')} /></div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="rule-create-submit" type="submit" className="btn btn-primary" disabled={loading}><Plus size={14} /> Add Rule</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function SecurityGroupsPage() {
  const [sgs, setSgs]         = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState({})
  const [showCreate, setShowCreate] = useState(false)
  const [addRuleSg, setAddRuleSg]   = useState(null)
  const toast = useToast()

  const load = () => { setLoading(true); api.getSecurityGroups().then(r => setSgs(r?.items || [])).finally(() => setLoading(false)) }
  useEffect(() => { load() }, [])

  const toggleExpand = id => setExpanded(e => ({ ...e, [id]: !e[id] }))

  const deleteSg = async (id, name) => {
    if (!confirm(`Delete security group "${name}"?`)) return
    try { await api.deleteSecurityGroup(id); toast('Security group deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const deleteRule = async (ruleId) => {
    try { await api.deleteSecurityGroupRule(ruleId); toast('Rule deleted', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div>
      <div className="page-header">
        <div><h1 className="page-title">Security Groups</h1><p className="page-subtitle">{sgs.length} security groups</p></div>
        <div className="page-actions">
          <button id="create-sg-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> Create Group</button>
        </div>
      </div>

      {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
        sgs.length === 0 ? <div className="empty-state"><div className="empty-state-icon">🛡️</div><h3>No security groups</h3></div> :
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {sgs.map(sg => (
            <div key={sg.id} className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 18px', cursor: 'pointer' }} onClick={() => toggleExpand(sg.id)}>
                {expanded[sg.id] ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                <span style={{ fontWeight: 600, flex: 1 }}>{sg.name}</span>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{sg.description}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)', margin: '0 8px' }}>{(sg.rules || []).length} rules</span>
                <button className="btn btn-primary btn-sm" onClick={e => { e.stopPropagation(); setAddRuleSg(sg) }}><Plus size={12} /> Rule</button>
                <button className="btn btn-icon btn-sm" id={`delete-sg-${sg.id}`} onClick={e => { e.stopPropagation(); deleteSg(sg.id, sg.name) }}><Trash2 size={13} /></button>
              </div>
              {expanded[sg.id] && (
                <div style={{ borderTop: '1px solid var(--border)', padding: '0 18px 14px' }}>
                  <table className="table" style={{ marginTop: 12 }}>
                    <thead><tr><th>Direction</th><th>Protocol</th><th>Port Range</th><th>Remote</th><th></th></tr></thead>
                    <tbody>
                      {(sg.rules || []).map(r => (
                        <tr key={r.id}>
                          <td><span className={`status-badge ${r.direction === 'ingress' ? 'status-active' : 'status-building'}`}>{r.direction}</span></td>
                          <td className="mono">{r.protocol || 'any'}</td>
                          <td className="mono">{r.port_range_min ? `${r.port_range_min}–${r.port_range_max}` : 'any'}</td>
                          <td className="mono">{r.remote_ip_prefix || r.remote_group_id || 'any'}</td>
                          <td><button className="btn btn-icon btn-sm" onClick={() => deleteRule(r.id)}><Trash2 size={12} /></button></td>
                        </tr>
                      ))}
                      {(sg.rules || []).length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 16 }}>No rules</td></tr>}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>}

      {showCreate && <CreateSGModal onClose={() => setShowCreate(false)} onCreate={async (b) => { await api.createSecurityGroup(b); load() }} />}
      {addRuleSg && <AddRuleModal sg={addRuleSg} onClose={() => setAddRuleSg(null)} onCreate={async (b) => { await api.createSecurityGroupRule(addRuleSg.id, b); toast('Rule added', 'success'); load() }} />}
    </div>
  )
}
