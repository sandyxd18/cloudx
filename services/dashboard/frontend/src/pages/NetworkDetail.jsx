import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Network, Globe, Router, Layers, Server, RefreshCw,
  Trash2, Shield, Activity, ChevronDown, ChevronRight
} from 'lucide-react'
import { api } from '../api.js'
import { useToast } from '../context.jsx'

function InfoRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{label}</span>
      <span style={{ color: 'rgba(255,255,255,0.9)', fontSize: 13, maxWidth: '65%', textAlign: 'right', wordBreak: 'break-all', fontFamily: 'monospace' }}>
        {value ?? '—'}
      </span>
    </div>
  )
}

function DetailCard({ title, icon: Icon, children, accent, badge }) {
  return (
    <div style={{ background: 'var(--card-bg, #1e2736)', border: `1px solid ${accent ? accent + '30' : 'rgba(255,255,255,0.08)'}`, borderRadius: 12, padding: '20px 24px', marginBottom: 20, borderLeft: accent ? `3px solid ${accent}` : undefined }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <Icon size={18} style={{ color: accent || '#6c8ebf' }} />
        <span style={{ fontWeight: 600, fontSize: 15, color: '#fff' }}>{title}</span>
        {badge !== undefined && <span style={{ marginLeft: 'auto', background: 'rgba(108,142,191,0.15)', color: '#6c8ebf', borderRadius: 10, padding: '1px 8px', fontSize: 12 }}>{badge}</span>}
      </div>
      {children}
    </div>
  )
}

export default function NetworkDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { addToast } = useToast()
  const [network, setNetwork] = useState(null)
  const [subnets, setSubnets] = useState([])
  const [ports, setPorts] = useState([])
  const [routers, setRouters] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedSubnet, setExpandedSubnet] = useState(null)

  const load = async () => {
    try {
      setLoading(true)
      const [net, subs, ps, rts] = await Promise.allSettled([
        api.getNetwork(id),
        api.listSubnets(),
        api.listPorts(),
        api.listRouters(),
      ])
      if (net.status === 'fulfilled') setNetwork(net.value)
      if (subs.status === 'fulfilled') {
        const all = subs.value?.items || subs.value || []
        setSubnets(all.filter(s => s.network_id === id))
      }
      if (ps.status === 'fulfilled') {
        const all = ps.value?.items || ps.value || []
        setPorts(all.filter(p => p.network_id === id))
      }
      if (rts.status === 'fulfilled') {
        setRouters(rts.value?.items || rts.value || [])
      }
    } catch(e) {
      addToast('Failed to load network', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300 }}>
      <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite', color: '#6c8ebf' }} />
    </div>
  )
  if (!network) return <div style={{ color: 'rgba(255,255,255,0.5)', textAlign: 'center', marginTop: 80 }}>Network not found</div>

  const attachedPorts = ports.filter(p => p.device_owner === 'compute:nova' || p.device_id)

  return (
    <div style={{ padding: '0 4px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 24 }}>
        <button onClick={() => navigate('/networks')}
          style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '6px 12px', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <ArrowLeft size={14} /> Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Network size={22} style={{ color: '#6c8ebf' }} />
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#fff' }}>{network.name}</h1>
            {network.external && (
              <span style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 6, padding: '2px 8px', fontSize: 12 }}>External</span>
            )}
            {network.shared && (
              <span style={{ background: 'rgba(108,142,191,0.15)', color: '#6c8ebf', border: '1px solid rgba(108,142,191,0.3)', borderRadius: 6, padding: '2px 8px', fontSize: 12 }}>Shared</span>
            )}
          </div>
          <p style={{ margin: '4px 0 0 34px', color: 'rgba(255,255,255,0.4)', fontSize: 13, fontFamily: 'monospace' }}>{network.id}</p>
        </div>
        <button onClick={load} style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '7px 14px', color: 'rgba(255,255,255,0.7)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Network Info */}
        <DetailCard title="Network Details" icon={Network}>
          <InfoRow label="Status" value="ACTIVE" />
          <InfoRow label="Admin State" value="UP" />
          <InfoRow label="Type" value={network.network_type || (network.external ? 'flat' : 'geneve')} />
          <InfoRow label="Physical Network" value={network.physical_network || (network.external ? 'physnet1' : '—')} />
          <InfoRow label="External" value={network.external ? 'Yes' : 'No'} />
          <InfoRow label="Shared" value={network.shared ? 'Yes' : 'No'} />
          <InfoRow label="Provider" value={network.provider ? 'Yes' : 'No'} />
          <InfoRow label="Created" value={network.created_at ? new Date(network.created_at).toLocaleString() : '—'} />
        </DetailCard>

        {/* Subnets */}
        <DetailCard title="Subnets" icon={Layers} badge={subnets.length} accent="#8b5cf6">
          {subnets.length === 0 ? (
            <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13 }}>No subnets configured</p>
          ) : subnets.map(sub => (
            <div key={sub.id} style={{ marginBottom: 10 }}>
              <div
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', background: 'rgba(139,92,246,0.08)', borderRadius: 8, cursor: 'pointer' }}
                onClick={() => setExpandedSubnet(expandedSubnet === sub.id ? null : sub.id)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {expandedSubnet === sub.id ? <ChevronDown size={14} style={{ color: '#8b5cf6' }} /> : <ChevronRight size={14} style={{ color: '#8b5cf6' }} />}
                  <span style={{ color: '#fff', fontSize: 13, fontWeight: 600 }}>{sub.name}</span>
                </div>
                <span style={{ color: '#8b5cf6', fontSize: 12, fontFamily: 'monospace' }}>{sub.cidr}</span>
              </div>
              {expandedSubnet === sub.id && (
                <div style={{ padding: '4px 12px 0', background: 'rgba(139,92,246,0.04)', borderRadius: '0 0 8px 8px', border: '1px solid rgba(139,92,246,0.15)', borderTop: 'none' }}>
                  <InfoRow label="Gateway" value={sub.gateway_ip || sub.gateway} />
                  <InfoRow label="DHCP" value={sub.enable_dhcp ? 'Enabled' : 'Disabled'} />
                  <InfoRow label="IP Version" value={`IPv${sub.ip_version || 4}`} />
                  <InfoRow label="DNS" value={(sub.dns_nameservers || []).join(', ') || '—'} />
                  {sub.allocation_start && <InfoRow label="Allocation Pool" value={`${sub.allocation_start} – ${sub.allocation_end}`} />}
                </div>
              )}
            </div>
          ))}
        </DetailCard>

        {/* Ports */}
        <DetailCard title={`Ports (${ports.length})`} icon={Activity} badge={`${attachedPorts.length} used`}>
          {ports.length === 0 ? (
            <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13 }}>No ports on this network</p>
          ) : (
            <div style={{ maxHeight: 240, overflowY: 'auto' }}>
              {ports.map(port => (
                <div key={port.id} style={{ padding: '8px 10px', background: 'rgba(255,255,255,0.04)', borderRadius: 8, marginBottom: 6 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: '#fff', fontSize: 12, fontFamily: 'monospace' }}>{port.mac_address}</span>
                    <span style={{ fontSize: 11, color: port.status === 'ACTIVE' ? '#50c878' : '#94a3b8' }}>{port.status}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                    <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11 }}>{port.ip_address || '—'}</span>
                    <span style={{ color: 'rgba(255,255,255,0.35)', fontSize: 11 }}>{port.device_owner || 'unbound'}</span>
                  </div>
                  {port.dns_name && (
                    <div style={{ color: '#6c8ebf', fontSize: 11, marginTop: 2 }}>DNS: {port.dns_name}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </DetailCard>

        {/* Attached Servers */}
        <DetailCard title="Attached Servers" icon={Server} badge={attachedPorts.length} accent="#50c878">
          {attachedPorts.length === 0 ? (
            <p style={{ color: 'rgba(255,255,255,0.35)', fontSize: 13 }}>No servers attached to this network</p>
          ) : attachedPorts.map(port => (
            <div key={port.id} style={{ padding: '8px 10px', background: 'rgba(80,200,120,0.06)', borderRadius: 8, marginBottom: 8, border: '1px solid rgba(80,200,120,0.15)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Server size={12} style={{ color: '#50c878' }} />
                <span style={{ color: '#50c878', fontSize: 12, fontFamily: 'monospace' }}>{port.device_id || '—'}</span>
              </div>
              <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: 11 }}>IP: {port.ip_address}</span>
            </div>
          ))}
        </DetailCard>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
