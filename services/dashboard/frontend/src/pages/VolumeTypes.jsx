/**
 * VolumeTypes.jsx — administration of AWS EBS-style volume types.
 *
 * A volume type is a performance profile (gp2, gp3, io1, io2, st1, sc1…) that
 * couples a disk tier hint with a storage backend (LVM or Ceph RBD). At least
 * one type must exist before users can provision volumes.
 */
import { useEffect, useState, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Badge from '@cloudscape-design/components/badge'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import Checkbox from '@cloudscape-design/components/checkbox'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

const DISK_TYPES = [
  { value: 'nvme_ssd', label: 'NVMe SSD', description: 'Highest IOPS — provisioned (io1, io2)' },
  { value: 'sas_ssd',  label: 'SAS SSD',  description: 'High throughput SSD (gp3)' },
  { value: 'sata_ssd', label: 'SATA SSD', description: 'General purpose SSD (gp2)' },
  { value: 'sas_hdd',  label: 'SAS HDD',  description: 'Throughput optimized HDD (st1)' },
  { value: 'sata_hdd', label: 'SATA HDD', description: 'Cold HDD (sc1)' },
]

const BACKENDS = [
  { value: 'lvm',  label: 'LVM', description: 'Local block device via logical volumes' },
  { value: 'ceph', label: 'Ceph RBD', description: 'Network block device from the storage node' },
]

const DISK_BADGE = {
  nvme_ssd: 'severity-high',
  sas_ssd: 'blue',
  sata_ssd: 'blue',
  sas_hdd: 'severity-medium',
  sata_hdd: 'grey',
}

// AWS EBS equivalents offered as one-click presets.
const PRESETS = [
  { name: 'gp2', disk_type: 'sata_ssd', backend: 'lvm',  max_iops: '3000',  max_throughput: '125',  description: 'General Purpose SSD (SATA)' },
  { name: 'gp3', disk_type: 'sas_ssd',  backend: 'lvm',  max_iops: '16000', max_throughput: '1000', description: 'General Purpose SSD (SAS) — throughput optimized' },
  { name: 'io1', disk_type: 'nvme_ssd', backend: 'lvm',  max_iops: '64000', max_throughput: '4000', description: 'Provisioned IOPS SSD NVMe' },
  { name: 'io2', disk_type: 'nvme_ssd', backend: 'ceph', max_iops: '64000', max_throughput: '4000', description: 'Provisioned IOPS SSD NVMe — Ceph RBD' },
  { name: 'st1', disk_type: 'sas_hdd',  backend: 'lvm',  max_iops: '500',   max_throughput: '500',  description: 'Throughput Optimized HDD' },
  { name: 'sc1', disk_type: 'sata_hdd', backend: 'lvm',  max_iops: '250',   max_throughput: '250',  description: 'Cold HDD — lowest cost' },
]

function CreateModal({ onDismiss, onCreate }) {
  const [form, setForm] = useState({
    name: '',
    disk_type: DISK_TYPES[2],
    backend: BACKENDS[0],
    max_iops: '',
    max_throughput: '',
    description: '',
    is_public: true,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const applyPreset = (preset) => setForm(f => ({
    ...f,
    name: preset.name,
    disk_type: DISK_TYPES.find(d => d.value === preset.disk_type) || f.disk_type,
    backend: BACKENDS.find(b => b.value === preset.backend) || f.backend,
    max_iops: preset.max_iops,
    max_throughput: preset.max_throughput,
    description: preset.description,
  }))

  const submit = async () => {
    if (!form.name) { setError('Type name is required.'); return }
    setLoading(true); setError('')
    try {
      await onCreate({
        name: form.name,
        disk_type: form.disk_type.value,
        backend: form.backend.value,
        max_iops: form.max_iops === '' ? null : Number(form.max_iops),
        max_throughput: form.max_throughput === '' ? null : Number(form.max_throughput),
        description: form.description,
        is_public: form.is_public,
      })
      onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create volume type"
      size="medium"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="vt-create-submit">Create type</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}

          <FormField label="Quick presets" description="AWS EBS equivalents — fills the form below.">
            <SpaceBetween direction="horizontal" size="xs">
              {PRESETS.map(p => (
                <Button key={p.name} onClick={() => applyPreset(p)}>{p.name}</Button>
              ))}
            </SpaceBetween>
          </FormField>

          <FormField label="Type name">
            <Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} placeholder="gp2" controlId="vt-name" />
          </FormField>

          <FormField label="Backend">
            <Select selectedOption={form.backend} onChange={({ detail }) => setForm(f => ({ ...f, backend: detail.selectedOption }))} options={BACKENDS} controlId="vt-backend" />
          </FormField>

          <FormField label="Disk type">
            <Select selectedOption={form.disk_type} onChange={({ detail }) => setForm(f => ({ ...f, disk_type: detail.selectedOption }))} options={DISK_TYPES} controlId="vt-disk-type" />
          </FormField>

          <FormField label="Max IOPS" description="Informational for LVM; enforced via rbd QoS on Ceph.">
            <Input value={form.max_iops} onChange={({ detail }) => setForm(f => ({ ...f, max_iops: detail.value }))} type="number" inputMode="numeric" placeholder="3000" controlId="vt-max-iops" />
          </FormField>

          <FormField label="Max throughput (MB/s)">
            <Input value={form.max_throughput} onChange={({ detail }) => setForm(f => ({ ...f, max_throughput: detail.value }))} type="number" inputMode="numeric" placeholder="125" controlId="vt-max-tp" />
          </FormField>

          <FormField label="Description">
            <Input value={form.description} onChange={({ detail }) => setForm(f => ({ ...f, description: detail.value }))} placeholder="General Purpose SSD" controlId="vt-description" />
          </FormField>

          <Checkbox checked={form.is_public} onChange={({ detail }) => setForm(f => ({ ...f, is_public: detail.checked }))}>
            Public — visible to all projects
          </Checkbox>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function VolumeTypesPage() {
  const toast = useToast()
  const [types, setTypes]     = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getVolumeTypes()
      .then(r => setTypes(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} volume type(s)? This fails if volumes still use them.`)) return
    for (const t of selected) {
      try { await api.deleteVolumeType(t.id) }
      catch (e) { toast(`${t.name}: ${e.message}`, 'error') }
    }
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? types.filter(t => t.name?.toLowerCase().includes(q) || t.disk_type?.toLowerCase().includes(q)) : types

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(types.length, selected.length)}
          description="Performance profiles that map volumes onto a storage backend"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} data-testid="refresh-vtypes" ariaLabel="Refresh volume types" />
              <Button disabled={!selected.length} onClick={deleteSelected}>Delete</Button>
              <Button variant="primary" onClick={() => setShowCreate(true)} data-testid="create-vtype-btn">Create type</Button>
            </SpaceBetween>
          }
        >
          Volume types
        </Header>
      }
    >
      <SpaceBetween size="l">
        {types.length === 0 && !loading && (
          <Alert type="warning" header="No volume types configured">
            Users cannot create volumes until at least one type exists. Use the presets in the create dialog
            to add AWS EBS equivalents (gp2, gp3, io1, io2, st1, sc1).
          </Alert>
        )}

        <Table
          variant="full-page"
          stickyHeader
          loading={loading}
          loadingText="Loading volume types"
          selectionType="multi"
          selectedItems={selected}
          onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
          items={filtered}
          trackBy="id"
          columnDefinitions={[
            { id: 'name', header: 'Type name', cell: item => <Box fontWeight="bold">{item.name}</Box>, isRowHeader: true, sortingField: 'name' },
            {
              id: 'disk_type',
              header: 'Disk type',
              cell: item => (
                <Badge color={DISK_BADGE[item.disk_type] || 'grey'}>
                  {DISK_TYPES.find(d => d.value === item.disk_type)?.label || item.disk_type}
                </Badge>
              ),
            },
            { id: 'backend', header: 'Backend', cell: item => <Badge color={item.backend === 'ceph' ? 'blue' : 'green'}>{String(item.backend || '').toUpperCase()}</Badge> },
            { id: 'lvm_type', header: 'LVM mode', cell: item => item.lvm_type || 'default' },
            { id: 'iops', header: 'Max IOPS', cell: item => (item.max_iops ? item.max_iops.toLocaleString() : '—') },
            { id: 'throughput', header: 'Max throughput', cell: item => (item.max_throughput ? `${item.max_throughput} MB/s` : '—') },
            { id: 'description', header: 'Description', cell: item => item.description || '—' },
            { id: 'public', header: 'Visibility', cell: item => <BoolStatus value={item.is_public} trueText="Public" falseText="Private" /> },
          ]}
          filter={
            <TextFilter
              filteringText={filter}
              filteringPlaceholder="Find volume types"
              filteringAriaLabel="Filter volume types"
              countText={`${filtered.length} matches`}
              onChange={({ detail }) => setFilter(detail.filteringText)}
            />
          }
          empty={
            filter
              ? <TableNoMatch onClear={() => setFilter('')} />
              : <TableEmpty
                  title="No volume types"
                  description="Add a volume type so users can provision volumes."
                  action={<Button variant="primary" onClick={() => setShowCreate(true)}>Create type</Button>}
                />
          }
        />
      </SpaceBetween>

      {showCreate && (
        <CreateModal
          onDismiss={() => setShowCreate(false)}
          onCreate={async (payload) => { await api.createVolumeType(payload); toast(`Volume type "${payload.name}" created`, 'success'); load() }}
        />
      )}
    </ContentLayout>
  )
}
