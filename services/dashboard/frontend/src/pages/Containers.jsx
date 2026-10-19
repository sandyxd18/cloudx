import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
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

const stateType = s => (s === 'running' ? 'success' : s === 'error' ? 'error' : s === 'stopped' ? 'stopped' : 'pending')

export default function ContainersPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [networks, setNetworks] = useState([])
  const [selected, setSelected] = useState([])
  const [modal, setModal] = useState(null) // 'create' | 'logs' | 'delete'
  const [busy, setBusy] = useState(false)
  const [logs, setLogs] = useState('')
  const [name, setName] = useState('')
  const [image, setImage] = useState('')
  const [net, setNet] = useState(null)
  const [cpu, setCpu] = useState('0.5')
  const [mem, setMem] = useState('128')

  const one = selected.length === 1 ? selected[0] : null

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [c, t] = await Promise.all([api.getContainers(), api.getNetworks()])
      setItems(c.items || [])
      setNetworks((t.networks || t.items || t || []).filter(x => !x.external))
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

  const showLogs = async () => {
    try { setLogs((await api.getContainerLogs(one.id)).logs || '(no output yet)'); setModal('logs') } catch (e) { toast(e.message, 'error') }
  }

  if (loading && !items.length) return <PageLoading text="Loading containers" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="Single containers with their own address on a tenant network. Security groups apply as for instances."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button disabled={!one} onClick={showLogs}>Logs</Button>
              <Button disabled={!one || one.status === 'running'} onClick={() => run(() => api.containerAction(one.id, 'start'))}>Start</Button>
              <Button disabled={!one || one.status !== 'running'} onClick={() => run(() => api.containerAction(one.id, 'stop'))}>Stop</Button>
              <Button disabled={!one} onClick={() => run(() => api.containerAction(one.id, 'restart'))}>Restart</Button>
              <Button disabled={!selected.length} onClick={() => setModal('delete')}>Delete</Button>
              <Button variant="primary" onClick={() => setModal('create')}>Create container</Button>
            </SpaceBetween>
          }
        >
          Containers
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
          { id: 'image', header: 'Image', cell: i => i.image },
          { id: 'ip', header: 'IP address', cell: i => i.ip },
          { id: 'status', header: 'Status', cell: i => <StatusIndicator type={stateType(i.status)}>{i.status}</StatusIndicator> },
          { id: 'error', header: 'Error', cell: i => i.error || '-' },
          { id: 'host', header: 'Host', cell: i => i.host },
        ]}
        items={items}
        loading={loading}
        empty={<TableEmpty title="No containers" description="Create a container from a registry image." />}
      />
      {modal === 'create' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create container"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name || !image || !net}
              onClick={() => run(() => api.createContainer({ name, image, network_id: net.value, cpu: Number(cpu), memory_mb: Number(mem) }), () => { setName(''); setImage('') })}>Create</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
            <FormField label="Image" description="For example nginx:1.27. The node pulls it from the registry.">
              <Input value={image} onChange={({ detail }) => setImage(detail.value)} />
            </FormField>
            <FormField label="Network">
              <Select selectedOption={net} onChange={({ detail }) => setNet(detail.selectedOption)}
                options={networks.map(n => ({ value: n.id, label: n.name }))} placeholder="Choose a network" />
            </FormField>
            <FormField label="CPU" constraintText="0.1 to 4"><Input type="number" value={cpu} onChange={({ detail }) => setCpu(detail.value)} /></FormField>
            <FormField label="Memory (MB)" constraintText="64 to 4096"><Input type="number" value={mem} onChange={({ detail }) => setMem(detail.value)} /></FormField>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'logs' && one && (
        <Modal visible size="large" onDismiss={() => setModal(null)} header={`Logs: ${one.name}`}>
          <Box variant="code"><pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{logs}</pre></Box>
        </Modal>
      )}
      {modal === 'delete' && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete container"
          loading={busy}
          onDismiss={() => setModal(null)}
          onConfirm={() => run(async () => { for (const s of selected) await api.deleteContainer(s.id) }, () => setSelected([]))}
        />
      )}
    </ContentLayout>
  )
}