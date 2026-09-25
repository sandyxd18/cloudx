import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, shortId } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function BucketDetailPage() {
  const { name } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [bucket, setBucket] = useState(null)
  const [objects, setObjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [fileToUpload, setFileToUpload] = useState(null)
  const [uploadKey, setUploadKey] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [deleteObjTarget, setDeleteObjTarget] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [b, o] = await Promise.all([
        api.getBucket(name),
        api.getBucketObjects(name),
      ])
      setBucket(b)
      setObjects(o?.objects || o?.items || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [name, toast])

  useEffect(() => { load() }, [load])

  const handleUpload = async () => {
    if (!fileToUpload) { setUploadError('Please select a file to upload.'); return }
    const key = uploadKey.trim() || fileToUpload.name
    setUploading(true)
    setUploadError('')
    try {
      await api.uploadObject(name, key, fileToUpload)
      toast(`Object "${key}" uploaded successfully`, 'success')
      setShowUploadModal(false)
      setFileToUpload(null)
      setUploadKey('')
      load()
    } catch (e) {
      setUploadError(e.message)
    } finally {
      setUploading(false)
    }
  }

  const handleDeleteObject = async () => {
    if (!deleteObjTarget) return
    setDeleting(true)
    try {
      await api.deleteObject(name, deleteObjTarget.key || deleteObjTarget.name)
      toast(`Object "${deleteObjTarget.key || deleteObjTarget.name}" deleted`, 'success')
      setDeleteObjTarget(null)
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setDeleting(false)
    }
  }

  if (loading && !bucket) return <PageLoading text="Loading bucket details" />
  if (!bucket) return <Box variant="h2">Bucket not found</Box>

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh bucket" />
              <Button variant="primary" onClick={() => setShowUploadModal(true)}>
                Upload object
              </Button>
            </SpaceBetween>
          }
        >
          Bucket: {bucket.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Bucket Overview</Header>}>
          <SpaceBetween size="m" direction="horizontal">
            <Box>
              <Box variant="awsui-key-label">Name</Box>
              <Box fontWeight="bold">{bucket.name}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Visibility</Box>
              <Box>{bucket.visibility || 'private'}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Bucket Type</Box>
              <Box>{bucket.bucket_type?.name || bucket.bucket_type_id || 'default'}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Total Objects</Box>
              <Box>{bucket.object_count ?? objects.length}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Total Size</Box>
              <Box>{bucket.size_bytes ?? 0} bytes</Box>
            </Box>
          </SpaceBetween>
        </Container>

        <Table
          variant="full-page"
          stickyHeader
          columnDefinitions={[
            { id: 'key', header: 'Object Key / Name', cell: item => item.key || item.name, isRowHeader: true },
            { id: 'size', header: 'Size (bytes)', cell: item => item.size_bytes || item.size || 0 },
            { id: 'type', header: 'Content Type', cell: item => item.content_type || 'application/octet-stream' },
            { id: 'modified', header: 'Last Modified', cell: item => item.last_modified || '-' },
            {
              id: 'actions',
              header: 'Actions',
              cell: item => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="link"
                    iconName="download"
                    href={`/api/object-store/buckets/${name}/objects/${item.key || item.name}`}
                    target="_blank"
                  >
                    Download
                  </Button>
                  <Button
                    variant="icon"
                    iconName="remove"
                    onClick={() => setDeleteObjTarget(item)}
                    ariaLabel="Delete object"
                  />
                </SpaceBetween>
              ),
            },
          ]}
          items={objects}
          loading={loading}
          loadingText="Loading bucket objects"
          empty={<TableEmpty title="No objects in bucket" subtitle="Upload objects to store binary files." />}
          header={<Header variant="h2" counter={`(${objects.length})`}>Objects</Header>}
        />
      </SpaceBetween>

      {showUploadModal && (
        <Modal
          visible
          onDismiss={() => setShowUploadModal(false)}
          header={`Upload object to ${bucket.name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowUploadModal(false)} disabled={uploading}>Cancel</Button>
                <Button variant="primary" loading={uploading} onClick={handleUpload}>Upload</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {uploadError && <Box color="text-status-error">{uploadError}</Box>}
            <FormField label="File" description="Select file from your local machine.">
              <input
                type="file"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) {
                    setFileToUpload(f)
                    if (!uploadKey) setUploadKey(f.name)
                  }
                }}
              />
            </FormField>
            <FormField label="Object Key" description="Key/Path inside bucket (defaults to file name).">
              <Input
                value={uploadKey}
                onChange={({ detail }) => setUploadKey(detail.value)}
                placeholder="folder/filename.ext"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {deleteObjTarget && (
        <DeleteModal
          visible
          itemName={deleteObjTarget.key || deleteObjTarget.name}
          title="Delete Object"
          loading={deleting}
          onDismiss={() => setDeleteObjTarget(null)}
          onConfirm={handleDeleteObject}
        />
      )}
    </ContentLayout>
  )
}
