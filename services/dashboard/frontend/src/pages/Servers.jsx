import { useEffect, useState, useRef } from 'react'
import {
  Play, Square, RotateCcw, Trash2, Plus, MoreVertical, Terminal,
  PauseCircle, PowerOff, Wrench, Camera, RefreshCw, ChevronDown,
  Shield, HardDrive, Link, Unlink, Lock, Unlock, Server, Monitor,
  Archive, ArchiveRestore, CheckCircle, XCircle
} from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import useEventStream from '../hooks/useEventStream.js'

function statusBadge(s) {
  const m = {
    ACTIVE: 'active', SHUTOFF: 'shutoff', ERROR: 'error', BUILD: 'building',
    PAUSED: 'paused', SUSPENDED: 'suspended', RESCUE: 'rescue',
    STARTING: 'building', STOPPING: 'building', REBOOT: 'building',
    PAUSING: 'building', RESUMING: 'building', SUSPENDING: 'building',
    DELETING: 'building', REBUILD: 'building', RESIZE: 'building',
    UNRESCUING: 'building', SHELVING: 'building', UNSHELVING: 'building',
    SHELVED_OFFLOADED: 'inactive', VERIFY_RESIZE: 'paused',
  }
  return 'status-badge status-' + (m[s] || 'inactive').toLowerCase()
}

function powerState(s) {
  if (s === 'ACTIVE') return 'Running'
  if (s === 'SHUTOFF') return 'Shutdown'
  if (s === 'PAUSED') return 'Paused'
  if (s === 'SUSPENDED') return 'Suspended'
  return '—'
}

function taskState(s) {
  const tasks = { BUILD: 'Spawning', STARTING: 'Powering On', STOPPING: 'Powering Off',
    REBOOT: 'Rebooting', PAUSING: 'Pausing', RESUMING: 'Resuming',
    SUSPENDING: 'Suspending', DELETING: 'Deleting', RESCUE: 'Rescuing',
    REBUILD: 'Rebuilding', RESIZE: 'Resizing', UNRESCUING: 'Unrescuing' }
  return tasks[s] || '—'
}

function timeAgo(dt) {
  if (!dt) return '—'
  const d = new Date(dt), now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`
  return `${Math.floor(diff/86400)}d ago`
}

// ── Actions dropdown ──────────────────────────────────────────────────────────
function ActionsDropdown({ server, onAction }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])

  const isActive  = server.status === 'ACTIVE'
  const isShutoff    = server.status === 'SHUTOFF'
  const isPaused     = server.status === 'PAUSED'
  const isShelved    = server.status === 'SHELVED_OFFLOADED'
  const isVerifyResize = server.status === 'VERIFY_RESIZE' || server.status === 'RESIZE'

  const items = [
    { label: 'Create Snapshot', icon: <Camera size={13}/>, action: 'snapshot', primary: true },
    { sep: true },
    { label: 'Open Console',   icon: <Monitor size={13}/>,     action: 'open-console', disabled: !isActive },
    { label: 'View Log',       icon: <Terminal size={13}/>,    action: 'logs' },
    { sep: true },
    { label: 'Associate Floating IP', icon: <Link size={13}/>,   action: 'associate-fip' },
    { label: 'Attach Volume',  icon: <HardDrive size={13}/>, action: 'attach-vol' },
    { label: 'Detach Volume',  icon: <Unlink size={13}/>,    action: 'detach-vol' },
    { label: 'Edit Security Groups', icon: <Shield size={13}/>, action: 'edit-sg' },
    { sep: true },
    { label: 'Rescue VM',      icon: <Wrench size={13}/>,       action: 'rescue',       disabled: !isActive },
    { label: 'Pause VM',       icon: <PauseCircle size={13}/>,  action: 'pause',        disabled: !isActive },
    { label: 'Resume VM',      icon: <Play size={13}/>,         action: 'resume',       disabled: !isPaused },
    { label: 'Suspend VM',     icon: <PowerOff size={13}/>,     action: 'suspend',      disabled: !isActive },
    { sep: true },
    { label: 'Shelve VM',      icon: <Archive size={13}/>,      action: 'shelve',       disabled: !isActive && !isShutoff },
    { label: 'Unshelve VM',    icon: <ArchiveRestore size={13}/>, action: 'unshelve',   disabled: !isShelved },
    { sep: true },
    { label: 'Confirm Resize', icon: <CheckCircle size={13}/>,  action: 'confirmResize', disabled: !isVerifyResize },
    { label: 'Revert Resize',  icon: <XCircle size={13}/>,      action: 'revertResize',  disabled: !isVerifyResize },
    { sep: true },
    { label: 'Soft Reboot VM', icon: <RotateCcw size={13}/>,    action: 'reboot',       disabled: !isActive },
    { label: 'Hard Reboot VM', icon: <RefreshCw size={13}/>,    action: 'hard_reboot',  disabled: !isActive },
    { label: 'Shut Off VM',    icon: <Square size={13}/>,       action: 'stop',         disabled: !isActive },
    { label: 'Start VM',       icon: <Play size={13}/>,         action: 'start',        disabled: !isShutoff },
    { label: 'Lock VM',        icon: <Lock size={13}/>,         action: 'lock' },
    { sep: true },
    { label: 'Delete VM',      icon: <Trash2 size={13}/>,       action: 'delete',       danger: true },
  ]

  return (
    <div className="hz-action-group" ref={ref}>
      <button className="btn btn-sm btn-hz-primary" onClick={() => onAction(server, items[0].action)}>
        {items[0].icon} {items[0].label}
      </button>
      <button className="btn btn-sm btn-hz-dropdown" onClick={() => setOpen(o => !o)}>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="dropdown-menu dropdown-menu-right">
          {items.slice(1).map((a, i) =>
            a.sep ? <div key={i} className="dropdown-sep" /> :
            <button
              key={a.action}
              className={`dropdown-item${a.danger ? ' danger' : ''}`}
              disabled={a.disabled}
              style={a.disabled ? { opacity: 0.35, pointerEvents: 'none' } : {}}
              onClick={() => { setOpen(false); onAction(server, a.action) }}
            >
              {a.icon} {a.label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── noVNC Console Modal ──────────────────────────────────────────────────────
function ConsoleModal({ server, onClose }) {
  const [url, setUrl] = useState(null)
  const [err, setErr] = useState('')
  const toast = useToast()

  useEffect(() => {
    api.getRemoteConsole(server.id)
      .then(data => {
        const consoleUrl = data?.remote_console?.url
        if (consoleUrl) setUrl(consoleUrl)
        else setErr('No console URL returned')
      })
      .catch(e => setErr(e.message))
  }, [server.id])

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width: '90vw', maxWidth: 1100, height: '85vh', display: 'flex', flexDirection: 'column' }}>
        <div className="modal-header">
          <span className="modal-title"><Monitor size={15} style={{marginRight:6}}/>Console — {server.name}</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <div style={{ flex: 1, background: '#000', borderRadius: '0 0 8px 8px', overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {err ? (
            <div style={{ color: '#f87171', textAlign: 'center', padding: 24 }}>
              <Monitor size={40} style={{ opacity: 0.4, marginBottom: 12 }} />
              <p style={{ marginBottom: 8 }}>Console unavailable</p>
              <p style={{ fontSize: 12, opacity: 0.7 }}>{err}</p>
              <p style={{ fontSize: 12, opacity: 0.5, marginTop: 12 }}>Make sure the VM is ACTIVE and novnc-proxy is running.</p>
            </div>
          ) : !url ? (
            <div style={{ color: '#9ca3af' }}><span className="spinner" /> Loading console…</div>
          ) : (
            <iframe
              src={url}
              title={`Console — ${server.name}`}
              style={{ width: '100%', height: '100%', border: 'none' }}
              allow="fullscreen"
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Create Instance Modal ─────────────────────────────────────────────────────
function LaunchModal({ onClose, onCreate }) {
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

  const f = field => e => setForm(v => ({ ...v, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault(); setLoading(true); setError('')
    try {
      await onCreate(form)
      toast('Instance launched!', 'success')
      onClose()
    } catch(err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Launch Instance</span>
          <button className="modal-close btn" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="alert alert-error">{error}</div>}
            <div className="form-group">
              <label className="form-label">Instance Name *</label>
              <input id="server-name" className="form-input" placeholder="my-instance" value={form.name} onChange={f('name')} required />
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
              {loading ? <span className="spinner" style={{width:14,height:14}} /> : <Play size={14} />}
              {loading ? 'Launching…' : 'Launch Instance'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── View Log Modal ────────────────────────────────────────────────────────────
function LogsModal({ server, onClose }) {
  const [logs, setLogs]       = useState('')
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const fetchLogs = () =>
    api.getServerLogs(server.id)
      .then(r => setLogs(r?.output || '(no output)'))
      .catch(e => setLogs('Error: ' + e.message))
      .finally(() => setLoading(false))

  useEffect(() => {
    fetchLogs()
  }, [server.id])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(fetchLogs, 2000)
    return () => clearInterval(id)
  }, [server.id, autoRefresh])

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Console Log — {server.name}</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 12, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={e => setAutoRefresh(e.target.checked)}
                style={{ cursor: 'pointer' }}
              />
              Auto-refresh (2s)
            </label>
            <button className="modal-close btn" onClick={onClose}>✕</button>
          </div>
        </div>
        <div className="modal-body">
          {loading ? <div className="loading-overlay"><span className="spinner" /></div> :
            <div className="console-output">{logs || '(empty)'}</div>}
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={fetchLogs}>↻ Refresh Now</button>
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}


// ── Main Page ─────────────────────────────────────────────────────────────────
export default function ServersPage() {
  const [servers, setServers]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [search, setSearch]     = useState('')
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [showLogs, setShowLogs] = useState(null)
  const [showConsole, setShowConsole] = useState(null)
  const toast = useToast()
  useEventStream('/api/compute/events', load)

  const load = () => {
    setLoading(true)
    api.getServers().then(r => setServers(r?.items || [])).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  async function handleAction(server, action) {
    if (action === 'delete') {
      if (!confirm(`Delete instance "${server.name}"?`)) return
      try { await api.deleteServer(server.id); toast('Instance deleted', 'success'); load() }
      catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'logs')         { setShowLogs(server); return }
    if (action === 'open-console') { setShowConsole(server); return }
    if (action === 'snapshot')     { toast('Snapshot feature coming soon', 'info'); return }
    if (action === 'associate-fip'){ toast('Use CLI: cloudx floating-ip associate <ip> --server ' + server.name, 'info'); return }
    if (action === 'edit-sg')      { toast('Use CLI: cloudx server edit-sg ' + server.name, 'info'); return }
    if (action === 'attach-vol')   { toast('Use CLI: cloudx volume attach --server ' + server.name, 'info'); return }
    if (action === 'detach-vol')   { toast('Use CLI: cloudx volume detach --server ' + server.name, 'info'); return }
    if (action === 'shelve') {
      try { await api.shelveServer(server.id); toast('Shelving…', 'success'); setTimeout(load, 2000) } catch(e) { toast(e.message, 'error') }
      return
    }
    if (action === 'unshelve') {
      try { await api.unshelveServer(server.id); toast('Unshelving…', 'success'); setTimeout(load, 2000) } catch(e) { toast(e.message, 'error') }
      return
    }
    if (action === 'confirmResize') {
      try { await api.confirmResize(server.id); toast('Resize confirmed', 'success'); setTimeout(load, 1500) } catch(e) { toast(e.message, 'error') }
      return
    }
    if (action === 'revertResize') {
      try { await api.revertResize(server.id); toast('Resize reverted', 'success'); setTimeout(load, 1500) } catch(e) { toast(e.message, 'error') }
      return
    }
    try {
      await api.serverAction(server.id, action)
      toast(`Action "${action}" sent`, 'success')
      setTimeout(load, 1500)
    } catch (e) { toast(e.message, 'error') }
  }

  const handleDeleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} instance(s)?`)) return
    for (const id of selected) {
      try { await api.deleteServer(id) } catch(e) {}
    }
    setSelected([])
    toast(`${selected.length} instance(s) deleted`, 'success')
    load()
  }

  const toggleSelect = (id) => setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  const toggleAll = () => setSelected(s => s.length === filtered.length ? [] : filtered.map(x=>x.id))

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

      {/* Toolbar */}
      <div className="toolbar">
        <div className="toolbar-left">
          <div className="search-box">
            <span style={{ color: 'var(--text-muted)' }}>🔍</span>
            <input placeholder="Filter instances…" value={search} onChange={e => setSearch(e.target.value)} />
          </div>
        </div>
        <div className="toolbar-right">
          {selected.length > 0 && (
            <button className="btn btn-danger btn-sm" onClick={handleDeleteSelected}>
              <Trash2 size={13} /> Delete Instances ({selected.length})
            </button>
          )}
        </div>
      </div>

      <div className="card">
        {loading ? (
          <div className="loading-overlay"><span className="spinner" /></div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon"><Server size={40} /></div>
            <h3>No instances found</h3>
            <p>Launch your first instance to get started</p>
            <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14}/> Launch Instance</button>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th style={{width:32}}>
                    <input type="checkbox" onChange={toggleAll} checked={selected.length === filtered.length && filtered.length > 0} />
                  </th>
                  <th>Instance Name</th>
                  <th>Image Name</th>
                  <th>IP Address</th>
                  <th>Flavor</th>
                  <th>Key Pair</th>
                  <th>Status</th>
                  <th>Availability Zone</th>
                  <th>Task</th>
                  <th>Power State</th>
                  <th>Age</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(s => (
                  <tr key={s.id} className={selected.includes(s.id) ? 'row-selected' : ''}>
                    <td><input type="checkbox" checked={selected.includes(s.id)} onChange={() => toggleSelect(s.id)} /></td>
                    <td>
                      <div className="name-cell">{s.name}</div>
                      <div className="mono text-muted" style={{fontSize:10}}>{s.id?.slice(0,16)}…</div>
                    </td>
                    <td className="text-muted">{s.image_name || s.image || '—'}</td>
                    <td className="mono">{s.ip_address || s.fixed_ip || '—'}</td>
                    <td>{s.flavor_name || s.flavor || '—'}</td>
                    <td>{s.keypair || '—'}</td>
                    <td><span className={statusBadge(s.status)}>{s.status}</span></td>
                    <td className="text-muted">nova</td>
                    <td className="text-muted">{taskState(s.status)}</td>
                    <td className="text-muted">{powerState(s.status)}</td>
                    <td className="text-muted">{timeAgo(s.created_at)}</td>
                    <td>
                      <ActionsDropdown server={s} onAction={handleAction} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate  && <LaunchModal onClose={() => setShowCreate(false)} onCreate={async (form) => { await api.createServer({ name: form.name, flavor: form.flavor, image: form.image, network: form.network, keypair: form.keypair || undefined }); load() }} />}
      {showLogs    && <LogsModal server={showLogs} onClose={() => setShowLogs(null)} />}
      {showConsole && <ConsoleModal server={showConsole} onClose={() => setShowConsole(null)} />}
    </div>
  )
}
