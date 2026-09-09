import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import Checkbox from '@cloudscape-design/components/checkbox'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function SubnetCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [searchParams] = useSearchParams()
  const preselectedNetworkId = searchParams.get('network')

  const [networks, setNetworks] = useState([])
  const [network, setNetwork] = useState(null)
  const [form, setForm] = useState({
    name: '', cidr: '', gateway_ip: '',
    allocation_start: '', allocation_end: '',
    dns: '8.8.8.8,8.8.4.4', dhcp: true,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => {
      const items = r?.items || []
      setNetworks(items)
      if (preselectedNetworkId) {
        const match = items.find(n => n.id === preselectedNetworkId)
        if (match) setNetwork({ value: match.id, label: match.name })
      }
    }).catch(() => setNetworks([]))
  }, [preselectedNetworkId])

  const back = () => navigate(preselectedNetworkId ? `/networks/${preselectedNetworkId}` : '/networks')

  const submit = async () => {
    if (!form.name.trim()) { setError('Subnet name is required.'); return }
    if (!network) { setError('Select a network.'); return }
    if (!form.cidr.trim()) { setError('Network address (CIDR) is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createSubnet({
        name: form.name.trim(),
        network: network.value,
        cidr: form.cidr.trim(),
        gateway_ip: form.gateway_ip || undefined,
        allocation_start: form.allocation_start || undefined,
        allocation_end: form.allocation_end || undefined,
        dns_nameservers: form.dns ? form.dns.split(',').map(s => s.trim()).filter(Boolean) : [],
        enable_dhcp: form.dhcp,
      })
      toast(`Subnet "${form.name.trim()}" created`, 'success')
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
      title="Create subnet"
      description="Add a subnet to allocate IP addresses on an existing network."
      error={error}
      loading={loading}
      submitText="Create subnet"
      submitTestId="subnet-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container header={<Header variant="h2">Subnet settings</Header>}>
        <SpaceBetween size="l">
          <FormField label="Network" description="The network this subnet belongs to.">
            <Select
              selectedOption={network}
              onChange={({ detail }) => setNetwork(detail.selectedOption)}
              options={networks.map(n => ({ value: n.id, label: n.name }))}
              placeholder="Choose a network"
              empty="No networks"
              disabled={!!preselectedNetworkId}
              controlId="subnet-network"
            />
          </FormField>

          <FormField label="Subnet name">
            <Input {...field('name')} placeholder="my-subnet-2" controlId="subnet-name" autoFocus />
          </FormField>

          <FormField label="Network address (CIDR)">
            <Input {...field('cidr')} placeholder="192.168.20.0/24" controlId="subnet-cidr" />
          </FormField>

          <FormField label="Gateway IP" description="Leave empty to use the first address in the range.">
            <Input {...field('gateway_ip')} placeholder="192.168.20.1" controlId="subnet-gw" />
          </FormField>

          <FormField label="Allocation pool start">
            <Input {...field('allocation_start')} placeholder="192.168.20.10" controlId="subnet-pool-start" />
          </FormField>

          <FormField label="Allocation pool end">
            <Input {...field('allocation_end')} placeholder="192.168.20.200" controlId="subnet-pool-end" />
          </FormField>

          <FormField label="DNS name servers" description="Comma-separated list.">
            <Input {...field('dns')} placeholder="8.8.8.8,8.8.4.4" controlId="subnet-dns" />
          </FormField>

          <Checkbox
            checked={form.dhcp}
            onChange={({ detail }) => setForm(f => ({ ...f, dhcp: detail.checked }))}
            controlId="subnet-dhcp"
          >
            Enable DHCP — instances receive addresses automatically
          </Checkbox>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
