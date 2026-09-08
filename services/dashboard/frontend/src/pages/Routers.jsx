import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Select from '@cloudscape-design/components/select'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

function InterfaceModal({ router, onDismiss, onAdd }) {
  const [subnets, setSubnets] = useState([])
  const [subnet, setSubnet] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getSubnets().then(r => setSubnets(r?.items || [])).catch(() => setSubnets([]))
  }, [])

  const submit = async () => {
    if (!subnet) { setError('Select a subnet.'); return }
    setLoading(true); setError('')
    try { await onAdd({ subnet: subnet.value }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Add interface — ${router.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="iface-add-submit">Add interface</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Subnet" description="The router receives the subnet gateway address.">
            <Select
              selectedOption={subnet}
              onChange={({ detail }) => setSubnet(detail.selectedOption)}
              options={subnets.map(s => ({ value: s.name, label: s.name, description: s.cidr }))}
              placeholder="Choose a subnet"
              empty="No subnets"
              controlId="iface-subnet"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

function GatewayModal({ router, onDismiss, onSet, networks }) {
  const [gw, setGw] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const externalNets = networks.filter(n => n.external || n['router:external'] === 'External')

  const submit = async () => {
    if (!gw) { setError('Select an external network.'); return }
    setLoading(true); setError('')
    try { await onSet({ network: gw.value }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Set gateway — ${router.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit}>Set gateway</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="External network">
            <Select
              selectedOption={gw}
              onChange={({ detail }) => setGw(detail.selectedOption)}
              options={(externalNets.length ? externalNets : networks).map(n => ({ value: n.name, label: n.name }))}
              placeholder="Choose an external network"
              empty="No external networks"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function RoutersPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [routers, setRouters]   = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [addIface, setAddIface] = useState(null)
  const [setGateway, setSetGateway] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getRouters(), api.getNetworks()])
      .then(([r, n]) => {
        if (r.status === 'fulfilled') setRouters(r.value?.items || [])
        if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} router(s)?`)) return
    for (const r of selected) {
      try { await api.deleteRouter(r.id) } catch (e) { toast(`${r.name}: ${e.message}`, 'error') }
    }
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? routers.filter(r => r.name?.toLowerCase().includes(q)) : routers

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(routers.length, selected.length)}
          description="OVN logical routers providing east-west and north-south routing"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh routers" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'interface', text: 'Add interface', disabled: !one },
                  { id: 'gateway',   text: 'Set gateway',   disabled: !one },
                  { id: 'delete',    text: 'Delete router' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') deleteSelected()
                  else if (detail.id === 'interface' && one) setAddIface(one)
                  else if (detail.id === 'gateway' && one) setSetGateway(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/routers/create')} data-testid="create-router-btn">Create router</Button>
            </SpaceBetween>
          }
        >
          Routers
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading routers"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Router name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Box fontWeight="bold">{item.name}</Box>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
          {
            id: 'gateway',
            header: 'External gateway',
            cell: item => item.external_gateway_network || item.external_gateway_info?.network_id || '—',
          },
          { id: 'admin', header: 'Admin state', cell: item => <BoolStatus value={item.admin_state_up !== false} trueText="UP" falseText="DOWN" /> },
          { id: 'az', header: 'Availability zone', cell: item => item.availability_zone || 'nova' },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find routers"
            filteringAriaLabel="Filter routers"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No routers"
                description="Create a router to connect subnets and reach external networks."
                action={<Button variant="primary" onClick={() => navigate('/routers/create')}>Create router</Button>}
              />
        }
      />

      {addIface && (
        <InterfaceModal
          router={addIface}
          onDismiss={() => setAddIface(null)}
          onAdd={async (payload) => { await api.addRouterInterface(addIface.id, payload); toast('Interface added', 'success'); load() }}
        />
      )}
      {setGateway && (
        <GatewayModal
          router={setGateway}
          networks={networks}
          onDismiss={() => setSetGateway(null)}
          onSet={async (payload) => { await api.setRouterGateway(setGateway.id, payload); toast('Gateway set', 'success'); load() }}
        />
      )}
    </ContentLayout>
  )
}
