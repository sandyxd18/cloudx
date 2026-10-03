import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Badge from '@cloudscape-design/components/badge'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Tabs from '@cloudscape-design/components/tabs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, ResourceStatus, fmtBytes, fmtDate } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function ImageDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [image, setImage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getImage(id)
      setImage(data)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await api.deleteImage(image.id)
      toast('Image deleted', 'success')
      navigate('/images')
    } catch (e) {
      toast(e.message, 'error')
      setDeleting(false)
      setDeleteModal(false)
    }
  }

  if (loading) return <PageLoading text="Loading image details" />
  if (!image) return <Alert type="error" header="Image not found">The requested image does not exist or is not visible.</Alert>

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={`ID: ${image.id}`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh image" />
              <Button onClick={() => navigate('/servers/create', { state: { imageId: image.id } })}>
                Launch instance
              </Button>
              <Button variant="primary" onClick={() => setDeleteModal(true)}>Delete image</Button>
            </SpaceBetween>
          }
        >
          {image.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Image Overview</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Name', value: image.name },
              { label: 'Status', value: <ResourceStatus status={image.status || 'ACTIVE'} /> },
              { label: 'Visibility', value: <Badge color={image.visibility === 'public' ? 'severity-low' : 'severity-neutral'}>{image.visibility || 'public'}</Badge> },
              { label: 'Disk Format', value: (image.disk_format || 'qcow2').toUpperCase() },
            ]}
          />
        </Container>

        <Tabs
          tabs={[
            {
              id: 'details',
              label: 'Details',
              content: (
                <Container header={<Header variant="h2">Image Details</Header>}>
                  <KeyValuePairs
                    columns={3}
                    items={[
                      { label: 'Image ID', value: image.id },
                      { label: 'Name', value: image.name },
                      { label: 'Status', value: image.status || 'ACTIVE' },
                      { label: 'Disk Format', value: image.disk_format || 'qcow2' },
                      { label: 'Container Format', value: image.container_format || 'bare' },
                      { label: 'Size', value: image.size ? fmtBytes(image.size) : '—' },
                      { label: 'Min Disk GB', value: `${image.min_disk_gb || image.min_disk || 0} GB` },
                      { label: 'Min RAM MB', value: `${image.min_ram || 0} MB` },
                      { label: 'Checksum (SHA256)', value: image.checksum || '—' },
                      { label: 'Storage Backend', value: image.storage_backend || 'file' },
                      { label: 'Project ID', value: image.project_id || '—' },
                      { label: 'Created At', value: fmtDate(image.created_at) },
                    ]}
                  />
                </Container>
              ),
            },
          ]}
        />
      </SpaceBetween>

      {deleteModal && (
        <DeleteModal
          visible
          itemName={image.name}
          title="Delete Image"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
