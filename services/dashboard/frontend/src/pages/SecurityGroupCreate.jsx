import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function SecurityGroupCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/security-groups')

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try {
      const sg = await api.createSecurityGroup({ name: name.trim(), description })
      toast(`Security group "${name.trim()}" created`, 'success')
      navigate(`/security-groups/${sg.id}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create security group"
      description="A security group acts as a virtual firewall for your instances."
      error={error}
      loading={loading}
      submitText="Create security group"
      submitTestId="sg-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Security group name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="web-servers" controlId="sg-name" autoFocus />
          </FormField>
          <FormField label="Description">
            <Input value={description} onChange={({ detail }) => setDescription(detail.value)} placeholder="Allows HTTP and SSH" controlId="sg-desc" />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
