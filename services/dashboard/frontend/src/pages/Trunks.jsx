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
import Multiselect from '@cloudscape-design/components/multiselect'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

import DeleteModal from '../components/DeleteModal.jsx'

const portOption = (p) => ({
  value: p.id,
  label: p.name || shortId(p.id, 16),
  description: [p.fixed_ips?.[0]?.ip_address, p.name ? shortId(p.id, 16) : null].filter(Boolean).join(' · ') || undefined,
})

// Rows of {port, segId} for the sub-ports of a trunk (port select + VLAN ID).
function SubportRows({ rows, setRows, portOptions }) {
  const update = (i, patch) => setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  return (
    <SpaceBetween size="s">
      {rows.map((row, i) => (
        <SpaceBetween key={i} direction="horizontal" size="xs" alignItems="end">
          <FormField label={i === 0 ? 'Sub-port' : undefined}>
            <Select
              selectedOption={row.port}
              onChange={({ detail }) => update(i, { port: detail.selectedOption })}
              options={portOptions}
              placeholder="Choose a port"
              empty="No free ports"
              ariaLabel={`Sub-port ${i + 1}`}
            />
          </FormField>
          <FormField label={i === 0 ? 'VLAN ID' : undefined}>
            <Input
              type="number"
              inputMode="numeric"
              value={row.segId}
              onChange={({ detail }) => update(i, { segId: detail.value })}
              placeholder="1-4094"
              ariaLabel={`VLAN ID ${i + 1}`}
            />
          </FormField>
          <Button iconName="close" variant="icon" ariaLabel={`Remove sub-port ${i + 1}`} onClick={() => setRows(rows.filter((_, idx) => idx !== i))} />
        </SpaceBetween>
      ))}
      <Button iconName="add-plus" onClick={() => setRows([...rows, { port: null, segId: '' }])}>Add sub-port</Button>
    </SpaceBetween>
  )
}

// Turns the form rows into the TrunkSubPortsPayload entries, or returns an error string.
function buildSubports(rows) {
  const out = []
  const seen = new Set()
  for (const row of rows) {
    if (!row.port) return { error: 'Choose a port for every sub-port row.' }
    const segId = Number(row.segId)
    if (!Number.isInteger(segId) || segId < 1 || segId > 4094) return { error: 'VLAN ID must be a whole number between 1 and 4094.' }
    if (seen.has(segId)) return { error: `VLAN ID ${segId} is used more than once.` }
    seen.add(segId)
    out.push({ port_id: row.port.value, segmentation_type: 'vlan', segmentation_id: segId })
  }
  return { subPorts: out }
}

function CreateTrunkModal({ ports, trunks, onDismiss, onDone }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [parent, setParent] = useState(null)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const used = new Set(trunks.flatMap(t => [t.port_id, ...(t.sub_ports || []).map(s => s.port_id)]))
  const parentOptions = ports.filter(p => !used.has(p.id)).map(portOption)
  const subOptions = parentOptions.filter(o => o.value !== parent?.value)

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    if (!parent) { setError('Select a parent port.'); return }
    const built = buildSubports(rows)
    if (built.error) { setError(built.error); return }
    if (built.subPorts.some(s => s.port_id === parent.value)) { setError('The parent port cannot also be a sub-port.'); return }
    setLoading(true); setError('')
    let trunk
    try {
      const body = { name: name.trim(), port_id: parent.value }
      if (description.trim()) body.description = description.trim()
      trunk = await api.createTrunk(body)
    } catch (e) { setError(e.message); setLoading(false); return }
    if (built.subPorts.length) {
      try { await api.addTrunkSubports(trunk.id, { sub_ports: built.subPorts }) }
      catch (e) {
        toast(`Trunk ${trunk.name} created, but adding sub-ports failed: ${e.message}`, 'error')
        setLoading(false); onDone(); onDismiss(); return
      }
    }
    toast(`Trunk ${trunk.name} created`, 'success')
    setLoading(false); onDone(); onDismiss()
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="large"
      header="Create trunk"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="trunk-create-submit">Create trunk</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="trunk-1" data-testid="trunk-name" />
          </FormField>
          <FormField label="Description" description="Optional.">
            <Input value={description} onChange={({ detail }) => setDescription(detail.value)} />
          </FormField>
          <FormField label="Parent port" description="The port that carries the tagged traffic. A port can be the parent of one trunk only.">
            <Select
              selectedOption={parent}
              onChange={({ detail }) => setParent(detail.selectedOption)}
              options={parentOptions}
              placeholder="Choose a port"
              empty="No free ports"
              filteringType="auto"
              controlId="trunk-parent-port"
            />
          </FormField>
          <FormField label="Sub-ports" description="Optional. Each sub-port is a port tagged with a VLAN ID inside the trunk.">
            <SubportRows rows={rows} setRows={setRows} portOptions={subOptions} />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

function AddSubportsModal({ trunk, ports, trunks, onDismiss, onDone }) {
  const toast = useToast()
  const [rows, setRows] = useState([{ port: null, segId: '' }])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const used = new Set(trunks.flatMap(t => [t.port_id, ...(t.sub_ports || []).map(s => s.port_id)]))
  const options = ports.filter(p => !used.has(p.id)).map(portOption)

  const submit = async () => {
    const built = buildSubports(rows)
    if (built.error) { setError(built.error); return }
    const taken = new Set((trunk.sub_ports || []).map(s => s.segmentation_id))
    const clash = built.subPorts.find(s => taken.has(s.segmentation_id))
    if (clash) { setError(`VLAN ID ${clash.segmentation_id} is already in use on this trunk.`); return }
    setLoading(true); setError('')
    try {
      await api.addTrunkSubports(trunk.id, { sub_ports: built.subPorts })
      toast(`Sub-ports added to ${trunk.name}`, 'success')
      onDone(); onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="large"
      header={`Add sub-ports — ${trunk.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="trunk-subport-add-submit">Add sub-ports</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <SubportRows rows={rows} setRows={setRows} portOptions={options.filter(o => o.value !== trunk.port_id)} />
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

function RemoveSubportsModal({ trunk, ports, onDismiss, onDone }) {
  const toast = useToast()
  const [selected, setSelected] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const portName = (id) => {
    const p = ports.find(x => x.id === id)
    return p?.name || shortId(id, 16)
  }
  const options = (trunk.sub_ports || []).map(s => ({
    value: s.port_id,
    label: portName(s.port_id),
    description: `${s.segmentation_type} ${s.segmentation_id}`,
  }))

  const submit = async () => {
    if (!selected.length) { setError('Select at least one sub-port.'); return }
    const chosen = new Set(selected.map(o => o.value))
    const sub_ports = trunk.sub_ports
      .filter(s => chosen.has(s.port_id))
      .map(s => ({ port_id: s.port_id, segmentation_type: s.segmentation_type, segmentation_id: s.segmentation_id }))
    setLoading(true); setError('')
    try {
      await api.removeTrunkSubports(trunk.id, { sub_ports })
      toast(`Sub-ports removed from ${trunk.name}`, 'success')
      onDone(); onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Remove sub-ports — ${trunk.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="trunk-subport-remove-submit">Remove sub-ports</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Sub-ports" description="The VLAN tag is cleared on each removed port.">
            <Multiselect
              selectedOptions={selected}
              onChange={({ detail }) => setSelected(detail.selectedOptions)}
              options={options}
              placeholder="Choose sub-ports"
              empty="This trunk has no sub-ports"
              keepOpen={false}
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function TrunksPage() {
  const toast = useToast()
  const [trunks, setTrunks]     = useState([])
  const [ports, setPorts]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [createOpen, setCreateOpen] = useState(false)
  const [addSub, setAddSub]     = useState(null)
  const [removeSub, setRemoveSub] = useState(null)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getTrunks(), api.getPorts()])
      .then(([t, p]) => {
        if (t.status === 'fulfilled') setTrunks(t.value?.trunks || [])
        else toast(`Could not load trunks: ${t.reason?.message}`, 'error')
        if (p.status === 'fulfilled') setPorts(p.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const t of selected) {
      try { await api.deleteTrunk(t.id) } catch (e) { toast(`${t.name}: ${e.message}`, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    load()
  }

  const portName = (id) => {
    const p = ports.find(x => x.id === id)
    return p?.name || shortId(id, 16)
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? trunks.filter(t => t.name?.toLowerCase().includes(q)) : trunks

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(trunks.length, selected.length)}
          description="Trunk ports carry several VLANs on one parent port through tagged sub-ports"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh trunks" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'add',    text: 'Add sub-ports',    disabled: !one },
                  { id: 'remove', text: 'Remove sub-ports', disabled: !one || !one.sub_ports?.length },
                  { id: 'delete', text: 'Delete trunk' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') setDeleteModal(true)
                  else if (detail.id === 'add' && one) setAddSub(one)
                  else if (detail.id === 'remove' && one) setRemoveSub(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setCreateOpen(true)} data-testid="create-trunk-btn">Create trunk</Button>
            </SpaceBetween>
          }
        >
          Trunks
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading trunks"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Trunk name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <span>{item.name}</span>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'parent', header: 'Parent port', cell: item => portName(item.port_id) },
          {
            id: 'subports',
            header: 'Sub-ports',
            cell: item => item.sub_ports?.length
              ? `${item.sub_ports.length} (VLAN ${item.sub_ports.map(s => s.segmentation_id).join(', ')})`
              : '0',
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find trunks"
            filteringAriaLabel="Filter trunks"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No trunks"
                description="Create a trunk to carry several VLANs over one port."
                action={<Button variant="primary" onClick={() => setCreateOpen(true)}>Create trunk</Button>}
              />
        }
      />

      {createOpen && (
        <CreateTrunkModal ports={ports} trunks={trunks} onDismiss={() => setCreateOpen(false)} onDone={load} />
      )}
      {addSub && (
        <AddSubportsModal trunk={addSub} ports={ports} trunks={trunks} onDismiss={() => setAddSub(null)} onDone={() => { setSelected([]); load() }} />
      )}
      {removeSub && (
        <RemoveSubportsModal trunk={removeSub} ports={ports} onDismiss={() => setRemoveSub(null)} onDone={() => { setSelected([]); load() }} />
      )}
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Trunk"
          description="A trunk can only be deleted after all of its sub-ports are removed."
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
