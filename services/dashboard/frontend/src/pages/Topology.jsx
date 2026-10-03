/**
 * Topology.jsx — network topology map.
 *
 * The graph itself is a custom SVG force-directed layout (there is no
 * Cloudscape graph component); the surrounding chrome, stats, legend, and the
 * node inspector are Cloudscape components so the page matches the console.
 */
import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Spinner from '@cloudscape-design/components/spinner'
import Badge from '@cloudscape-design/components/badge'
import Grid from '@cloudscape-design/components/grid'
import { api } from '../api.js'
import { ComputeStatus, shortId } from '../components/common.jsx'

const NODE_R = { network: 28, external: 28, router: 22, server: 18, floatingip: 14, subnet: 16 }

const NODE_COLORS = {
  network:    { fill: '#0972d3', stroke: '#033160', label: '#fff' },
  external:   { fill: '#8b5cf6', stroke: '#4c1d95', label: '#fff' },
  router:     { fill: '#f89256', stroke: '#8d4004', label: '#fff' },
  server:     { fill: '#037f0c', stroke: '#023d05', label: '#fff' },
  floatingip: { fill: '#d63aff', stroke: '#7d1fa2', label: '#fff' },
  subnet:     { fill: '#0972d3', stroke: '#033160', label: '#fff' },
}

const STATUS_RING = {
  ACTIVE: '#037f0c', SHUTOFF: '#8c8c94', ERROR: '#d91515',
  BUILD: '#f89256', SHELVED_OFFLOADED: '#8b5cf6',
}

const LINK_COLORS = { gateway: '#f89256', interface: '#0972d3', port: '#037f0c', floating: '#d63aff' }

// ── Force-directed layout (synchronous, ~120 ticks) ──────────────────────────
function layout(nodes, links, width, height) {
  const cx = width / 2, cy = height / 2
  nodes.forEach((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * 2 * Math.PI
    n.x = cx + Math.cos(angle) * (width * 0.3)
    n.y = cy + Math.sin(angle) * (height * 0.3)
    n.vx = 0; n.vy = 0
  })

  const byId = {}
  nodes.forEach(n => { byId[n.id] = n })

  for (let tick = 0; tick < 120; tick++) {
    const alpha = 0.3 * (1 - tick / 120)

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

    links.forEach(l => {
      const a = byId[l.source], b = byId[l.target]
      if (!a || !b) return
      const dx = b.x - a.x, dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const force = ((dist - 120) / dist) * 0.08 * alpha
      a.vx += dx * force; a.vy += dy * force
      b.vx -= dx * force; b.vy -= dy * force
    })

    nodes.forEach(n => {
      n.vx += (cx - n.x) * 0.005 * alpha
      n.vy += (cy - n.y) * 0.005 * alpha
      n.x += n.vx; n.y += n.vy
      n.vx *= 0.8; n.vy *= 0.8
      n.x = Math.max(46, Math.min(width - 46, n.x))
      n.y = Math.max(46, Math.min(height - 46, n.y))
    })
  }
  return nodes
}

// ── Build the graph model from API data ──────────────────────────────────────
function buildGraph({ networks, subnets, routers, servers, floatingIPs }) {
  const nodes = []
  const links = []

  networks.forEach(n => nodes.push({
    id: 'net-' + n.id,
    type: n.external ? 'external' : 'network',
    label: n.name,
    sublabel: n.external ? 'External' : 'Network',
    raw: n,
  }))

  routers.forEach(r => {
    nodes.push({ id: 'rtr-' + r.id, type: 'router', label: r.name, sublabel: 'Router', raw: r })
    if (r.external_network_id) {
      links.push({ source: 'rtr-' + r.id, target: 'net-' + r.external_network_id, type: 'gateway' })
    }
    const rtrSubnetIds = new Set([
      ...(r.subnet_ids || []),
      ...(r.interfaces_info || []).map(i => i.subnet_id).filter(Boolean),
      ...(r.interfaces || []).map(i => i.subnet_id || i.id).filter(Boolean)
    ])
    subnets
      .filter(s => s.router_id === r.id || rtrSubnetIds.has(s.id))
      .forEach(s => links.push({ source: 'rtr-' + r.id, target: 'net-' + s.network_id, type: 'interface' }))
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
    if (s.network_id) {
      links.push({ source: 'srv-' + s.id, target: 'net-' + s.network_id, type: 'port' })
    }
  })

  floatingIPs.forEach(f => {
    if (!f.floating_ip_address) return
    nodes.push({
      id: 'fip-' + f.id,
      type: 'floatingip',
      label: f.floating_ip_address,
      sublabel: f.server_id ? 'Associated' : 'Unassociated',
      raw: f,
    })
    if (f.external_network_id || f.floating_network_id) {
      links.push({ source: 'fip-' + f.id, target: 'net-' + (f.external_network_id || f.floating_network_id), type: 'floating' })
    }
    if (f.server_id) {
      links.push({ source: 'fip-' + f.id, target: 'srv-' + f.server_id, type: 'floating' })
    }
  })

  return { nodes, links }
}

// ── SVG canvas ───────────────────────────────────────────────────────────────
function TopologySVG({ graph, width, height, onSelect, selectedId }) {
  const positioned = useMemo(
    () => layout(graph.nodes.map(n => ({ ...n })), graph.links, width, height),
    [graph, width, height],
  )
  const posMap = {}
  positioned.forEach(n => { posMap[n.id] = { x: n.x, y: n.y } })

  return (
    <svg width={width} height={height} className="topology-canvas" role="img" aria-label="Network topology graph">
      <defs>
        {Object.entries(LINK_COLORS).map(([type, color]) => (
          <marker key={type} id={`arrow-${type}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill={color} opacity={0.75} />
          </marker>
        ))}
      </defs>

      {graph.links.map((l, i) => {
        const a = posMap[l.source], b = posMap[l.target]
        if (!a || !b) return null
        const color = LINK_COLORS[l.type] || '#8c8c94'
        return (
          <line
            key={i}
            x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            stroke={color}
            strokeWidth={l.type === 'gateway' ? 2.5 : 1.5}
            strokeOpacity={0.65}
            strokeDasharray={l.type === 'floating' ? '5,3' : undefined}
            markerEnd={`url(#arrow-${l.type})`}
          />
        )
      })}

      {positioned.map(n => {
        const pos = posMap[n.id]
        if (!pos) return null
        const r = NODE_R[n.type] || 18
        const colors = NODE_COLORS[n.type] || NODE_COLORS.network
        const ring = n.status ? STATUS_RING[n.status] : null
        const isSelected = selectedId === n.id
        return (
          <g
            key={n.id}
            className="topology-node"
            transform={`translate(${pos.x},${pos.y})`}
            onClick={() => onSelect(n)}
            tabIndex={0}
            role="button"
            aria-label={`${n.type} ${n.label}`}
            onKeyDown={e => { if (e.key === 'Enter') onSelect(n) }}
          >
            <title>{n.label}{n.sublabel ? ` (${n.sublabel})` : ''}</title>
            {isSelected && <circle r={r + 9} fill="none" stroke={colors.fill} strokeWidth={2} strokeDasharray="4,3" />}
            <circle r={r} fill={colors.fill} stroke={colors.stroke} strokeWidth={2} />
            {ring && <circle r={r + 4} fill="none" stroke={ring} strokeWidth={2.5} opacity={0.8} />}
            <text textAnchor="middle" dominantBaseline="central" fontSize={r * 0.75} fill={colors.label}>
              {n.type === 'network' || n.type === 'external' ? '⬡'
                : n.type === 'router' ? '⤢'
                : n.type === 'server' ? '▣'
                : n.type === 'floatingip' ? '◎' : '◯'}
            </text>
            <text y={r + 14} textAnchor="middle" fontSize={11} fill="currentColor" fontWeight="600">
              {n.label?.length > 16 ? n.label.slice(0, 14) + '…' : n.label}
            </text>
            {n.sublabel && (
              <text y={r + 27} textAnchor="middle" fontSize={9} fill="currentColor" opacity={0.65}>
                {n.sublabel}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// ── Node inspector ───────────────────────────────────────────────────────────
function NodeInspector({ node, onNavigate }) {
  if (!node) {
    return (
      <Container header={<Header variant="h2">Resource details</Header>}>
        <Box color="text-body-secondary">Select a node in the graph to inspect it.</Box>
      </Container>
    )
  }

  const raw = node.raw || {}
  const items = []

  if (node.type === 'server') {
    items.push(
      { label: 'Instance', value: <Link href={`/servers/${raw.id}`} onFollow={(e) => { e.preventDefault(); onNavigate(`/servers/${raw.id}`) }}>{raw.name}</Link> },
      { label: 'State', value: <ComputeStatus status={raw.status} /> },
      { label: 'Private IP', value: raw.ip_address || '—' },
      { label: 'Flavor', value: raw.flavor_name || raw.flavor || '—' },
      { label: 'Instance ID', value: shortId(raw.id, 18) },
    )
  } else if (node.type === 'network' || node.type === 'external') {
    items.push(
      { label: 'Network', value: <Link href={`/networks/${raw.id}`} onFollow={(e) => { e.preventDefault(); onNavigate(`/networks/${raw.id}`) }}>{raw.name}</Link> },
      { label: 'Type', value: raw.external ? <Badge color="severity-medium">External</Badge> : 'Tenant' },
      { label: 'Shared', value: raw.shared ? 'Yes' : 'No' },
      { label: 'Network ID', value: shortId(raw.id, 18) },
    )
  } else if (node.type === 'router') {
    items.push(
      { label: 'Router', value: raw.name },
      { label: 'External gateway', value: raw.external_gateway_network || (raw.external_network_id ? shortId(raw.external_network_id, 12) : '—') },
      { label: 'Router ID', value: shortId(raw.id, 18) },
    )
  } else if (node.type === 'floatingip') {
    items.push(
      { label: 'Floating IP', value: raw.floating_ip_address },
      { label: 'Fixed IP', value: raw.fixed_ip_address || raw.fixed_ip || '—' },
      { label: 'Status', value: node.sublabel },
      { label: 'Port', value: raw.port_id ? shortId(raw.port_id, 14) : '—' },
    )
  }

  return (
    <Container header={<Header variant="h2" description={node.sublabel}>{node.label}</Header>}>
      <KeyValuePairs columns={1} items={items} />
    </Container>
  )
}

// ── Legend ───────────────────────────────────────────────────────────────────
function Legend() {
  const nodeItems = [
    ['Network', NODE_COLORS.network.fill],
    ['External network', NODE_COLORS.external.fill],
    ['Router', NODE_COLORS.router.fill],
    ['Instance', NODE_COLORS.server.fill],
    ['Floating IP', NODE_COLORS.floatingip.fill],
  ]
  return (
    <Container header={<Header variant="h3">Legend</Header>}>
      <SpaceBetween direction="horizontal" size="l">
        {nodeItems.map(([label, color]) => (
          <SpaceBetween key={label} direction="horizontal" size="xxs">
            <svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="6" fill={color} /></svg>
            <Box variant="small">{label}</Box>
          </SpaceBetween>
        ))}
        {Object.entries(LINK_COLORS).map(([type, color]) => (
          <SpaceBetween key={type} direction="horizontal" size="xxs">
            <svg width="24" height="10" aria-hidden="true">
              <line x1="0" y1="5" x2="24" y2="5" stroke={color} strokeWidth="2" strokeDasharray={type === 'floating' ? '5,3' : undefined} />
            </svg>
            <Box variant="small">{type.charAt(0).toUpperCase() + type.slice(1)}</Box>
          </SpaceBetween>
        ))}
      </SpaceBetween>
    </Container>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function TopologyPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const containerRef = useRef(null)
  const [dims, setDims] = useState({ width: 800, height: 520 })

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      setData(await api.getNetworkTopology())
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
      const { width } = entries[0].contentRect
      setDims({ width: Math.max(400, width), height: 520 })
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  const graph = useMemo(() => (data ? buildGraph(data) : { nodes: [], links: [] }), [data])
  const hasNodes = graph.nodes.length > 0

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Visual map of networks, routers, instances, and floating IPs"
          actions={<Button iconName="refresh" onClick={load} loading={loading}>Refresh</Button>}
        >
          Network topology
        </Header>
      }
    >
      <SpaceBetween size="l">
        {data && (
          <Container>
            <KeyValuePairs
              columns={4}
              items={[
                { label: 'Networks', value: data.networks.length },
                { label: 'Routers', value: data.routers.length },
                { label: 'Instances', value: data.servers.length },
                { label: 'Floating IPs', value: data.floatingIPs.length },
              ]}
            />
          </Container>
        )}

        <Grid gridDefinition={[{ colspan: { default: 12, l: 8 } }, { colspan: { default: 12, l: 4 } }]}>
          <Container header={<Header variant="h2">Topology graph</Header>} disableContentPaddings>
            <div ref={containerRef}>
              {loading ? (
                <Box textAlign="center" padding="xxxl"><SpaceBetween size="s" alignItems="center"><Spinner size="large" /><Box>Loading topology…</Box></SpaceBetween></Box>
              ) : error ? (
                <Box padding="l"><Alert type="error" header="Failed to load topology">{error}</Alert></Box>
              ) : !hasNodes ? (
                <Box textAlign="center" padding="xxxl" color="text-body-secondary">
                  No network resources found. Create networks, routers, or instances to populate the topology.
                </Box>
              ) : (
                <TopologySVG
                  graph={graph}
                  width={dims.width}
                  height={dims.height}
                  onSelect={setSelected}
                  selectedId={selected?.id}
                />
              )}
            </div>
          </Container>

          <NodeInspector node={selected} onNavigate={navigate} />
        </Grid>

        <Legend />
      </SpaceBetween>
    </ContentLayout>
  )
}
