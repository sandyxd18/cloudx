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

const POLICIES = [
  { value: 'anti-affinity',      label: 'Anti-affinity',      description: 'Place instances on different hosts' },
  { value: 'affinity',           label: 'Affinity',           description: 'Place instances on the same host' },
  { value: 'soft-anti-affinity', label: 'Soft anti-affinity', description: 'Prefer different hosts (best effort)' },
  { value: 'soft-affinity',      label: 'Soft affinity',      description: 'Prefer the same host (best effort)' },
]

export default function ServerGroupCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')
  const [policy, setPolicy] = useState(POLICIES[0])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/server-groups')

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createServerGroup({ name: name.trim(), policy: policy.value })
      toast(`Server group "${name.trim()}" created`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create server group"
      description="Affinity and anti-affinity placement policies for instances."
      error={error}
      loading={loading}
      submitText="Create server group"
      submitTestId="servergroup-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-server-group" controlId="sg-name" autoFocus />
          </FormField>
          <FormField label="Placement policy" description="Controls how the scheduler distributes group members across hosts.">
            <Select selectedOption={policy} onChange={({ detail }) => setPolicy(detail.selectedOption)} options={POLICIES} controlId="sg-policy" />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
