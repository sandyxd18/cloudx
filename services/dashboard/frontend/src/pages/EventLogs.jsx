import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Multiselect from '@cloudscape-design/components/multiselect'
import Pagination from '@cloudscape-design/components/pagination'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

const PAGE_SIZE = 50
const ts = (d) => (d ? new Date(d).toLocaleString() : '—')
const service = (t) => (t || '').split('.')[0].toUpperCase() || 'CLOUDX'

function usePaged(fetcher) {
  const toast = useToast()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const load = useCallback(() => {
    setLoading(true)
    fetcher({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then(r => { const a = Array.isArray(r) ? r : []; setItems(a); setHasMore(a.length === PAGE_SIZE) })
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [fetcher, page, toast])
  useEffect(() => { load() }, [load])
  const pagination = <Pagination currentPageIndex={page} pagesCount={hasMore ? page + 1 : page} openEnd={hasMore} onChange={({ detail }) => setPage(detail.currentPageIndex)} />
  return { items, loading, load, pagination }
}

export default function EventLogsPage() {
  const toast = useToast()
  const [tab, setTab] = useState('logs')
  const [filter, setFilter] = useState('')
  const [payload, setPayload] = useState(null)
  const [types, setTypes] = useState([])
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ name: '', endpoint_url: '', event_types: [] })
  const [secret, setSecret] = useState(null)
  const [busy, setBusy] = useState(false)

  const logs = usePaged(api.getEventLogs)
  const subs = usePaged(api.getEventSubscriptions)
  const deliveries = usePaged(api.getEventDeliveries)

  useEffect(() => {
    api.getEventTypes().then(r => setTypes((Array.isArray(r) ? r : []).map(t => {
      const v = typeof t === 'string' ? t : t.event_type || t.name
      return { value: v, label: v, description: t.description }
    }))).catch(() => {})
  }, [])

  const q = filter.trim().toLowerCase()
  const shown = q
    ? logs.items.filter(l => [l.event_type, l.user_id, l.resource_id, l.resource_type].some(v => String(v || '').toLowerCase().includes(q)))
    : logs.items

  const createSub = async () => {
    setBusy(true)
    try {
      const r = await api.createEventSubscription({
        name: form.name || undefined,
        endpoint_url: form.endpoint_url,
        event_types: form.event_types.length ? form.event_types.map(o => o.value) : ['*'],
      })
      setSecret(r?.secret || null)
      setCreateOpen(false)
      setForm({ name: '', endpoint_url: '', event_types: [] })
      subs.load()
      toast('Subscription created', 'success')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const act = async (fn, ok) => {
    try { await fn(); toast(ok, 'success'); subs.load(); deliveries.load() } catch (e) { toast(e.message, 'error') }
  }

  return (
    <ContentLayout header={<Header variant="h1" description="Platform events, webhook subscriptions and delivery status">Events & Webhooks</Header>}>
      <Tabs
        activeTabId={tab}
        onChange={({ detail }) => setTab(detail.activeTabId)}
        tabs={[
          {
            id: 'logs', label: 'Event log',
            content: (
              <Table
                variant="full-page" stickyHeader
                header={<Header variant="h2" counter={counterText(shown.length)} actions={<Button iconName="refresh" onClick={logs.load} ariaLabel="Refresh event logs" />}>System events</Header>}
                columnDefinitions={[
                  { id: 'time', header: 'Timestamp', cell: i => ts(i.timestamp || i.created_at) },
                  { id: 'type', header: 'Event type', cell: i => <Button variant="inline-link" onClick={() => setPayload(i)}>{i.event_type}</Button> },
                  { id: 'svc', header: 'Service', cell: i => service(i.event_type) },
                  { id: 'actor', header: 'Actor', cell: i => i.user_id || 'system' },
                  { id: 'res', header: 'Resource', cell: i => i.resource_id ? `${i.resource_type || ''} ${i.resource_id}`.trim() : '—' },
                ]}
                items={shown} loading={logs.loading} loadingText="Loading events"
                filter={<TextFilter filteringText={filter} onChange={({ detail }) => setFilter(detail.filteringText)} filteringPlaceholder="Filter this page" />}
                pagination={logs.pagination}
                empty={filter ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No events" subtitle="Events appear when resources change." />}
              />
            ),
          },
          {
            id: 'subs', label: 'Webhooks',
            content: (
              <Table
                variant="full-page" stickyHeader
                header={<Header variant="h2" counter={counterText(subs.items.length)} actions={<SpaceBetween direction="horizontal" size="xs"><Button iconName="refresh" onClick={subs.load} ariaLabel="Refresh subscriptions" /><Button variant="primary" onClick={() => setCreateOpen(true)}>Create webhook</Button></SpaceBetween>}>Subscriptions</Header>}
                columnDefinitions={[
                  { id: 'name', header: 'Name', cell: i => i.name || i.id },
                  { id: 'url', header: 'Endpoint', cell: i => i.endpoint_url },
                  { id: 'ev', header: 'Events', cell: i => (i.event_types || []).join(', ') || '*' },
                  { id: 'act', header: 'Active', cell: i => <ResourceStatus status={i.is_active === false ? 'INACTIVE' : 'ACTIVE'} /> },
                  { id: 'del', header: 'Actions', cell: i => <Button variant="inline-link" onClick={() => act(() => api.deleteEventSubscription(i.id), 'Subscription deleted')}>Delete</Button> },
                ]}
                items={subs.items} loading={subs.loading} pagination={subs.pagination}
                empty={<TableEmpty title="No webhooks" subtitle="Create one to receive events over HTTP." />}
              />
            ),
          },
          {
            id: 'deliveries', label: 'Deliveries',
            content: (
              <Table
                variant="full-page" stickyHeader
                header={<Header variant="h2" counter={counterText(deliveries.items.length)} actions={<Button iconName="refresh" onClick={deliveries.load} ariaLabel="Refresh deliveries" />}>Webhook deliveries</Header>}
                columnDefinitions={[
                  { id: 'ev', header: 'Event', cell: i => i.event_type || i.event_id },
                  { id: 'st', header: 'Status', cell: i => <ResourceStatus status={String(i.status || '').toUpperCase()} /> },
                  { id: 'at', header: 'Attempts', cell: i => i.attempt_count ?? i.attempts ?? 0 },
                  { id: 'code', header: 'HTTP', cell: i => i.response_code || '—' },
                  { id: 'err', header: 'Last error', cell: i => i.last_error || '—' },
                  { id: 'nx', header: 'Next retry', cell: i => ts(i.next_retry_at) },
                  { id: 'act', header: 'Actions', cell: i => i.status !== 'success' && <Button variant="inline-link" onClick={() => act(() => api.retryEventDelivery(i.id), 'Retry queued')}>Retry</Button> },
                ]}
                items={deliveries.items} loading={deliveries.loading} pagination={deliveries.pagination}
                empty={<TableEmpty title="No deliveries" subtitle="Deliveries are created when events match a webhook." />}
              />
            ),
          },
        ]}
      />

      <Modal visible={!!payload} onDismiss={() => setPayload(null)} size="large" header={payload?.event_type || 'Event'} footer={<Box float="right"><Button onClick={() => setPayload(null)}>Close</Button></Box>}>
        <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(payload?.payload ?? payload, null, 2)}</pre>
      </Modal>

      <Modal visible={!!secret} onDismiss={() => setSecret(null)} header="Webhook secret" footer={<Box float="right"><Button onClick={() => setSecret(null)}>Done</Button></Box>}>
        <SpaceBetween size="s">
          <Box>Copy it now. It is shown only once.</Box>
          <Box variant="code">{secret}</Box>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createOpen} onDismiss={() => setCreateOpen(false)} header="Create webhook"
        footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs"><Button variant="link" onClick={() => setCreateOpen(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!form.endpoint_url} onClick={createSub}>Create</Button></SpaceBetween></Box>}
      >
        <SpaceBetween size="m">
          <FormField label="Name"><Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} /></FormField>
          <FormField label="Endpoint URL"><Input value={form.endpoint_url} placeholder="https://example.com/hook" onChange={({ detail }) => setForm(f => ({ ...f, endpoint_url: detail.value }))} /></FormField>
          <FormField label="Event types" description="Empty means all events">
            <Multiselect selectedOptions={form.event_types} options={types} placeholder="All events" onChange={({ detail }) => setForm(f => ({ ...f, event_types: detail.selectedOptions }))} />
          </FormField>
        </SpaceBetween>
      </Modal>
    </ContentLayout>
  )
}
