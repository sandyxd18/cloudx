import { useState } from 'react'
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
  const [form, setForm] = useState({ name: '', visibility: VISIBILITY_OPTIONS[0] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/object-store')

  const submit = async () => {
    if (!form.name.trim()) { setError('Bucket name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createBucket({
        name: form.name.trim(),
        visibility: form.visibility.value,
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
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
