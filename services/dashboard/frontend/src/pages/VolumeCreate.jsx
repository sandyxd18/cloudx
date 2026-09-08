import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function VolumeCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
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

  const back = () => navigate('/volumes')

  const submit = async () => {
    if (!form.name) { setError('Volume name is required.'); return }
    if (typesLoaded && types.length === 0) { setError('No volume types are configured. An administrator must create one first.'); return }
    setLoading(true); setError('')
    try {
      const payload = { name: form.name, size_gb: Number(form.size_gb) }
      if (form.volume_type) payload.volume_type = form.volume_type.value
      await api.createVolume(payload)
      toast('Volume creation started', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create volume"
      description="Persistent block storage backed by LVM or Ceph."
      error={error}
      loading={loading}
      submitText="Create volume"
      submitTestId="vol-create-submit"
      submitDisabled={typesLoaded && types.length === 0}
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          {typesLoaded && types.length === 0 && (
            <Alert type="warning" header="No volume types configured">
              An administrator must create at least one volume type before volumes can be provisioned.
            </Alert>
          )}
          <FormField label="Volume name">
            <Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} placeholder="my-volume" controlId="vol-name" autoFocus />
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
      </Container>
    </FormPage>
  )
}
