import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
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

export default function SnapshotDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getSnapshot(id)
      setSnapshot(data)
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
      await api.deleteSnapshot(snapshot.id)
      toast('Snapshot deleted', 'success')
      navigate('/snapshots')
    } catch (e) {
      toast(e.message, 'error')
      setDeleting(false)
      setDeleteModal(false)
    }
  }

  if (loading && !snapshot) return <PageLoading text="Loading snapshot details" />
  if (!snapshot) return <Box variant="h2">Snapshot not found</Box>

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh snapshot" />
              <Button onClick={() => navigate('/volumes/create', { state: { snapshotId: snapshot.id } })}>
                Create volume
              </Button>
              <Button variant="primary" onClick={() => setDeleteModal(true)}>Delete snapshot</Button>
            </SpaceBetween>
          }
        >
          Snapshot: {snapshot.name}
        </Header>
      }
    >
      <Container header={<Header variant="h2">Snapshot Overview</Header>}>
        <SpaceBetween size="m" direction="horizontal">
          <Box>
            <Box variant="awsui-key-label">Name</Box>
            <Box fontWeight="bold">{snapshot.name}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">ID</Box>
            <Box>{snapshot.id}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Status</Box>
            <ResourceStatus status={snapshot.status || 'available'} />
          </Box>
          <Box>
            <Box variant="awsui-key-label">Volume ID</Box>
            <Box>{snapshot.volume_id || '-'}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Size</Box>
            <Box>{snapshot.size} GiB</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Created At</Box>
            <Box>{snapshot.created_at || '-'}</Box>
          </Box>
        </SpaceBetween>
      </Container>

      {deleteModal && (
        <DeleteModal
          visible
          itemName={snapshot.name}
          title="Delete Snapshot"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
