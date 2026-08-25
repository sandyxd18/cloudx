import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
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
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import useEventStream from '../hooks/useEventStream.js'
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

const PAGE_SIZE = 15

// ── Create volume ────────────────────────────────────────────────────────────
function CreateModal({ onDismiss, onCreate }) {
  const [form, setForm] = useState({ name: '', size_gb: '10', volume_type: null })
  const [types, setTypes] = useState([])
  const [typesLoaded, setTypesLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getVolumeTypes()
      .then(r => setTypes(r?.items || []))
      .catch(() => setTypes([]))
      .finally(() => setTypesLoaded(true))
  }, [])

  const submit = async () => {
    if (!form.name) { setError('Volume name is required.'); return }
    if (typesLoaded && types.length === 0) { setError('No volume types are configured. An administrator must create one first.'); return }
    setLoading(true); setError('')
    try {
      const payload = { name: form.name, size_gb: Number(form.size_gb) }
      if (form.volume_type) payload.volume_type = form.volume_type.value
      await onCreate(payload)
      onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create volume"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} disabled={typesLoaded && types.length === 0} onClick={submit} data-testid="vol-create-submit">
              Create volume
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          {typesLoaded && types.length === 0 && (
            <Alert type="warning" header="No volume types configured">
              An administrator must create at least one volume type before volumes can be provisioned.
            </Alert>
          )}
          <FormField label="Volume name">
            <Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} placeholder="my-volume" controlId="vol-name" />
          </FormField>
          <FormField label="Size (GiB)">
            <Input value={form.size_gb} onChange={({ detail }) => setForm(f => ({ ...f, size_gb: detail.value }))} type="number" inputMode="numeric" controlId="vol-size" />
          </FormField>
          <FormField label="Volume type" description="Performance profile that also selects the storage backend.">
            <Select
              selectedOption={form.volume_type}
              onChange={({ detail }) => setForm(f => ({ ...f, volume_type: detail.selectedOption }))}
              options={types.map(t => ({
                value: t.name,
                label: t.name,
                description: `${t.disk_type} · ${String(t.backend || '').toUpperCase()}`,
              }))}
              placeholder={types.length ? 'Auto-select' : 'No types available'}
              disabled={types.length === 0}
              empty="No volume types"
              controlId="vol-type"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Extend volume ────────────────────────────────────────────────────────────
function ExtendModal({ volume, onDismiss, onExtend }) {
  const current = volume.size_gb || volume.size || 0
  const [size, setSize] = useState(String(current + 10))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const next = Number(size)
    if (!next || next <= current) { setError(`New size must be greater than ${current} GiB.`); return }
    setLoading(true); setError('')
    try { await onExtend(volume.id, next); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Extend volume — ${volume.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="vol-extend-submit">Extend volume</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <Alert type="info">Current size: <b>{current} GiB</b>. Volumes can only be grown, never shrunk.</Alert>
          <FormField label="New size (GiB)">
            <Input value={size} onChange={({ detail }) => setSize(detail.value)} type="number" inputMode="numeric" controlId="vol-extend-size" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Create snapshot ──────────────────────────────────────────────────────────
function SnapshotModal({ volume, onDismiss, onCreate }) {
  const [name, setName] = useState(`${volume.name}-snap`)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name) { setError('Snapshot name is required.'); return }
    setLoading(true); setError('')
    try { await onCreate(volume.id, { name }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Create snapshot — ${volume.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit}>Create snapshot</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Snapshot name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function VolumesPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [volumes, setVolumes] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [page, setPage]       = useState(1)
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [extendVol, setExtendVol]   = useState(null)
  const [snapshotVol, setSnapshotVol] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getVolumes()
      .then(r => setVolumes(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const { lastEvent } = useEventStream()
  useEffect(() => {
    if (lastEvent?.type?.startsWith('volume.')) load()
  }, [lastEvent, load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} volume(s)?`)) return
    for (const v of selected) {
      try { await api.deleteVolume(v.id) } catch { /* continue */ }
    }
    setSelected([])
    toast(`${selected.length} volume(s) queued for deletion`, 'success')
    load()
  }

  const detach = async (volume) => {
    try { await api.detachVolume(volume.id); toast('Volume detach started', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const backup = async (volume) => {
    try {
      await api.createVolumeBackup(volume.id, { name: `backup-${volume.name}` })
      toast('Volume backup started — uploading to object storage', 'success')
    } catch (e) { toast(e.message, 'error') }
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? volumes.filter(v =>
        v.name?.toLowerCase().includes(q) ||
        v.status?.toLowerCase().includes(q) ||
        v.volume_type?.toLowerCase().includes(q))
    : volumes
  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const totalGB = volumes.reduce((a, v) => a + (v.size_gb || v.size || 0), 0)

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(volumes.length, selected.length)}
          description={`Persistent block storage — ${totalGB} GiB provisioned across LVM and Ceph backends`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh volumes" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'extend',   text: 'Extend volume',   disabled: !one },
                  { id: 'snapshot', text: 'Create snapshot', disabled: !one },
                  { id: 'backup',   text: 'Back up to object storage', disabled: !one },
                  { id: 'detach',   text: 'Detach volume',   disabled: !one || one.status !== 'in-use' },
                  { id: 'delete',   text: 'Delete volume' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') deleteSelected()
                  else if (!one) return
                  else if (detail.id === 'extend') setExtendVol(one)
                  else if (detail.id === 'snapshot') setSnapshotVol(one)
                  else if (detail.id === 'backup') backup(one)
                  else if (detail.id === 'detach') detach(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowCreate(true)} data-testid="create-volume-btn">Create volume</Button>
            </SpaceBetween>
          }
        >
          Volumes
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading volumes"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={pageItems}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Volume name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Link href={`/volumes/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/volumes/${item.id}`) }}>
                  {item.name || '—'}
                </Link>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'status', header: 'Volume state', cell: item => <ResourceStatus status={item.status} />, sortingField: 'status' },
          { id: 'size', header: 'Size', cell: item => `${item.size_gb || item.size || 0} GiB`, sortingField: 'size_gb' },
          { id: 'type', header: 'Volume type', cell: item => item.volume_type || item.backend || '—' },
          { id: 'backend', header: 'Backend', cell: item => String(item.backend || '—').toUpperCase() },
          {
            id: 'attached',
            header: 'Attached instance',
            cell: item => item.attached_to_server_id
              ? <Link href={`/servers/${item.attached_to_server_id}`} onFollow={(e) => { e.preventDefault(); navigate(`/servers/${item.attached_to_server_id}`) }}>
                  {shortId(item.attached_to_server_id, 12)}
                </Link>
              : '—',
          },
          { id: 'device', header: 'Device', cell: item => item.target_device ? `/dev/${item.target_device}` : '—' },
          { id: 'bootable', header: 'Bootable', cell: item => <BoolStatus value={item.bootable} /> },
          { id: 'multiattach', header: 'Multi-attach', cell: item => <BoolStatus value={item.multiattach} /> },
          { id: 'az', header: 'Availability zone', cell: item => item.availability_zone || 'nova' },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find volumes"
            filteringAriaLabel="Filter volumes"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => { setFilter(detail.filteringText); setPage(1) }}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={page}
            pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}
            onChange={({ detail }) => setPage(detail.currentPageIndex)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No volumes"
                description="Create a volume to provision persistent block storage."
                action={<Button variant="primary" onClick={() => setShowCreate(true)}>Create volume</Button>}
              />
        }
      />

      {showCreate && (
        <CreateModal
          onDismiss={() => setShowCreate(false)}
          onCreate={async (payload) => { await api.createVolume(payload); toast('Volume creation started', 'success'); load() }}
        />
      )}
      {extendVol && (
        <ExtendModal
          volume={extendVol}
          onDismiss={() => setExtendVol(null)}
          onExtend={async (id, size) => { await api.extendVolume(id, { new_size_gb: size }); toast('Volume extend started', 'success'); load() }}
        />
      )}
      {snapshotVol && (
        <SnapshotModal
          volume={snapshotVol}
          onDismiss={() => setSnapshotVol(null)}
          onCreate={async (id, payload) => { await api.createSnapshot(id, payload); toast('Snapshot creation started', 'success') }}
        />
      )}
    </ContentLayout>
  )
}
