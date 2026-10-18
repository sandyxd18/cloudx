import { useEffect, useState, useCallback } from 'react'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const LEVELS = [{ value: 'rw', label: 'Read/write' }, { value: 'ro', label: 'Read only' }]
const stateType = s => (s === 'available' || s === 'active' ? 'success' : s === 'error' ? 'error' : 'pending')

export default function FileStorePage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [shares, setShares] = useState([])
  const [nets, setNets] = useState([])
  const [networks, setNetworks] = useState([])
  const [selected, setSelected] = useState([])
  const [rules, setRules] = useState([])
  const [modal, setModal] = useState(null) // 'share' | 'net' | 'extend' | 'delete'
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [size, setSize] = useState('1')
  const [netRef, setNetRef] = useState(null)
  const [tenantNet, setTenantNet] = useState(null)
  const [to, setTo] = useState('')
  const [level, setLevel] = useState(LEVELS[0])

  const one = selected.length === 1 ? selected[0] : null

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, n, t] = await Promise.all([api.getShares(), api.getShareNetworks(), api.getNetworks()])
      setShares(s.items || [])
      setNets(n.items || [])
      setNetworks((t.networks || t.items || t || []).filter(x => !x.external))
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  const loadRules = useCallback(async () => {
    if (!one) return setRules([])
    try { setRules((await api.getShareAccess(one.id)).items || []) } catch (e) { toast(e.message, 'error') }
  }, [one?.id, toast])

  useEffect(() => { loadRules() }, [loadRules])

  const run = async (fn, after) => {
    setBusy(true)
    try {
      await fn()
      setModal(null)
      if (after) after()
      load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const netOptions = nets.filter(n => n.status === 'active').map(n => ({ value: n.id, label: `${n.name} (${n.ip})` }))

  if (loading && !shares.length && !nets.length) return <PageLoading text="Loading file store" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(shares.length, selected.length)}
          description="NFS shares mounted over a tenant network. A share is reachable only by the addresses you allow."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button disabled={!one || one.status !== 'available'} onClick={() => { setSize(String(one.size_gb + 1)); setModal('extend') }}>Extend</Button>
              <Button disabled={!selected.length} onClick={() => setModal('delete')}>Delete</Button>
              <Button onClick={() => setModal('net')}>Create share network</Button>
              <Button variant="primary" disabled={!netOptions.length} onClick={() => setModal('share')}>Create share</Button>
            </SpaceBetween>
          }
        >
          File Store
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Table
          variant="container"
          selectionType="multi"
          selectedItems={selected}
          onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
          trackBy="id"
          columnDefinitions={[
            { id: 'name', header: 'Share', cell: i => i.name, sortingField: 'name' },
            { id: 'size', header: 'Size (GB)', cell: i => i.size_gb },
            { id: 'status', header: 'Status', cell: i => <StatusIndicator type={stateType(i.status)}>{i.status}</StatusIndicator> },
            { id: 'mount', header: 'Mount target', cell: i => <code>{i.export_location}</code> },
          ]}
          items={shares}
          loading={loading}
          empty={<TableEmpty title="No shares" description={nets.length ? 'Create a share.' : 'Create a share network first.'} />}
          header={<Header variant="h2">Shares</Header>}
        />
        {one && (
          <Table
            variant="container"
            trackBy="id"
            columnDefinitions={[
              { id: 'to', header: 'Allowed address', cell: i => i.to },
              { id: 'level', header: 'Access', cell: i => (i.level === 'rw' ? 'Read/write' : 'Read only') },
              {
                id: 'rm', header: '', cell: i => (
                  <Button variant="link" onClick={() => run(() => api.deleteShareAccess(one.id, i.id), loadRules)}>Remove</Button>
                ),
              },
            ]}
            items={rules}
            empty={<TableEmpty title="Nobody can mount this share" description="Add an address to allow access." />}
            header={
              <Header
                variant="h2"
                description="Root is always squashed; with no rule nobody can mount."
                actions={<Button onClick={() => { setTo(''); setModal('access') }}>Add access rule</Button>}
              >
                Access for {one.name}
              </Header>
            }
          />
        )}
        <Table
          variant="container"
          trackBy="id"
          columnDefinitions={[
            { id: 'name', header: 'Share network', cell: i => i.name },
            { id: 'ip', header: 'Mount address', cell: i => i.ip },
            { id: 'status', header: 'Status', cell: i => <StatusIndicator type={stateType(i.status)}>{i.status}</StatusIndicator> },
            {
              id: 'rm', header: '', cell: i => (
                <Button variant="link" onClick={() => run(() => api.deleteShareNetwork(i.id))}>Delete</Button>
              ),
            },
          ]}
          items={nets}
          empty={<TableEmpty title="No share networks" description="A share network gives shares an address on your network." />}
          header={<Header variant="h2">Share networks</Header>}
        />
      </SpaceBetween>

      {modal === 'net' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create share network"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name || !tenantNet}
              onClick={() => run(() => api.createShareNetwork({ name, network_id: tenantNet.value }), () => setName(''))}>Create</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
            <FormField label="Network" description="VMs on this network can mount the shares.">
              <Select selectedOption={tenantNet} onChange={({ detail }) => setTenantNet(detail.selectedOption)}
                options={networks.map(n => ({ value: n.id, label: n.name }))} placeholder="Choose a network" />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'share' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create share"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name || !netRef || !Number(size)}
              onClick={() => run(() => api.createShare({ name, size_gb: Number(size), share_network_id: netRef.value }), () => setName(''))}>Create</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
            <FormField label="Size (GB)" constraintText="1 to 1000"><Input type="number" value={size} onChange={({ detail }) => setSize(detail.value)} /></FormField>
            <FormField label="Share network">
              <Select selectedOption={netRef} onChange={({ detail }) => setNetRef(detail.selectedOption)} options={netOptions} placeholder="Choose a share network" />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'extend' && one && (
        <Modal visible onDismiss={() => setModal(null)} header={`Extend ${one.name}`}
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={Number(size) <= one.size_gb}
              onClick={() => run(() => api.extendShare(one.id, Number(size)), () => setSelected([]))}>Extend</Button>
          </SpaceBetween>}>
          <FormField label="New size (GB)" description="Online; the share stays mounted. It cannot shrink.">
            <Input type="number" value={size} onChange={({ detail }) => setSize(detail.value)} />
          </FormField>
        </Modal>
      )}
      {modal === 'access' && one && (
        <Modal visible onDismiss={() => setModal(null)} header={`Allow access to ${one.name}`}
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!to}
              onClick={() => run(() => api.addShareAccess(one.id, { to, level: level.value }), loadRules)}>Add</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Address or CIDR" description="For example 10.0.0.5 or 10.0.0.0/24. Ranges wider than /8 are refused.">
              <Input value={to} onChange={({ detail }) => setTo(detail.value)} />
            </FormField>
            <FormField label="Access">
              <Select selectedOption={level} onChange={({ detail }) => setLevel(detail.selectedOption)} options={LEVELS} />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'delete' && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete share"
          loading={busy}
          onDismiss={() => setModal(null)}
          onConfirm={() => run(async () => { for (const s of selected) await api.deleteShare(s.id) }, () => setSelected([]))}
        />
      )}
    </ContentLayout>
  )
}
