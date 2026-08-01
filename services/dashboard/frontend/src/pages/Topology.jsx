import { useEffect, useState, useRef, useCallback } from 'react'
import { RefreshCw, Network, Server, Globe, Router, Layers } from 'lucide-react'
import { api } from '../api.js'

// ── Constants ────────────────────────────────────────────────────────────────
const NODE_R = { network: 28, router: 22, server: 18, floatingip: 14, subnet: 16 }

const NODE_COLORS = {
  network:   { fill: '#3b82f6', stroke: '#1d4ed8', label: '#fff' },
  external:  { fill: '#8b5cf6', stroke: '#6d28d9', label: '#fff' },
  router:    { fill: '#f59e0b', stroke: '#b45309', label: '#fff' },
  server:    { fill: '#10b981', stroke: '#047857', label: '#fff' },
  floatingip:{ fill: '#ec4899', stroke: '#be185d', label: '#fff' },
  subnet:    { fill: '#6366f1', stroke: '#4338ca', label: '#fff' },
}

const STATUS_COLORS = {
  ACTIVE: '#10b981', SHUTOFF: '#6b7280', ERROR: '#ef4444',
  BUILD: '#f59e0b', SHELVED_OFFLOADED: '#8b5cf6',
}

// ── Simple force-directed layout ─────────────────────────────────────────────
function initLayout(nodes, links, width, height) {
  // Seed positions
  const cx = width / 2, cy = height / 2
  nodes.forEach((n, i) => {
    const angle = (i / nodes.length) * 2 * Math.PI
    n.x = cx + Math.cos(angle) * (width * 0.3) + (Math.random() - 0.5) * 60
    n.y = cy + Math.sin(angle) * (height * 0.3) + (Math.random() - 0.5) * 60
    n.vx = 0; n.vy = 0
  })

  // Simple force simulation (synchronous, ~120 ticks)
  const linkMap = {}
  links.forEach(l => {
    const key = l.source + '|' + l.target
    linkMap[key] = true
    linkMap[l.target + '|' + l.source] = true
  })

  const nodeById = {}
  nodes.forEach(n => nodeById[n.id] = n)

  for (let tick = 0; tick < 120; tick++) {
    const alpha = 0.3 * (1 - tick / 120)

    // Repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j]
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const force = (3000 / (dist * dist)) * alpha
        const fx = (dx / dist) * force, fy = (dy / dist) * force
        a.vx -= fx; a.vy -= fy
        b.vx += fx; b.vy += fy
      }
    }

    // Attraction along links
    links.forEach(l => {
      const a = nodeById[l.source], b = nodeById[l.target]
      if (!a || !b) return
      const dx = b.x - a.x, dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const ideal = 120
      const force = ((dist - ideal) / dist) * 0.08 * alpha
      const fx = dx * force, fy = dy * force
      a.vx += fx; a.vy += fy
      b.vx -= fx; b.vy -= fy
    })

    // Center gravity
    nodes.forEach(n => {
      n.vx += (cx - n.x) * 0.005 * alpha
      n.vy += (cy - n.y) * 0.005 * alpha
    })

    // Apply velocity + damping
    nodes.forEach(n => {
      n.x += n.vx
      n.y += n.vy
      n.vx *= 0.8; n.vy *= 0.8
      // Bounds
      n.x = Math.max(40, Math.min(width - 40, n.x))
      n.y = Math.max(40, Math.min(height - 40, n.y))
    })
  }

  return nodes
}

// ── Topology SVG ─────────────────────────────────────────────────────────────
function TopologySVG({ data, width, height }) {
  const { networks, subnets, routers, servers, floatingIPs } = data

  // Build nodes
  const nodes = []
  const links = []

  networks.forEach(n => {
    nodes.push({
      id: 'net-' + n.id,
      type: n.external ? 'external' : 'network',
      label: n.name,
      sublabel: n.external ? 'External' : 'Network',
      raw: n,
    })
  })

  routers.forEach(r => {
    nodes.push({
      id: 'rtr-' + r.id,
      type: 'router',
      label: r.name,
      sublabel: 'Router',
      raw: r,
    })
    // Router to external gateway
    if (r.external_network_id) {
      links.push({ source: 'rtr-' + r.id, target: 'net-' + r.external_network_id, type: 'gateway' })
    }
    // Router to subnets
    const rSubnets = subnets.filter(s => s.router_id === r.id || r.subnet_ids?.includes(s.id))
    rSubnets.forEach(s => {
      links.push({ source: 'rtr-' + r.id, target: 'net-' + s.network_id, type: 'interface' })
    })
  })

  servers.forEach(s => {
    nodes.push({
      id: 'srv-' + s.id,
      type: 'server',
      label: s.name,
      sublabel: s.flavor_name || s.flavor || '',
      status: s.status,
      raw: s,
    })
    // Server to network
    if (s.network_id) {
      links.push({ source: 'srv-' + s.id, target: 'net-' + s.network_id, type: 'port' })
    } else if (s.ip_address) {
      // Try to infer network from IP
      const matchingNet = networks[0]
      if (matchingNet) links.push({ source: 'srv-' + s.id, target: 'net-' + matchingNet.id, type: 'port' })
    }
  })

  floatingIPs.forEach(f => {
    if (!f.floating_ip_address) return
    nodes.push({
      id: 'fip-' + f.id,
      type: 'floatingip',
      label: f.floating_ip_address,
      sublabel: f.server_id ? 'Assigned' : 'Unassigned',
      raw: f,
    })
    if (f.external_network_id) {
      links.push({ source: 'fip-' + f.id, target: 'net-' + f.external_network_id, type: 'floating' })
    }
    if (f.server_id) {
      links.push({ source: 'fip-' + f.id, target: 'srv-' + f.server_id, type: 'floating' })
    }
  })

  const positioned = initLayout([...nodes], links, width, height)
  const posMap = {}
  positioned.forEach(n => posMap[n.id] = { x: n.x, y: n.y })

  const linkColors = { gateway: '#f59e0b', interface: '#3b82f6', port: '#10b981', floating: '#ec4899' }

  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <defs>
        {Object.entries(linkColors).map(([type, color]) => (
          <marker
            key={type}
            id={`arrow-${type}`}
            viewBox="0 0 10 10"
            refX="9" refY="5"
            markerWidth="5" markerHeight="5"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill={color} opacity={0.7} />
          </marker>
        ))}
      </defs>

      {/* Links */}
      {links.map((l, i) => {
        const a = posMap[l.source], b = posMap[l.target]
        if (!a || !b) return null
        const color = linkColors[l.type] || '#6b7280'
        return (
          <line
            key={i}
            x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke={color} strokeWidth={l.type === 'gateway' ? 2.5 : 1.5}
            strokeOpacity={0.6}
            strokeDasharray={l.type === 'floating' ? '5,3' : undefined}
            markerEnd={`url(#arrow-${l.type})`}
          />
        )
      })}

      {/* Nodes */}
      {positioned.map(n => {
        const pos = posMap[n.id]
        if (!pos) return null
        const r = NODE_R[n.type] || 18
        const colors = NODE_COLORS[n.type] || NODE_COLORS.network
        const statusColor = n.status ? (STATUS_COLORS[n.status] || '#6b7280') : null

        return (
          <g key={n.id} transform={`translate(${pos.x},${pos.y})`} style={{ cursor: 'pointer' }}>
            <title>{n.label}{n.sublabel ? ` (${n.sublabel})` : ''}</title>
            {/* Shadow */}
            <circle cx={2} cy={2} r={r} fill="rgba(0,0,0,0.25)" />
            {/* Node body */}
            <circle
              r={r}
              fill={colors.fill}
              stroke={colors.stroke}
              strokeWidth={2}
            />
            {/* Status ring for servers */}
            {statusColor && (
              <circle r={r + 4} fill="none" stroke={statusColor} strokeWidth={2.5} opacity={0.7} />
            )}
            {/* Icon glyph */}
            <text
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={r * 0.75}
              fill={colors.label}
              style={{ userSelect: 'none', pointerEvents: 'none' }}
            >
              {n.type === 'network' || n.type === 'external' ? '⬡' :
               n.type === 'router' ? '⤢' :
               n.type === 'server' ? '▣' :
               n.type === 'floatingip' ? '◎' : '◯'}
            </text>
            {/* Label below */}
            <text
              y={r + 13}
              textAnchor="middle"
              fontSize={10}
              fill="#e2e8f0"
              fontWeight="600"
              style={{ userSelect: 'none', pointerEvents: 'none' }}
            >
              {n.label.length > 16 ? n.label.slice(0, 14) + '…' : n.label}
            </text>
            {n.sublabel && (
              <text
                y={r + 25}
                textAnchor="middle"
                fontSize={8.5}
                fill="#94a3b8"
                style={{ userSelect: 'none', pointerEvents: 'none' }}
              >
                {n.sublabel}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// ── Legend ───────────────────────────────────────────────────────────────────
function Legend() {
  const items = [
    { color: NODE_COLORS.network.fill,    label: 'Network' },
    { color: NODE_COLORS.external.fill,   label: 'External Network' },
    { color: NODE_COLORS.router.fill,     label: 'Router' },
    { color: NODE_COLORS.server.fill,     label: 'Server' },
    { color: NODE_COLORS.floatingip.fill, label: 'Floating IP' },
  ]
  const linkItems = [
    { color: '#f59e0b', dash: false, label: 'Gateway' },
    { color: '#3b82f6', dash: false, label: 'Interface' },
    { color: '#10b981', dash: false, label: 'Port' },
    { color: '#ec4899', dash: true,  label: 'Floating IP' },
  ]
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'center',
      padding: '10px 16px',
      background: 'var(--surface)',
      borderRadius: 8,
      border: '1px solid var(--border)',
      fontSize: 12,
    }}>
      <span style={{ color: 'var(--text-muted)', fontWeight: 600, marginRight: 4 }}>Nodes:</span>
      {items.map(it => (
        <span key={it.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 12, height: 12, borderRadius: '50%', background: it.color, display: 'inline-block' }} />
          <span style={{ color: 'var(--text-secondary)' }}>{it.label}</span>
        </span>
      ))}
      <span style={{ color: 'var(--text-muted)', fontWeight: 600, marginLeft: 8, marginRight: 4 }}>Links:</span>
      {linkItems.map(it => (
        <span key={it.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <svg width="24" height="10">
            <line x1="0" y1="5" x2="24" y2="5"
              stroke={it.color} strokeWidth="2"
              strokeDasharray={it.dash ? '5,3' : undefined} />
          </svg>
          <span style={{ color: 'var(--text-secondary)' }}>{it.label}</span>
        </span>
      ))}
    </div>
  )
}

// ── Stats Bar ────────────────────────────────────────────────────────────────
function StatsBar({ data }) {
  const items = [
    { icon: <Network size={14}/>,  label: 'Networks', count: data.networks.length },
    { icon: <Router size={14}/>,   label: 'Routers',  count: data.routers.length },
    { icon: <Server size={14}/>,   label: 'Servers',  count: data.servers.length },
    { icon: <Globe size={14}/>,    label: 'Floating IPs', count: data.floatingIPs.length },
  ]
  return (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
      {items.map(it => (
        <div key={it.label} style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '5px 12px',
          background: 'var(--surface)',
          borderRadius: 6,
          border: '1px solid var(--border)',
          fontSize: 13,
          color: 'var(--text-secondary)',
        }}>
          <span style={{ color: 'var(--primary)' }}>{it.icon}</span>
          <strong style={{ color: 'var(--text-primary)' }}>{it.count}</strong>
          <span>{it.label}</span>
        </div>
      ))}
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────
export default function Topology() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const containerRef = useRef()
  const [dims, setDims] = useState({ width: 800, height: 520 })

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const topo = await api.getNetworkTopology()
      setData(topo)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      setDims({ width: Math.max(400, width), height: Math.max(350, height) })
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  const hasNodes = data && (
    data.networks.length + data.routers.length + data.servers.length + data.floatingIPs.length > 0
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Network Topology</h1>
          <p className="page-subtitle">Visual map of your networks, routers, and instances</p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      {data && <StatsBar data={data} />}

      {/* Main canvas */}
      <div
        ref={containerRef}
        className="card"
        style={{
          flex: 1, minHeight: 420, padding: 0, overflow: 'hidden',
          background: 'radial-gradient(ellipse at 50% 50%, #0f172a 0%, #020617 100%)',
          borderRadius: 12,
          position: 'relative',
        }}
      >
        {loading && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 12, color: '#94a3b8',
          }}>
            <Layers size={40} style={{ opacity: 0.3 }} />
            <span className="spinner" />
            <span style={{ fontSize: 13 }}>Loading topology…</span>
          </div>
        )}

        {error && !loading && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 8, color: '#f87171',
          }}>
            <Network size={40} style={{ opacity: 0.3 }} />
            <p style={{ fontSize: 14 }}>Failed to load topology</p>
            <p style={{ fontSize: 12, opacity: 0.7 }}>{error}</p>
          </div>
        )}

        {!loading && !error && !hasNodes && (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 12, color: '#475569',
          }}>
            <Network size={60} style={{ opacity: 0.2 }} />
            <p style={{ fontSize: 15, color: '#64748b' }}>No network resources found</p>
            <p style={{ fontSize: 13, color: '#475569' }}>Create networks, routers, or instances to see the topology.</p>
          </div>
        )}

        {!loading && !error && hasNodes && (
          <TopologySVG data={data} width={dims.width} height={dims.height} />
        )}

        {/* Grid overlay for aesthetics */}
        {!loading && hasNodes && (
          <svg
            style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.03 }}
            width={dims.width} height={dims.height}
          >
            <defs>
              <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#94a3b8" strokeWidth="0.5"/>
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#grid)" />
          </svg>
        )}
      </div>

      <Legend />
    </div>
  )
}
