import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

export default function TelemetryPage() {
  const toast = useToast()
  const [servers, setServers] = useState([])
  const [selectedServer, setSelectedServer] = useState(null)
  const [metrics, setMetrics] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [globalStats, setGlobalStats] = useState(null)

  const loadData = useCallback(() => {
    setLoading(true)
    Promise.allSettled([
      api.getServers(),
      api.getTelemetryStats(),
      api.getTelemetryMetrics(selectedServer?.value),
    ]).then(([srvRes, statsRes, metRes]) => {
      if (srvRes.status === 'fulfilled') {
        const srvs = srvRes.value?.items || []
        setServers(srvs)
      }
      if (statsRes.status === 'fulfilled') {
        setGlobalStats(statsRes.value)
      }
      if (metRes.status === 'fulfilled') {
        setMetrics(metRes.value || [])
      }
    }).catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [selectedServer, toast])

  useEffect(() => { loadData() }, [loadData])

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? metrics.filter(m => m.metric_name?.toLowerCase().includes(q) || m.resource_id?.toLowerCase().includes(q))
    : metrics

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <Button iconName="refresh" onClick={loadData} ariaLabel="Refresh telemetry metrics" />
          }
          description="Ceilometer & Gnocchi-compatible telemetry metric samples and system utilization"
        >
          Telemetry & Metrics
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Metric stats summary widgets */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
          <Container header={<Header variant="h3">Total Samples</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {globalStats?.total_samples ?? metrics.length}
            </Box>
            <Box color="text-status-inactive">Collected metric samples</Box>
          </Container>
          <Container header={<Header variant="h3">Monitored VMs</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {servers.length}
            </Box>
            <Box color="text-status-inactive">Active compute instances</Box>
          </Container>
          <Container header={<Header variant="h3">Active Metric Types</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {new Set(metrics.map(m => m.metric_name)).size || 4}
            </Box>
            <Box color="text-status-inactive">CPU, RAM, Disk, Net I/O</Box>
          </Container>
        </div>

        {/* Filter by VM */}
        <Container header={<Header variant="h2">Filter by Instance</Header>}>
          <SpaceBetween direction="horizontal" size="m">
            <div style={{ minWidth: '320px' }}>
              <Select
                selectedOption={selectedServer}
                onChange={({ detail }) => setSelectedServer(detail.selectedOption)}
                options={[
                  { value: null, label: 'All Instances' },
                  ...servers.map(s => ({ value: s.id, label: s.name, description: s.id })),
                ]}
                placeholder="Select an instance to filter samples"
              />
            </div>
            {selectedServer?.value && (
              <Button onClick={() => setSelectedServer(null)}>Reset Filter</Button>
            )}
          </SpaceBetween>
        </Container>

        {/* Metric Samples Table */}
        <Table
          variant="full-page"
          stickyHeader
          header={
            <Header
              variant="h2"
              counter={counterText(metrics.length)}
            >
              Metric Samples
            </Header>
          }
          columnDefinitions={[
            { id: 'metric', header: 'Metric Name', cell: item => <Box fontWeight="bold">{item.metric_name}</Box>, sortingField: 'metric_name' },
            { id: 'value', header: 'Value', cell: item => `${item.value} ${item.unit || ''}` },
            { id: 'resource', header: 'Resource ID', cell: item => item.resource_id || item.instance_id || '—' },
            { id: 'timestamp', header: 'Timestamp', cell: item => item.timestamp || item.created_at || '—' },
          ]}
          items={filtered}
          loading={loading}
          loadingText="Loading telemetry metrics"
          filter={
            <TextFilter
              filteringText={filter}
              onChange={({ detail }) => setFilter(detail.filteringText)}
              filteringPlaceholder="Filter metrics by name or resource"
            />
          }
          empty={
            filter ? (
              <TableNoMatch onClear={() => setFilter('')} />
            ) : (
              <TableEmpty
                title="No metrics collected yet"
                subtitle="Metrics are automatically polled from active VM instances every 60 seconds."
              />
            )
          }
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
