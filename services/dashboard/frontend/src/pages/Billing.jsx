import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

export default function BillingPage() {
  const toast = useToast()
  const [activeTab, setActiveTab] = useState('invoices')

  const [invoices, setInvoices] = useState([])
  const [loadingInvoices, setLoadingInvoices] = useState(true)
  const [invoiceFilter, setInvoiceFilter] = useState('')

  const [ratingSheets, setRatingSheets] = useState([])
  const [loadingRates, setLoadingRates] = useState(true)

  const [summary, setSummary] = useState(null)
  const [loadingSummary, setLoadingSummary] = useState(true)

  const loadInvoices = useCallback(() => {
    setLoadingInvoices(true)
    api.getInvoices()
      .then(r => setInvoices(r || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingInvoices(false))
  }, [toast])

  const loadRates = useCallback(() => {
    setLoadingRates(true)
    api.getRatingSheets()
      .then(r => setRatingSheets(r || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingRates(false))
  }, [toast])

  const loadSummary = useCallback(() => {
    setLoadingSummary(true)
    api.getBillingSummary()
      .then(r => setSummary(r))
      .catch(() => {})
      .finally(() => setLoadingSummary(false))
  }, [])

  useEffect(() => {
    loadInvoices()
    loadRates()
    loadSummary()
  }, [loadInvoices, loadRates, loadSummary])

  const handleDownloadPdf = async (inv) => {
    try {
      const res = await api.downloadInvoicePdf(inv.id)
      toast(`Invoice PDF generated: ${res?.download_url || inv.id}`, 'success')
    } catch (e) {
      toast(`PDF download error: ${e.message}`, 'error')
    }
  }

  const iq = invoiceFilter.trim().toLowerCase()
  const filteredInvoices = iq
    ? invoices.filter(i => i.id?.toLowerCase().includes(iq) || i.status?.toLowerCase().includes(iq))
    : invoices

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Cloudkitty-compatible rating, usage metering, and invoice generation"
        >
          Usage & Billing
        </Header>
      }
    >
      <SpaceBetween size="l">
        {/* Cost Summary Cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
          <Container header={<Header variant="h3">Current Month Total</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              ${(summary?.total_cost ?? 0).toFixed(2)}
            </Box>
            <Box color="text-status-inactive">Accumulated estimated charges</Box>
          </Container>
          <Container header={<Header variant="h3">Active Invoices</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {invoices.length}
            </Box>
            <Box color="text-status-inactive">Billing statements</Box>
          </Container>
          <Container header={<Header variant="h3">Pricing Rules</Header>}>
            <Box fontSize="display-l" fontWeight="bold">
              {ratingSheets.length}
            </Box>
            <Box color="text-status-inactive">Active rating rules</Box>
          </Container>
        </div>

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId)}
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
                      counter={counterText(invoices.length)}
                      actions={
                        <Button iconName="refresh" onClick={loadInvoices} ariaLabel="Refresh invoices" />
                      }
                    >
                      Invoices & Statements
                    </Header>
                  }
                  columnDefinitions={[
                    { id: 'id', header: 'Invoice ID', cell: item => <Box fontWeight="bold">{item.id}</Box> },
                    { id: 'period', header: 'Billing Period', cell: item => `${item.period_start || '—'} to ${item.period_end || '—'}` },
                    { id: 'amount', header: 'Amount Due', cell: item => `$${(item.total_amount ?? 0).toFixed(2)}` },
                    { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'PAID'} /> },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: item => (
                        <Button variant="inline-link" iconName="download" onClick={() => handleDownloadPdf(item)}>
                          Download PDF
                        </Button>
                      ),
                    },
                  ]}
                  items={filteredInvoices}
                  loading={loadingInvoices}
                  loadingText="Loading invoices"
                  filter={
                    <TextFilter
                      filteringText={invoiceFilter}
                      onChange={({ detail }) => setInvoiceFilter(detail.filteringText)}
                      filteringPlaceholder="Filter invoices"
                    />
                  }
                  empty={
                    invoiceFilter ? (
                      <TableNoMatch onClear={() => setInvoiceFilter('')} />
                    ) : (
                      <TableEmpty title="No invoices available" subtitle="Invoices are generated at the end of every billing cycle." />
                    )
                  }
                />
              ),
            },
            {
              id: 'pricing',
              label: 'Rating Rules (Pricing)',
              content: (
                <Table
                  variant="full-page"
                  stickyHeader
                  header={
                    <Header
                      variant="h2"
                      counter={counterText(ratingSheets.length)}
                      actions={
                        <Button iconName="refresh" onClick={loadRates} ariaLabel="Refresh rating sheets" />
                      }
                    >
                      Resource Pricing Rules
                    </Header>
                  }
                  columnDefinitions={[
                    { id: 'service', header: 'Service / Resource', cell: item => <Box fontWeight="bold">{item.service || item.service_name}</Box> },
                    { id: 'rate', header: 'Rate per Unit', cell: item => `$${item.rate ?? 0.01} / ${item.unit || 'hour'}` },
                    { id: 'currency', header: 'Currency', cell: item => item.currency || 'USD' },
                    { id: 'description', header: 'Description', cell: item => item.description || '-' },
                  ]}
                  items={ratingSheets}
                  loading={loadingRates}
                  empty={<TableEmpty title="No rating sheets defined" subtitle="Default rates apply across standard resource classes." />}
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
