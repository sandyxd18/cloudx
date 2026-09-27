import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty } from '../components/common.jsx'

const PROTOCOLS = [
  { value: 'tcp',  label: 'TCP' },
  { value: 'udp',  label: 'UDP' },
  { value: 'icmp', label: 'ICMP' },
  { value: 'any',  label: 'All traffic' },
]

/**
 * Parses a single "port range" input into {port_range_min, port_range_max}.
 *   "22"        -> {min: 22,   max: 22}
 *   "8000-9000" -> {min: 8000, max: 9000}
 *   "" / "ALL"  -> {min: null, max: null}   (all ports)
 */
function parsePortRange(value) {
  const trimmed = (value || '').trim()
  if (!trimmed || trimmed.toUpperCase() === 'ALL') return { min: null, max: null }
  const m = trimmed.match(/^(\d+)\s*-\s*(\d+)$/)
  if (m) return { min: Number(m[1]), max: Number(m[2]) }
  const n = Number(trimmed)
  if (!Number.isFinite(n)) return null
  return { min: n, max: n }
}

function formatPortRange(rule) {
  if (rule.port_range_min == null) return 'All'
  if (rule.port_range_min === rule.port_range_max) return String(rule.port_range_min)
  return `${rule.port_range_min} – ${rule.port_range_max}`
}

// ── One direction's rule table + "add rule" form (rendered inside a Tab) ────
function RuleTab({ direction, rules, onAddRule, onDeleteRule }) {
  const [protocol, setProtocol] = useState(PROTOCOLS[0])
  const [portRange, setPortRange] = useState('')
  const [cidr, setCidr] = useState('0.0.0.0/0')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const addRule = async () => {
    const parsed = parsePortRange(portRange)
    if (parsed === null) { setError('Port range must be a number (e.g. 22) or a range (e.g. 8000-9000).'); return }
    setLoading(true); setError('')
    try {
      await onAddRule({
        direction,
        protocol: protocol.value,
        port_range_min: parsed.min,
        port_range_max: parsed.max,
        remote_ip_prefix: cidr || null,
      })
      setPortRange('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <SpaceBetween size="l">
      <Table
        variant="container"
        header={<Header variant="h2" counter={`(${rules.length})`}>{direction === 'ingress' ? 'Inbound rules' : 'Outbound rules'}</Header>}
        items={rules}
        trackBy="id"
        columnDefinitions={[
          { id: 'protocol', header: 'Protocol', cell: r => (r.protocol === 'any' ? 'All' : String(r.protocol || 'All').toUpperCase()), isRowHeader: true },
          { id: 'ports', header: 'Port range', cell: r => formatPortRange(r) },
          { id: 'ethertype', header: 'Ether type', cell: r => r.ethertype || 'IPv4' },
          {
            id: 'source',
            header: direction === 'ingress' ? 'Source' : 'Destination',
            cell: r => r.remote_ip_prefix || r.remote_group_id || 'Anywhere',
          },
          {
            id: 'actions',
            header: 'Actions',
            cell: r => <Button variant="inline-link" onClick={() => onDeleteRule(r.id)}>Delete</Button>,
          },
        ]}
        empty={<TableEmpty title={`No ${direction === 'ingress' ? 'inbound' : 'outbound'} rules`} description="This security group denies all traffic in this direction until a rule is added." />}
      />

      <Container header={<Header variant="h3">Add rule</Header>}>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <SpaceBetween direction="horizontal" size="m">
            <FormField label="Protocol">
              <Select selectedOption={protocol} onChange={({ detail }) => setProtocol(detail.selectedOption)} options={PROTOCOLS} controlId={`rule-protocol-${direction}`} />
            </FormField>
            <FormField label="Port range" description="e.g. 22, or 8000-9000, or leave blank for all.">
              <Input value={portRange} onChange={({ detail }) => setPortRange(detail.value)} placeholder="22" controlId={`rule-ports-${direction}`} />
            </FormField>
            <FormField label={direction === 'ingress' ? 'Source' : 'Destination'} description="CIDR block.">
              <Input value={cidr} onChange={({ detail }) => setCidr(detail.value)} placeholder="0.0.0.0/0" controlId={`rule-cidr-${direction}`} />
            </FormField>
          </SpaceBetween>
          <Box float="right">
            <Button variant="primary" loading={loading} onClick={addRule} data-testid={`rule-create-submit-${direction}`}>Add rule</Button>
          </Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  )
}

export default function SecurityGroupDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [sg, setSg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [presetLoading, setPresetLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const detail = await api.getSecurityGroup(id)
      setSg({ ...detail, rules: detail?.security_group_rules || detail?.rules || [] })
    } catch (e) {
      toast(e.message, 'error')
      setSg(null)
    } finally {
      setLoading(false)
    }
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const handleApplyPreset = async (presetKey) => {
    setPresetLoading(true)
    try {
      await api.applySecurityGroupPreset(id, presetKey)
      toast(`Applied preset '${presetKey}' successfully`, 'success')
      await load()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setPresetLoading(false)
    }
  }

  if (loading) return <PageLoading text="Loading security group" />
  if (!sg) return <Alert type="error" header="Security group not found">The requested security group does not exist or is not visible in this project.</Alert>

  const inbound  = sg.rules.filter(r => r.direction === 'ingress')
  const outbound = sg.rules.filter(r => r.direction === 'egress')

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={sg.id}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button onClick={() => navigate('/security-groups')}>Back to security groups</Button>
            </SpaceBetween>
          }
        >
          {sg.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2" actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Box fontSize="body-s" fontWeight="bold">Apply Preset:</Box>
            <Button size="small" loading={presetLoading} onClick={() => handleApplyPreset('ssh')}>SSH (22)</Button>
            <Button size="small" loading={presetLoading} onClick={() => handleApplyPreset('web')}>Web (80/443)</Button>
            <Button size="small" loading={presetLoading} onClick={() => handleApplyPreset('db')}>DB (3306/5432)</Button>
            <Button size="small" loading={presetLoading} onClick={() => handleApplyPreset('ping')}>Ping (ICMP)</Button>
            <Button size="small" loading={presetLoading} onClick={() => handleApplyPreset('all-traffic')}>All Traffic</Button>
          </SpaceBetween>
        }>Details</Header>}>
          <KeyValuePairs
            columns={3}
            items={[
              { label: 'Security group ID', value: sg.id },
              { label: 'Name', value: sg.name },
              { label: 'Description', value: sg.description || '—' },
              { label: 'Inbound rules', value: inbound.length },
              { label: 'Outbound rules', value: outbound.length },
              { label: 'Project', value: sg.project_id || '—' },
            ]}
          />
        </Container>

        <Tabs
          tabs={[
            {
              id: 'inbound',
              label: `Inbound rules (${inbound.length})`,
              content: (
                <RuleTab
                  direction="ingress"
                  rules={inbound}
                  onAddRule={async (payload) => {
                    await api.createSecurityGroupRule(sg.id, payload)
                    toast('Inbound rule added', 'success')
                    load()
                  }}
                  onDeleteRule={async (ruleId) => {
                    await api.deleteSecurityGroupRule(ruleId)
                    toast('Rule deleted', 'success')
                    load()
                  }}
                />
              ),
            },
            {
              id: 'outbound',
              label: `Outbound rules (${outbound.length})`,
              content: (
                <RuleTab
                  direction="egress"
                  rules={outbound}
                  onAddRule={async (payload) => {
                    await api.createSecurityGroupRule(sg.id, payload)
                    toast('Outbound rule added', 'success')
                    load()
                  }}
                  onDeleteRule={async (ruleId) => {
                    await api.deleteSecurityGroupRule(ruleId)
                    toast('Rule deleted', 'success')
                    load()
                  }}
                />
              ),
            },
          ]}
        />
      </SpaceBetween>
    </ContentLayout>
  )
}
