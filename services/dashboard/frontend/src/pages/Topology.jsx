/**
 * Topology.jsx — CloudX Hierarchical Network Topology Map
 *
 * Deterministic top-down tiered layout:
 * - Tier 0: External Networks / Internet
 * - Tier 1: Virtual Routers
 * - Tier 2: Internal Subnets & Private Networks
 * - Tier 3: Compute Instances (VMs)
 *
 * Features:
 * - Clean minimal VM cards (icon + name; status & IP details viewable upon click)
 * - Smooth vertical cubic bezier connectors with endpoint indicators
 * - Interactive Canvas: Pan (drag), Zoom (wheel), and floating Zoom controls
 * - Detailed On-Click Inspector Panel for all network resource types
 */
import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonGroup from '@cloudscape-design/components/button-group'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Spinner from '@cloudscape-design/components/spinner'
import Badge from '@cloudscape-design/components/badge'
import Grid from '@cloudscape-design/components/grid'
import StatusIndicator from '@cloudscape-design/components/status-indicator'

import { api } from '../api.js'
import { ComputeStatus, shortId } from '../components/common.jsx'
import { computeHierarchicalLayout } from '../utils/topologyLayout.js'
import {
  ServerNode,
  RouterNode,
  SubnetNode,
  ExternalNetworkNode,
} from '../components/topology/TopologyNodes.jsx'
import { TopologyLink } from '../components/topology/TopologyLinks.jsx'

// ── SVG Interactive Skyline Canvas ───────────────────────────────────────────
function SkylineTopologyCanvas({
  layoutResult,
  width,
  height,
  selectedId,
  onSelectNode,
}) {
  const { nodes, links, bounds } = layoutResult
  const nodeMap = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes])

  // Canvas Pan & Zoom State
  const [zoom, setZoom] = useState(1.0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const isDragging = useRef(false)
  const dragStart = useRef({ x: 0, y: 0 })

  // Auto-center or fit to canvas on initial layout
  const fitToScreen = useCallback(() => {
    if (!bounds.width || !bounds.height) return
    const padding = 60
    const scaleX = (width - padding) / bounds.width
    const scaleY = (height - padding) / bounds.height
    const newZoom = Math.min(Math.max(Math.min(scaleX, scaleY), 0.5), 1.2)
    const offsetX = (width - bounds.width * newZoom) / 2
    const offsetY = 20
    setZoom(newZoom)
    setPan({ x: offsetX, y: offsetY })
  }, [bounds, width, height])

  useEffect(() => {
    fitToScreen()
  }, [bounds.width, bounds.height, fitToScreen])

  // Mouse Wheel Zoom
  const handleWheel = (e) => {
    e.preventDefault()
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9
    setZoom(prev => Math.min(Math.max(prev * zoomFactor, 0.35), 2.5))
  }

  // Mouse Drag to Pan
  const handleMouseDown = (e) => {
    if (e.button !== 0) return // Left click only
    isDragging.current = true
    dragStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y }
  }

  const handleMouseMove = (e) => {
    if (!isDragging.current) return
    setPan({
      x: e.clientX - dragStart.current.x,
      y: e.clientY - dragStart.current.y,
    })
  }

  const handleMouseUp = () => {
    isDragging.current = false
  }

  const zoomIn = () => setZoom(prev => Math.min(prev * 1.2, 2.5))
  const zoomOut = () => setZoom(prev => Math.max(prev / 1.2, 0.35))
  const resetZoom = () => {
    setZoom(1.0)
    setPan({ x: 0, y: 0 })
  }

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: `${height}px`,
        overflow: 'hidden',
        backgroundColor: '#0f172a',
        borderRadius: '8px',
        userSelect: 'none',
      }}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Floating Zoom & View Controls Toolbar */}
      <div
        style={{
          position: 'absolute',
          top: '12px',
          right: '12px',
          zIndex: 10,
          backgroundColor: 'rgba(30, 41, 59, 0.85)',
          backdropFilter: 'blur(6px)',
          borderRadius: '6px',
          padding: '4px',
          border: '1px solid #334155',
          boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.2)',
        }}
      >
        <SpaceBetween direction="horizontal" size="xs">
          <Button variant="inline-icon" iconName="zoom-in" ariaLabel="Zoom In" onClick={zoomIn} />
          <Button variant="inline-icon" iconName="zoom-out" ariaLabel="Zoom Out" onClick={zoomOut} />
          <Button variant="inline-icon" iconName="expand" ariaLabel="Fit to Screen" onClick={fitToScreen} />
          <Button variant="inline-link" onClick={resetZoom}>
            {Math.round(zoom * 100)}%
          </Button>
        </SpaceBetween>
      </div>

      {/* SVG Canvas */}
      <svg
        width={width}
        height={height}
        style={{ cursor: isDragging.current ? 'grabbing' : 'grab', display: 'block' }}
      >
        {/* Grid pattern background */}
        <defs>
          <pattern id="skyline-grid" width="30" height="30" patternUnits="userSpaceOnUse">
            <path d="M 30 0 L 0 0 0 30" fill="none" stroke="#1e293b" strokeWidth="0.75" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#skyline-grid)" />

        <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
          {/* Render Connection Links */}
          {links.map(link => {
            const isHighlighted = selectedId && (link.source === selectedId || link.target === selectedId)
            return (
              <TopologyLink
                key={link.id}
                link={link}
                nodeMap={nodeMap}
                isHighlighted={Boolean(isHighlighted)}
              />
            )
          })}

          {/* Render Nodes by Tier */}
          {nodes.map(node => {
            const isSelected = selectedId === node.id
            const selectHandler = (e) => {
              e.stopPropagation()
              onSelectNode(node)
            }

            switch (node.type) {
              case 'server':
                return (
                  <ServerNode
                    key={node.id}
                    node={node}
                    isSelected={isSelected}
                    onClick={selectHandler}
                  />
                )
              case 'router':
                return (
                  <RouterNode
                    key={node.id}
                    node={node}
                    isSelected={isSelected}
                    onClick={selectHandler}
                  />
                )
              case 'subnet':
                return (
                  <SubnetNode
                    key={node.id}
                    node={node}
                    isSelected={isSelected}
                    onClick={selectHandler}
                  />
                )
              case 'external':
                return (
                  <ExternalNetworkNode
                    key={node.id}
                    node={node}
                    isSelected={isSelected}
                    onClick={selectHandler}
                  />
                )
              default:
                return null
            }
          })}
        </g>
      </svg>
    </div>
  )
}

// ── Detailed On-Click Node Inspector Panel ───────────────────────────────────
function NodeInspector({ node, onNavigate }) {
  if (!node) {
    return (
      <Container header={<Header variant="h2">Resource Inspector</Header>}>
        <Box textAlign="center" color="text-body-secondary" padding="l">
          Click any resource card in the topology to inspect its details and connections.
        </Box>
      </Container>
    )
  }

  const raw = node.raw || {}

  if (node.type === 'server') {
    const items = [
      {
        label: 'Instance Name',
        value: (
          <Link
            href={`/servers/${raw.id}`}
            onFollow={(e) => {
              e.preventDefault()
              onNavigate(`/servers/${raw.id}`)
            }}
          >
            {raw.name}
          </Link>
        ),
      },
      {
        label: 'Status',
        value: <ComputeStatus status={raw.status} />,
      },
      {
        label: 'Private IP Address',
        value: (
          <Box variant="code">
            {raw.ip_address || (raw.addresses ? JSON.stringify(raw.addresses) : '—')}
          </Box>
        ),
      },
      {
        label: 'Floating IP',
        value: node.floating_ip ? (
          <Badge color="severity-medium">{node.floating_ip}</Badge>
        ) : (
          <Box color="text-status-inactive">None attached</Box>
        ),
      },
      {
        label: 'Flavor',
        value: raw.flavor_name || raw.flavor || '—',
      },
      {
        label: 'Hypervisor / Host',
        value: raw.host || raw['OS-EXT-SRV-ATTR:host'] || 'cloudx-compute-01',
      },
      {
        label: 'Instance ID',
        value: <Box variant="code">{shortId(raw.id, 24)}</Box>,
      },
    ]

    return (
      <Container
        header={
          <Header
            variant="h2"
            description="Compute Instance"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  iconName="external"
                  onClick={() => onNavigate(`/console/${raw.id}`)}
                >
                  Console
                </Button>
                <Button
                  onClick={() => onNavigate(`/servers/${raw.id}`)}
                >
                  Details
                </Button>
              </SpaceBetween>
            }
          >
            {raw.name}
          </Header>
        }
      >
        <KeyValuePairs columns={1} items={items} />
      </Container>
    )
  }

  if (node.type === 'router') {
    const items = [
      {
        label: 'Router Name',
        value: (
          <Link
            href={`/routers/${raw.id}`}
            onFollow={(e) => {
              e.preventDefault()
              onNavigate(`/routers/${raw.id}`)
            }}
          >
            {raw.name}
          </Link>
        ),
      },
      {
        label: 'Status',
        value: <StatusIndicator type="success">{raw.status || 'ACTIVE'}</StatusIndicator>,
      },
      {
        label: 'External Gateway Network',
        value: raw.external_gateway_info?.network_id
          ? shortId(raw.external_gateway_info.network_id, 16)
          : (raw.external_gateway_network || (raw.external_network_id ? shortId(raw.external_network_id, 16) : '—')),
      },
      {
        label: 'External Gateway IP',
        value: (raw.external_gateway_info?.external_fixed_ips?.[0]?.ip_address || raw.external_gateway_ip) ? (
          <Box variant="code">
            {raw.external_gateway_info?.external_fixed_ips?.[0]?.ip_address || raw.external_gateway_ip}
          </Box>
        ) : '—',
      },
      {
        label: 'Connected Subnets',
        value: (raw.subnet_ids?.length || 0) + ' subnets',
      },
      {
        label: 'Router ID',
        value: <Box variant="code">{shortId(raw.id, 24)}</Box>,
      },
    ]

    return (
      <Container
        header={
          <Header
            variant="h2"
            description="Virtual Router"
            actions={
              <Button onClick={() => onNavigate(`/routers/${raw.id}`)}>
                Router Details
              </Button>
            }
          >
            {raw.name}
          </Header>
        }
      >
        <KeyValuePairs columns={1} items={items} />
      </Container>
    )
  }

  if (node.type === 'subnet') {
    const primarySub = node.subnets?.[0] || {}
    const items = [
      {
        label: 'Network Name',
        value: (
          <Link
            href={`/networks/${node.network_id}`}
            onFollow={(e) => {
              e.preventDefault()
              onNavigate(`/networks/${node.network_id}`)
            }}
          >
            {node.label}
          </Link>
        ),
      },
      {
        label: 'Subnet CIDR',
        value: <Box variant="code">{node.cidr || primarySub.cidr || '—'}</Box>,
      },
      {
        label: 'Gateway IP',
        value: (primarySub.gateway_ip || raw.gateway_ip) ? (
          <Box variant="code">{primarySub.gateway_ip || raw.gateway_ip}</Box>
        ) : '—',
      },
      {
        label: 'DHCP Enabled',
        value: primarySub.enable_dhcp !== false ? 'Yes' : 'No',
      },
      {
        label: 'Network ID',
        value: (
          <Link
            href={`/networks/${node.network_id}`}
            onFollow={(e) => {
              e.preventDefault()
              onNavigate(`/networks/${node.network_id}`)
            }}
          >
            {shortId(node.network_id, 24)}
          </Link>
        ),
      },
    ]

    return (
      <Container
        header={
          <Header
            variant="h2"
            description="Internal Tenant Network"
            actions={
              <Button onClick={() => onNavigate(`/networks/${node.network_id}`)}>
                Network Details
              </Button>
            }
          >
            {node.label}
          </Header>
        }
      >
        <KeyValuePairs columns={1} items={items} />
      </Container>
    )
  }

  if (node.type === 'external') {
    const items = [
      {
        label: 'Network Name',
        value: (
          <Link
            href={`/networks/${raw.id}`}
            onFollow={(e) => {
              e.preventDefault()
              onNavigate(`/networks/${raw.id}`)
            }}
          >
            {raw.name}
          </Link>
        ),
      },
      {
        label: 'External CIDR',
        value: <Box variant="code">{node.cidr || '—'}</Box>,
      },
      {
        label: 'Type',
        value: <Badge color="severity-medium">External / Public Gateway</Badge>,
      },
      {
        label: 'Shared Network',
        value: raw.shared ? 'Yes' : 'No',
      },
      {
        label: 'Network ID',
        value: <Box variant="code">{shortId(raw.id, 24)}</Box>,
      },
    ]

    return (
      <Container
        header={
          <Header
            variant="h2"
            description="External Provider Network"
            actions={
              <Button onClick={() => onNavigate(`/networks/${raw.id}`)}>
                Network Details
              </Button>
            }
          >
            {raw.name}
          </Header>
        }
      >
        <KeyValuePairs columns={1} items={items} />
      </Container>
    )
  }

  return null
}

// ── Skyline Topology Legend ──────────────────────────────────────────────────
function SkylineLegend() {
  const nodeItems = [
    { label: 'External Network', color: '#a855f7' },
    { label: 'Virtual Router', color: '#f97316' },
    { label: 'Internal Network', color: '#0284c7' },
    { label: 'Compute Instance', color: '#22c55e' },
  ]

  const linkItems = [
    { label: 'External Gateway', color: '#f97316' },
    { label: 'Router Interface', color: '#0284c7' },
    { label: 'Instance Port', color: '#22c55e' },
  ]

  return (
    <Container header={<Header variant="h3">Topology Legend</Header>}>
      <SpaceBetween direction="horizontal" size="xl">
        <SpaceBetween direction="horizontal" size="l">
          {nodeItems.map(item => (
            <SpaceBetween key={item.label} direction="horizontal" size="xxs" alignItems="center">
              <span
                style={{
                  display: 'inline-block',
                  width: '12px',
                  height: '12px',
                  borderRadius: '3px',
                  backgroundColor: item.color,
                }}
              />
              <Box variant="small">{item.label}</Box>
            </SpaceBetween>
          ))}
        </SpaceBetween>

        <SpaceBetween direction="horizontal" size="l">
          {linkItems.map(item => (
            <SpaceBetween key={item.label} direction="horizontal" size="xxs" alignItems="center">
              <svg width="22" height="10" aria-hidden="true">
                <line
                  x1="0"
                  y1="5"
                  x2="22"
                  y2="5"
                  stroke={item.color}
                  strokeWidth="2.5"
                  strokeDasharray={item.dashed ? '4,3' : undefined}
                />
              </svg>
              <Box variant="small">{item.label}</Box>
            </SpaceBetween>
          ))}
        </SpaceBetween>
      </SpaceBetween>
    </Container>
  )
}

// ── Main Page Component ───────────────────────────────────────────────────────
export default function TopologyPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedNode, setSelectedNode] = useState(null)
  const containerRef = useRef(null)
  const [dims, setDims] = useState({ width: 850, height: 620 })

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api.getNetworkTopology()
      setData(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Track container dimensions dynamically
  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver(entries => {
      if (!entries[0]) return
      const { width } = entries[0].contentRect
      setDims({ width: Math.max(450, width), height: 620 })
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  // Calculate Hierarchical Layout
  const layoutResult = useMemo(() => {
    if (!data) return { nodes: [], links: [], bounds: { width: 900, height: 620 } }
    return computeHierarchicalLayout({
      networks: data.networks || [],
      subnets: data.subnets || [],
      routers: data.routers || [],
      servers: data.servers || [],
      floatingIPs: data.floatingIPs || [],
      width: dims.width,
      height: dims.height,
    })
  }, [data, dims.width, dims.height])

  const hasNodes = layoutResult.nodes.length > 0

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Hierarchical tiered map of virtual networks, routers, subnets, and compute workloads"
          actions={
            <Button iconName="refresh" onClick={load} loading={loading}>
              Refresh
            </Button>
          }
        >
          Network Topology
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Resource Counters */}
        {data && (
          <Container>
            <KeyValuePairs
              columns={4}
              items={[
                { label: 'Networks', value: data.networks?.length || 0 },
                { label: 'Virtual Routers', value: data.routers?.length || 0 },
                { label: 'Subnets', value: data.subnets?.length || 0 },
                { label: 'Compute Instances', value: data.servers?.length || 0 },
              ]}
            />
          </Container>
        )}

        {/* Main Grid: Interactive Canvas (8 cols) + Resource Inspector (4 cols) */}
        <Grid gridDefinition={[{ colspan: { default: 12, l: 8 } }, { colspan: { default: 12, l: 4 } }]}>
          <Container
            header={
              <Header
                variant="h2"
                description="Top-down hierarchical topology. Click any node to inspect details."
              >
                Topology Canvas
              </Header>
            }
            disableContentPaddings
          >
            <div ref={containerRef}>
              {loading ? (
                <Box textAlign="center" padding="xxxl">
                  <SpaceBetween size="s" alignItems="center">
                    <Spinner size="large" />
                    <Box>Building hierarchical topology…</Box>
                  </SpaceBetween>
                </Box>
              ) : error ? (
                <Box padding="l">
                  <Alert type="error" header="Failed to load topology">
                    {error}
                  </Alert>
                </Box>
              ) : !hasNodes ? (
                <Box textAlign="center" padding="xxxl" color="text-body-secondary">
                  No network resources found. Create networks, routers, or instances to populate the topology.
                </Box>
              ) : (
                <SkylineTopologyCanvas
                  layoutResult={layoutResult}
                  width={dims.width}
                  height={dims.height}
                  selectedId={selectedNode?.id}
                  onSelectNode={setSelectedNode}
                />
              )}
            </div>
          </Container>

          <NodeInspector node={selectedNode} onNavigate={navigate} />
        </Grid>

        {/* Skyline Legend */}
        <SkylineLegend />
      </SpaceBetween>
    </ContentLayout>
  )
}
