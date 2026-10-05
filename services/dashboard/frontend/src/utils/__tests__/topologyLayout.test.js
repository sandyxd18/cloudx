import test from 'node:test'
import assert from 'node:assert/strict'
import { computeHierarchicalLayout, getOrthogonalRoundedPath } from '../topologyLayout.js'

test('computeHierarchicalLayout correctly handles string router:external and consolidates network/subnets', () => {
  // Realistic Networking API payload
  const networks = [
    { id: 'net-ext-1', name: 'public-net', 'router:external': 'External', status: 'ACTIVE' },
    { id: 'net-int-1', name: 'private-net', 'router:external': 'Internal', status: 'ACTIVE' },
  ]
  const subnets = [
    { id: 'sub-ext-1', name: 'public-subnet', network_id: 'net-ext-1', cidr: '172.24.4.0/24' },
    { id: 'sub-int-1', name: 'private-subnet', network_id: 'net-int-1', cidr: '10.0.0.0/24', router_id: 'rtr-1' },
  ]
  const routers = [
    { id: 'rtr-1', name: 'public-router', external_network_id: 'net-ext-1', subnet_ids: ['sub-int-1'] },
  ]
  const servers = [
    { id: 'srv-1', name: 'vm-demo-01', network_id: 'net-int-1', status: 'ACTIVE' },
  ]
  const floatingIPs = [
    { id: 'fip-1', floating_ip_address: '172.24.4.100', server_id: 'srv-1', external_network_id: 'net-ext-1' },
  ]

  const result = computeHierarchicalLayout({
    networks,
    subnets,
    routers,
    servers,
    floatingIPs,
    width: 1000,
    height: 700,
  })

  // 1. Check Tier 0: External Network card should be public-net with external CIDR attached
  const extNode = result.nodes.find(n => n.id === 'net-net-ext-1')
  assert.ok(extNode, 'external network node net-net-ext-1 exists')
  assert.equal(extNode.label, 'public-net')
  assert.equal(extNode.cidr, '172.24.4.0/24')
  assert.equal(extNode.tier, 0)

  // 2. Check Tier 1: Router
  const rtrNode = result.nodes.find(n => n.id === 'rtr-rtr-1')
  assert.ok(rtrNode, 'router node rtr-rtr-1 exists')
  assert.equal(rtrNode.label, 'public-router')
  assert.equal(rtrNode.tier, 1)

  // 3. Check Tier 2: Internal Network should be private-net (NOT public-subnet, NOT private-subnet)
  const intNetNode = result.nodes.find(n => n.id === 'net-net-int-1')
  assert.ok(intNetNode, 'internal network node net-net-int-1 exists in Tier 2')
  assert.equal(intNetNode.label, 'private-net')
  assert.equal(intNetNode.cidr, '10.0.0.0/24')
  assert.equal(intNetNode.tier, 2)

  // Ensure public-subnet does NOT exist in Tier 2
  const leakedExtSubnet = result.nodes.find(n => n.tier === 2 && n.label.includes('public-subnet'))
  assert.equal(leakedExtSubnet, undefined, 'public-subnet must not leak into Tier 2')

  // 4. Check Tier 3: Compute Instance vm-demo-01
  const srvNode = result.nodes.find(n => n.id === 'srv-srv-1')
  assert.ok(srvNode, 'server node srv-srv-1 exists')
  assert.equal(srvNode.label, 'vm-demo-01')
  assert.equal(srvNode.tier, 3)

  // 5. Check Floating IP links: must be 0 (Floating IP links removed from canvas)
  const fipLinks = result.links.filter(l => l.type === 'floating')
  assert.equal(fipLinks.length, 0, 'floating IP links must be 0')

  // 6. Check Structural links: gateway link and port link
  const gwLink = result.links.find(l => l.type === 'gateway')
  assert.ok(gwLink, 'gateway link exists')
  assert.equal(gwLink.source, 'net-net-ext-1')
  assert.equal(gwLink.target, 'rtr-rtr-1')

  const ifLink = result.links.find(l => l.type === 'interface')
  assert.ok(ifLink, 'router-to-internal-network link exists')
  assert.equal(ifLink.source, 'rtr-rtr-1')
  assert.equal(ifLink.target, 'net-net-int-1')

  const portLink = result.links.find(l => l.type === 'port')
  assert.ok(portLink, 'port link exists')
  assert.equal(portLink.source, 'net-net-int-1')
  assert.equal(portLink.target, 'srv-srv-1')
})

test('getOrthogonalRoundedPath generates straight line when x1 === x2', () => {
  const path = getOrthogonalRoundedPath(100, 50, 100, 200, 10)
  assert.equal(path, 'M 100 50 L 100 200')
})

test('getOrthogonalRoundedPath generates stepped path with rounded corners when x1 !== x2', () => {
  // Stepping rightward: (100, 50) -> (300, 200), midY = 125, r = 10
  const pathRight = getOrthogonalRoundedPath(100, 50, 300, 200, 10)
  assert.ok(pathRight.startsWith('M 100 50 L 100 115 Q 100 125 110 125 L 290 125 Q 300 125 300 135 L 300 200'),
    `unexpected path: ${pathRight}`)

  // Stepping leftward: (300, 50) -> (100, 200), midY = 125, r = 10
  const pathLeft = getOrthogonalRoundedPath(300, 50, 100, 200, 10)
  assert.ok(pathLeft.startsWith('M 300 50 L 300 115 Q 300 125 290 125 L 110 125 Q 100 125 100 135 L 100 200'),
    `unexpected path: ${pathLeft}`)
})
