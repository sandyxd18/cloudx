import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

export default function EventLogsPage() {
  const toast = useToast()
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')

  const loadLogs = useCallback(() => {
    setLoading(true)
    api.getEventLogs(200)
      .then(r => setLogs(r || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { loadLogs() }, [loadLogs])

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? logs.filter(l =>
        l.event_type?.toLowerCase().includes(q) ||
        l.service?.toLowerCase().includes(q) ||
        l.user_id?.toLowerCase().includes(q) ||
        l.resource_id?.toLowerCase().includes(q)
      )
    : logs

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <Button iconName="refresh" onClick={loadLogs} ariaLabel="Refresh event logs" />
          }
          description="CloudTrail / Cadf-compatible centralized security, identity, and resource audit events"
        >
          Audit Event Logs
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        header={
          <Header
            variant="h2"
            counter={counterText(logs.length)}
          >
            System & Security Events
          </Header>
        }
        columnDefinitions={[
          { id: 'time', header: 'Timestamp', cell: item => item.timestamp || item.created_at || '—' },
          { id: 'type', header: 'Event Type', cell: item => <Box fontWeight="bold">{item.event_type}</Box>, sortingField: 'event_type' },
          { id: 'service', header: 'Service', cell: item => (item.service || 'cloudx').toUpperCase() },
          { id: 'actor', header: 'Actor / User ID', cell: item => item.user_id || 'system' },
          { id: 'resource', header: 'Resource ID', cell: item => item.resource_id || '—' },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'SUCCESS'} /> },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading audit events"
        filter={
          <TextFilter
            filteringText={filter}
            onChange={({ detail }) => setFilter(detail.filteringText)}
            filteringPlaceholder="Filter events by type, service, user or resource"
          />
        }
        empty={
          filter ? (
            <TableNoMatch onClear={() => setFilter('')} />
          ) : (
            <TableEmpty title="No event logs found" subtitle="System events are dispatched whenever cloud resources change." />
          )
        }
      />
    </ContentLayout>
  )
}
