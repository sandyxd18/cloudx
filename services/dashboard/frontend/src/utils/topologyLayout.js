/**
 * topologyLayout.js
 *
 * Deterministic Top-Down Hierarchical layout engine for CloudX network topology.
 * Emulates the OpenStack Skyline tiered architecture:
 * Tier 0 (top):    External Networks (e.g. public-net with external CIDR)
 * Tier 1:          Virtual Routers
 * Tier 2:          Internal / Tenant Networks (e.g. private-net with internal CIDR)
 * Tier 3 (bottom): Compute Instances (Servers)
 */

export const NODE_DIMENSIONS = {
  external: { width: 210, height: 50 },
  router:   { width: 180, height: 50 },
  subnet:   { width: 200, height: 50 },
  server:   { width: 160, height: 46 },
}

/**
 * Check if a network is an external / provider network.
 * Handles boolean, string ('External'), and provider network types.
 */
export function isExternalNet(net) {
  if (!net) return false
  return (
    net.external === true ||
    net['router:external'] === true ||
    net['router:external'] === 'External' ||
    net.router_external === true ||
    net['provider:network_type'] === 'external'
  )
}

export function computeHierarchicalLayout({
  networks = [],
  subnets = [],
  routers = [],
  servers = [],
  floatingIPs = [],
  width = 1100,
  height = 750,
}) {
  const nodes = []
  const links = []

  // 1. Group Subnets by Network ID
  const subnetsByNet = {}
  subnets.forEach(s => {
    if (!subnetsByNet[s.network_id]) subnetsByNet[s.network_id] = []
    subnetsByNet[s.network_id].push(s)
  })

  // Map Floating IPs by Server ID
  const fipByServer = {}
  floatingIPs.forEach(f => {
    if (f.server_id && f.floating_ip_address) {
      fipByServer[f.server_id] = f
    }
  })

  // 2. Classify Networks
  const extNets = networks.filter(isExternalNet)
  const intNets = networks.filter(n => !isExternalNet(n))

  // Tier 0: External Networks
  const tier0Nodes = extNets.map(net => {
    const netSubs = subnetsByNet[net.id] || []
    const cidrs = netSubs.map(s => s.cidr).filter(Boolean).join(', ')
    return {
      id: 'net-' + net.id,
      type: 'external',
      label: net.name || 'public-net',
      cidr: cidrs || '172.24.4.0/24',
      sublabel: 'External / Internet',
      raw: net,
      subnets: netSubs,
      tier: 0,
      width: NODE_DIMENSIONS.external.width,
      height: NODE_DIMENSIONS.external.height,
    }
  })

  // Fallback if no external network is flagged but router references one
  if (tier0Nodes.length === 0 && routers.some(r => r.external_network_id || r.external_gateway_info?.network_id)) {
    const rtrWithExt = routers.find(r => r.external_network_id || r.external_gateway_info?.network_id)
    const extNetId = rtrWithExt.external_network_id || rtrWithExt.external_gateway_info?.network_id
    tier0Nodes.push({
      id: 'net-' + extNetId,
      type: 'external',
      label: 'public-net',
      cidr: '172.24.4.0/24',
      sublabel: 'External / Internet',
      raw: { id: extNetId, name: 'public-net', external: true },
      tier: 0,
      width: NODE_DIMENSIONS.external.width,
      height: NODE_DIMENSIONS.external.height,
    })
  }

  // Tier 1: Virtual Routers
  const tier1Nodes = routers.map(rtr => ({
    id: 'rtr-' + rtr.id,
    type: 'router',
    label: rtr.name || 'router',
    sublabel: 'Virtual Router',
    raw: rtr,
    tier: 1,
    width: NODE_DIMENSIONS.router.width,
    height: NODE_DIMENSIONS.router.height,
  }))

  // Tier 2: Internal Networks (Tenant Networks)
  // Each internal network is a card displaying the Network Name + its Subnet CIDR
  const tier2Nodes = intNets.map(net => {
    const netSubs = subnetsByNet[net.id] || []
    const cidrs = netSubs.map(s => s.cidr).filter(Boolean).join(', ')
    return {
      id: 'net-' + net.id,
      type: 'subnet',
      label: net.name || 'private-net',
      cidr: cidrs || 'Private Network',
      network_id: net.id,
      raw: net,
      subnets: netSubs,
      tier: 2,
      width: NODE_DIMENSIONS.subnet.width,
      height: NODE_DIMENSIONS.subnet.height,
    }
  })

  // Tier 3: Compute Instances (Servers)
  const tier3Nodes = servers.map(srv => {
    const fip = fipByServer[srv.id]
    return {
      id: 'srv-' + srv.id,
      type: 'server',
      label: srv.name || 'server',
      status: srv.status,
      flavor: srv.flavor_name || srv.flavor || '',
      floating_ip: fip?.floating_ip_address,
      raw: srv,
      tier: 3,
      width: NODE_DIMENSIONS.server.width,
      height: NODE_DIMENSIONS.server.height,
    }
  })

  // 3. Vertical Tier Coordinates (Top-Down)
  const yTiers = {
    0: 70,
    1: 210,
    2: 370,
    3: 540,
  }

  // 4. Horizontal Distribution
  const padX = 40
  const maxNodesInTier = Math.max(
    tier0Nodes.length,
    tier1Nodes.length,
    tier2Nodes.length,
    tier3Nodes.length,
    1
  )
  const minRequiredWidth = Math.max(width, maxNodesInTier * 230 + padX * 2)

  function distributeX(tierItems, yPos) {
    const count = tierItems.length
    if (count === 0) return
    const step = (minRequiredWidth - padX * 2) / (count + 1)
    tierItems.forEach((node, i) => {
      node.x = padX + step * (i + 1)
      node.y = yPos
    })
  }

  distributeX(tier0Nodes, yTiers[0])
  distributeX(tier1Nodes, yTiers[1])
  distributeX(tier2Nodes, yTiers[2])
  distributeX(tier3Nodes, yTiers[3])

  nodes.push(...tier0Nodes, ...tier1Nodes, ...tier2Nodes, ...tier3Nodes)
  const nodeMap = new Map(nodes.map(n => [n.id, n]))

  // 5. Build Links
  // 5a. Gateway links: External Network -> Virtual Router
  routers.forEach(r => {
    const extNetId = r.external_network_id || r.external_gateway_info?.network_id
    if (extNetId) {
      const extId = 'net-' + extNetId
      const rtrId = 'rtr-' + r.id
      if (nodeMap.has(extId) && nodeMap.has(rtrId)) {
        links.push({
          id: `link-gw-${r.id}`,
          source: extId,
          target: rtrId,
          type: 'gateway',
        })
      }
    }
  })

  // 5b. Router to Internal Network interface links
  routers.forEach(r => {
    const rtrId = 'rtr-' + r.id
    const rtrSubnetIds = new Set([
      ...(r.subnet_ids || []),
      ...(r.interfaces_info || []).map(i => i.subnet_id).filter(Boolean),
      ...(r.interfaces || []).map(i => i.subnet_id || i.id).filter(Boolean),
    ])

    intNets.forEach(net => {
      const netSubs = subnetsByNet[net.id] || []
      const isConnected = netSubs.some(s => s.router_id === r.id || rtrSubnetIds.has(s.id))
      const targetNetId = 'net-' + net.id

      if (isConnected && nodeMap.has(rtrId) && nodeMap.has(targetNetId)) {
        links.push({
          id: `link-if-${r.id}-${net.id}`,
          source: rtrId,
          target: targetNetId,
          type: 'interface',
        })
      }
    })
  })

  // 5c. Internal Network to Server port links
  servers.forEach(s => {
    const srvId = 'srv-' + s.id
    if (!nodeMap.has(srvId)) return

    // Find parent network
    let targetNetNodeId = null
    if (s.network_id && nodeMap.has('net-' + s.network_id)) {
      targetNetNodeId = 'net-' + s.network_id
    } else if (s.subnet_id) {
      const sub = subnets.find(item => item.id === s.subnet_id)
      if (sub && nodeMap.has('net-' + sub.network_id)) {
        targetNetNodeId = 'net-' + sub.network_id
      }
    } else if (tier2Nodes.length > 0) {
      targetNetNodeId = tier2Nodes[0].id
    }

    if (targetNetNodeId) {
      links.push({
        id: `link-port-${s.id}-${targetNetNodeId}`,
        source: targetNetNodeId,
        target: srvId,
        type: 'port',
      })
    }
  })

  // Note: Floating IP links are removed from canvas layout per requirement.

  return {
    nodes,
    links,
    bounds: {
      width: Math.max(minRequiredWidth, 1000),
      height: Math.max(yTiers[3] + 120, height),
    },
  }
}

/**
 * Generate orthogonal stepped path with rounded corners (right-angle elbows).
 * Creates a clean circuit/diagram style connector:
 * (x1, y1) -> down to midY -> 90° rounded turn -> horizontal to x2 -> 90° rounded turn -> down to (x2, y2).
 */
export function getOrthogonalRoundedPath(x1, y1, x2, y2, radius = 10) {
  // If perfectly or nearly aligned vertically, render a straight vertical line
  if (Math.abs(x1 - x2) < 2) {
    return `M ${x1} ${y1} L ${x2} ${y2}`
  }

  const midY = (y1 + y2) / 2
  const dx = x2 - x1
  const dy = y2 - y1

  // Clamp radius so corners do not exceed half the distance
  const r = Math.min(radius, Math.abs(dx) / 2, Math.abs(dy) / 4)

  if (dx > 0) {
    // Stepping to the right
    return `M ${x1} ${y1} L ${x1} ${midY - r} Q ${x1} ${midY} ${x1 + r} ${midY} L ${x2 - r} ${midY} Q ${x2} ${midY} ${x2} ${midY + r} L ${x2} ${y2}`
  } else {
    // Stepping to the left
    return `M ${x1} ${y1} L ${x1} ${midY - r} Q ${x1} ${midY} ${x1 - r} ${midY} L ${x2 + r} ${midY} Q ${x2} ${midY} ${x2} ${midY + r} L ${x2} ${y2}`
  }
}
