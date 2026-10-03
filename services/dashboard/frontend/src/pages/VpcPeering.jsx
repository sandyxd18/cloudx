import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function VpcPeeringPage() {
  const toast = useToast()
  const [peerings, setPeerings] = useState([])
  const [routers, setRouters] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState([])

  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState({ requesterRouter: null, accepterRouter: null })

  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([
      api.getVpcPeerings(),
      api.getRouters(),
    ]).then(([p, r]) => {
      if (p.status === 'fulfilled') setPeerings(p.value?.items || (Array.isArray(p.value) ? p.value : []))
      if (r.status === 'fulfilled') setRouters(r.value?.items || (Array.isArray(r.value) ? r.value : []))
    }).catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!form.requesterRouter || !form.accepterRouter) {
      setCreateError('Both requester and accepter routers are required.')
      return
    }
    if (form.requesterRouter.value === form.accepterRouter.value) {
      setCreateError('Requester and accepter routers must be distinct.')
      return
    }
    setCreating(true); setCreateError('')
    try {
      await api.createVpcPeering(form.requesterRouter.value, {
        accepter_router_id: form.accepterRouter.value,
      })
      toast('VPC Peering connection established', 'success')
      setShowCreate(false)
      setForm({ requesterRouter: null, accepterRouter: null })
      load()
    } catch (e) {
      setCreateError(e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const p of selected) {
      try { await api.deleteVpcPeering(p.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} VPC Peering(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? peerings.filter(p => p.id?.toLowerCase().includes(q) || p.status?.toLowerCase().includes(q))
    : peerings

  const routerMap = Object.fromEntries(routers.map(r => [r.id, r.name]))

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(peerings.length, selected.length)}
          description="Direct inter-router L3 interconnects enabling private communication between isolated networks"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh VPC peerings" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)}>
                Delete
              </Button>
              <Button variant="primary" onClick={() => setShowCreate(true)}>
                Create peering
              </Button>
            </SpaceBetween>
          }
        >
          VPC Peering Connections
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
          { id: 'id', header: 'Peering ID', cell: item => <Box fontWeight="bold">{item.id}</Box>, sortingField: 'id' },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status} /> },
          { id: 'requester', header: 'Requester Router', cell: item => routerMap[item.requester_router_id] || item.requester_router_id },
          { id: 'accepter', header: 'Accepter Router', cell: item => routerMap[item.accepter_router_id] || item.accepter_router_id },
          { id: 'created_at', header: 'Created', cell: item => item.created_at || '—' },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading VPC peerings"
        filter={
          <TextFilter
            filteringText={filter}
            onChange={({ detail }) => setFilter(detail.filteringText)}
            filteringPlaceholder="Filter peerings"
          />
        }
        empty={
          filter ? (
            <TableNoMatch onClear={() => setFilter('')} />
          ) : (
            <TableEmpty
              title="No VPC peerings"
              subtitle="Connect two routers directly without traversing external floating IPs."
            />
          )
        }
      />

      {showCreate && (
        <Modal
          visible
          onDismiss={() => setShowCreate(false)}
          header="Create VPC Peering Connection"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreate(false)}>Cancel</Button>
                <Button variant="primary" loading={creating} onClick={handleCreate}>
                  Create peering
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {createError && <Box color="text-status-error">{createError}</Box>}
            <FormField label="Requester Router">
              <Select
                selectedOption={form.requesterRouter}
                onChange={({ detail }) => setForm(f => ({ ...f, requesterRouter: detail.selectedOption }))}
                options={routers.map(r => ({ value: r.id, label: r.name }))}
                placeholder="Select first router"
              />
            </FormField>
            <FormField label="Accepter Router">
              <Select
                selectedOption={form.accepterRouter}
                onChange={({ detail }) => setForm(f => ({ ...f, accepterRouter: detail.selectedOption }))}
                options={routers.map(r => ({ value: r.id, label: r.name }))}
                placeholder="Select second router"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].id : undefined}
          title="Delete VPC Peering"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
