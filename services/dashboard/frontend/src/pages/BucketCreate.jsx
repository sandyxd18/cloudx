import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

const VISIBILITY_OPTIONS = [
  { value: 'private', label: 'Private (Owner only)' },
  { value: 'public-read', label: 'Public Read (Anyone can read objects)' },
]

export default function BucketCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [form, setForm] = useState({ name: '', visibility: VISIBILITY_OPTIONS[0], selectedNetwork: null })
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => {
      const items = r?.items || []
      const tenantNets = items.filter(n => !n.external && n['router:external'] !== 'External')
      setNetworks(tenantNets)
    }).catch(() => {})
  }, [])

  const back = () => navigate('/object-store')

  const submit = async () => {
    if (!form.name.trim()) { setError('Bucket name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createBucket({
        name: form.name.trim(),
        visibility: form.visibility.value,
        network_id: form.selectedNetwork?.value || undefined,
      })
      toast('Bucket created successfully', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create bucket"
      description="Create an object storage bucket for files and binary objects."
      error={error}
      loading={loading}
      submitText="Create bucket"
      submitTestId="bucket-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Bucket name" description="Must be unique within object storage.">
            <Input
              value={form.name}
              onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
              placeholder="my-bucket"
              controlId="bucket-name"
              autoFocus
            />
          </FormField>
          <FormField label="Visibility" description="Access permissions for objects inside this bucket.">
            <Select
              selectedOption={form.visibility}
              onChange={({ detail }) => setForm(f => ({ ...f, visibility: detail.selectedOption }))}
              options={VISIBILITY_OPTIONS}
              controlId="bucket-visibility"
            />
          </FormField>
          <FormField label="Network (Optional)" description="Select associated tenant network for this bucket endpoint.">
            <Select
              selectedOption={form.selectedNetwork}
              onChange={({ detail }) => setForm(f => ({ ...f, selectedNetwork: detail.selectedOption }))}
              options={networks.map(n => ({ label: n.name, value: n.id }))}
              placeholder="Select network (Optional)"
              controlId="bucket-network"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
