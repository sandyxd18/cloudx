import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const STATUS_MAP = {
  ACTIVE: 'success', PENDING_CREATE: 'pending', ERROR: 'error', ONLINE: 'success', OFFLINE: 'stopped',
}

export default function LoadBalancersPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getLoadBalancers()
      setItems(data.load_balancers || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const lb of selected) {
      try { await api.deleteLoadBalancer(lb.id) } catch { /* ignore */ }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} load balancer(s) deleted`, 'success')
    load()
  }

  if (loading && !items.length) return <PageLoading text="Loading load balancers" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="High-availability traffic distribution using OVN L4 and HAProxy L7 backends"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh load balancers" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'delete', text: 'Delete load balancer' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') setDeleteModal(true)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/load-balancers/create')}>
                Create load balancer
              </Button>
            </SpaceBetween>
          }
        >
          Load Balancers
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            cell: item => (
              <Link href={`/load-balancers/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/load-balancers/${item.id}`) }}>
                {item.name}
              </Link>
            ),
            sortingField: 'name',
          },
          {
            id: 'type',
            header: 'Type',
            cell: item => (
              <Box>{item.lb_type === 'ALB' ? 'Application (L7)' : 'Network (L4)'}</Box>
            ),
          },
          { id: 'vip', header: 'VIP Address', cell: item => item.vip_address || '-' },
          {
            id: 'status',
            header: 'Provisioning status',
            cell: item => (
              <StatusIndicator type={STATUS_MAP[item.provisioning_status] || 'info'}>
                {item.provisioning_status}
              </StatusIndicator>
            ),
          },
          {
            id: 'operating',
            header: 'Operating status',
            cell: item => (
              <StatusIndicator type={STATUS_MAP[item.operating_status] || 'info'}>
                {item.operating_status}
              </StatusIndicator>
            ),
          },
        ]}
        items={items}
        loading={loading}
        loadingText="Loading load balancers"
        empty={<TableEmpty title="No load balancers" subtitle="Create a load balancer to distribute traffic." />}
        header={<Header variant="h2">Load Balancers</Header>}
      />
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Load Balancer"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
