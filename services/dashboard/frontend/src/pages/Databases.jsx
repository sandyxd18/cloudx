import { useEffect, useState, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
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

const stateType = s => (s === 'active' ? 'success' : s === 'error' ? 'error' : s === 'stopped' ? 'stopped' : 'pending')
const opt = v => ({ value: v, label: v })
const ENGINES = ['postgresql', 'mariadb'].map(opt)
const BACKENDS = ['vm', 'container'].map(opt)
const STORAGES = ['local', 'block', 'file'].map(opt)

export default function DatabasesPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [networks, setNetworks] = useState([])
  const [images, setImages] = useState([])
  const [flavors, setFlavors] = useState([])
  const [selected, setSelected] = useState([])
  const [modal, setModal] = useState(null) // 'create' | 'delete'
  const [busy, setBusy] = useState(false)
  const [secret, setSecret] = useState(null)
  const [name, setName] = useState('')
  const [engine, setEngine] = useState(ENGINES[0])
  const [backend, setBackend] = useState(BACKENDS[0])
  const [storage, setStorage] = useState(STORAGES[0])
  const [size, setSize] = useState('5')
  const [net, setNet] = useState(null)
  const [image, setImage] = useState(null)
  const [flavor, setFlavor] = useState(null)

  const one = selected.length === 1 ? selected[0] : null
  const vm = backend.value === 'vm'

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [d, n, i, f] = await Promise.all([api.getDatabases(), api.getNetworks(), api.getImages(), api.getFlavors()])
      setItems(d.items || [])
      setNetworks((n.networks || n.items || n || []).filter(x => !x.external))
      setImages(i.images || i.items || i || [])
      setFlavors(f.flavors || f.items || f || [])
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

  const create = () => {
    const body = { name, engine: engine.value, backend: backend.value, network_id: net.value, storage: vm ? storage.value : 'local' }
    if (vm) { body.image = image.value; body.flavor = flavor.value }
    if (vm && body.storage !== 'local') body.size_gb = Number(size)
    return run(async () => { setSecret(await api.createDatabase(body)) }, () => setName(''))
  }

  if (loading && !items.length) return <PageLoading text="Loading databases" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="PostgreSQL and MariaDB on a VM or in a container. Reachable only from the allowed network."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button disabled={!one || one.status !== 'stopped'} onClick={() => run(() => api.databaseAction(one.id, 'start'))}>Start</Button>
              <Button disabled={!one || one.status !== 'active'} onClick={() => run(() => api.databaseAction(one.id, 'stop'))}>Stop</Button>
              <Button disabled={!selected.length} onClick={() => setModal('delete')}>Delete</Button>
              <Button variant="primary" onClick={() => setModal('create')}>Create database</Button>
            </SpaceBetween>
          }
        >
          Databases
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
          { id: 'engine', header: 'Engine', cell: i => i.engine },
          { id: 'backend', header: 'Backend', cell: i => `${i.backend} / ${i.storage}` },
          { id: 'endpoint', header: 'Endpoint', cell: i => (i.host ? `${i.host}:${i.port}` : '-') },
          { id: 'status', header: 'Status', cell: i => <StatusIndicator type={stateType(i.status)}>{i.status}</StatusIndicator> },
          { id: 'error', header: 'Error', cell: i => i.error || '-' },
        ]}
        items={items}
        loading={loading}
        empty={<TableEmpty title="No databases" description="Create a PostgreSQL or MariaDB instance." />}
      />
      {modal === 'create' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create database"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name || !net || (vm && (!image || !flavor))} onClick={create}>Create</Button>
          </SpaceBetween>}>
          <SpaceBetween size="m">
            <FormField label="Name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
            <FormField label="Engine"><Select selectedOption={engine} onChange={({ detail }) => setEngine(detail.selectedOption)} options={ENGINES} /></FormField>
            <FormField label="Backend" description="A container supports local storage only.">
              <Select selectedOption={backend} onChange={({ detail }) => setBackend(detail.selectedOption)} options={BACKENDS} />
            </FormField>
            <FormField label="Network">
              <Select selectedOption={net} onChange={({ detail }) => setNet(detail.selectedOption)}
                options={networks.map(n => ({ value: n.id, label: n.name }))} placeholder="Choose a network" />
            </FormField>
            {vm && (
              <>
                <FormField label="Image" description="An Ubuntu cloud image.">
                  <Select selectedOption={image} onChange={({ detail }) => setImage(detail.selectedOption)}
                    options={images.map(n => ({ value: n.id, label: n.name }))} placeholder="Choose an image" />
                </FormField>
                <FormField label="Flavor">
                  <Select selectedOption={flavor} onChange={({ detail }) => setFlavor(detail.selectedOption)}
                    options={flavors.map(n => ({ value: n.name, label: n.name }))} placeholder="Choose a flavor" />
                </FormField>
                <FormField label="Storage" description="NFS is not recommended for databases; prefer block.">
                  <Select selectedOption={storage} onChange={({ detail }) => setStorage(detail.selectedOption)} options={STORAGES} />
                </FormField>
                {storage.value !== 'local' && (
                  <FormField label="Size (GB)"><Input type="number" value={size} onChange={({ detail }) => setSize(detail.value)} /></FormField>
                )}
              </>
            )}
          </SpaceBetween>
        </Modal>
      )}
      {secret && (
        <Modal visible onDismiss={() => setSecret(null)} header="Save the admin password"
          footer={<Button variant="primary" onClick={() => setSecret(null)}>Done</Button>}>
          <SpaceBetween size="m">
            <Alert type="warning">This password is shown once and is not stored. Copy it now.</Alert>
            <Box variant="code">{secret.admin_user} / {secret.password}</Box>
          </SpaceBetween>
        </Modal>
      )}
      {modal === 'delete' && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete database"
          loading={busy}
          onDismiss={() => setModal(null)}
          onConfirm={() => run(async () => { for (const s of selected) await api.deleteDatabase(s.id) }, () => setSelected([]))}
        />
      )}
    </ContentLayout>
  )
}
