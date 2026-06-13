import { useEffect, useState, useRef } from 'react'
import { Play, Square, RotateCcw, Trash2, Plus, MoreVertical, Terminal, PauseCircle, PowerOff, Wrench, KeyRound } from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function statusClass(s) {
  const m = { ACTIVE: 'active', SHUTOFF: 'shutoff', ERROR: 'error', BUILD: 'building', PAUSED: 'paused', SUSPENDED: 'suspended', RESCUE: 'rescue' }
  return 'status-badge status-' + (m[s] || 'inactive').toLowerCase()
}

function ActionsMenu({ server, onAction }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const actions = [
    { label: 'Start',    icon: <Play size={13} />,        action: 'start',    disabled: server.status === 'ACTIVE' },
    { label: 'Stop',     icon: <Square size={13} />,       action: 'stop',     disabled: server.status !== 'ACTIVE' },
    { label: 'Reboot',   icon: <RotateCcw size={13} />,   action: 'reboot',   disabled: server.status !== 'ACTIVE' },
    { label: 'Pause',    icon: <PauseCircle size={13} />, action: 'pause',    disabled: server.status !== 'ACTIVE' },
    { label: 'Resume',   icon: <Play size={13} />,        action: 'resume',   disabled: server.status !== 'PAUSED' },
    { label: 'Suspend',  icon: <PowerOff size={13} />,    action: 'suspend',  disabled: server.status !== 'ACTIVE' },
    { sep: true },
    { label: 'Console',  icon: <Terminal size={13} />,    action: 'console' },
    { label: 'Logs',     icon: <Terminal size={13} />,    action: 'logs' },
    { label: 'Rescue',   icon: <Wrench size={13} />,      action: 'rescue' },
    { sep: true },
    { label: 'Delete',   icon: <Trash2 size={13} />,      action: 'delete',   danger: true },
  ]

  return (
    <div className="dropdown" ref={ref}>
      <button className="btn-icon btn-sm" id={`server-menu-${server.id}`} onClick={() => setOpen(o => !o)}>
        <MoreVertical size={14} />
      </button>
      {open && (
        <div className="dropdown-menu">
          {actions.map((a, i) =>
            a.sep ? <div key={i} className="dropdown-sep" /> :
            <button
              key={a.action}
              className={`dropdown-item${a.danger ? ' danger' : ''}`}
              disabled={a.disabled}
              onClick={() => { setOpen(false); onAction(server, a.action) }}
              style={a.disabled ? { opacity: 0.35, pointerEvents: 'none' } : {}}
            >
              {a.icon}{a.label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function CreateServerModal({ onClose, onCreate }) {
  const [form, setForm] = useState({ name: '', flavor: '', image: '', keypair: '', network: '' })
  const [flavors, setFlavors]   = useState([])
  const [images, setImages]     = useState([])
  const [keypairs, setKeypairs] = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const toast = useToast()

  useEffect(() => {
    Promise.all([api.getFlavors(), api.getImages(), api.getKeypairs(), api.getNetworks()])
      .then(([f, im, k, n]) => {
        setFlavors(f?.items || [])
        setImages((im?.items || []).filter(i => i.status === 'ACTIVE'))
        setKeypairs(k?.items || [])
        setNetworks(n?.items || [])
      })
  }, [])

  const f = (field) => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      await onCreate(form)
      toast('Instance created!', 'success')
      onClose()
    } catch(err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Launch New Instance</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Instance Name *</label>
              <input id="server-name" className="form-input" placeholder="my-server-01" value={form.name} onChange={f('name')} required />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Flavor *</label>
                <select id="server-flavor" className="form-select" value={form.flavor} onChange={f('flavor')} required>
                  <option value="">-- Select Flavor --</option>
                  {flavors.map(fl => (
                    <option key={fl.id} value={fl.name}>{fl.name} ({fl.vcpus}vCPU / {fl.ram_mb}MB)</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Image *</label>
                <select id="server-image" className="form-select" value={form.image} onChange={f('image')} required>
                  <option value="">-- Select Image --</option>
                  {images.map(im => (
                    <option key={im.id} value={im.name}>{im.name} ({im.disk_format})</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Network *</label>
                <select id="server-network" className="form-select" value={form.network} onChange={f('network')} required>
                  <option value="">-- Select Network --</option>
                  {networks.map(n => (
                    <option key={n.id} value={n.name}>{n.name}</option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Key Pair</label>
                <select id="server-keypair" className="form-select" value={form.keypair} onChange={f('keypair')}>
                  <option value="">-- No Key Pair --</option>
                  {keypairs.map(k => (
                    <option key={k.name} value={k.name}>{k.name}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button id="server-create-submit" type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Plus size={14} />}
              {loading ? 'Launching…' : 'Launch Instance'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function LogsModal({ server, onClose }) {
  const [logs, setLogs]   = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getServerLogs(server.id)
      .then(r => setLogs(r?.output || '(no output)'))
      .catch(e => setLogs('Error: ' + e.message))
      .finally(() => setLoading(false))
  }, [server.id])

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Console Logs — {server.name}</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
            <div className="console-output">{logs || '(empty)'}</div>}
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

export default function ServersPage() {
  const [servers, setServers]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [search, setSearch]     = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [showLogs, setShowLogs] = useState(null)
  const toast = useToast()

  const load = () => {
    setLoading(true)
    api.getServers()
      .then(r => setServers(r?.items || []))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleAction = async (server, action) => {
    if (action === 'delete') {
      if (!confirm(`Delete instance "${server.name}"?`)) return
      try {
        await api.deleteServer(server.id)
        toast('Instance deleted', 'success')
        load()
      } catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'logs') { setShowLogs(server); return }
    if (action === 'console') {
      toast('Connect via: cloudx server console ' + server.name, 'info')
      return
    }
    try {
      await api.serverAction(server.id, action)
      toast(`Action "${action}" sent`, 'success')
      setTimeout(load, 1500)
    } catch (e) { toast(e.message, 'error') }
  }

  const handleCreate = async (form) => {
    await api.createServer({
      name: form.name,
      flavor: form.flavor,
      image: form.image,
      network: form.network,
      keypair: form.keypair || undefined,
    })
    load()
  }

  const filtered = servers.filter(s =>
    s.name?.toLowerCase().includes(search.toLowerCase()) ||
    s.status?.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Instances</h1>
          <p className="page-subtitle">{servers.length} instances total</p>
        </div>
        <div className="page-actions">
          <button id="refresh-servers" className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
          <button id="create-server-btn" className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={14} /> Launch Instance
          </button>
        </div>
      </div>

      <div className="toolbar">
        <div className="search-box">
          <span style={{ color: 'var(--text-muted)' }}>🔍</span>
          <input placeholder="Search instances…" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
      </div>

      <div className="card">
        {loading ? (
          <div className="loading-overlay"><span className="spinner" /></div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🖥️</div>
            <h3>No instances found</h3>
            <p>Launch your first instance to get started</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Flavor</th>
                  <th>IP Address</th>
                  <th>Image</th>
                  <th>Created</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(s => (
                  <tr key={s.id}>
                    <td>
                      <div className="name-cell">{s.name}</div>
                      <div className="mono text-muted" style={{ fontSize: 10 }}>{s.id}</div>
                    </td>
                    <td><span className={statusClass(s.status)}>{s.status}</span></td>
                    <td>{s.flavor_name || s.flavor || '—'}</td>
                    <td className="mono">{s.ip_address || s.fixed_ip || '—'}</td>
                    <td>{s.image_name || s.image || '—'}</td>
                    <td className="text-muted">{s.created_at ? new Date(s.created_at).toLocaleDateString() : '—'}</td>
                    <td><div className="table-actions"><ActionsMenu server={s} onAction={handleAction} /></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && <CreateServerModal onClose={() => setShowCreate(false)} onCreate={handleCreate} />}
      {showLogs   && <LogsModal server={showLogs} onClose={() => setShowLogs(null)} />}
    </div>
  )
}
