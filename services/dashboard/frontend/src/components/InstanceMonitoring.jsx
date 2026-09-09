/**
 * InstanceMonitoring.jsx — EC2-style "Monitoring" tab content for an instance.
 *
 * Polls GET /compute/servers/:id/metrics (compute-worker's in-memory 10s-sample
 * ring buffer, 24h retention) and renders CPU/memory/network/disk charts,
 * matching the AWS EC2 instance detail "Monitoring" tab layout.
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import AreaChart from '@cloudscape-design/components/area-chart'
import Box from '@cloudscape-design/components/box'
import Checkbox from '@cloudscape-design/components/checkbox'
import Container from '@cloudscape-design/components/container'
import Grid from '@cloudscape-design/components/grid'
import Header from '@cloudscape-design/components/header'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'

const AUTO_REFRESH_MS = 10000

const TIME_RANGES = [
  { value: '1h',  label: 'Last 1 hour' },
  { value: '3h',  label: 'Last 3 hours' },
  { value: '6h',  label: 'Last 6 hours' },
  { value: '12h', label: 'Last 12 hours' },
  { value: '24h', label: 'Last 24 hours' },
]

function fmtBytesPerSec(v) {
  if (!v) return '0 B/s'
  if (v < 1024) return `${v.toFixed(0)} B/s`
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB/s`
  return `${(v / 1024 ** 2).toFixed(1)} MB/s`
}

/** One metric chart: title, description, series builder, and a value formatter for the tooltip/axis. */
function MetricChart({ title, description, data, series, yFormatter, empty }) {
  return (
    <Container header={<Header variant="h3" description={description}>{title}</Header>}>
      <AreaChart
        series={series}
        xScaleType="time"
        height={200}
        hideFilter
        yTitle=""
        xTitle=""
        i18nStrings={{
          filterLabel: 'Filter',
          xTickFormatter: d => new Date(d).toLocaleTimeString(),
          yTickFormatter: yFormatter,
        }}
        detailPopoverSeriesContent={({ y }) => ({ key: '', value: yFormatter ? yFormatter(y) : y })}
        ariaLabel={title}
        empty={
          <Box textAlign="center" color="inherit">
            {empty || 'No data available yet — samples accumulate every 10 seconds while the instance is running.'}
          </Box>
        }
      />
    </Container>
  )
}

export default function InstanceMonitoring({ serverId, isActive }) {
  const [timeRange, setTimeRange] = useState(TIME_RANGES[0])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [samples, setSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const timerRef = useRef(null)

  const load = useCallback(() => {
    api.getServerMetrics(serverId, timeRange.value)
      .then(r => {
        setSamples(r?.samples || [])
        setError(r?.error ? 'Metrics temporarily unavailable — worker unreachable.' : '')
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [serverId, timeRange])

  useEffect(() => { setLoading(true); load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    timerRef.current = setInterval(load, AUTO_REFRESH_MS)
    return () => clearInterval(timerRef.current)
  }, [autoRefresh, load])

  const points = (key) => samples.map(s => ({ x: new Date(s.timestamp * 1000), y: s[key] ?? 0 }))

  return (
    <SpaceBetween size="l">
      <SpaceBetween direction="horizontal" size="m" alignItems="center">
        <Select
          selectedOption={timeRange}
          onChange={({ detail }) => setTimeRange(detail.selectedOption)}
          options={TIME_RANGES}
          ariaLabel="Monitoring time range"
        />
        <Checkbox checked={autoRefresh} onChange={({ detail }) => setAutoRefresh(detail.checked)}>
          Auto-refresh (10s)
        </Checkbox>
      </SpaceBetween>

      {!isActive && (
        <Box color="text-status-inactive">
          Instance is not running — showing the last recorded samples, if any.
        </Box>
      )}
      {error && <Box color="text-status-error">{error}</Box>}

      <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
        <MetricChart
          title="CPU utilization"
          description="Percentage of allocated vCPUs in use"
          series={[{ title: 'CPU %', type: 'area', data: points('cpu_percent') }]}
          yFormatter={v => `${v}%`}
        />
        <MetricChart
          title="Memory utilization"
          description="Guest memory in use vs. allocated"
          series={[{ title: 'Memory (MB)', type: 'area', data: points('mem_used_mb') }]}
          yFormatter={v => `${v} MB`}
        />
      </Grid>

      <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
        <MetricChart
          title="Network in"
          description="Inbound traffic on the instance's network interface"
          series={[{ title: 'Network in', type: 'area', data: points('net_rx_bps') }]}
          yFormatter={fmtBytesPerSec}
        />
        <MetricChart
          title="Network out"
          description="Outbound traffic on the instance's network interface"
          series={[{ title: 'Network out', type: 'area', data: points('net_tx_bps') }]}
          yFormatter={fmtBytesPerSec}
        />
      </Grid>

      <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
        <MetricChart
          title="Disk read"
          description="Root volume read throughput"
          series={[{ title: 'Disk read', type: 'area', data: points('disk_read_bps') }]}
          yFormatter={fmtBytesPerSec}
        />
        <MetricChart
          title="Disk write"
          description="Root volume write throughput"
          series={[{ title: 'Disk write', type: 'area', data: points('disk_write_bps') }]}
          yFormatter={fmtBytesPerSec}
        />
      </Grid>
    </SpaceBetween>
  )
}
