import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Server, HardDrive, Network, Tag, Play, Square,
  RotateCcw, Trash2, Terminal, RefreshCw, Clock, Cpu, MemoryStick,
  Globe, Shield, Key, CheckCircle, XCircle, Activity
} from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function statusBadge(s) {
  const m = { ACTIVE: 'active', SHUTOFF: 'shutoff', ERROR: 'error', BUILD: 'building', PAUSED: 'paused' }
  return 'status-badge status-' + (m[s] || 'inactive').toLowerCase()
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

function DetailCard({ title, icon: Icon, children }) {
  return (
    <div style={{
      background: 'var(--card-bg, #1e2736)',
      border: '1px solid var(--border-color, rgba(255,255,255,0.08))',
      borderRadius: 12,
      padding: '20px 24px',
      marginBottom: 20
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, color: 'var(--text-primary, #fff)' }}>
        <Icon size={18} style={{ color: '#6c8ebf' }} />
        <span style={{ fontWeight: 600, fontSize: 15 }}>{title}</span>
      </div>
      {children}
    </div>
  )
}

function InfoRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{label}</span>
      <span style={{ color: 'rgba(255,255,255,0.9)', fontSize: 13, fontFamily: typeof value === 'string' && value.includes('-') ? 'monospace' : 'inherit', maxWidth: '60%', textAlign: 'right', wordBreak: 'break-all' }}>
        {value ?? '—'}
      </span>
    </div>
  )
}

export default function ServerDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { addToast } = useToast()
  const [server, setServer] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const [logs, setLogs] = useState('')
  const [showLogs, setShowLogs] = useState(false)

  const load = async () => {
    try {
      setLoading(true)
      const data = await api.getServer(id)
      setServer(data)
    } catch(e) {
      addToast('Failed to load server: ' + (e.message || 'Unknown error'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])

  const doAction = async (fn, label) => {
    setActionLoading(true)
    try {
      await fn()
      addToast(`${label} initiated`, 'success')
      setTimeout(load, 1500)
    } catch(e) {
      addToast(`Failed: ${e.message}`, 'error')
    } finally {
      setActionLoading(false)
    }
  }

  const loadLogs = async () => {
    try {
      const res = await api.getServerLogs(id)
      setLogs(res.output || 'No logs available')
      setShowLogs(true)
    } catch(e) {
      addToast('Could not load logs: ' + e.message, 'error')
    }
  }

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300 }}>
      <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite', color: '#6c8ebf' }} />
    </div>
  )
  if (!server) return <div style={{ color: 'rgba(255,255,255,0.5)', textAlign: 'center', marginTop: 80 }}>Server not found</div>

  const addresses = server.addresses || {}
  const allIPs = Object.values(addresses).flat()

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <button
          onClick={() => navigate('/servers')}
          style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '6px 12px', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
        >
          <ArrowLeft size={14} /> Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Server size={22} style={{ color: '#6c8ebf' }} />
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#fff' }}>{server.name}</h1>
            <span className={statusBadge(server.status)}>{server.status}</span>
          </div>
          <p style={{ margin: '4px 0 0 34px', color: 'rgba(255,255,255,0.4)', fontSize: 13, fontFamily: 'monospace' }}>{server.id}</p>
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 8 }}>
          {server.status === 'SHUTOFF' && (
            <button className="btn-primary" style={{ padding: '7px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
              disabled={actionLoading} onClick={() => doAction(() => api.startServer(id), 'Start')}>
              <Play size={14} /> Start
            </button>
          )}
          {server.status === 'ACTIVE' && (
            <>
              <button className="btn-danger" style={{ padding: '7px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
                disabled={actionLoading} onClick={() => doAction(() => api.stopServer(id), 'Stop')}>
                <Square size={14} /> Stop
              </button>
              <button className="btn-secondary" style={{ padding: '7px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
                disabled={actionLoading} onClick={() => doAction(() => api.rebootServer(id), 'Reboot')}>
                <RotateCcw size={14} /> Reboot
              </button>
              <Link to={`/console/${id}`} style={{ textDecoration: 'none' }}>
                <button className="btn-secondary" style={{ padding: '7px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Terminal size={14} /> Console
                </button>
              </Link>
            </>
          )}
          <button className="btn-secondary" style={{ padding: '7px 14px', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
            onClick={loadLogs}>
            <Activity size={14} /> Logs
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Overview */}
        <DetailCard title="Overview" icon={Server}>
          <InfoRow label="Status" value={server.status} />
          <InfoRow label="Task State" value={server.task_state || 'None'} />
          <InfoRow label="Image" value={server.image_name || server.image_id || '—'} />
          <InfoRow label="Flavor" value={`${server.flavor_name || server.flavor_id} (${server.vcpus || '?'} vCPU, ${server.ram_mb ? Math.round(server.ram_mb/1024) + ' GB RAM' : '?'})`} />
          <InfoRow label="Created" value={server.created_at ? new Date(server.created_at).toLocaleString() : '—'} />
          <InfoRow label="Age" value={timeAgo(server.created_at)} />
          <InfoRow label="Project" value={server.project_id} />
          <InfoRow label="User" value={server.user_id} />
        </DetailCard>

        {/* Network */}
        <DetailCard title="Network" icon={Network}>
          {allIPs.length === 0 ? (
            <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13 }}>No network addresses</p>
          ) : allIPs.map((addr, i) => (
            <InfoRow key={i} label={`${addr.version === 6 ? 'IPv6' : 'IPv4'} (${Object.keys(addresses)[0] || 'net'})`} value={addr.addr} />
          ))}
          {server.floating_ip && <InfoRow label="Floating IP" value={server.floating_ip} />}
          {server.port_id && <InfoRow label="Port ID" value={server.port_id} />}
        </DetailCard>

        {/* Resources */}
        <DetailCard title="Resources" icon={Cpu}>
          <InfoRow label="vCPUs" value={server.vcpus} />
          <InfoRow label="RAM" value={server.ram_mb ? `${server.ram_mb} MB (${Math.round(server.ram_mb/1024)} GB)` : '—'} />
          <InfoRow label="Disk" value={server.disk_gb ? `${server.disk_gb} GB` : '—'} />
          <InfoRow label="Hypervisor" value={server.hypervisor_hostname || 'localhost'} />
          <InfoRow label="Availability Zone" value={server.availability_zone || 'nova'} />
          <InfoRow label="Host" value={server.host || '—'} />
        </DetailCard>

        {/* Security & Access */}
        <DetailCard title="Security & Access" icon={Shield}>
          {server.key_name && <InfoRow label="Key Pair" value={server.key_name} />}
          {server.security_groups?.length > 0 && (
            <div style={{ padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
              <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>Security Groups</span>
              <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {server.security_groups.map(sg => (
                  <span key={sg} style={{ background: 'rgba(108,142,191,0.15)', border: '1px solid rgba(108,142,191,0.3)', borderRadius: 6, padding: '2px 8px', fontSize: 12, color: '#6c8ebf' }}>{sg}</span>
                ))}
              </div>
            </div>
          )}
          <InfoRow label="Instance UUID" value={server.instance_uuid || server.id} />
        </DetailCard>

        {/* Metadata / Tags */}
        {(server.tags?.length > 0 || server.metadata) && (
          <DetailCard title="Tags & Metadata" icon={Tag}>
            {server.tags?.length > 0 && (
              <div style={{ padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>Tags</span>
                <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {server.tags.map(t => (
                    <span key={t} style={{ background: 'rgba(80,200,120,0.12)', border: '1px solid rgba(80,200,120,0.25)', borderRadius: 6, padding: '2px 8px', fontSize: 12, color: '#50c878' }}>{t}</span>
                  ))}
                </div>
              </div>
            )}
          </DetailCard>
        )}
      </div>

      {/* Console Logs Modal */}
      {showLogs && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 999, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowLogs(false)}>
          <div style={{ background: '#0d1117', border: '1px solid rgba(255,255,255,0.12)', borderRadius: 12, width: '80%', maxHeight: '70vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: '#fff', fontWeight: 600 }}>Console Output — {server.name}</span>
              <button onClick={() => setShowLogs(false)} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.5)', cursor: 'pointer', fontSize: 18 }}>✕</button>
            </div>
            <pre style={{ margin: 0, padding: '16px 20px', overflowY: 'auto', color: '#c8d3f0', fontSize: 12, lineHeight: 1.6, fontFamily: 'monospace', flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {logs}
            </pre>
          </div>
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
