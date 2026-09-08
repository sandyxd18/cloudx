import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '@cloudscape-design/components/button'
import Checkbox from '@cloudscape-design/components/checkbox'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

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

const PRESETS = [
  { name: 'gp2', disk_type: 'sata_ssd', backend: 'lvm',  max_iops: '3000',  max_throughput: '125',  description: 'General Purpose SSD (SATA)' },
  { name: 'gp3', disk_type: 'sas_ssd',  backend: 'lvm',  max_iops: '16000', max_throughput: '1000', description: 'General Purpose SSD (SAS) — throughput optimized' },
  { name: 'io1', disk_type: 'nvme_ssd', backend: 'lvm',  max_iops: '64000', max_throughput: '4000', description: 'Provisioned IOPS SSD NVMe' },
  { name: 'io2', disk_type: 'nvme_ssd', backend: 'ceph', max_iops: '64000', max_throughput: '4000', description: 'Provisioned IOPS SSD NVMe — Ceph RBD' },
  { name: 'st1', disk_type: 'sas_hdd',  backend: 'lvm',  max_iops: '500',   max_throughput: '500',  description: 'Throughput Optimized HDD' },
  { name: 'sc1', disk_type: 'sata_hdd', backend: 'lvm',  max_iops: '250',   max_throughput: '250',  description: 'Cold HDD — lowest cost' },
]

export default function VolumeTypeCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
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

  const back = () => navigate('/volume-types')

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
    if (!form.name.trim()) { setError('Type name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createVolumeType({
        name: form.name.trim(),
        disk_type: form.disk_type.value,
        backend: form.backend.value,
        max_iops: form.max_iops === '' ? null : Number(form.max_iops),
        max_throughput: form.max_throughput === '' ? null : Number(form.max_throughput),
        description: form.description,
        is_public: form.is_public,
      })
      toast(`Volume type "${form.name.trim()}" created`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create volume type"
      description="Performance profiles that map volumes onto a storage backend."
      error={error}
      loading={loading}
      submitText="Create type"
      submitTestId="vt-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Quick presets" description="AWS EBS equivalents — fills the form below.">
            <SpaceBetween direction="horizontal" size="xs">
              {PRESETS.map(p => (
                <Button key={p.name} onClick={() => applyPreset(p)}>{p.name}</Button>
              ))}
            </SpaceBetween>
          </FormField>

          <FormField label="Type name">
            <Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} placeholder="gp2" controlId="vt-name" autoFocus />
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
      </Container>
    </FormPage>
  )
}
