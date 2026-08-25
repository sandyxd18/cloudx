import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Tabs from '@cloudscape-design/components/tabs'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import Checkbox from '@cloudscape-design/components/checkbox'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

// ── Create network / subnet modal ────────────────────────────────────────────
function CreateModal({ onDismiss, onDone, networks }) {
  const [tab, setTab] = useState('network')
  const [net, setNet] = useState({ name: '', external: false, shared: false })
  const [sub, setSub] = useState({
    name: '', network: null, cidr: '', gateway_ip: '',
    dns: '8.8.8.8,8.8.4.4', allocation_start: '', allocation_end: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const createNetwork = async () => {
    if (!net.name) { setError('Network name is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createNetwork({ name: net.name, external: net.external, shared: net.shared })
      onDone('Network created')
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  const createSubnet = async () => {
    if (!sub.name || !sub.network || !sub.cidr) { setError('Name, network, and CIDR are required.'); return }
    setLoading(true); setError('')
    try {
      await api.createSubnet({
        name: sub.name,
        network: sub.network.value,
        cidr: sub.cidr,
        gateway_ip: sub.gateway_ip || undefined,
        allocation_start: sub.allocation_start || undefined,
        allocation_end: sub.allocation_end || undefined,
        dns_nameservers: sub.dns ? sub.dns.split(',').map(s => s.trim()).filter(Boolean) : [],
      })
      onDone('Subnet created')
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create network resources"
      size="large"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button
              variant="primary"
              loading={loading}
              onClick={tab === 'network' ? createNetwork : createSubnet}
              data-testid={tab === 'network' ? 'network-create-submit' : 'subnet-create-submit'}
            >
              {tab === 'network' ? 'Create network' : 'Create subnet'}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
        <Tabs
          activeTabId={tab}
          onChange={({ detail }) => { setTab(detail.activeTabId); setError('') }}
          tabs={[
            {
              id: 'network',
              label: 'Network',
              content: (
                <Form>
                  <SpaceBetween size="m">
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
                </Form>
              ),
            },
            {
              id: 'subnet',
              label: 'Subnet',
              content: (
                <Form>
                  <SpaceBetween size="m">
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
                </Form>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Modal>
  )
}

export default function NetworksPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [networks, setNetworks] = useState([])
  const [subnets, setSubnets]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getNetworks(), api.getSubnets()])
      .then(([n, s]) => {
        if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
        if (s.status === 'fulfilled') setSubnets(s.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const subnetsFor = (netId) => subnets.filter(s => s.network_id === netId)

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} network(s)?`)) return
    for (const n of selected) {
      try { await api.deleteNetwork(n.id) } catch (e) { toast(`${n.name}: ${e.message}`, 'error') }
    }
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? networks.filter(n => n.name?.toLowerCase().includes(q)) : networks

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(networks.length, selected.length)}
          description={`Virtual networks backed by OVN — ${subnets.length} subnet(s) defined`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh networks" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'detail', text: 'View details', disabled: selected.length !== 1 },
                  { id: 'delete', text: 'Delete network' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') deleteSelected()
                  else if (detail.id === 'detail' && selected[0]) navigate(`/networks/${selected[0].id}`)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowCreate(true)} data-testid="create-network-btn">Create network</Button>
            </SpaceBetween>
          }
        >
          Networks
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading networks"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Network name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Link href={`/networks/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/networks/${item.id}`) }}>
                  {item.name}
                </Link>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          {
            id: 'subnets',
            header: 'Associated subnets',
            cell: item => {
              const list = subnetsFor(item.id)
              if (!list.length) return '—'
              return (
                <SpaceBetween size="xxxs">
                  {list.map(s => <Box key={s.id} variant="small">{s.name} · {s.cidr}</Box>)}
                </SpaceBetween>
              )
            },
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
          { id: 'external', header: 'External', cell: item => <BoolStatus value={!!(item.external || item['router:external'] === 'External')} /> },
          { id: 'shared', header: 'Shared', cell: item => <BoolStatus value={!!item.shared} /> },
          { id: 'admin', header: 'Admin state', cell: item => <BoolStatus value={item.admin_state_up !== false} trueText="UP" falseText="DOWN" /> },
          { id: 'az', header: 'Availability zone', cell: item => item.availability_zone || 'nova' },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find networks"
            filteringAriaLabel="Filter networks"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No networks"
                description="Create a network and subnet to give instances connectivity."
                action={<Button variant="primary" onClick={() => setShowCreate(true)}>Create network</Button>}
              />
        }
      />

      {showCreate && (
        <CreateModal
          networks={networks}
          onDismiss={() => setShowCreate(false)}
          onDone={(msg) => { toast(msg, 'success'); setShowCreate(false); load() }}
        />
      )}
    </ContentLayout>
  )
}
