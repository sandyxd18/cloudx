import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Pagination from '@cloudscape-design/components/pagination'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast, useAuth } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

const PAGE_SIZE = 25
const money = (n, cur = 'IDR') =>
  `${cur} ${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const day = (d) => (d ? String(d).slice(0, 10) : '—')

function saveBlob(name, blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

// Fetch a list page by page-size; `hasMore` is guessed from a full page.
function usePaged(fetcher, deps) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const toast = useToast()
  const load = useCallback(() => {
    setLoading(true)
    fetcher({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })
      .then(r => { const a = Array.isArray(r) ? r : []; setItems(a); setHasMore(a.length === PAGE_SIZE) })
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, toast, ...deps])
  useEffect(() => { load() }, [load])
  const pagination = (
    <Pagination
      currentPageIndex={page}
      pagesCount={hasMore ? page + 1 : page}
      openEnd={hasMore}
      onChange={({ detail }) => setPage(detail.currentPageIndex)}
    />
  )
  return { items, loading, load, pagination, setPage }
}

export default function BillingPage() {
  const toast = useToast()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [activeTab, setActiveTab] = useState('invoices')
  const [invoiceFilter, setInvoiceFilter] = useState('')
  const [summary, setSummary] = useState(null)
  const [ratingSheets, setRatingSheets] = useState([])
  const [loadingRates, setLoadingRates] = useState(true)
  const [detail, setDetail] = useState(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({ project_id: '', year: '', month: '' })
  const [busy, setBusy] = useState(false)

  const invoices = usePaged(api.getInvoices, [])
  const usage = usePaged(api.getUsageRecords, [])

  const loadRates = useCallback(() => {
    setLoadingRates(true)
    api.getRatingSheets({ limit: 200 })
      .then(r => setRatingSheets(Array.isArray(r) ? r : []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingRates(false))
  }, [toast])

  const loadSummary = useCallback(() => {
    api.getBillingSummary().then(setSummary).catch(e => toast(`Summary: ${e.message}`, 'error'))
  }, [toast])

  useEffect(() => { loadRates(); loadSummary() }, [loadRates, loadSummary])

  const handleDownloadPdf = async (inv) => {
    try {
      const res = await api.downloadInvoicePdf(inv.id)
      const name = `invoice-${inv.invoice_number || inv.id}`
      if (res?.pdf_base64) {
        const bin = atob(res.pdf_base64)
        const bytes = Uint8Array.from(bin, c => c.charCodeAt(0))
        saveBlob(`${name}.pdf`, new Blob([bytes], { type: 'application/pdf' }))
      } else if (res?.html) {
        saveBlob(`${name}.html`, new Blob([res.html], { type: 'text/html' }))
      } else {
        throw new Error('Server returned no document')
      }
    } catch (e) {
      toast(`PDF download error: ${e.message}`, 'error')
    }
  }

  const openDetail = async (inv) => {
    try { setDetail(await api.getInvoice(inv.id)) } catch (e) { toast(e.message, 'error') }
  }

  const submitInvoice = async () => {
    const body = {}
    if (form.project_id.trim()) body.project_id = form.project_id.trim()
    if (form.year) body.year = Number(form.year)
    if (form.month) body.month = Number(form.month)
    setBusy(true)
    try {
      await api.createInvoice(body)
      toast('Invoice created', 'success')
      setCreateOpen(false)
      invoices.load(); loadSummary()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const iq = invoiceFilter.trim().toLowerCase()
  const shown = iq
    ? invoices.items.filter(i =>
        String(i.invoice_number || i.id).toLowerCase().includes(iq) ||
        String(i.status || '').toLowerCase().includes(iq) ||
        String(i.project_id || '').toLowerCase().includes(iq))
    : invoices.items

  return (
    <ContentLayout
      header={<Header variant="h1" description="Resource rating, usage metering, and invoice generation">Usage & Billing</Header>}
    >
      <SpaceBetween size="l">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
          <Container header={<Header variant="h3">Current period total</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{money(summary?.total_cost, summary?.currency)}</Box>
            <Box color="text-status-inactive">Accumulated usage charges</Box>
          </Container>
          <Container header={<Header variant="h3">Invoices</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{invoices.items.length}{invoices.items.length === PAGE_SIZE ? '+' : ''}</Box>
            <Box color="text-status-inactive">On this page</Box>
          </Container>
          <Container header={<Header variant="h3">Pricing rules</Header>}>
            <Box fontSize="display-l" fontWeight="bold">{ratingSheets.length}</Box>
            <Box color="text-status-inactive">Active rating rules</Box>
          </Container>
        </div>

        {summary?.by_resource?.length > 0 && (
          <Table
            header={<Header variant="h2">Cost by resource</Header>}
            columnDefinitions={[
              { id: 'r', header: 'Resource', cell: i => <Box fontWeight="bold">{i.resource_type}</Box> },
              { id: 'q', header: 'Quantity', cell: i => Number(i.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }) },
              { id: 'c', header: 'Cost', cell: i => money(i.cost, summary.currency) },
            ]}
            items={summary.by_resource}
          />
        )}

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail: d }) => setActiveTab(d.activeTabId)}
          tabs={[
            {
              id: 'invoices',
              label: 'Invoices',
              content: (
                <Table
                  variant="full-page"
                  stickyHeader
                  header={
                    <Header
                      variant="h2"
                      counter={counterText(shown.length)}
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button iconName="refresh" onClick={invoices.load} ariaLabel="Refresh invoices" />
                          {isAdmin && <Button variant="primary" onClick={() => setCreateOpen(true)}>Create invoice</Button>}
                        </SpaceBetween>
                      }
                    >
                      Invoices & Statements
                    </Header>
                  }
                  columnDefinitions={[
                    { id: 'no', header: 'Invoice', cell: i => <Button variant="inline-link" onClick={() => openDetail(i)}>{i.invoice_number || i.id}</Button> },
                    ...(isAdmin ? [{ id: 'proj', header: 'Project', cell: i => i.project_id || '—' }] : []),
                    { id: 'period', header: 'Billing period', cell: i => `${day(i.period_start)} to ${day(i.period_end)}` },
                    { id: 'amount', header: 'Amount due', cell: i => money(i.total, i.currency) },
                    { id: 'status', header: 'Status', cell: i => <ResourceStatus status={i.status || 'UNKNOWN'} /> },
                    { id: 'actions', header: 'Actions', cell: i => <Button variant="inline-link" iconName="download" onClick={() => handleDownloadPdf(i)}>Download</Button> },
                  ]}
                  items={shown}
                  loading={invoices.loading}
                  loadingText="Loading invoices"
                  filter={<TextFilter filteringText={invoiceFilter} onChange={({ detail: d }) => setInvoiceFilter(d.filteringText)} filteringPlaceholder="Filter this page" />}
                  pagination={invoices.pagination}
                  empty={invoiceFilter
                    ? <TableNoMatch onClear={() => setInvoiceFilter('')} />
                    : <TableEmpty title="No invoices available" subtitle="Invoices are generated monthly from recorded usage." />}
                />
              ),
            },
            {
              id: 'usage',
              label: 'Usage records',
              content: (
                <Table
                  variant="full-page"
                  stickyHeader
                  header={<Header variant="h2" counter={counterText(usage.items.length)} actions={<Button iconName="refresh" onClick={usage.load} ariaLabel="Refresh usage" />}>Metered usage</Header>}
                  columnDefinitions={[
                    { id: 't', header: 'Resource type', cell: i => <Box fontWeight="bold">{i.resource_type}</Box> },
                    { id: 'id', header: 'Resource', cell: i => i.resource_name || i.resource_id || '—' },
                    { id: 'q', header: 'Quantity', cell: i => `${Number(i.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${i.unit || ''}` },
                    { id: 'c', header: 'Cost', cell: i => money(i.total_cost) },
                    { id: 'p', header: 'Period', cell: i => `${day(i.period_start)} → ${day(i.period_end)}` },
                  ]}
                  items={usage.items}
                  loading={usage.loading}
                  loadingText="Loading usage records"
                  pagination={usage.pagination}
                  empty={<TableEmpty title="No usage recorded" subtitle="The billing worker records usage hourly for running resources." />}
                />
              ),
            },
            {
              id: 'pricing',
              label: 'Rating rules',
              content: (
                <Table
                  variant="full-page"
                  stickyHeader
                  header={<Header variant="h2" counter={counterText(ratingSheets.length)} actions={<Button iconName="refresh" onClick={loadRates} ariaLabel="Refresh rating sheets" />}>Resource pricing rules</Header>}
                  columnDefinitions={[
                    { id: 'service', header: 'Resource', cell: i => <Box fontWeight="bold">{i.resource_type}</Box> },
                    { id: 'rate', header: 'Rate', cell: i => `${money(i.rate_per_unit, i.currency)} / ${i.unit || 'hour'}` },
                    { id: 'description', header: 'Description', cell: i => i.description || '-' },
                  ]}
                  items={ratingSheets}
                  loading={loadingRates}
                  empty={<TableEmpty title="No rating sheets defined" subtitle="Usage cannot be priced until rating sheets exist." />}
                />
              ),
            },
          ]}
        />
      </SpaceBetween>

      <Modal
        visible={!!detail}
        onDismiss={() => setDetail(null)}
        size="large"
        header={`Invoice ${detail?.invoice_number || detail?.id || ''}`}
        footer={<Box float="right"><Button onClick={() => setDetail(null)}>Close</Button></Box>}
      >
        <Table
          variant="embedded"
          columnDefinitions={[
            { id: 'd', header: 'Description', cell: i => i.description || i.resource_type },
            { id: 'q', header: 'Quantity', cell: i => `${Number(i.quantity || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${i.unit || ''}` },
            { id: 'r', header: 'Rate', cell: i => money(i.rate_per_unit, detail?.currency) },
            { id: 'a', header: 'Amount', cell: i => money(i.amount ?? i.total, detail?.currency) },
          ]}
          items={detail?.items || []}
          header={<Header variant="h3" description={`${day(detail?.period_start)} to ${day(detail?.period_end)}`}>Total {money(detail?.total, detail?.currency)}</Header>}
          empty={<TableEmpty title="No line items" subtitle="" />}
        />
      </Modal>

      <Modal
        visible={createOpen}
        onDismiss={() => setCreateOpen(false)}
        header="Create invoice"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} onClick={submitInvoice}>Create</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Project ID" description="Leave empty for your own project"><Input value={form.project_id} onChange={({ detail: d }) => setForm(f => ({ ...f, project_id: d.value }))} /></FormField>
          <FormField label="Year" description="Optional, e.g. 2026"><Input type="number" value={form.year} onChange={({ detail: d }) => setForm(f => ({ ...f, year: d.value }))} /></FormField>
          <FormField label="Month" description="Optional, 1-12"><Input type="number" value={form.month} onChange={({ detail: d }) => setForm(f => ({ ...f, month: d.value }))} /></FormField>
        </SpaceBetween>
      </Modal>
    </ContentLayout>
  )
}
