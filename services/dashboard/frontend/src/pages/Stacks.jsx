import { useState, useEffect, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, counterText, fmtDate } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const kind = s => (s || '').endsWith('_COMPLETE') ? 'success' : (s || '').endsWith('_FAILED') ? 'error' : 'in-progress'
const list = (d, k) => (Array.isArray(d) ? d : d?.[k] || d?.items || [])

export default function StacksPage() {
  const toast = useToast()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState([])
  const [detail, setDetail] = useState({ resources: [], events: [] })
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ name: '', template: '', parameters: '{}' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showDelete, setShowDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getStacks().then(d => setItems(list(d, 'stacks')))
      .catch(e => toast(e.message || 'Failed to load stacks', 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const pick = sel => {
    setSelected(sel)
    setDetail({ resources: [], events: [] })
    if (sel.length !== 1) return
    const id = sel[0].id
    Promise.allSettled([api.getStackResources(id), api.getStackEvents(id)]).then(([r, e]) =>
      setDetail({
        resources: r.status === 'fulfilled' ? list(r.value, 'resources') : [],
        events: e.status === 'fulfilled' ? list(e.value, 'events') : [],
      }))
  }

  const create = async () => {
    setSaving(true); setError('')
    try {
      const parameters = JSON.parse(form.parameters || '{}')
      await api.createStack({ name: form.name, template: form.template, parameters })
      toast('Stack creation started', 'success')
      setShowAdd(false); setForm({ name: '', template: '', parameters: '{}' }); load()
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  const act = async action => {
    try { await api.stackAction(selected[0].id, action); toast(`Stack ${action} requested`, 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const remove = async () => {
    setDeleting(true)
    for (const s of selected) {
      try { await api.deleteStack(s.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false); setShowDelete(false); pick([]); load()
  }

  const one = selected.length === 1 ? selected[0] : null

  return (
    <ContentLayout header={<Header variant="h1" description="Heat-like templates that create networks, servers and more as one unit">Stacks</Header>}>
      <SpaceBetween size="l">
        <Table
          variant="full-page"
          stickyHeader
          selectionType="multi"
          selectedItems={selected}
          onSelectionChange={({ detail }) => pick(detail.selectedItems)}
          trackBy="id"
          header={
            <Header
              variant="h2"
              counter={counterText(items.length, selected.length)}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="refresh" onClick={load} ariaLabel="Refresh stacks" />
                  <Button disabled={!one} onClick={() => act('suspend')}>Suspend</Button>
                  <Button disabled={!one} onClick={() => act('resume')}>Resume</Button>
                  <Button disabled={!selected.length} onClick={() => setShowDelete(true)}>Delete</Button>
                  <Button variant="primary" onClick={() => { setError(''); setShowAdd(true) }}>Create stack</Button>
                </SpaceBetween>
              }
            >
              Stacks
            </Header>
          }
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: s => <Box fontWeight="bold">{s.stack_name}</Box> },
            { id: 'status', header: 'Status', cell: s => <StatusIndicator type={kind(s.stack_status)}>{s.stack_status}</StatusIndicator> },
            { id: 'reason', header: 'Reason', cell: s => s.stack_status_reason || '-' },
            { id: 'created', header: 'Created', cell: s => fmtDate(s.creation_time) },
          ]}
          items={items}
          loading={loading}
          loadingText="Loading stacks"
          empty={<TableEmpty title="No stacks" description="Create a stack from a template." />}
        />
        {one && (
          <Tabs
            tabs={[
              {
                id: 'resources', label: 'Resources',
                content: (
                  <Table
                    columnDefinitions={[
                      { id: 'n', header: 'Name', cell: r => r.resource_name || r.name },
                      { id: 't', header: 'Type', cell: r => r.resource_type || r.type },
                      { id: 's', header: 'Status', cell: r => r.resource_status || r.status },
                      { id: 'p', header: 'Physical ID', cell: r => r.physical_resource_id || r.physical_id || '-' },
                    ]}
                    items={detail.resources}
                    empty={<TableEmpty title="No resources" />}
                  />
                ),
              },
              {
                id: 'events', label: 'Events',
                content: (
                  <Table
                    columnDefinitions={[
                      { id: 'at', header: 'Time', cell: e => fmtDate(e.event_time || e.created_at) },
                      { id: 'r', header: 'Resource', cell: e => e.resource_name || '-' },
                      { id: 's', header: 'Status', cell: e => e.resource_status || e.status },
                      { id: 'm', header: 'Reason', cell: e => e.resource_status_reason || e.reason || '-' },
                    ]}
                    items={detail.events}
                    empty={<TableEmpty title="No events" />}
                  />
                ),
              },
              {
                id: 'outputs', label: 'Outputs',
                content: <Box variant="code"><pre>{JSON.stringify(one.outputs || {}, null, 2)}</pre></Box>,
              },
            ]}
          />
        )}
      </SpaceBetween>

      {showAdd && (
        <Modal
          visible
          size="large"
          onDismiss={() => setShowAdd(false)}
          header="Create stack"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowAdd(false)}>Cancel</Button>
                <Button variant="primary" loading={saving} disabled={!form.name || !form.template} onClick={create}>Create</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Name"><Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} /></FormField>
            <FormField label="Template" description="YAML or JSON"><Textarea rows={12} value={form.template} onChange={({ detail }) => setForm(f => ({ ...f, template: detail.value }))} /></FormField>
            <FormField label="Parameters (JSON)"><Textarea rows={3} value={form.parameters} onChange={({ detail }) => setForm(f => ({ ...f, parameters: detail.value }))} /></FormField>
          </SpaceBetween>
        </Modal>
      )}

      {showDelete && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={one ? one.stack_name : undefined}
          title="Delete stack"
          confirmButtonText="Delete"
          loading={deleting}
          onDismiss={() => setShowDelete(false)}
          onConfirm={remove}
        />
      )}
    </ContentLayout>
  )
}
