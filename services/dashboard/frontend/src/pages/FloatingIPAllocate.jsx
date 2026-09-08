import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function FloatingIPAllocatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [pool, setPool] = useState(null)
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => setNetworks(r?.items || [])).catch(() => setNetworks([]))
  }, [])

  const back = () => navigate('/floating-ips')

  const submit = async () => {
    if (!pool) { setError('Select an external network pool.'); return }
    setLoading(true); setError('')
    try {
      await api.createFloatingIP({ network: pool.value })
      toast('Floating IP allocated', 'success')
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
      title="Allocate floating IP"
      description="Public addresses mapped to instance ports via OVN DNAT/SNAT."
      error={error}
      loading={loading}
      submitText="Allocate floating IP"
      submitTestId="fip-allocate-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Pool (external network)" description="Select the external provider network to allocate an IP from.">
            <Select
              selectedOption={pool}
              onChange={({ detail }) => setPool(detail.selectedOption)}
              options={(externalNets.length ? externalNets : networks).map(n => ({ value: n.name, label: n.name }))}
              placeholder="Choose a pool"
              empty="No external networks"
              controlId="fip-network"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
