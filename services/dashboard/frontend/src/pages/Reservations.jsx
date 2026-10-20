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

const stateType = s => (s === 'active' ? 'success' : s === 'expired' ? 'stopped' : 'pending')
const local = d => new Date(d + 'Z').toLocaleString() // the API sends UTC without an offset

export default function ReservationsPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [flavors, setFlavors] = useState([])
  const [selected, setSelected] = useState([])
  const [modal, setModal] = useState(null) // 'create' | 'delete'
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [flavor, setFlavor] = useState(null)
  const [amount, setAmount] = useState('1')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [l, f] = await Promise.all([api.getLeases(), api.getFlavors()])
      setItems(l.items || [])
      setFlavors(f.items || f.flavors || f || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

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

  // datetime-local gives local time without an offset; send it as UTC ISO with a Z.
  const iso = v => new Date(v).toISOString()

  if (loading && !items.length) return <PageLoading text="Loading reservations" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="Reserve flavor capacity for a time window. A lease is refused when the cluster cannot hold it. It is a capacity record: it does not block or create instances."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button disabled={!selected.length} onClick={() => setModal('delete')}>Delete</Button>
              <Button variant="primary" onClick={() => setModal('create')}>Create lease</Button>
            </SpaceBetween>
          }
        >
          Reservations
        </Header>
      }
    >
      <Table
        variant="container"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="id"
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: i => i.name, sortingField: 'name' },
          { id: 'flavor', header: 'Flavor', cell: i => `${i.flavor} x ${i.amount}` },
          { id: 'res', header: 'Reserved', cell: i => `${i.vcpus} vCPU, ${i.ram_mb} MB` },
          { id: 'start', header: 'Start', cell: i => local(i.start_at) },
          { id: 'end', header: 'End', cell: i => local(i.end_at) },
          { id: 'status', header: 'Status', cell: i => <StatusIndicator type={stateType(i.status)}>{i.status}</StatusIndicator> },
        ]}
        items={items}
        loading={loading}
        empty={<TableEmpty title="No leases" description="Create a lease to reserve capacity for a period." />}
      />
      {modal === 'create' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create lease"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name || !flavor || !start || !end}
              onClick={() => run(() => api.createLease({ name, flavor: flavor.value, amount: Number(amount), start_at: iso(start), end_at: iso(end) }), () => setName(''))}>Create</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
            <FormField label="Flavor">
              <Select selectedOption={flavor} onChange={({ detail }) => setFlavor(detail.selectedOption)}
                options={flavors.map(f => ({ value: f.name, label: `${f.name} (${f.vcpus} vCPU, ${f.ram_mb} MB)` }))} placeholder="Choose a flavor" />
            </FormField>
            <FormField label="Amount" constraintText="1 to 100"><Input type="number" value={amount} onChange={({ detail }) => setAmount(detail.value)} /></FormField>
            <FormField label="Start" constraintText="Your local time">
              <input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} aria-label="Start" />
            </FormField>
            <FormField label="End" constraintText="At most 30 days after the start">
              <input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} aria-label="End" />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'delete' && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete lease"
          loading={busy}
          onDismiss={() => setModal(null)}
          onConfirm={() => run(async () => { for (const s of selected) await api.deleteLease(s.id) }, () => setSelected([]))}
        />
      )}
    </ContentLayout>
  )
}
