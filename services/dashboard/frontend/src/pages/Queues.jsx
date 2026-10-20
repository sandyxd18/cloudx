import { useEffect, useState, useCallback } from 'react'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function QueuesPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState([])
  const [modal, setModal] = useState(null) // 'create' | 'delete'
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = (await api.getQueues()).items || []
      const stats = await Promise.all(qs.map(q => api.getQueueStats(q.name).catch(() => ({}))))
      setItems(qs.map((q, i) => ({ ...q, ...stats[i] })))
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

  if (loading && !items.length) return <PageLoading text="Loading queues" />

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(items.length, selected.length)}
          description="Message queues for your applications. Producers post, consumers claim and delete; unacknowledged messages come back."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button disabled={!selected.length} onClick={() => setModal('delete')}>Delete</Button>
              <Button variant="primary" onClick={() => setModal('create')}>Create queue</Button>
            </SpaceBetween>
          }
        >
          Queues
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
          { id: 'total', header: 'Messages', cell: i => i.total ?? '-' },
          { id: 'free', header: 'Waiting', cell: i => i.free ?? '-' },
          { id: 'claimed', header: 'Claimed', cell: i => i.claimed ?? '-' },
        ]}
        items={items}
        loading={loading}
        empty={<TableEmpty title="No queues" description="Create a queue, then post to it with the API or `cloudx queue post`." />}
      />
      {modal === 'create' && (
        <Modal visible onDismiss={() => setModal(null)} header="Create queue"
          footer={<SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={!name}
              onClick={() => run(() => api.createQueue(name), () => setName(''))}>Create</Button>
          </SpaceBetween>}>
          <FormField label="Name" constraintText="1 to 64 letters, digits, _ or -">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} />
          </FormField>
        </Modal>
      )}
      {modal === 'delete' && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete queue"
          loading={busy}
          onDismiss={() => setModal(null)}
          onConfirm={() => run(async () => { for (const s of selected) await api.deleteQueue(s.name) }, () => setSelected([]))}
        />
      )}
    </ContentLayout>
  )
}
