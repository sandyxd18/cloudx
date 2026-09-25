import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, ResourceStatus } from '../components/common.jsx'
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

  if (loading && !image) return <PageLoading text="Loading image details" />
  if (!image) return <Box variant="h2">Image not found</Box>

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
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
          Image: {image.name}
        </Header>
      }
    >
      <Container header={<Header variant="h2">Image Overview</Header>}>
        <SpaceBetween size="m" direction="horizontal">
          <Box>
            <Box variant="awsui-key-label">Name</Box>
            <Box fontWeight="bold">{image.name}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">ID</Box>
            <Box>{image.id}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Status</Box>
            <ResourceStatus status={image.status || 'ACTIVE'} />
          </Box>
          <Box>
            <Box variant="awsui-key-label">Disk Format</Box>
            <Box>{image.disk_format || 'qcow2'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Container Format</Box>
            <Box>{image.container_format || 'bare'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Size</Box>
            <Box>{image.size ? `${(image.size / (1024 * 1024)).toFixed(1)} MB` : '-'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Min Disk / RAM</Box>
            <Box>{image.min_disk || 0} GB / {image.min_ram || 0} MB</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Visibility</Box>
            <Box>{image.visibility || 'public'}</Box>
          </Box>
        </SpaceBetween>
      </Container>

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
