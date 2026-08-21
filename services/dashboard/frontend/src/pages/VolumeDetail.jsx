import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, HardDrive, RefreshCw, Trash2, Expand, Camera,
  Server, Link2, LinkIcon, Unlink, Info, BarChart2, Database
} from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function statusColor(s) {
  const m = { available: '#50c878', in_use: '#6c8ebf', error: '#ef4444', creating: '#f59e0b', deleting: '#f59e0b', deleted: '#64748b' }
  return m[(s || '').toLowerCase()] || '#94a3b8'
}

function InfoRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{label}</span>
      <span style={{ color: 'rgba(255,255,255,0.9)', fontSize: 13, maxWidth: '60%', textAlign: 'right', wordBreak: 'break-all' }}>
        {value ?? '—'}
      </span>
    </div>
  )
}

function DetailCard({ title, icon: Icon, children, accent }) {
  return (
    <div style={{
      background: 'var(--card-bg, #1e2736)',
      border: `1px solid ${accent ? accent + '30' : 'rgba(255,255,255,0.08)'}`,
      borderRadius: 12,
      padding: '20px 24px',
      marginBottom: 20,
      borderLeft: accent ? `3px solid ${accent}` : undefined
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, color: '#fff' }}>
        <Icon size={18} style={{ color: accent || '#6c8ebf' }} />
        <span style={{ fontWeight: 600, fontSize: 15 }}>{title}</span>
      </div>
      {children}
    </div>
  )
}

export default function VolumeDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { addToast } = useToast()
  const [volume, setVolume] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [loading, setLoading] = useState(true)

  const load = async () => {
    try {
      setLoading(true)
      const [vol, snaps] = await Promise.allSettled([
        api.getVolume(id),
        api.listSnapshots(),
      ])
      if (vol.status === 'fulfilled') setVolume(vol.value)
      if (snaps.status === 'fulfilled') {
        const items = snaps.value?.items || snaps.value || []
        setSnapshots(items.filter(s => s.volume_id === id || s.source_volid === id))
      }
    } catch(e) {
      addToast('Failed to load volume', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])

  const doDelete = async () => {
    if (!confirm(`Delete volume "${volume?.name}"? This is irreversible.`)) return
    try {
      await api.deleteVolume(id)
      addToast('Volume deleted', 'success')
      navigate('/volumes')
    } catch(e) {
      addToast('Delete failed: ' + e.message, 'error')
    }
  }

  const doSnapshot = async () => {
    const name = prompt('Snapshot name:', `${volume?.name}-snap-${Date.now()}`)
    if (!name) return
    try {
      await api.createSnapshot({ volume_id: id, name, force: volume?.status === 'in-use' })
      addToast('Snapshot created', 'success')
      load()
    } catch(e) {
      addToast('Snapshot failed: ' + e.message, 'error')
    }
  }

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300 }}>
      <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite', color: '#6c8ebf' }} />
    </div>
  )
  if (!volume) return <div style={{ color: 'rgba(255,255,255,0.5)', textAlign: 'center', marginTop: 80 }}>Volume not found</div>

  const statusC = statusColor(volume.status)
  const attachments = volume.attachments || (volume.server_id ? [{ server_id: volume.server_id, device: volume.attached_to || '/dev/vdb' }] : [])

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <button onClick={() => navigate('/volumes')}
          style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '6px 12px', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <HardDrive size={22} style={{ color: '#6c8ebf' }} />
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#fff' }}>{volume.name}</h1>
            <span style={{ background: statusC + '20', color: statusC, border: `1px solid ${statusC}40`, borderRadius: 6, padding: '2px 10px', fontSize: 12, fontWeight: 600 }}>
              {volume.status}
            </span>
          </div>
          <p style={{ margin: '4px 0 0 34px', color: 'rgba(255,255,255,0.4)', fontSize: 13, fontFamily: 'monospace' }}>{volume.id}</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={doSnapshot}
            style={{ background: 'rgba(108,142,191,0.15)', border: '1px solid rgba(108,142,191,0.3)', borderRadius: 8, padding: '7px 14px', color: '#6c8ebf', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <Camera size={14} /> Snapshot
          </button>
          {volume.status === 'available' && (
            <button onClick={doDelete}
              style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: '7px 14px', color: '#ef4444', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <Trash2 size={14} /> Delete
            </button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Details */}
        <DetailCard title="Volume Details" icon={HardDrive}>
          <InfoRow label="Size" value={`${volume.size} GB`} />
          <InfoRow label="Status" value={volume.status} />
          <InfoRow label="Type" value={volume.volume_type || 'default'} />
          <InfoRow label="Bootable" value={volume.bootable ? 'Yes' : 'No'} />
          <InfoRow label="Encrypted" value={volume.encrypted ? 'Yes' : 'No'} />
          <InfoRow label="Created" value={volume.created_at ? new Date(volume.created_at).toLocaleString() : '—'} />
          <InfoRow label="Project" value={volume.project_id} />
          <InfoRow label="Availability Zone" value={volume.availability_zone || 'nova'} />
        </DetailCard>

        {/* Backend Info */}
        <DetailCard title="Storage Backend" icon={Database} accent="#8b5cf6">
          <InfoRow label="Backend Driver" value={volume.backend || 'ceph'} />
          <InfoRow label="Pool" value={volume.pool || 'volumes'} />
          <InfoRow label="Source" value={volume.source_volid ? 'Clone of ' + volume.source_volid.slice(0, 8) + '...' : volume.snapshot_id ? 'From snapshot' : 'Blank'} />
          {volume.max_iops && <InfoRow label="QoS IOPS Limit" value={volume.max_iops.toLocaleString()} />}
          {volume.max_throughput && <InfoRow label="QoS Throughput Limit" value={`${volume.max_throughput} MB/s`} />}
        </DetailCard>

        {/* Attachments */}
        <DetailCard title="Attachments" icon={Link2} accent={attachments.length > 0 ? '#50c878' : undefined}>
          {attachments.length === 0 ? (
            <div style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Unlink size={14} /> Not attached to any server
            </div>
          ) : attachments.map((att, i) => (
            <div key={i} style={{ padding: '10px', background: 'rgba(80,200,120,0.06)', borderRadius: 8, marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#50c878', marginBottom: 6 }}>
                <Server size={14} />
                <span style={{ fontSize: 13, fontWeight: 600 }}>Attached to Server</span>
              </div>
              <InfoRow label="Server ID" value={att.server_id} />
              {att.device && <InfoRow label="Device" value={att.device} />}
            </div>
          ))}
        </DetailCard>

        {/* Snapshots */}
        <DetailCard title={`Snapshots (${snapshots.length})`} icon={Camera}>
          {snapshots.length === 0 ? (
            <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13 }}>No snapshots for this volume</p>
          ) : snapshots.map(snap => (
            <div key={snap.id} style={{ padding: '8px 10px', background: 'rgba(255,255,255,0.04)', borderRadius: 8, marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#fff', fontSize: 13, fontWeight: 600 }}>{snap.name}</span>
                <span style={{ fontSize: 12, color: snap.status === 'available' ? '#50c878' : '#f59e0b' }}>{snap.status}</span>
              </div>
              <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: 12, marginTop: 4, fontFamily: 'monospace' }}>{snap.id}</div>
            </div>
          ))}
        </DetailCard>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
