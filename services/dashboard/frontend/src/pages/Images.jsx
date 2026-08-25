import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
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
import FileUpload from '@cloudscape-design/components/file-upload'
import ProgressBar from '@cloudscape-design/components/progress-bar'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, fmtBytes, shortId, counterText } from '../components/common.jsx'

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

// ── Upload image modal ───────────────────────────────────────────────────────
function UploadModal({ onDismiss, onSuccess }) {
  const [files, setFiles]   = useState([])
  const [name, setName]     = useState('')
  const [format, setFormat] = useState(DISK_FORMATS[0])
  const [visibility, setVisibility] = useState(VISIBILITIES[0])
  const [progress, setProgress] = useState(null)
  const [error, setError]   = useState('')
  const toast = useToast()

  const file = files[0]

  const handleFiles = (selected) => {
    setFiles(selected)
    const f = selected[0]
    if (!f) return
    if (!name) setName(f.name.replace(/\.(qcow2|img|raw|vmdk|iso)$/i, ''))
    const ext = f.name.split('.').pop().toLowerCase()
    const match = DISK_FORMATS.find(d => d.value === (ext === 'img' ? 'raw' : ext))
    if (match) setFormat(match)
  }

  const uploading = progress !== null && progress >= 0 && progress < 100

  const submit = async () => {
    if (!file) { setError('Select an image file to upload.'); return }
    if (!name.trim()) { setError('Image name is required.'); return }
    setError('')
    setProgress(0)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name.trim())
    fd.append('disk_format', format.value)
    fd.append('container_format', 'bare')
    fd.append('visibility', visibility.value)
    try {
      await api.uploadImageWithProgress(fd, pct => setProgress(pct))
      setProgress(100)
      toast('Image uploaded successfully', 'success')
      onSuccess()
      setTimeout(onDismiss, 600)
    } catch (e) {
      setError(e.message)
      setProgress(null)
    }
  }

  return (
    <Modal
      visible
      onDismiss={() => !uploading && onDismiss()}
      header="Upload image"
      size="medium"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss} disabled={uploading}>Cancel</Button>
            <Button variant="primary" loading={uploading} disabled={!file} onClick={submit} data-testid="image-upload-submit">
              Upload image
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}

          <FormField label="Image file" description="Accepted formats: .qcow2, .img, .raw, .vmdk, .iso">
            <FileUpload
              value={files}
              onChange={({ detail }) => handleFiles(detail.value)}
              accept=".qcow2,.img,.raw,.vmdk,.iso"
              i18nStrings={{
                uploadButtonText: () => 'Choose file',
                dropzoneText: () => 'Drop image file to upload',
                removeFileAriaLabel: i => `Remove file ${i + 1}`,
                errorIconAriaLabel: 'Error',
              }}
              showFileSize
              constraintText="Maximum one file per image."
            />
          </FormField>

          {progress !== null && (
            <ProgressBar
              value={progress}
              status={progress >= 100 ? 'success' : 'in-progress'}
              label="Upload progress"
              additionalInfo={progress >= 100 ? 'Upload complete' : `${progress}%`}
            />
          )}

          <FormField label="Image name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="ubuntu-22.04" disabled={uploading} />
          </FormField>

          <FormField label="Disk format">
            <Select selectedOption={format} onChange={({ detail }) => setFormat(detail.selectedOption)} options={DISK_FORMATS} disabled={uploading} />
          </FormField>

          <FormField label="Visibility">
            <Select selectedOption={visibility} onChange={({ detail }) => setVisibility(detail.selectedOption)} options={VISIBILITIES} disabled={uploading} />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

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
  const [showUpload, setShowUpload] = useState(false)

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
    if (!confirm(`Delete ${selected.length} image(s)?`)) return
    for (const img of selected) {
      try { await api.deleteImage(img.id) } catch { /* continue */ }
    }
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
                  if (detail.id === 'delete') deleteSelected()
                  else if (detail.id === 'visibility' && one) toggleVisibility(one)
                  else if (detail.id === 'active' && one) toggleActive(one)
                  else if (detail.id === 'launch') navigate('/servers')
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowUpload(true)} data-testid="upload-image-btn">
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
                <Box fontWeight="bold">{item.name}</Box>
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
                action={<Button variant="primary" onClick={() => setShowUpload(true)}>Upload image</Button>}
              />
        }
      />

      {showUpload && <UploadModal onDismiss={() => setShowUpload(false)} onSuccess={load} />}
    </ContentLayout>
  )
}
