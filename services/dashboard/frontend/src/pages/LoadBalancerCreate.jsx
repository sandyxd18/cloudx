import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Wizard from '@cloudscape-design/components/wizard'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty } from '../components/common.jsx'

export default function LoadBalancerCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [activeStepIndex, setActiveStepIndex] = useState(0)

  // Step 1: Load Balancer
  const [name, setName] = useState('')
  const [lbType, setLbType] = useState({ label: 'Network Load Balancer (L4)', value: 'NLB' })
  const [networks, setNetworks] = useState([])
  const [selectedNetwork, setSelectedNetwork] = useState(null)

  // Step 2: Listener
  const [listenerName, setListenerName] = useState('')
  const [listenerPort, setListenerPort] = useState('80')
  const [listenerProtocol, setListenerProtocol] = useState({ label: 'TCP', value: 'TCP' })

  // Step 3: Pool
  const [poolName, setPoolName] = useState('')
  const [poolAlgorithm, setPoolAlgorithm] = useState({ label: 'Round Robin', value: 'ROUND_ROBIN' })
  const [instances, setInstances] = useState([])
  const [selectedInstances, setSelectedInstances] = useState([])
  const [memberPort, setMemberPort] = useState('80')

  // Step 4: Health Monitor
  const [healthType, setHealthType] = useState({ label: 'HTTP', value: 'HTTP' })
  const [healthDelay, setHealthDelay] = useState('5')
  const [healthTimeout, setHealthTimeout] = useState('5')
  const [healthMaxRetries, setHealthMaxRetries] = useState('3')
  const [healthPath, setHealthPath] = useState('/')

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getNetworks().then(r => {
      const allNets = r?.items || []
      const tenantNets = allNets.filter(n => !n.external && n['router:external'] !== 'External')
      setNetworks(tenantNets)
      if (tenantNets.length) {
        setSelectedNetwork({ label: tenantNets[0].name, value: tenantNets[0].id })
      }
    }).catch(() => {})

    api.getServers().then(r => {
      const srvs = r?.items || []
      setInstances(srvs)
    }).catch(() => {})
  }, [])

  const submit = async () => {
    if (!name.trim()) { setError('Load balancer name is required.'); return }
    if (!selectedNetwork) { setError('VIP network is required.'); return }
    if (!selectedInstances.length) { setError('Select at least one instance for the load balancer pool.'); return }

    setLoading(true)
    setError('')
    try {
      const lb = await api.createLoadBalancer({
        name: name.trim(),
        lb_type: lbType.value,
        vip_network_id: selectedNetwork.value,
      })

      const listener = await api.createListener(lb.id, {
        name: listenerName.trim() || `${name.trim()}-listener`,
        protocol: listenerProtocol.value,
        protocol_port: parseInt(listenerPort, 10) || 80,
      })

      const membersPayload = selectedInstances.map(inst => {
        const ip = inst.ip_address || (inst.addresses && Object.values(inst.addresses)[0]?.[0]?.addr) || '192.168.12.10'
        return {
          name: inst.name,
          address: ip,
          instance_id: inst.id,
          protocol_port: parseInt(memberPort, 10) || 80,
        }
      })

      const pool = await api.createPool(listener.id, {
        name: poolName.trim() || `${name.trim()}-pool`,
        lb_algorithm: poolAlgorithm.value,
        protocol_port: parseInt(memberPort, 10) || 80,
        members: membersPayload,
      })

      if (healthType?.value) {
        try {
          await api.createHealthMonitor(pool.id, {
            type: healthType.value,
            delay: parseInt(healthDelay, 10) || 5,
            timeout: parseInt(healthTimeout, 10) || 5,
            max_retries: parseInt(healthMaxRetries, 10) || 3,
            url_path: healthType.value === 'HTTP' ? (healthPath.trim() || '/') : undefined,
          })
        } catch { /* proceed even if monitor registration has warning */ }
      }

      toast('Load balancer created successfully', 'success')
      navigate(`/load-balancers/${lb.id}`)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const typeOptions = [
    { label: 'Network Load Balancer (L4)', value: 'NLB' },
    { label: 'Application Load Balancer (L7)', value: 'ALB' },
  ]

  const protocolOptions = lbType.value === 'ALB'
    ? [{ label: 'HTTP', value: 'HTTP' }, { label: 'HTTPS', value: 'HTTPS' }, { label: 'TCP', value: 'TCP' }]
    : [{ label: 'TCP', value: 'TCP' }, { label: 'UDP', value: 'UDP' }]

  const algorithmOptions = [
    { label: 'Round Robin', value: 'ROUND_ROBIN' },
    { label: 'Least Connections', value: 'LEAST_CONNECTIONS' },
    { label: 'Source IP', value: 'SOURCE_IP' },
  ]

  const healthTypeOptions = [
    { label: 'HTTP', value: 'HTTP' },
    { label: 'TCP', value: 'TCP' },
    { label: 'PING', value: 'PING' },
  ]

  return (
    <Wizard
      i18nStrings={{
        stepNumberLabel: stepNumber => `Step ${stepNumber}`,
        collapsedStepsLabel: (stepNumber, stepsCount) => `Step ${stepNumber} of ${stepsCount}`,
        skipToButtonLabel: (step, stepNumber) => `Skip to ${step.title}`,
        cancelButton: 'Cancel',
        previousButton: 'Previous',
        nextButton: 'Next',
        submitButton: 'Create load balancer',
        optional: 'optional',
      }}
      activeStepIndex={activeStepIndex}
      onNavigate={({ detail }) => {
        setError('')
        setActiveStepIndex(detail.requestedStepIndex)
      }}
      onCancel={() => navigate('/load-balancers')}
      onSubmit={submit}
      isLoadingNextStep={loading}
      steps={[
        {
          title: 'Load balancer details',
          description: 'Step 1: Specify basic load balancer information and network',
          content: (
            <Container header={<Header variant="h2">Basic settings</Header>}>
              <SpaceBetween size="l">
                {error && <Alert type="error">{error}</Alert>}
                <FormField label="Load balancer name" description="Unique identifier for the load balancer instance.">
                  <Input
                    value={name}
                    onChange={({ detail }) => setName(detail.value)}
                    placeholder="my-lb"
                    controlId="lb-name"
                    autoFocus
                  />
                </FormField>
                <FormField label="Load balancer type" description="NLB uses OVN native L4 forwarding, ALB uses HAProxy L7.">
                  <Select
                    selectedOption={lbType}
                    onChange={({ detail }) => {
                      setLbType(detail.selectedOption)
                      setListenerProtocol(detail.selectedOption.value === 'ALB'
                        ? { label: 'HTTP', value: 'HTTP' }
                        : { label: 'TCP', value: 'TCP' })
                    }}
                    options={typeOptions}
                    controlId="lb-type"
                  />
                </FormField>
                <FormField label="VIP network" description="Tenant network to allocate the Virtual IP (VIP).">
                  <Select
                    selectedOption={selectedNetwork}
                    onChange={({ detail }) => setSelectedNetwork(detail.selectedOption)}
                    options={networks.map(n => ({ label: n.name, value: n.id }))}
                    placeholder="Select tenant network"
                    empty="No tenant networks available"
                    controlId="lb-vip-net"
                  />
                </FormField>
              </SpaceBetween>
            </Container>
          ),
        },
        {
          title: 'Listener details',
          description: 'Step 2: Configure frontend listener protocol and port',
          content: (
            <Container header={<Header variant="h2">Listener settings</Header>}>
              <SpaceBetween size="l">
                <FormField label="Listener name" description="Optional — defaults to LB name + -listener.">
                  <Input
                    value={listenerName}
                    onChange={({ detail }) => setListenerName(detail.value)}
                    placeholder={name ? `${name}-listener` : 'my-lb-listener'}
                    controlId="lb-listener-name"
                  />
                </FormField>
                <FormField label="Frontend protocol">
                  <Select
                    selectedOption={listenerProtocol}
                    onChange={({ detail }) => setListenerProtocol(detail.selectedOption)}
                    options={protocolOptions}
                    controlId="lb-listener-protocol"
                  />
                </FormField>
                <FormField label="Port" description="Port number the load balancer listens on.">
                  <Input
                    value={listenerPort}
                    onChange={({ detail }) => setListenerPort(detail.value)}
                    type="number"
                    placeholder="80"
                    controlId="lb-listener-port"
                  />
                </FormField>
              </SpaceBetween>
            </Container>
          ),
        },
        {
          title: 'Pool details',
          description: 'Step 3: Define backend server pool and target instances',
          content: (
            <Container header={<Header variant="h2">Pool settings</Header>}>
              <SpaceBetween size="l">
                {error && <Alert type="error">{error}</Alert>}
                <FormField label="Pool name" description="Optional — defaults to LB name + -pool.">
                  <Input
                    value={poolName}
                    onChange={({ detail }) => setPoolName(detail.value)}
                    placeholder={name ? `${name}-pool` : 'my-lb-pool'}
                    controlId="lb-pool-name"
                  />
                </FormField>
                <FormField label="Algorithm" description="Traffic distribution method across pool members.">
                  <Select
                    selectedOption={poolAlgorithm}
                    onChange={({ detail }) => setPoolAlgorithm(detail.selectedOption)}
                    options={algorithmOptions}
                    controlId="lb-pool-algo"
                  />
                </FormField>
                <FormField label="Member port" description="Target port running on backend instances.">
                  <Input
                    value={memberPort}
                    onChange={({ detail }) => setMemberPort(detail.value)}
                    type="number"
                    placeholder="80"
                    controlId="lb-member-port"
                  />
                </FormField>
                <FormField label="Target instances" description="Select instance(s) to add as members to this load balancer pool.">
                  <Table
                    selectionType="multi"
                    selectedItems={selectedInstances}
                    onSelectionChange={({ detail }) => setSelectedInstances(detail.selectedItems)}
                    trackBy="id"
                    columnDefinitions={[
                      { id: 'name', header: 'Instance Name', cell: item => <Box fontWeight="bold">{item.name}</Box> },
                      { id: 'ip', header: 'IP Address', cell: item => item.ip_address || (item.addresses && Object.values(item.addresses)[0]?.[0]?.addr) || '-' },
                      { id: 'status', header: 'Status', cell: item => item.status || 'ACTIVE' },
                    ]}
                    items={instances}
                    empty={<TableEmpty title="No active instances" subtitle="Launch instances first to add them to load balancer pool." />}
                  />
                </FormField>
              </SpaceBetween>
            </Container>
          ),
        },
        {
          title: 'Monitor details',
          description: 'Step 4: Configure health check probe parameters',
          content: (
            <Container header={<Header variant="h2">Health monitor settings</Header>}>
              <SpaceBetween size="l">
                {error && <Alert type="error">{error}</Alert>}
                <FormField label="Health monitor type">
                  <Select
                    selectedOption={healthType}
                    onChange={({ detail }) => setHealthType(detail.selectedOption)}
                    options={healthTypeOptions}
                    controlId="lb-health-type"
                  />
                </FormField>
                <FormField label="Delay (seconds)" description="Interval between health check probes.">
                  <Input
                    value={healthDelay}
                    onChange={({ detail }) => setHealthDelay(detail.value)}
                    type="number"
                    placeholder="5"
                    controlId="lb-health-delay"
                  />
                </FormField>
                <FormField label="Timeout (seconds)" description="Maximum time allowed for probe response.">
                  <Input
                    value={healthTimeout}
                    onChange={({ detail }) => setHealthTimeout(detail.value)}
                    type="number"
                    placeholder="5"
                    controlId="lb-health-timeout"
                  />
                </FormField>
                <FormField label="Max retries" description="Consecutive failed probes before marking member down.">
                  <Input
                    value={healthMaxRetries}
                    onChange={({ detail }) => setHealthMaxRetries(detail.value)}
                    type="number"
                    placeholder="3"
                    controlId="lb-health-retries"
                  />
                </FormField>
                {healthType.value === 'HTTP' && (
                  <FormField label="HTTP URL path" description="Path requested during HTTP health check.">
                    <Input
                      value={healthPath}
                      onChange={({ detail }) => setHealthPath(detail.value)}
                      placeholder="/"
                      controlId="lb-health-path"
                    />
                  </FormField>
                )}
              </SpaceBetween>
            </Container>
          ),
        },
      ]}
    />
  )
}
