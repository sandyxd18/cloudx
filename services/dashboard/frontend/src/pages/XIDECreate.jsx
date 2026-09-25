import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

const PROFILES = [
  { value: 'default',  label: 'Default (2 vCPU, 4 GiB)', description: 'General-purpose coding' },
  { value: 'large',    label: 'Large (4 vCPU, 8 GiB)',   description: 'Builds, containers, heavier workloads' },
  { value: 'xlarge',   label: 'XLarge (8 vCPU, 16 GiB)', description: 'Monorepos, data processing' },
]

const AUTO_STOP_OPTIONS = [
  { value: '30m', label: '30 minutes' },
  { value: '1h',  label: '1 hour' },
  { value: '4h',  label: '4 hours' },
  { value: 'never', label: 'Never' },
]

export default function XIDECreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [form, setForm] = useState({
    name: '',
    description: '',
    profile: PROFILES[0],
    auto_stop: AUTO_STOP_OPTIONS[1],
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/xide')

  const submit = async () => {
    if (!form.name.trim()) { setError('Environment name is required.'); return }
    setLoading(true)
    setError('')
    try {
      await api.createXIDEWorkspace({
        name: form.name.trim(),
        description: form.description.trim(),
        profile: form.profile.value,
        auto_stop: form.auto_stop.value,
      })
      toast('XIDE environment created', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create XIDE environment"
      description="Provision a cloud-based VS Code workspace with isolated storage."
      error={error}
      loading={loading}
      submitText="Create environment"
      submitTestId="xide-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Environment name">
            <Input
              value={form.name}
              onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
              placeholder="my-project-env"
              controlId="xide-name"
              autoFocus
            />
          </FormField>
          <FormField label="Description" description="Optional description of this environment's purpose.">
            <Textarea
              value={form.description}
              onChange={({ detail }) => setForm(f => ({ ...f, description: detail.value }))}
              placeholder="e.g. React frontend development workspace"
              controlId="xide-desc"
              rows={3}
            />
          </FormField>
          <FormField label="Instance profile" description="Compute resources allocated to the environment.">
            <Select
              selectedOption={form.profile}
              onChange={({ detail }) => setForm(f => ({ ...f, profile: detail.selectedOption }))}
              options={PROFILES}
              controlId="xide-profile"
            />
          </FormField>
          <FormField label="Auto-stop timeout" description="Idle time before the environment is automatically suspended.">
            <Select
              selectedOption={form.auto_stop}
              onChange={({ detail }) => setForm(f => ({ ...f, auto_stop: detail.selectedOption }))}
              options={AUTO_STOP_OPTIONS}
              controlId="xide-autostop"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}