import { useEffect, useState, useCallback } from 'react'
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
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

function AllocateModal({ onDismiss, onCreate, networks }) {
  const [pool, setPool] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const externalNets = networks.filter(n => n.external || n['router:external'] === 'External')

  const submit = async () => {
    if (!pool) { setError('Select an external network pool.'); return }
    setLoading(true); setError('')
    try { await onCreate({ network: pool.value }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Allocate floating IP"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="fip-allocate-submit">Allocate</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Pool (external network)">
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
      </Form>
    </Modal>
  )
}

function AssociateModal({ fip, onDismiss, onAssociate }) {
  const [target, setTarget] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!target) { setError('Enter a server name or port ID.'); return }
    setLoading(true); setError('')
    try { await onAssociate({ port: target }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Associate floating IP"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="fip-assoc-submit">Associate</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <Alert type="info">Floating IP: <b>{fip.floating_ip_address || fip.floating_ip}</b></Alert>
          <FormField label="Server name or port ID" description="The DNAT rule is programmed in OVN for the selected port.">
            <Input value={target} onChange={({ detail }) => setTarget(detail.value)} placeholder="my-instance" controlId="fip-assoc-port" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function FloatingIPsPage() {
  const toast = useToast()
  const [fips, setFips]         = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [showAllocate, setShowAllocate] = useState(false)
  const [associate, setAssociate] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getFloatingIPs(), api.getNetworks()])
      .then(([f, n]) => {
        if (f.status === 'fulfilled') setFips(f.value?.items || [])
        if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null
  const oneAssociated = !!(one?.port_id || one?.fixed_ip_address || one?.fixed_ip)

  const releaseSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Release ${selected.length} floating IP(s)?`)) return
    for (const f of selected) {
      try { await api.deleteFloatingIP(f.id) } catch (e) { toast(e.message, 'error') }
    }
    setSelected([])
    toast(`${selected.length} floating IP(s) released`, 'success')
    load()
  }

  const disassociate = async (fip) => {
    try { await api.disassociateFloatingIP(fip.id); toast('Floating IP disassociated', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? fips.filter(f => (f.floating_ip_address || f.floating_ip || '').toLowerCase().includes(q))
    : fips

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(fips.length, selected.length)}
          description="Public addresses mapped to instance ports via OVN DNAT/SNAT"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh floating IPs" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'associate',    text: 'Associate',    disabled: !one || oneAssociated },
                  { id: 'disassociate', text: 'Disassociate', disabled: !one || !oneAssociated },
                  { id: 'release',      text: 'Release floating IP' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'release') releaseSelected()
                  else if (detail.id === 'associate' && one) setAssociate(one)
                  else if (detail.id === 'disassociate' && one) disassociate(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowAllocate(true)} data-testid="allocate-fip-btn">
                Allocate floating IP
              </Button>
            </SpaceBetween>
          }
        >
          Floating IPs
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading floating IPs"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'ip',
            header: 'Floating IP',
            cell: item => <Box fontWeight="bold">{item.floating_ip_address || item.floating_ip || '—'}</Box>,
            isRowHeader: true,
          },
          {
            id: 'status',
            header: 'Status',
            cell: item => <ResourceStatus status={item.status || (item.port_id ? 'active' : 'down')} />,
          },
          { id: 'fixed', header: 'Mapped fixed IP', cell: item => item.fixed_ip_address || item.fixed_ip || '—' },
          { id: 'port', header: 'Port', cell: item => (item.port_id ? shortId(item.port_id, 12) : '—') },
          { id: 'pool', header: 'Pool', cell: item => (item.floating_network_id ? shortId(item.floating_network_id, 12) : '—') },
          { id: 'router', header: 'Router', cell: item => (item.router_id ? shortId(item.router_id, 12) : '—') },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find floating IPs"
            filteringAriaLabel="Filter floating IPs"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No floating IPs"
                description="Allocate a floating IP from an external network pool to expose an instance."
                action={<Button variant="primary" onClick={() => setShowAllocate(true)}>Allocate floating IP</Button>}
              />
        }
      />

      {showAllocate && (
        <AllocateModal
          networks={networks}
          onDismiss={() => setShowAllocate(false)}
          onCreate={async (payload) => { await api.createFloatingIP(payload); toast('Floating IP allocated', 'success'); load() }}
        />
      )}
      {associate && (
        <AssociateModal
          fip={associate}
          onDismiss={() => setAssociate(null)}
          onAssociate={async (payload) => { await api.associateFloatingIP(associate.id, payload); toast('Floating IP associated', 'success'); load() }}
        />
      )}
    </ContentLayout>
  )
}
