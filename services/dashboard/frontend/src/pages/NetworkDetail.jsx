import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import Badge from '@cloudscape-design/components/badge'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, PageLoading, TableEmpty, fmtDate, shortId } from '../components/common.jsx'

export default function NetworkDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [network, setNetwork] = useState(null)
  const [subnets, setSubnets] = useState([])
  const [ports, setPorts]     = useState([])
  const [liveServerIds, setLiveServerIds] = useState(new Set())
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const [net, subs, prts, srvs] = await Promise.allSettled([
      api.getNetwork(id),
      api.getSubnets(),
      api.getPorts(),
      api.getServers(),
    ])
    if (net.status === 'fulfilled') setNetwork(net.value)
    else toast('Failed to load network', 'error')
    if (subs.status === 'fulfilled') setSubnets((subs.value?.items || []).filter(s => s.network_id === id))
    if (prts.status === 'fulfilled') setPorts((prts.value?.items || []).filter(p => p.network_id === id))
    if (srvs.status === 'fulfilled') {
      // Live server ids — ports referencing a deleted instance are rendered
      // as non-clickable "Deleted instance" instead of a dead detail link.
      setLiveServerIds(new Set((srvs.value?.items || []).map(s => s.id)))
    }
    setLoading(false)
  }, [id, toast])

  useEffect(() => { load() }, [load])

  if (loading) return <PageLoading text="Loading network" />
  if (!network) return <Alert type="error" header="Network not found">The requested network does not exist or is not visible in this project.</Alert>

  const isExternal = !!(network.external || network['router:external'] === 'External')
  const attachedPorts = ports.filter(p => !!p.device_id)

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={network.id}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button onClick={() => navigate('/networks')}>Back to networks</Button>
            </SpaceBetween>
          }
        >
          {network.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Network summary</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Status', value: <ResourceStatus status={network.status || 'active'} /> },
              { label: 'External', value: isExternal ? <Badge color="severity-medium">External</Badge> : 'No' },
              { label: 'Shared', value: <BoolStatus value={!!network.shared} /> },
              { label: 'Subnets', value: subnets.length },
            ]}
          />
        </Container>

        <Tabs
          tabs={[
            {
              id: 'details',
              label: 'Details',
              content: (
                <Container header={<Header variant="h2">Network details</Header>}>
                  <KeyValuePairs
                    columns={3}
                    items={[
                      { label: 'Network ID', value: network.id },
                      { label: 'Name', value: network.name },
                      { label: 'Admin state', value: <BoolStatus value={network.admin_state_up !== false} trueText="UP" falseText="DOWN" /> },
                      { label: 'Network type', value: network.network_type || (isExternal ? 'flat' : 'geneve') },
                      { label: 'Physical network', value: network.physical_network || (isExternal ? 'physnet1' : '—') },
                      { label: 'Segmentation ID', value: network.segmentation_id ?? '—' },
                      { label: 'MTU', value: network.mtu ?? '—' },
                      { label: 'Project', value: network.project_id || '—' },
                      { label: 'Created', value: fmtDate(network.created_at) },
                      { label: 'DNS domain', value: network.dns_domain || '—' },
                      { label: 'OVN logical switch', value: network.ovn_switch_name || '—' },
                      { label: 'Availability zone', value: network.availability_zone || 'nova' },
                    ]}
                  />
                </Container>
              ),
            },
            {
              id: 'subnets',
              label: `Subnets (${subnets.length})`,
              content: (
                <Table
                  variant="container"
                  header={
                    <Header
                      variant="h2"
                      counter={`(${subnets.length})`}
                      actions={<Button variant="primary" onClick={() => navigate(`/subnets/create?network=${id}`)}>Create subnet</Button>}
                    >
                      Subnets
                    </Header>
                  }
                  items={subnets}
                  trackBy="id"
                  columnDefinitions={[
                    { id: 'name', header: 'Name', cell: s => s.name, isRowHeader: true },
                    { id: 'cidr', header: 'CIDR', cell: s => s.cidr },
                    { id: 'version', header: 'IP version', cell: s => `IPv${s.ip_version || 4}` },
                    { id: 'gateway', header: 'Gateway', cell: s => s.gateway_ip || s.gateway || '—' },
                    { id: 'dhcp', header: 'DHCP', cell: s => <BoolStatus value={s.enable_dhcp !== false} trueText="Enabled" falseText="Disabled" /> },
                    { id: 'pool', header: 'Allocation pool', cell: s => (s.allocation_start ? `${s.allocation_start} – ${s.allocation_end}` : '—') },
                    { id: 'dns', header: 'DNS servers', cell: s => (s.dns_nameservers || []).join(', ') || '—' },
                  ]}
                  empty={<TableEmpty title="No subnets" description="Add a subnet to allocate IP addresses on this network." />}
                />
              ),
            },
            {
              id: 'ports',
              label: `Ports (${ports.length})`,
              content: (
                <Table
                  variant="container"
                  header={<Header variant="h2" counter={`(${ports.length})`} description={`${attachedPorts.length} port(s) bound to a device`}>Ports</Header>}
                  items={ports}
                  trackBy="id"
                  columnDefinitions={[
                    { id: 'id', header: 'Port ID', cell: p => shortId(p.id, 16), isRowHeader: true },
                    { id: 'mac', header: 'MAC address', cell: p => p.mac_address || '—' },
                    { id: 'ip', header: 'Fixed IP', cell: p => p.ip_address || '—' },
                    { id: 'status', header: 'Status', cell: p => <ResourceStatus status={p.status || 'down'} /> },
                    {
                      id: 'device',
                      header: 'Attached device',
                      cell: p => {
                        if (!p.device_id) return 'Unbound'
                        if (p.device_owner?.startsWith('object-store:') || p.device_owner === 'object-store:bucket') {
                          return <Box fontWeight="bold">Bucket: {p.device_id}</Box>
                        }
                        if (p.device_owner?.startsWith('network:')) {
                          return <Box color="text-body-secondary">{p.device_owner}</Box>
                        }
                        if (liveServerIds.has(p.device_id)) {
                          return <Link href={`/servers/${p.device_id}`} onFollow={(e) => { e.preventDefault(); navigate(`/servers/${p.device_id}`) }}>{shortId(p.device_id, 12)}</Link>
                        }
                        if (p.device_owner?.startsWith('compute:') || p.device_owner === 'compute:nova-like') {
                          return <Box variant="small" color="text-body-secondary">Deleted instance ({shortId(p.device_id, 8)})</Box>
                        }
                        return <Box fontWeight="bold">{shortId(p.device_id, 12)}</Box>
                      },
                    },
                    { id: 'owner', header: 'Device owner', cell: p => p.device_owner || '—' },
                    { id: 'dns', header: 'DNS name', cell: p => p.dns_name || '—' },
                  ]}
                  empty={<TableEmpty title="No ports" description="No ports have been created on this network yet." />}
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
