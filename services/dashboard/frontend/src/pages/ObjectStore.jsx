import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function ObjectStorePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [buckets, setBuckets] = useState([])
  const [types, setTypes] = useState([])
  const [activeTab, setActiveTab] = useState('buckets')
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [b, t] = await Promise.allSettled([
        api.getBuckets(),
        api.getBucketTypes(),
      ])
      setBuckets(b.status === 'fulfilled' ? (b.value.buckets || []) : [])
      setTypes(t.status === 'fulfilled' ? (t.value.bucket_types || []) : [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const toggleVisibility = async (b) => {
    const next = b.visibility === 'public-read' ? 'private' : 'public-read'
    try {
      await api.updateBucket(b.name, { visibility: next })
      toast(`Visibility set to ${next}`, 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    let deletedCount = 0
    let lastError = null
    for (const b of selected) {
      try {
        await api.deleteBucket(b.name)
        deletedCount++
      } catch (e) {
        lastError = e
      }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    if (lastError && deletedCount === 0) {
      toast(lastError.message || 'Failed to delete bucket', 'error')
    } else {
      toast(`${deletedCount} bucket(s) deleted`, 'success')
    }
    load()
  }

  if (loading && !buckets.length && !types.length) return <PageLoading text="Loading object store" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(buckets.length, selected.length)}
          description="S3 / Swift compatible object storage for unstructured data and backups"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh object store" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'visibility', text: one?.visibility === 'public-read' ? 'Make private' : 'Make public', disabled: !one },
                  { id: 'delete', text: 'Delete bucket' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') setDeleteModal(true)
                  else if (detail.id === 'visibility' && one) toggleVisibility(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/object-store/create')}>
                Create bucket
              </Button>
            </SpaceBetween>
          }
        >
          Object Store
        </Header>
      }
    >
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          {
            id: 'buckets',
            label: 'Buckets',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selected}
                onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
                trackBy="name"
                columnDefinitions={[
                  {
                    id: 'name',
                    header: 'Name',
                    cell: item => (
                      <Link href={`/object-store/buckets/${item.name}`} onFollow={(e) => { e.preventDefault(); navigate(`/object-store/buckets/${item.name}`) }}>
                        {item.name}
                      </Link>
                    ),
                    sortingField: 'name',
                  },
                  {
                    id: 'type',
                    header: 'Bucket Type',
                    cell: item => item.bucket_type?.name || types.find(t => t.id === item.bucket_type_id)?.name || item.bucket_type_id || 'default',
                  },
                  { id: 'visibility', header: 'Visibility', cell: item => item.visibility },
                  { id: 'objects', header: 'Objects', cell: item => item.object_count ?? 0 },
                  { id: 'size', header: 'Size (bytes)', cell: item => item.size_bytes ?? 0 },
                  { id: 'status', header: 'Status', cell: item => item.status || 'active' },
                ]}
                items={buckets}
                loading={loading}
                loadingText="Loading buckets"
                empty={<TableEmpty title="No buckets" subtitle="Create a bucket to get started." />}
                header={<Header variant="h2">Buckets</Header>}
              />
            ),
          },
          {
            id: 'types',
            label: 'Bucket Types',
            content: (
              <Table
                columnDefinitions={[
                  { id: 'name', header: 'Name', cell: item => item.name },
                  { id: 'driver', header: 'Driver', cell: item => item.driver },
                  { id: 'description', header: 'Description', cell: item => item.description || '-' },
                ]}
                items={types}
                loading={loading}
                loadingText="Loading bucket types"
                empty={<TableEmpty title="No bucket types" subtitle="Admin can register storage backends here." />}
                header={<Header variant="h2">Bucket Types (Admin)</Header>}
              />
            ),
          },
        ]}
      />
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Bucket"
          requireCheckbox={selected.some(b => (b.object_count ?? 0) > 0)}
          checkboxLabel="I understand that selected bucket(s) contain objects and deleting will permanently delete all objects stored inside."
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
