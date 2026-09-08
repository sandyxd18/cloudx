import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Pagination from '@cloudscape-design/components/pagination'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, fmtDate, shortId, counterText } from '../components/common.jsx'

const PAGE_SIZE = 15

export default function SnapshotsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [snaps, setSnaps]     = useState([])
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [page, setPage]       = useState(1)
  const [selected, setSelected] = useState([])

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
              <Button variant="primary" onClick={() => navigate('/snapshots/create')} data-testid="create-snapshot-btn">Create snapshot</Button>
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
                action={<Button variant="primary" onClick={() => navigate('/snapshots/create')}>Create snapshot</Button>}
              />
        }
      />
    </ContentLayout>
  )
}
