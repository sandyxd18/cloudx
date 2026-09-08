import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Checkbox from '@cloudscape-design/components/checkbox'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Tabs from '@cloudscape-design/components/tabs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function NetworkCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [tab, setTab] = useState('network')
  const [networks, setNetworks] = useState([])
  const [net, setNet] = useState({ name: '', external: false, shared: false })
  const [sub, setSub] = useState({
    name: '', network: null, cidr: '', gateway_ip: '',
    dns: '8.8.8.8,8.8.4.4', allocation_start: '', allocation_end: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => setNetworks(r?.items || [])).catch(() => setNetworks([]))
  }, [])

  const back = () => navigate('/networks')

  const submit = async () => {
    setLoading(true); setError('')
    try {
      if (tab === 'network') {
        if (!net.name) { setError('Network name is required.'); setLoading(false); return }
        await api.createNetwork({ name: net.name, external: net.external, shared: net.shared })
        toast('Network created', 'success')
      } else {
        if (!sub.name || !sub.network || !sub.cidr) { setError('Name, network, and CIDR are required.'); setLoading(false); return }
        await api.createSubnet({
          name: sub.name,
          network: sub.network.value,
          cidr: sub.cidr,
          gateway_ip: sub.gateway_ip || undefined,
          allocation_start: sub.allocation_start || undefined,
          allocation_end: sub.allocation_end || undefined,
          dns_nameservers: sub.dns ? sub.dns.split(',').map(s => s.trim()).filter(Boolean) : [],
        })
        toast('Subnet created', 'success')
      }
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create network resources"
      description="Virtual networks and subnets backed by OVN."
      error={error}
      loading={loading}
      submitText={tab === 'network' ? 'Create network' : 'Create subnet'}
      submitTestId={tab === 'network' ? 'network-create-submit' : 'subnet-create-submit'}
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <Tabs
          activeTabId={tab}
          onChange={({ detail }) => { setTab(detail.activeTabId); setError('') }}
          tabs={[
            {
              id: 'network',
              label: 'Network',
              content: (
                <SpaceBetween size="l">
                  <FormField label="Network name">
                    <Input value={net.name} onChange={({ detail }) => setNet(n => ({ ...n, name: detail.value }))} placeholder="my-network" controlId="network-name" />
                  </FormField>
                  <Checkbox checked={net.external} onChange={({ detail }) => setNet(n => ({ ...n, external: detail.checked }))} controlId="network-external">
                    External network — provides floating IPs and north-south routing
                  </Checkbox>
                  <Checkbox checked={net.shared} onChange={({ detail }) => setNet(n => ({ ...n, shared: detail.checked }))}>
                    Shared — usable by all projects
                  </Checkbox>
                </SpaceBetween>
              ),
            },
            {
              id: 'subnet',
              label: 'Subnet',
              content: (
                <SpaceBetween size="l">
                  <FormField label="Subnet name">
                    <Input value={sub.name} onChange={({ detail }) => setSub(s => ({ ...s, name: detail.value }))} placeholder="my-subnet" controlId="subnet-name" />
                  </FormField>
                  <FormField label="Network">
                    <Select
                      selectedOption={sub.network}
                      onChange={({ detail }) => setSub(s => ({ ...s, network: detail.selectedOption }))}
                      options={networks.map(n => ({ value: n.name, label: n.name }))}
                      placeholder="Choose a network"
                      empty="No networks"
                      controlId="subnet-network"
                    />
                  </FormField>
                  <FormField label="Network address (CIDR)">
                    <Input value={sub.cidr} onChange={({ detail }) => setSub(s => ({ ...s, cidr: detail.value }))} placeholder="192.168.10.0/24" controlId="subnet-cidr" />
                  </FormField>
                  <FormField label="Gateway IP" description="Leave empty to use the first address in the range.">
                    <Input value={sub.gateway_ip} onChange={({ detail }) => setSub(s => ({ ...s, gateway_ip: detail.value }))} placeholder="192.168.10.1" controlId="subnet-gw" />
                  </FormField>
                  <FormField label="Allocation pool start">
                    <Input value={sub.allocation_start} onChange={({ detail }) => setSub(s => ({ ...s, allocation_start: detail.value }))} placeholder="192.168.10.10" />
                  </FormField>
                  <FormField label="Allocation pool end">
                    <Input value={sub.allocation_end} onChange={({ detail }) => setSub(s => ({ ...s, allocation_end: detail.value }))} placeholder="192.168.10.200" />
                  </FormField>
                  <FormField label="DNS name servers" description="Comma-separated list.">
                    <Input value={sub.dns} onChange={({ detail }) => setSub(s => ({ ...s, dns: detail.value }))} placeholder="8.8.8.8,8.8.4.4" controlId="subnet-dns" />
                  </FormField>
                </SpaceBetween>
              ),
            },
          ]}
        />
      </Container>
    </FormPage>
  )
}
