import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
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
  const [bucketTypeName, setBucketTypeName] = useState('default')
  const [networkName, setNetworkName] = useState('')
  const [networkIp, setNetworkIp] = useState('')
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
      const [b, o, t, nData, sData] = await Promise.all([
        api.getBucket(name),
        api.getBucketObjects(name),
        api.getBucketTypes().catch(() => ({ bucket_types: [] })),
        api.getNetworks().catch(() => ({ items: [] })),
        api.getSubnets().catch(() => ({ items: [] })),
      ])
      setBucket(b)
      setObjects(o?.objects || o?.items || [])

      const typesList = t?.bucket_types || []
      const foundType = typesList.find(item => item.id === b.bucket_type_id)
      setBucketTypeName(b.bucket_type?.name || foundType?.name || b.bucket_type_id || 'default')

      if (b.network_id) {
        const nets = nData?.items || []
        const subs = sData?.items || []
        const matchedNet = nets.find(item => item.id === b.network_id || item.name === b.network_id)
        const matchedSub = subs.find(item => item.network_id === (matchedNet?.id || b.network_id))
        setNetworkName(matchedNet?.name || b.network_id)
        const ip = matchedSub?.gateway_ip || matchedSub?.gateway || (matchedSub?.cidr ? matchedSub.cidr.split('/')[0] : '')
        setNetworkIp(ip)
      } else {
        setNetworkName('')
        setNetworkIp('')
      }
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

  const hostIp = (bucket?.visibility === 'public-read' && bucket?.floating_ip)
    ? bucket.floating_ip
    : (networkIp || window.location.hostname || '127.0.0.1')
  const endpointUrl = `http://${hostIp}:8080/api/object-store/buckets/${bucket.name}`

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
          <SpaceBetween size="m">
            <SpaceBetween size="m" direction="horizontal">
              <Box>
                <Box variant="awsui-key-label">Name</Box>
                <Box fontWeight="bold">{bucket.name}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Visibility</Box>
                <Box>{bucket.visibility || 'private'}</Box>
              </Box>
              {bucket.floating_ip && (
                <Box>
                  <Box variant="awsui-key-label">Floating IP</Box>
                  <Box fontWeight="bold">{bucket.floating_ip}</Box>
                </Box>
              )}
              <Box>
                <Box variant="awsui-key-label">Bucket Type</Box>
                <Box>{bucketTypeName}</Box>
              </Box>
              {bucket.network_id && (
                <Box>
                  <Box variant="awsui-key-label">Associated Network</Box>
                  <Box>{networkName || bucket.network_id}</Box>
                </Box>
              )}
              <Box>
                <Box variant="awsui-key-label">Total Objects</Box>
                <Box>{bucket.object_count ?? objects.length}</Box>
              </Box>
              <Box>
                <Box variant="awsui-key-label">Total Size</Box>
                <Box>{bucket.size_bytes ?? 0} bytes</Box>
              </Box>
            </SpaceBetween>
            <Box>
              <Box variant="awsui-key-label">Endpoint URL</Box>
              <SpaceBetween direction="horizontal" size="xs">
                <Box fontWeight="bold">{endpointUrl}</Box>
                <Button
                  variant="inline-icon"
                  iconName="copy"
                  onClick={() => {
                    navigator.clipboard.writeText(endpointUrl)
                    toast('Endpoint URL copied to clipboard', 'success')
                  }}
                  ariaLabel="Copy endpoint URL"
                />
              </SpaceBetween>
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
