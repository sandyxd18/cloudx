import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import Pagination from '@cloudscape-design/components/pagination'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, fmtDate, shortId, counterText } from '../components/common.jsx'

const PAGE_SIZE = 15

function CreateModal({ onDismiss, onCreate }) {
  const [volumes, setVolumes] = useState([])
  const [volume, setVolume]   = useState(null)
  const [name, setName]       = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  useEffect(() => {
    api.getVolumes().then(r => setVolumes(r?.items || [])).catch(() => setVolumes([]))
  }, [])

  const submit = async () => {
    if (!volume) { setError('Select a source volume.'); return }
    if (!name) { setError('Snapshot name is required.'); return }
    setLoading(true); setError('')
    try { await onCreate(volume.value, { name }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create volume snapshot"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="snap-create-submit">Create snapshot</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Source volume">
            <Select
              selectedOption={volume}
              onChange={({ detail }) => setVolume(detail.selectedOption)}
              options={volumes.map(v => ({ value: v.id, label: v.name, description: `${v.size_gb || v.size || 0} GiB · ${v.status}` }))}
              placeholder="Choose a volume"
              empty="No volumes"
              controlId="snap-volume"
            />
          </FormField>
          <FormField label="Snapshot name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="snap-2026-01-01" controlId="snap-name" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function SnapshotsPage() {
  const toast = useToast()
  const [snaps, setSnaps]     = useState([])
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [page, setPage]       = useState(1)
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getSnapshots(), api.getVolumes()])
      .then(([s, v]) => {
        if (s.status === 'fulfilled') setSnaps(s.value?.items || [])
        if (v.status === 'fulfilled') setVolumes(v.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const volumeName = (volId) => volumes.find(v => v.id === volId)?.name || shortId(volId, 12)

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} snapshot(s)?`)) return
    for (const s of selected) {
      try { await api.deleteSnapshot(s.id) } catch { /* continue */ }
    }
    setSelected([])
    toast(`${selected.length} snapshot(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? snaps.filter(s => s.name?.toLowerCase().includes(q) || s.status?.toLowerCase().includes(q)) : snaps
  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(snaps.length, selected.length)}
          description="Point-in-time copies of block storage volumes"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh snapshots" />
              <Button disabled={!selected.length} onClick={deleteSelected}>Delete</Button>
              <Button variant="primary" onClick={() => setShowCreate(true)}>Create snapshot</Button>
            </SpaceBetween>
          }
        >
          Snapshots
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading snapshots"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={pageItems}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Snapshot name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Box fontWeight="bold">{item.name || '—'}</Box>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'status', header: 'State', cell: item => <ResourceStatus status={item.status} />, sortingField: 'status' },
          { id: 'size', header: 'Size', cell: item => `${item.size_gb || item.size || 0} GiB`, sortingField: 'size_gb' },
          { id: 'volume', header: 'Source volume', cell: item => volumeName(item.volume_id) },
          { id: 'backend', header: 'Backend', cell: item => String(item.backend || '—').toUpperCase() },
          { id: 'created', header: 'Created', cell: item => fmtDate(item.created_at) },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find snapshots"
            filteringAriaLabel="Filter snapshots"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => { setFilter(detail.filteringText); setPage(1) }}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={page}
            pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}
            onChange={({ detail }) => setPage(detail.currentPageIndex)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No snapshots"
                description="Create a snapshot to capture a point-in-time copy of a volume."
                action={<Button variant="primary" onClick={() => setShowCreate(true)}>Create snapshot</Button>}
              />
        }
      />

      {showCreate && (
        <CreateModal
          onDismiss={() => setShowCreate(false)}
          onCreate={async (volId, payload) => { await api.createSnapshot(volId, payload); toast('Snapshot creation started', 'success'); load() }}
        />
      )}
    </ContentLayout>
  )
}
