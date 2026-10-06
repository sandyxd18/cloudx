import { useEffect, useState, useCallback, useMemo } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import LineChart from '@cloudscape-design/components/line-chart'
import Pagination from '@cloudscape-design/components/pagination'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

const RANGES = [
  { value: '1', label: 'Last 1 hour', granularity: 'minute' },
  { value: '6', label: 'Last 6 hours', granularity: 'minute' },
  { value: '24', label: 'Last 24 hours', granularity: 'hour' },
  { value: '168', label: 'Last 7 days', granularity: 'hour' },
]
const PAGE_SIZE = 50
const ALL = { value: '', label: 'All instances' }
const fmt = (n) => (Number.isFinite(n) ? Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—')

export default function TelemetryPage() {
  const toast = useToast()
  const [servers, setServers] = useState([])
  const [instance, setInstance] = useState(ALL)
  const [range, setRange] = useState(RANGES[0])
  const [metricType, setMetricType] = useState(null)

  const [stats, setStats] = useState([])
  const [series, setSeries] = useState([])
  const [samples, setSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [chartLoading, setChartLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(1)

  const serverName = useMemo(() => Object.fromEntries(servers.map(s => [s.id, s.name])), [servers])
  const instanceId = instance.value || undefined

  const loadData = useCallback(() => {
    setLoading(true)
    const common = { instance_id: instanceId, hours: range.value }
    Promise.allSettled([
      api.getServers(),
      api.getTelemetryStats(common),
      api.getTelemetryMetrics({ instance_id: instanceId, limit: 500 }),
    ]).then(([srv, st, met]) => {
      if (srv.status === 'fulfilled') setServers(srv.value?.items || [])
      if (st.status === 'fulfilled') setStats(Array.isArray(st.value) ? st.value : [])
      else toast(`Stats: ${st.reason?.message}`, 'error')
      if (met.status === 'fulfilled') setSamples(Array.isArray(met.value) ? met.value : [])
      else toast(`Samples: ${met.reason?.message}`, 'error')
    }).finally(() => setLoading(false))
  }, [instanceId, range, toast])

  useEffect(() => { loadData(); setPage(1) }, [loadData])

  const metricTypes = useMemo(
    () => [...new Set(stats.map(s => s.metric_type))].sort().map(t => ({ value: t, label: t })),
    [stats],
  )
  useEffect(() => {
    if (!metricTypes.length) { setMetricType(null); return }
    if (!metricType || !metricTypes.some(m => m.value === metricType.value)) setMetricType(metricTypes[0])
  }, [metricTypes]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!metricType) { setSeries([]); return }
    setChartLoading(true)
    api.getTelemetrySeries({
      metric_type: metricType.value,
      instance_id: instanceId,
      hours: range.value,
      granularity: range.granularity,
    })
      .then(r => setSeries(Array.isArray(r) ? r : []))
      .catch(e => { setSeries([]); toast(`Chart: ${e.message}`, 'error') })
      .finally(() => setChartLoading(false))
  }, [metricType, instanceId, range, toast])

  const unit = stats.find(s => s.metric_type === metricType?.value)?.unit || ''
  const points = (key) => series.map(p => ({ x: new Date(p.timestamp), y: Number(p[key]) }))
  const chartSeries = [
    { title: 'Average', type: 'line', data: points('avg') },
    { title: 'Max', type: 'line', data: points('max'), color: '#d13212' },
  ]

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? samples.filter(m =>
        m.metric_type?.toLowerCase().includes(q) ||
        String(m.instance_id || '').toLowerCase().includes(q) ||
        String(serverName[m.instance_id] || '').toLowerCase().includes(q))
    : samples
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const visible = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const totalSamples = stats.reduce((a, s) => a + (s.count || 0), 0)

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={<Button iconName="refresh" onClick={loadData} ariaLabel="Refresh telemetry metrics" />}
          description="Instance utilization metrics sampled by the telemetry worker"
        >
          Telemetry & Metrics
        </Header>
      }
    >
      <SpaceBetween size="l">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
          <Container header={<Header variant="h3">Samples in range</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{totalSamples.toLocaleString()}</Box>
            <Box color="text-status-inactive">{range.label.toLowerCase()}</Box>
          </Container>
          <Container header={<Header variant="h3">Monitored instances</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{new Set(stats.map(s => s.instance_id).filter(Boolean)).size || servers.length}</Box>
            <Box color="text-status-inactive">Reporting metrics</Box>
          </Container>
          <Container header={<Header variant="h3">Metric types</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{metricTypes.length}</Box>
            <Box color="text-status-inactive">{metricTypes.map(m => m.label).join(', ') || 'None yet'}</Box>
          </Container>
        </div>

        <Container
          header={
            <Header variant="h2" description="Time-bucketed average and peak values">
              Utilization over time
            </Header>
          }
        >
          <SpaceBetween size="m">
            <SpaceBetween direction="horizontal" size="m">
              <div style={{ minWidth: 260 }}>
                <Select
                  selectedOption={instance}
                  onChange={({ detail }) => setInstance(detail.selectedOption)}
                  options={[ALL, ...servers.map(s => ({ value: s.id, label: s.name, description: s.id }))]}
                  ariaLabel="Instance"
                />
              </div>
              <div style={{ minWidth: 200 }}>
                <Select
                  selectedOption={metricType}
                  onChange={({ detail }) => setMetricType(detail.selectedOption)}
                  options={metricTypes}
                  placeholder="Metric"
                  empty="No metrics yet"
                  ariaLabel="Metric type"
                />
              </div>
              <div style={{ minWidth: 180 }}>
                <Select
                  selectedOption={{ value: range.value, label: range.label }}
                  onChange={({ detail }) => setRange(RANGES.find(r => r.value === detail.selectedOption.value))}
                  options={RANGES.map(r => ({ value: r.value, label: r.label }))}
                  ariaLabel="Time range"
                />
              </div>
            </SpaceBetween>
            <LineChart
              series={chartSeries}
              xScaleType="time"
              xTitle="Time"
              yTitle={unit || 'Value'}
              height={280}
              hideFilter
              statusType={chartLoading ? 'loading' : 'finished'}
              loadingText="Loading chart"
              empty={<Box textAlign="center" color="inherit">No data for this selection</Box>}
              noMatch={<Box textAlign="center" color="inherit">No matching data</Box>}
              ariaLabel="Metric time series"
              i18nStrings={{ xTickFormatter: d => d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' }) }}
            />
          </SpaceBetween>
        </Container>

        <Table
          header={<Header variant="h2" counter={counterText(stats.length)}>Summary per metric</Header>}
          columnDefinitions={[
            { id: 'type', header: 'Metric', cell: i => <Box fontWeight="bold">{i.metric_type}</Box> },
            { id: 'inst', header: 'Instance', cell: i => (i.instance_id ? (serverName[i.instance_id] || i.instance_id) : 'All') },
            { id: 'last', header: 'Last', cell: i => `${fmt(i.last)} ${i.unit || ''}` },
            { id: 'avg', header: 'Avg', cell: i => fmt(i.avg) },
            { id: 'min', header: 'Min', cell: i => fmt(i.min) },
            { id: 'max', header: 'Max', cell: i => fmt(i.max) },
            { id: 'count', header: 'Samples', cell: i => i.count },
          ]}
          items={stats}
          loading={loading}
          loadingText="Loading statistics"
          empty={<TableEmpty title="No statistics" subtitle="Metrics appear once the telemetry worker has polled running instances (every 30s)." />}
        />

        <Table
          variant="full-page"
          stickyHeader
          header={<Header variant="h2" counter={counterText(filtered.length)}>Latest samples</Header>}
          columnDefinitions={[
            { id: 'metric', header: 'Metric', cell: i => <Box fontWeight="bold">{i.metric_type}</Box> },
            { id: 'value', header: 'Value', cell: i => `${fmt(i.value)} ${i.unit || ''}` },
            { id: 'inst', header: 'Instance', cell: i => (serverName[i.instance_id] || i.instance_id || '—') },
            { id: 'ts', header: 'Timestamp', cell: i => (i.timestamp ? new Date(i.timestamp).toLocaleString() : '—') },
          ]}
          items={visible}
          loading={loading}
          loadingText="Loading samples"
          filter={
            <TextFilter
              filteringText={filter}
              onChange={({ detail }) => { setFilter(detail.filteringText); setPage(1) }}
              filteringPlaceholder="Filter by metric or instance"
            />
          }
          pagination={<Pagination currentPageIndex={page} pagesCount={pages} onChange={({ detail }) => setPage(detail.currentPageIndex)} />}
          empty={filter ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No samples" subtitle="Nothing collected for this instance yet." />}
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
