import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Link from '@cloudscape-design/components/link'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const SCOPES = [{ value: 'public', label: 'Public (served on the internet)' }, { value: 'internal', label: 'Internal (tenant VMs only)' }]

export default function DnsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [createModal, setCreateModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [scope, setScope] = useState(SCOPES[0])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems((await api.getZones()).zones || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  const create = async () => {
    setBusy(true)
    try {
      await api.createZone({ name, scope: scope.value, ...(email ? { email } : {}) })
      setCreateModal(false)
      setName('')
      setEmail('')
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const deleteSelected = async () => {
    setBusy(true)
    for (const z of selected) {
      try { await api.deleteZone(z.id) } catch (e) { toast(e.message, 'error') }
    }
    setBusy(false)
    setDeleteModal(false)
    setSelected([])
    load()
  }

  if (loading && !items.length) return <PageLoading text="Loading zones" />

  const removable = selected.length > 0 && selected.every(z => z.kind === 'user')

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="Zones and records. Platform zones (cloud.lab) are managed automatically and read-only."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh zones" />
              <Button disabled={!removable} onClick={() => setDeleteModal(true)}>Delete</Button>
              <Button variant="primary" onClick={() => setCreateModal(true)}>Create zone</Button>
            </SpaceBetween>
          }
        >
          DNS
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
            header: 'Zone',
            cell: item => (
              <Link href={`/dns/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/dns/${item.id}`) }}>{item.name}</Link>
            ),
            sortingField: 'name',
          },
          { id: 'scope', header: 'Scope', cell: item => item.scope },
          { id: 'kind', header: 'Owner', cell: item => (item.kind === 'platform' ? 'Platform' : 'You') },
          {
            id: 'status',
            header: 'Status',
            cell: item => (
              <StatusIndicator type={item.status === 'ACTIVE' ? 'success' : 'pending'}>{item.status}</StatusIndicator>
            ),
          },
          { id: 'serial', header: 'Serial', cell: item => item.serial },
        ]}
        items={items}
        loading={loading}
        loadingText="Loading zones"
        empty={<TableEmpty title="No zones" subtitle="Create a zone to host your records." />}
        header={<Header variant="h2">Zones</Header>}
      />
      {createModal && (
        <Modal
          visible
          onDismiss={() => setCreateModal(false)}
          header="Create zone"
          footer={
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModal(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!name} onClick={create}>Create</Button>
            </SpaceBetween>
          }
        >
          <SpaceBetween size="m">
            <FormField label="Zone name" description="For example example.com. Names under the platform domain are reserved.">
              <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="example.com" />
            </FormField>
            <FormField label="Scope">
              <Select selectedOption={scope} onChange={({ detail }) => setScope(detail.selectedOption)} options={SCOPES} />
            </FormField>
            <FormField label="Contact email" constraintText="Optional">
              <Input value={email} onChange={({ detail }) => setEmail(detail.value)} placeholder="admin@example.com" />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete zone"
          loading={busy}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}