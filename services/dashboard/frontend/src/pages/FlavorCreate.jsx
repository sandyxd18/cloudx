import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function FlavorCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [form, setForm] = useState({ name: '', vcpus: '1', ram_mb: '512', disk_gb: '10' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/flavors')

  const submit = async () => {
    if (!form.name.trim()) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createFlavor({
        name: form.name.trim(),
        vcpus: Number(form.vcpus),
        ram_mb: Number(form.ram_mb),
        disk_gb: Number(form.disk_gb),
      })
      toast(`Flavor "${form.name.trim()}" created`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const field = (key) => ({
    value: form[key],
    onChange: ({ detail }) => setForm(f => ({ ...f, [key]: detail.value })),
  })

  return (
    <FormPage
      title="Create flavor"
      description="Instance types defining vCPU, memory, and root disk size."
      error={error}
      loading={loading}
      submitText="Create flavor"
      submitTestId="flavor-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Flavor name">
            <Input {...field('name')} placeholder="m1.small" controlId="flavor-name" autoFocus />
          </FormField>
          <FormField label="vCPUs">
            <Input {...field('vcpus')} type="number" inputMode="numeric" controlId="flavor-vcpus" />
          </FormField>
          <FormField label="Memory (MB)">
            <Input {...field('ram_mb')} type="number" inputMode="numeric" controlId="flavor-ram" />
          </FormField>
          <FormField label="Root disk (GB)">
            <Input {...field('disk_gb')} type="number" inputMode="numeric" controlId="flavor-disk" />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
