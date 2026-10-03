import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const DRIVER_OPTIONS = [
  { value: 'file', label: 'File (Local storage)' },
  { value: 'minio', label: 'MinIO (S3 Compatible)' },
  { value: 'ceph', label: 'Ceph RGW' },
]

export default function BucketTypesPage() {
  const toast = useToast()
  const [types, setTypes]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', driver: DRIVER_OPTIONS[0], description: '' })
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getBucketTypes()
      .then(r => setTypes(r?.bucket_types || r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!createForm.name.trim()) {
      setCreateError('Bucket type name is required.')
      return
    }
    setCreating(true); setCreateError('')
    try {
      await api.createBucketType({
        name: createForm.name.trim(),
        driver: createForm.driver.value,
        description: createForm.description.trim() || undefined,
      })
      toast('Bucket type created successfully', 'success')
      setShowCreateModal(false)
      setCreateForm({ name: '', driver: DRIVER_OPTIONS[0], description: '' })
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
    for (const t of selected) {
      try {
        await api.deleteBucketType(t.id || t.name)
      } catch (e) {
        toast(`${t.name}: ${e.message}`, 'error')
      }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} bucket type(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? types.filter(t => t.name?.toLowerCase().includes(q) || t.driver?.toLowerCase().includes(q)) : types

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(types.length, selected.length)}
          description="Administration of object store bucket profiles and backend storage drivers"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh bucket types" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)}>Delete</Button>
              <Button variant="primary" onClick={() => setShowCreateModal(true)}>Create type</Button>
            </SpaceBetween>
          }
        >
          Bucket Types
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
          { id: 'name', header: 'Name', cell: item => item.name, sortingField: 'name' },
          { id: 'driver', header: 'Driver', cell: item => item.driver },
          { id: 'description', header: 'Description', cell: item => item.description || '-' },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading bucket types"
        filter={
          <TextFilter
            filteringText={filter}
            onChange={({ detail }) => setFilter(detail.filteringText)}
            filteringPlaceholder="Filter bucket types"
          />
        }
        empty={
          filter ? (
            <TableNoMatch onClear={() => setFilter('')} />
          ) : (
            <TableEmpty title="No bucket types" subtitle="Create a bucket type profile to get started." />
          )
        }
      />

      {showCreateModal && (
        <Modal
          visible
          onDismiss={() => setShowCreateModal(false)}
          header="Create Bucket Type"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateModal(false)}>Cancel</Button>
                <Button variant="primary" loading={creating} onClick={handleCreate}>Create type</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {createError && <Box color="text-status-error">{createError}</Box>}
            <FormField label="Type Name" description="Name of the bucket profile.">
              <Input
                value={createForm.name}
                onChange={({ detail }) => setCreateForm(f => ({ ...f, name: detail.value }))}
                placeholder="standard-file"
                autoFocus
              />
            </FormField>
            <FormField label="Backend Driver">
              <Select
                selectedOption={createForm.driver}
                onChange={({ detail }) => setCreateForm(f => ({ ...f, driver: detail.selectedOption }))}
                options={DRIVER_OPTIONS}
              />
            </FormField>
            <FormField label="Description (optional)">
              <Input
                value={createForm.description}
                onChange={({ detail }) => setCreateForm(f => ({ ...f, description: detail.value }))}
                placeholder="Default local file storage driver"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Bucket Type"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
