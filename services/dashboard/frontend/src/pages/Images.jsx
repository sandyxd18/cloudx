import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import Pagination from '@cloudscape-design/components/pagination'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, fmtBytes, shortId, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const PAGE_SIZE = 15

const DISK_FORMATS = [
  { value: 'qcow2', label: 'QCOW2' },
  { value: 'raw',   label: 'RAW' },
  { value: 'vmdk',  label: 'VMDK' },
  { value: 'iso',   label: 'ISO' },
]

const VISIBILITIES = [
  { value: 'public',  label: 'Public' },
  { value: 'private', label: 'Private' },
]

// ── Page ─────────────────────────────────────────────────────────────────────
export default function ImagesPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [images, setImages]   = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [visFilter, setVisFilter] = useState({ value: '', label: 'All visibility' })
  const [page, setPage]       = useState(1)
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getImages()
      .then(r => setImages(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const img of selected) {
      try { await api.deleteImage(img.id) } catch { /* continue */ }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} image(s) deleted`, 'success')
    load()
  }

  const toggleActive = async (img) => {
    try {
      if (String(img.status).toUpperCase() === 'ACTIVE') {
        await api.deactivateImage(img.id); toast('Image deactivated', 'success')
      } else {
        await api.reactivateImage(img.id); toast('Image reactivated', 'success')
      }
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  const toggleVisibility = async (img) => {
    const next = img.visibility === 'public' ? 'private' : 'public'
    try {
      await api.updateImage(img.id, { visibility: next })
      toast(`Visibility set to ${next}`, 'success')
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  const q = filter.trim().toLowerCase()
  const filtered = images.filter(i => {
    const matchText = !q || i.name?.toLowerCase().includes(q) || i.disk_format?.toLowerCase().includes(q)
    const matchVis  = !visFilter.value || i.visibility === visFilter.value
    return matchText && matchVis
  })
  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(images.length, selected.length)}
          description="Boot images available to launch instances from"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} data-testid="refresh-images" ariaLabel="Refresh images" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'launch',     text: 'Launch instance from image', disabled: !one },
                  { id: 'visibility', text: one?.visibility === 'public' ? 'Make private' : 'Make public', disabled: !one },
                  { id: 'active',     text: String(one?.status).toUpperCase() === 'ACTIVE' ? 'Deactivate' : 'Reactivate', disabled: !one },
                  { id: 'delete',     text: 'Delete' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') setDeleteModal(true)
                  else if (detail.id === 'visibility' && one) toggleVisibility(one)
                  else if (detail.id === 'active' && one) toggleActive(one)
                  else if (detail.id === 'launch') navigate('/servers')
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/images/create')} data-testid="upload-image-btn">
                Upload image
              </Button>
            </SpaceBetween>
          }
        >
          Images
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading images"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={pageItems}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Image name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Link href={`/images/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/images/${item.id}`) }}>
                  {item.name}
                </Link>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status} />, sortingField: 'status' },
          { id: 'visibility', header: 'Visibility', cell: item => <BoolStatus value={item.visibility === 'public'} trueText="Public" falseText="Private" /> },
          { id: 'disk_format', header: 'Disk format', cell: item => item.disk_format || '—' },
          { id: 'container', header: 'Container format', cell: item => item.container_format || '—' },
          { id: 'size', header: 'Size', cell: item => fmtBytes(item.size) },
          { id: 'owner', header: 'Owner', cell: item => item.owner || item.project_id || '—' },
        ]}
        filter={
          <SpaceBetween direction="horizontal" size="xs">
            <TextFilter
              filteringText={filter}
              filteringPlaceholder="Find images"
              filteringAriaLabel="Filter images"
              countText={`${filtered.length} matches`}
              onChange={({ detail }) => { setFilter(detail.filteringText); setPage(1) }}
            />
            <Select
              selectedOption={visFilter}
              onChange={({ detail }) => { setVisFilter(detail.selectedOption); setPage(1) }}
              options={[{ value: '', label: 'All visibility' }, ...VISIBILITIES]}
              ariaLabel="Filter by visibility"
            />
          </SpaceBetween>
        }
        pagination={
          <Pagination
            currentPageIndex={page}
            pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}
            onChange={({ detail }) => setPage(detail.currentPageIndex)}
          />
        }
        empty={
          filter || visFilter.value
            ? <TableNoMatch onClear={() => { setFilter(''); setVisFilter({ value: '', label: 'All visibility' }) }} />
            : <TableEmpty
                title="No images"
                description="Upload an image, or import one with: cloudx image upload --name NAME --file FILE"
                action={<Button variant="primary" onClick={() => navigate('/images/create')}>Upload image</Button>}
              />
        }
      />
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Image"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
