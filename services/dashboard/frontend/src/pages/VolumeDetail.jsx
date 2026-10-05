import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, PageLoading, TableEmpty, fmtDate, shortId } from '../components/common.jsx'

export default function VolumeDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [volume, setVolume] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [backups, setBackups] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const [vol, snaps, bks] = await Promise.allSettled([
      api.getVolume(id),
      api.getSnapshots(),
      api.getVolumeBackups(),
    ])
    if (vol.status === 'fulfilled') setVolume(vol.value)
    else toast('Failed to load volume', 'error')
    if (snaps.status === 'fulfilled') {
      setSnapshots((snaps.value?.items || []).filter(s => s.volume_id === id))
    }
    if (bks.status === 'fulfilled') {
      setBackups((bks.value?.items || []).filter(b => b.volume_id === id))
    }
    setLoading(false)
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const doDelete = async () => {
    if (!confirm(`Delete volume "${volume?.name}"? This cannot be undone.`)) return
    try {
      await api.deleteVolume(id)
      toast('Volume deletion started', 'success')
      navigate('/volumes')
    } catch (e) { toast(e.message, 'error') }
  }

  const doSnapshot = async () => {
    try {
      await api.createSnapshot(id, { name: `${volume.name}-snap-${Date.now()}` })
      toast('Snapshot creation started', 'success')
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  const doBackup = async () => {
    try {
      await api.createVolumeBackup(id, { name: `backup-${volume.name}` })
      toast('Volume backup started', 'success')
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  const doDetach = async () => {
    try { await api.detachVolume(id); toast('Volume detach started', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  if (loading) return <PageLoading text="Loading volume" />
  if (!volume) return <Alert type="error" header="Volume not found">The requested volume does not exist or is not visible in this project.</Alert>

  const size = volume.size_gb || volume.size || 0
  const inUse = volume.status === 'in-use'

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={volume.id}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <ButtonDropdown
                items={[
                  { id: 'snapshot', text: 'Create snapshot' },
                  { id: 'backup',   text: 'Back up to object storage' },
                  { id: 'detach',   text: 'Detach volume', disabled: !inUse },
                  { id: 'delete',   text: 'Delete volume', disabled: inUse },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'snapshot') doSnapshot()
                  else if (detail.id === 'backup') doBackup()
                  else if (detail.id === 'detach') doDetach()
                  else if (detail.id === 'delete') doDelete()
                }}
              >
                Actions
              </ButtonDropdown>
            </SpaceBetween>
          }
        >
          {volume.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Volume summary</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Volume state', value: <ResourceStatus status={volume.status} /> },
              { label: 'Size', value: `${size} GiB` },
              { label: 'Volume type', value: volume.volume_type || '—' },
              { label: 'Backend', value: String(volume.backend || '—').toUpperCase() },
            ]}
          />
        </Container>

        <Tabs
          tabs={[
            {
              id: 'details',
              label: 'Details',
              content: (
                <SpaceBetween size="l">
                  <Container header={<Header variant="h2">Volume details</Header>}>
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Volume ID', value: volume.id },
                        { label: 'Name', value: volume.name },
                        { label: 'State', value: <ResourceStatus status={volume.status} /> },
                        { label: 'Size', value: `${size} GiB` },
                        { label: 'Bootable', value: <BoolStatus value={volume.bootable} /> },
                        { label: 'Multi-attach', value: <BoolStatus value={volume.multiattach} /> },
                        { label: 'Created', value: fmtDate(volume.created_at) },
                        { label: 'Project', value: volume.project_id || '—' },
                        { label: 'Availability zone', value: volume.availability_zone || 'default' },
                        { label: 'Error', value: volume.error_message || '—' },
                      ]}
                    />
                  </Container>

                  <Container header={<Header variant="h2">Storage backend</Header>}>
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Backend driver', value: String(volume.backend || '—').toUpperCase() },
                        { label: 'Device path', value: volume.path || '—' },
                        {
                          label: 'Source',
                          value: volume.source_volume_id ? `Clone of ${shortId(volume.source_volume_id)}`
                            : volume.snapshot_id ? `From snapshot ${shortId(volume.snapshot_id)}`
                            : volume.image_id ? `From image ${shortId(volume.image_id)}`
                            : 'Blank volume',
                        },
                      ]}
                    />
                  </Container>

                  <Container header={<Header variant="h2">Attachment</Header>}>
                    {volume.attached_to_server_id ? (
                      <KeyValuePairs
                        columns={2}
                        items={[
                          {
                            label: 'Attached instance',
                            value: (
                              <Link href={`/servers/${volume.attached_to_server_id}`} onFollow={(e) => { e.preventDefault(); navigate(`/servers/${volume.attached_to_server_id}`) }}>
                                {volume.attached_to_server_id}
                              </Link>
                            ),
                          },
                          { label: 'Device', value: volume.target_device ? `/dev/${volume.target_device}` : '—' },
                        ]}
                      />
                    ) : (
                      <Box color="text-body-secondary">This volume is not attached to any instance.</Box>
                    )}
                  </Container>
                </SpaceBetween>
              ),
            },
            {
              id: 'snapshots',
              label: `Snapshots (${snapshots.length})`,
              content: (
                <Table
                  variant="container"
                  header={<Header variant="h2" counter={`(${snapshots.length})`} actions={<Button onClick={doSnapshot}>Create snapshot</Button>}>Snapshots</Header>}
                  items={snapshots}
                  trackBy="id"
                  columnDefinitions={[
                    { id: 'name', header: 'Name', cell: s => s.name, isRowHeader: true },
                    { id: 'status', header: 'State', cell: s => <ResourceStatus status={s.status} /> },
                    { id: 'size', header: 'Size', cell: s => `${s.size_gb || 0} GiB` },
                    { id: 'created', header: 'Created', cell: s => fmtDate(s.created_at) },
                  ]}
                  empty={<TableEmpty title="No snapshots" description="Create a snapshot to capture a point-in-time copy of this volume." />}
                />
              ),
            },
            {
              id: 'backups',
              label: `Backups (${backups.length})`,
              content: (
                <Table
                  variant="container"
                  header={<Header variant="h2" counter={`(${backups.length})`} actions={<Button onClick={doBackup}>Create backup</Button>}>Object storage backups</Header>}
                  items={backups}
                  trackBy="id"
                  columnDefinitions={[
                    { id: 'name', header: 'Name', cell: b => b.name, isRowHeader: true },
                    { id: 'status', header: 'State', cell: b => <ResourceStatus status={b.status} /> },
                    { id: 'size', header: 'Size', cell: b => `${b.size_gb || 0} GiB` },
                    { id: 'object', header: 'Object key', cell: b => b.object_key || '—' },
                    { id: 'created', header: 'Created', cell: b => fmtDate(b.created_at) },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: b => (
                        <Button
                          variant="inline-link"
                          disabled={b.status !== 'available'}
                          onClick={async () => {
                            try { await api.restoreVolumeBackup(b.id, {}); toast('Restore started — a new volume is being created', 'success') }
                            catch (e) { toast(e.message, 'error') }
                          }}
                        >
                          Restore
                        </Button>
                      ),
                    },
                  ]}
                  empty={<TableEmpty title="No backups" description="Back up this volume to MinIO/S3 for off-node durability." />}
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
