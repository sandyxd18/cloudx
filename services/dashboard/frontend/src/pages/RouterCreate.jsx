import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function RouterCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')
  const [gw, setGw] = useState(null)
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => setNetworks(r?.items || [])).catch(() => setNetworks([]))
  }, [])

  const back = () => navigate('/routers')

  const submit = async () => {
    if (!name) { setError('Router name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createRouter({ name, external_gateway_network: gw?.value || undefined })
      toast('Router created', 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const externalNets = networks.filter(n => n.external || n['router:external'] === 'External')

  return (
    <FormPage
      title="Create router"
      description="OVN logical routers providing east-west and north-south routing."
      error={error}
      loading={loading}
      submitText="Create router"
      submitTestId="router-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Router name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-router" controlId="router-name" autoFocus />
          </FormField>
          <FormField label="External gateway network" description="Optional — enables SNAT and floating IPs.">
            <Select
              selectedOption={gw}
              onChange={({ detail }) => setGw(detail.selectedOption)}
              options={(externalNets.length ? externalNets : networks).map(n => ({ value: n.name, label: n.name }))}
              placeholder="No gateway"
              empty="No networks"
              controlId="router-gw"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
