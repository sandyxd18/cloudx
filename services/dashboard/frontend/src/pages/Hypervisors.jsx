import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import ProgressBar from '@cloudscape-design/components/progress-bar'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

export default function HypervisorsPage() {
  const toast = useToast()
  const [nodes, setNodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')

  const loadNodes = useCallback(() => {
    setLoading(true)
    api.getComputeNodes()
      .then(r => setNodes(r?.compute_nodes || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { loadNodes() }, [loadNodes])

  const totalVms = nodes.reduce((acc, n) => acc + (n.running_vms || 0), 0)
  const totalRamMb = nodes.reduce((acc, n) => acc + (n.memory_mb_total || 0), 0)
  const usedRamMb = nodes.reduce((acc, n) => acc + (n.memory_mb_used || 0), 0)

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? nodes.filter(n => n.host?.toLowerCase().includes(q) || n.name?.toLowerCase().includes(q))
    : nodes

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <Button iconName="refresh" onClick={loadNodes} ariaLabel="Refresh hypervisors" />
          }
          description="Physical KVM/libvirt compute host status, capacity allocations, and resource providers"
        >
          Hypervisors & Host Nodes
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Host capacity summary widgets */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
          <Container header={<Header variant="h3">Compute Nodes</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {nodes.length}
            </Box>
            <Box color="text-status-inactive">Active libvirt/KVM hosts</Box>
          </Container>
          <Container header={<Header variant="h3">Running VMs</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {totalVms}
            </Box>
            <Box color="text-status-inactive">Instances hosted across cluster</Box>
          </Container>
          <Container header={<Header variant="h3">Memory Allocation</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {totalRamMb > 0 ? `${(usedRamMb / 1024).toFixed(1)} / ${(totalRamMb / 1024).toFixed(1)} GB` : '—'}
            </Box>
            <ProgressBar
              value={totalRamMb > 0 ? Math.round((usedRamMb / totalRamMb) * 100) : 0}
              variant="standalone"
            />
          </Container>
        </div>

        {/* Nodes Table */}
        <Table
          variant="full-page"
          stickyHeader
          header={
            <Header
              variant="h2"
              counter={counterText(nodes.length)}
            >
              Hypervisor Nodes
            </Header>
          }
          columnDefinitions={[
            { id: 'host', header: 'Hostname', cell: item => <Box fontWeight="bold">{item.host || item.name}</Box>, sortingField: 'host' },
            { id: 'status', header: 'State', cell: item => <ResourceStatus status={item.status || 'UP'} /> },
            { id: 'vms', header: 'Running VMs', cell: item => item.running_vms ?? 0 },
            { id: 'cpu', header: 'CPU Usage', cell: item => `${item.cpu_percent_used ?? 0}%` },
            {
              id: 'memory',
              header: 'Memory Allocation',
              cell: item => {
                const used = (item.memory_mb_used || 0) / 1024
                const tot = (item.memory_mb_total || 0) / 1024
                return `${used.toFixed(1)} / ${tot.toFixed(1)} GB`
              },
            },
            { id: 'rp', header: 'Placement RP UUID', cell: item => item.resource_provider_uuid || '—' },
            { id: 'seen', header: 'Last Heartbeat', cell: item => item.last_seen || '—' },
          ]}
          items={filtered}
          loading={loading}
          loadingText="Loading hypervisor nodes"
          filter={
            <TextFilter
              filteringText={filter}
              onChange={({ detail }) => setFilter(detail.filteringText)}
              filteringPlaceholder="Filter compute nodes by host"
            />
          }
          empty={
            filter ? (
              <TableNoMatch onClear={() => setFilter('')} />
            ) : (
              <TableEmpty title="No compute nodes reporting" subtitle="Compute nodes register automatically via placement heartbeat." />
            )
          }
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
