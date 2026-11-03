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
import Checkbox from '@cloudscape-design/components/checkbox'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

import DeleteModal from '../components/DeleteModal.jsx'

const DIRECTIONS = [
  { value: 'egress', label: 'Egress', description: 'Traffic leaving the instance' },
  { value: 'ingress', label: 'Ingress', description: 'Traffic entering the instance' },
]

function CreatePolicyModal({ onDismiss, onDone }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [shared, setShared] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try {
      const body = { name: name.trim(), shared }
      if (description.trim()) body.description = description.trim()
      await api.createQosPolicy(body)
      toast(`QoS policy ${name.trim()} created`, 'success')
      onDone(); onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create QoS policy"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="qos-create-submit">Create policy</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="bw-limit-10mbit" data-testid="qos-name" />
          </FormField>
          <FormField label="Description" description="Optional.">
            <Input value={description} onChange={({ detail }) => setDescription(detail.value)} />
          </FormField>
          <Checkbox checked={shared} onChange={({ detail }) => setShared(detail.checked)} description="Other projects can use this policy.">
            Shared
          </Checkbox>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

function BandwidthRuleModal({ policy, onDismiss, onDone }) {
  const toast = useToast()
  const [maxKbps, setMaxKbps] = useState('')
  const [burstKbps, setBurstKbps] = useState('')
  const [direction, setDirection] = useState(DIRECTIONS[0])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    const max = Number(maxKbps)
    if (!Number.isInteger(max) || max < 1) { setError('Maximum rate must be a whole number of kbps, 1 or more.'); return }
    const burst = burstKbps === '' ? 0 : Number(burstKbps)
    if (!Number.isInteger(burst) || burst < 0) { setError('Burst must be a whole number of kbps, 0 or more.'); return }
    setLoading(true); setError('')
    try {
      await api.createQosBwRule(policy.id, { max_kbps: max, max_burst_kbps: burst, direction: direction.value })
      toast(`Bandwidth rule added to ${policy.name}`, 'success')
      onDone(); onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Add bandwidth rule — ${policy.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="qos-rule-submit">Add rule</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Maximum rate (kbps)">
            <Input type="number" inputMode="numeric" value={maxKbps} onChange={({ detail }) => setMaxKbps(detail.value)} placeholder="10000" data-testid="qos-max-kbps" />
          </FormField>
          <FormField label="Maximum burst (kbps)" description="Optional. Leave empty to use the maximum rate.">
            <Input type="number" inputMode="numeric" value={burstKbps} onChange={({ detail }) => setBurstKbps(detail.value)} />
          </FormField>
          <FormField label="Direction">
            <Select
              selectedOption={direction}
              onChange={({ detail }) => setDirection(detail.selectedOption)}
              options={DIRECTIONS}
              controlId="qos-direction"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

const ruleText = (r) => `${r.direction}: ${r.max_kbps} kbps${r.max_burst_kbps ? `, burst ${r.max_burst_kbps} kbps` : ''}`

export default function QosPoliciesPage() {
  const toast = useToast()
  const [policies, setPolicies] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [createOpen, setCreateOpen] = useState(false)
  const [ruleFor, setRuleFor]   = useState(null)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getQosPolicies()
      .then(r => setPolicies(r?.policies || []))
      .catch(e => toast(`Could not load QoS policies: ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const p of selected) {
      try { await api.deleteQosPolicy(p.id) } catch (e) { toast(`${p.name}: ${e.message}`, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? policies.filter(p => p.name?.toLowerCase().includes(q)) : policies

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(policies.length, selected.length)}
          description="Bandwidth limits that can be applied to ports"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh QoS policies" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'rule',   text: 'Add bandwidth rule', disabled: !one },
                  { id: 'delete', text: 'Delete policy' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') setDeleteModal(true)
                  else if (detail.id === 'rule' && one) setRuleFor(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setCreateOpen(true)} data-testid="create-qos-btn">Create QoS policy</Button>
            </SpaceBetween>
          }
        >
          QoS policies
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading QoS policies"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Policy name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <span>{item.name}</span>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'description', header: 'Description', cell: item => item.description || '—' },
          { id: 'shared', header: 'Shared', cell: item => <BoolStatus value={!!item.shared} /> },
          {
            id: 'rules',
            header: 'Bandwidth rules',
            cell: item => item.rules?.length
              ? <SpaceBetween size="xxxs">{item.rules.map(r => <span key={r.id}>{ruleText(r)}</span>)}</SpaceBetween>
              : '—',
          },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find QoS policies"
            filteringAriaLabel="Filter QoS policies"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No QoS policies"
                description="Create a policy and add a bandwidth rule to limit port traffic."
                action={<Button variant="primary" onClick={() => setCreateOpen(true)}>Create QoS policy</Button>}
              />
        }
      />

      {createOpen && <CreatePolicyModal onDismiss={() => setCreateOpen(false)} onDone={load} />}
      {ruleFor && (
        <BandwidthRuleModal policy={ruleFor} onDismiss={() => setRuleFor(null)} onDone={() => { setSelected([]); load() }} />
      )}
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete QoS Policy"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
