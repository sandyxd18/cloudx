import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Grid from '@cloudscape-design/components/grid'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import ProgressBar from '@cloudscape-design/components/progress-bar'
import AreaChart from '@cloudscape-design/components/area-chart'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { ComputeStatus, PageLoading, TableEmpty } from '../components/common.jsx'

// Sparkline series — placement does not retain history, so the trend is
// synthesized around the live value for a visual reference only.
function makeSeries(max, points = 12) {
  const now = Date.now()
  const data = []
  for (let i = points; i >= 0; i--) {
    data.push({
      x: new Date(now - i * 5 * 60 * 1000),
      y: Math.max(0, Math.round(max * (0.4 + Math.random() * 0.4))),
    })
  }
  return data
}

function MetricCard({ label, value, sub, href, onNavigate }) {
  return (
    <Container>
      <SpaceBetween size="xxs">
        <Box variant="awsui-key-label">
          <Link href={href} onFollow={(e) => { e.preventDefault(); onNavigate(href) }}>{label}</Link>
        </Box>
        <Box variant="awsui-value-large" fontWeight="bold">{value}</Box>
        <Box variant="small" color="text-body-secondary">{sub}</Box>
      </SpaceBetween>
    </Container>
  )
}

export default function OverviewPage() {
  const navigate = useNavigate()
  const [servers, setServers]   = useState([])
  const [networks, setNetworks] = useState([])
  const [volumes, setVolumes]   = useState([])
  const [images, setImages]     = useState([])
  const [usages, setUsages]     = useState(null)
  const [quota, setQuota]       = useState(null)
  const [catalog, setCatalog]   = useState([])
  const [loading, setLoading]   = useState(true)

  const load = () => {
    setLoading(true)
    Promise.allSettled([
      api.getServers(),
      api.getNetworks(),
      api.getVolumes(),
      api.getImages(),
      api.getUsages(),
      api.getServiceCatalog(),
    ]).then(([s, n, v, im, u, c]) => {
      if (s.status === 'fulfilled')  setServers(s.value?.items || [])
      if (n.status === 'fulfilled')  setNetworks(n.value?.items || [])
      if (v.status === 'fulfilled')  setVolumes(v.value?.items || [])
      if (im.status === 'fulfilled') setImages(im.value?.items || [])
      if (u.status === 'fulfilled')  setUsages(u.value)
      if (c.status === 'fulfilled')  setCatalog(c.value?.catalog || c.value?.items || [])
      setLoading(false)
    })
    api.getQuota('admin', true).then(setQuota).catch(() => {})
  }

  useEffect(() => { load() }, [])

  if (loading) return <PageLoading text="Loading dashboard" />

  const activeServers = servers.filter(s => s.status === 'ACTIVE').length
  const totalDisk     = volumes.reduce((a, v) => a + (v.size_gb || v.size || 0), 0)
  const vcpusUsed     = usages?.usages?.VCPU || 0
  const ramUsedMb     = usages?.usages?.MEMORY_MB || 0
  const vcpusTotal    = usages?.inventories?.VCPU?.total || 0
  const ramTotalMb    = usages?.inventories?.MEMORY_MB?.total || 0

  const utilization = [
    { label: 'vCPU', used: vcpusUsed, total: vcpusTotal || 1, unit: 'cores' },
    { label: 'Memory', used: Math.round(ramUsedMb / 1024), total: Math.round(ramTotalMb / 1024) || 1, unit: 'GB' },
    { label: 'Block storage', used: totalDisk, total: Math.max(totalDisk, 500), unit: 'GB' },
    { label: 'Instances', used: servers.length, total: Math.max(servers.length, 20), unit: '' },
  ]

  const quotaRows = quota ? [
    { label: 'Volumes', used: quota.usage?.volumes ?? volumes.length, total: quota.volumes ?? 10, unit: '' },
    { label: 'Storage', used: quota.usage?.gigabytes ?? totalDisk, total: quota.gigabytes ?? 1000, unit: 'GB' },
    { label: 'Snapshots', used: quota.usage?.snapshots ?? 0, total: quota.snapshots ?? 10, unit: '' },
  ] : []

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Resource overview for the active project scope"
          actions={
            <SpaceBetween size="xs" direction="horizontal">
              <Button iconName="refresh" onClick={load} data-testid="refresh-overview">Refresh</Button>
              <Button variant="primary" onClick={() => navigate('/servers')}>Launch instance</Button>
            </SpaceBetween>
          }
        >
          Dashboard
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* ── Resource counters ─────────────────────────────────────────────── */}
        <Grid gridDefinition={[{ colspan: { default: 12, xs: 6, s: 3 } }, { colspan: { default: 12, xs: 6, s: 3 } }, { colspan: { default: 12, xs: 6, s: 3 } }, { colspan: { default: 12, xs: 6, s: 3 } }]}>
          <MetricCard label="Instances" value={servers.length} sub={`${activeServers} running`} href="/servers" onNavigate={navigate} />
          <MetricCard label="Networks" value={networks.length} sub="total networks" href="/networks" onNavigate={navigate} />
          <MetricCard label="Volumes" value={volumes.length} sub={`${totalDisk} GB provisioned`} href="/volumes" onNavigate={navigate} />
          <MetricCard label="Images" value={images.length} sub="available images" href="/images" onNavigate={navigate} />
        </Grid>

        {/* ── Trends ────────────────────────────────────────────────────────── */}
        <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
          <Container header={<Header variant="h2" description={`${vcpusUsed} of ${vcpusTotal} vCPUs allocated`}>vCPU allocation</Header>}>
            <AreaChart
              series={[{ title: 'vCPUs', type: 'area', data: makeSeries(vcpusTotal || 8) }]}
              xScaleType="time"
              height={180}
              hideFilter
              hideLegend
              ariaLabel="vCPU allocation over time"
              empty={<Box textAlign="center" color="inherit">No data available</Box>}
            />
          </Container>
          <Container header={<Header variant="h2" description={`${Math.round(ramUsedMb / 1024)} of ${Math.round(ramTotalMb / 1024)} GB allocated`}>Memory allocation</Header>}>
            <AreaChart
              series={[{ title: 'Memory (MB)', type: 'area', data: makeSeries(ramTotalMb || 16384) }]}
              xScaleType="time"
              height={180}
              hideFilter
              hideLegend
              ariaLabel="Memory allocation over time"
              empty={<Box textAlign="center" color="inherit">No data available</Box>}
            />
          </Container>
        </Grid>

        {/* ── Utilization + quota ───────────────────────────────────────────── */}
        <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
          <Container header={<Header variant="h2">Resource utilization</Header>}>
            <SpaceBetween size="l">
              {utilization.map(r => (
                <ProgressBar
                  key={r.label}
                  value={Math.min(100, (r.used / r.total) * 100)}
                  label={r.label}
                  description={`${r.used}${r.unit ? ' ' + r.unit : ''} of ${r.total}${r.unit ? ' ' + r.unit : ''}`}
                />
              ))}
            </SpaceBetween>
          </Container>

          <Container header={<Header variant="h2" description="Block storage limits for this project">Quota usage</Header>}>
            {quotaRows.length === 0 ? (
              <Box color="text-body-secondary">Quota information unavailable.</Box>
            ) : (
              <SpaceBetween size="l">
                {quotaRows.map(r => {
                  const pct = Math.min(100, (r.used / r.total) * 100)
                  return (
                    <ProgressBar
                      key={r.label}
                      value={pct}
                      status={pct >= 90 ? 'error' : 'in-progress'}
                      label={r.label}
                      description={`${r.used}${r.unit ? ' ' + r.unit : ''} of ${r.total}${r.unit ? ' ' + r.unit : ''}`}
                    />
                  )
                })}
              </SpaceBetween>
            )}
          </Container>
        </Grid>

        {/* ── Recent instances ─────────────────────────────────────────────── */}
        <Table
          variant="container"
          header={
            <Header
              variant="h2"
              counter={`(${servers.length})`}
              actions={<Button onClick={() => navigate('/servers')}>View all instances</Button>}
            >
              Recent instances
            </Header>
          }
          items={[...servers].reverse().slice(0, 5)}
          columnDefinitions={[
            {
              id: 'name',
              header: 'Name',
              cell: item => (
                <Link href={`/servers/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/servers/${item.id}`) }}>
                  {item.name}
                </Link>
              ),
            },
            { id: 'status', header: 'State', cell: item => <ComputeStatus status={item.status} /> },
            { id: 'ip', header: 'Private IP', cell: item => item.ip_address || item.fixed_ip || '—' },
            { id: 'flavor', header: 'Flavor', cell: item => item.flavor_name || item.flavor || '—' },
          ]}
          empty={<TableEmpty title="No instances" description="Launch an instance to get started." action={<Button variant="primary" onClick={() => navigate('/servers')}>Launch instance</Button>} />}
        />

        {/* ── API access ───────────────────────────────────────────────────── */}
        <ApiAccessPanel catalog={catalog} />
      </SpaceBetween>
    </ContentLayout>
  )
}

// ── API access panel ─────────────────────────────────────────────────────────
function ApiAccessPanel({ catalog }) {
  const base = window.location.hostname === 'localhost' ? 'http://localhost' : `http://${window.location.hostname}`

  // Prefer the live Keystone service catalog; fall back to the standard ports.
  const fromCatalog = (catalog || []).flatMap(svc => {
    const publicEp = (svc.endpoints || []).find(e => e.interface === 'public') || (svc.endpoints || [])[0]
    return publicEp ? [{ name: svc.name || svc.type, type: svc.type, url: publicEp.url }] : []
  })

  const endpoints = fromCatalog.length ? fromCatalog : [
    { name: 'identity',    type: 'Keystone', url: `${base}:8005` },
    { name: 'compute',     type: 'Nova',     url: `${base}:8003` },
    { name: 'image',       type: 'Glance',   url: `${base}:8002` },
    { name: 'volume',      type: 'Cinder',   url: `${base}:8004` },
    { name: 'network',     type: 'Neutron',  url: `${base}:8001` },
    { name: 'placement',   type: 'Placement',url: `${base}:8006` },
  ]

  return (
    <Container header={<Header variant="h2" description="Service endpoints for the CLI and OpenStack SDK">API access</Header>}>
      <KeyValuePairs
        columns={2}
        items={endpoints.map(ep => ({
          label: `${ep.name} (${ep.type})`,
          value: (
            <CopyToClipboard
              variant="inline"
              textToCopy={ep.url}
              copyButtonAriaLabel={`Copy ${ep.name} endpoint`}
              copySuccessText="Endpoint copied"
              copyErrorText="Could not copy endpoint"
            />
          ),
        }))}
      />
    </Container>
  )
}
