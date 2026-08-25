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
import Badge from '@cloudscape-design/components/badge'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

const DIRECTIONS = [
  { value: 'ingress', label: 'Inbound' },
  { value: 'egress',  label: 'Outbound' },
]

const PROTOCOLS = [
  { value: 'tcp',  label: 'TCP' },
  { value: 'udp',  label: 'UDP' },
  { value: 'icmp', label: 'ICMP' },
  { value: '',     label: 'All traffic' },
]

function CreateModal({ onDismiss, onCreate }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try { await onCreate({ name, description: desc }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create security group"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="sg-create-submit">Create security group</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="web-servers" controlId="sg-name" />
          </FormField>
          <FormField label="Description">
            <Input value={desc} onChange={({ detail }) => setDesc(detail.value)} placeholder="Allows HTTP and SSH" controlId="sg-desc" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

function RulesModal({ sg, onDismiss, onAddRule, onDeleteRule }) {
  const [form, setForm] = useState({
    direction: DIRECTIONS[0],
    protocol: PROTOCOLS[0],
    port_min: '',
    port_max: '',
    remote_ip_prefix: '0.0.0.0/0',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setLoading(true); setError('')
    try {
      await onAddRule({
        direction: form.direction.value,
        protocol: form.protocol.value || 'any',
        port_range_min: form.port_min ? Number(form.port_min) : null,
        port_range_max: form.port_max ? Number(form.port_max) : null,
        remote_ip_prefix: form.remote_ip_prefix || null,
      })
      setForm(f => ({ ...f, port_min: '', port_max: '' }))
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="large"
      header={`Security group rules — ${sg.name}`}
      footer={<Box float="right"><Button variant="primary" onClick={onDismiss}>Close</Button></Box>}
    >
      <SpaceBetween size="l">
        <Table
          variant="embedded"
          header={<Header variant="h3" counter={`(${(sg.rules || []).length})`}>Existing rules</Header>}
          items={sg.rules || []}
          trackBy="id"
          columnDefinitions={[
            {
              id: 'direction',
              header: 'Type',
              cell: r => <Badge color={r.direction === 'ingress' ? 'green' : 'blue'}>{r.direction === 'ingress' ? 'Inbound' : 'Outbound'}</Badge>,
              isRowHeader: true,
            },
            { id: 'protocol', header: 'Protocol', cell: r => r.protocol || 'All' },
            { id: 'ports', header: 'Port range', cell: r => (r.port_range_min != null ? `${r.port_range_min} – ${r.port_range_max}` : 'All') },
            { id: 'ethertype', header: 'Ether type', cell: r => r.ethertype || 'IPv4' },
            { id: 'source', header: 'Source / destination', cell: r => r.remote_ip_prefix || r.remote_group_id || 'Any' },
            {
              id: 'actions',
              header: 'Actions',
              cell: r => <Button variant="inline-link" onClick={() => onDeleteRule(r.id)}>Delete</Button>,
            },
          ]}
          empty={<TableEmpty title="No rules" description="This security group denies all traffic until a rule is added." />}
        />

        <Form
          header={<Header variant="h3">Add rule</Header>}
          actions={<Button variant="primary" loading={loading} onClick={submit} data-testid="rule-create-submit">Add rule</Button>}
        >
          <SpaceBetween size="m">
            {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
            <FormField label="Type">
              <Select selectedOption={form.direction} onChange={({ detail }) => setForm(f => ({ ...f, direction: detail.selectedOption }))} options={DIRECTIONS} controlId="rule-direction" />
            </FormField>
            <FormField label="Protocol">
              <Select selectedOption={form.protocol} onChange={({ detail }) => setForm(f => ({ ...f, protocol: detail.selectedOption }))} options={PROTOCOLS} controlId="rule-protocol" />
            </FormField>
            <FormField label="Port range" description="Leave empty to allow all ports.">
              <SpaceBetween direction="horizontal" size="xs">
                <Input value={form.port_min} onChange={({ detail }) => setForm(f => ({ ...f, port_min: detail.value }))} type="number" placeholder="From" controlId="rule-port-min" />
                <Input value={form.port_max} onChange={({ detail }) => setForm(f => ({ ...f, port_max: detail.value }))} type="number" placeholder="To" controlId="rule-port-max" />
              </SpaceBetween>
            </FormField>
            <FormField label="Source (CIDR)">
              <Input value={form.remote_ip_prefix} onChange={({ detail }) => setForm(f => ({ ...f, remote_ip_prefix: detail.value }))} placeholder="0.0.0.0/0" controlId="rule-cidr" />
            </FormField>
          </SpaceBetween>
        </Form>
      </SpaceBetween>
    </Modal>
  )
}

export default function SecurityGroupsPage() {
  const toast = useToast()
  const [sgs, setSgs]         = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [selected, setSelected] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [rulesFor, setRulesFor] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getSecurityGroups()
      .then(async r => {
        const groups = r?.items || []
        const withRules = await Promise.all(groups.map(async sg => {
          try {
            const detail = await api.getSecurityGroup(sg.id)
            return { ...sg, rules: detail?.security_group_rules || detail?.rules || [] }
          } catch { return { ...sg, rules: [] } }
        }))
        setSgs(withRules)
      })
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} security group(s)?`)) return
    for (const sg of selected) {
      try { await api.deleteSecurityGroup(sg.id) } catch (e) { toast(`${sg.name}: ${e.message}`, 'error') }
    }
    setSelected([])
    load()
  }

  const refreshRules = async (sgId) => {
    const updated = await api.getSecurityGroup(sgId)
    const rules = updated?.security_group_rules || updated?.rules || []
    setRulesFor(prev => (prev ? { ...prev, rules } : prev))
    setSgs(list => list.map(g => (g.id === sgId ? { ...g, rules } : g)))
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? sgs.filter(sg => sg.name?.toLowerCase().includes(q) || sg.description?.toLowerCase().includes(q)) : sgs

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(sgs.length, selected.length)}
          description="Stateful packet filters enforced as OVN ACLs on instance ports"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh security groups" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'rules',  text: 'Manage rules', disabled: !one },
                  { id: 'delete', text: 'Delete security group' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') deleteSelected()
                  else if (detail.id === 'rules' && one) setRulesFor(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowCreate(true)} data-testid="create-sg-btn">Create security group</Button>
            </SpaceBetween>
          }
        >
          Security groups
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading security groups"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Security group name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Box fontWeight="bold">{item.name}</Box>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'description', header: 'Description', cell: item => item.description || '—' },
          {
            id: 'inbound',
            header: 'Inbound rules',
            cell: item => (item.rules || []).filter(r => r.direction === 'ingress').length,
          },
          {
            id: 'outbound',
            header: 'Outbound rules',
            cell: item => (item.rules || []).filter(r => r.direction === 'egress').length,
          },
          {
            id: 'manage',
            header: 'Rules',
            cell: item => <Button variant="inline-link" onClick={() => setRulesFor(item)}>Manage rules</Button>,
          },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find security groups"
            filteringAriaLabel="Filter security groups"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No security groups"
                description="Create a security group to control traffic to and from your instances."
                action={<Button variant="primary" onClick={() => setShowCreate(true)}>Create security group</Button>}
              />
        }
      />

      {showCreate && (
        <CreateModal
          onDismiss={() => setShowCreate(false)}
          onCreate={async (payload) => { await api.createSecurityGroup(payload); toast('Security group created', 'success'); load() }}
        />
      )}
      {rulesFor && (
        <RulesModal
          sg={rulesFor}
          onDismiss={() => setRulesFor(null)}
          onAddRule={async (payload) => {
            await api.createSecurityGroupRule(rulesFor.id, payload)
            toast('Rule added', 'success')
            await refreshRules(rulesFor.id)
          }}
          onDeleteRule={async (ruleId) => {
            await api.deleteSecurityGroupRule(ruleId)
            toast('Rule deleted', 'success')
            await refreshRules(rulesFor.id)
          }}
        />
      )}
    </ContentLayout>
  )
}
