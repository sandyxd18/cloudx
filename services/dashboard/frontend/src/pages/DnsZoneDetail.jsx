import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import Badge from '@cloudscape-design/components/badge'
import Button from '@cloudscape-design/components/button'
import ColumnLayout from '@cloudscape-design/components/column-layout'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty } from '../components/common.jsx'

const TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'SRV', 'NS', 'PTR'].map(t => ({ value: t, label: t }))

export default function DnsZoneDetailPage() {
  const { id } = useParams()
  const toast = useToast()
  const [zone, setZone] = useState(null)
  const [records, setRecords] = useState([])
  const [secondaries, setSecondaries] = useState([])
  const [name, setName] = useState('')
  const [rtype, setRtype] = useState(TYPES[0])
  const [values, setValues] = useState('')
  const [address, setAddress] = useState('')

  const load = useCallback(async () => {
    try {
      const z = await api.getZone(id)
      setZone(z)
      setRecords((await api.getRecordsets(id)).recordsets || [])
      if (z.kind === 'user' && z.scope === 'public') setSecondaries((await api.getSecondaries(id)).secondaries || [])
    } catch (e) {
      toast(e.message, 'error')
    }
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    try { await fn(); load() } catch (e) { toast(e.message, 'error') }
  }

  if (!zone) return <PageLoading text="Loading zone" />
  const editable = zone.kind === 'user'

  return (
    <ContentLayout header={<Header variant="h1" actions={<Button iconName="refresh" onClick={load} ariaLabel="Refresh" />}>{zone.name}</Header>}>
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Details</Header>}>
          <ColumnLayout columns={4} variant="text-grid">
            <div><Header variant="h3">Scope</Header>{zone.scope}</div>
            <div><Header variant="h3">TTL</Header>{zone.ttl}</div>
            <div><Header variant="h3">Serial</Header>{zone.serial}</div>
            <div>
              <Header variant="h3">Status</Header>
              <StatusIndicator type={zone.status === 'ACTIVE' ? 'success' : 'pending'}>{zone.status}</StatusIndicator>
            </div>
          </ColumnLayout>
        </Container>

        <Table
          trackBy="id"
          header={<Header variant="h2" counter={`(${records.length})`}>Records</Header>}
          items={records}
          empty={<TableEmpty title="No records" subtitle="Add a record below." />}
          columnDefinitions={[
            { id: 'name', header: 'Name', cell: r => r.name },
            { id: 'type', header: 'Type', cell: r => r.type },
            { id: 'records', header: 'Value', cell: r => r.records.join(', ') },
            { id: 'ttl', header: 'TTL', cell: r => r.ttl || zone.ttl },
            {
              id: 'actions',
              header: '',
              cell: r => (r.managed_by
                ? <Badge color="grey">managed: {r.managed_by.split(':')[0]}</Badge>
                : editable && <Button variant="inline-link" onClick={() => act(() => api.deleteRecordset(id, r.id))}>Delete</Button>),
            },
          ]}
        />

        {editable && (
          <Container header={<Header variant="h2">Add record</Header>}>
            <SpaceBetween size="m">
              <ColumnLayout columns={2}>
                <FormField label="Name" description="Relative to the zone; @ is the zone itself">
                  <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="www" />
                </FormField>
                <FormField label="Type">
                  <Select selectedOption={rtype} onChange={({ detail }) => setRtype(detail.selectedOption)} options={TYPES} />
                </FormField>
              </ColumnLayout>
              <FormField label="Values" description="One per line. MX: '10 mail.example.com'. SRV: 'priority weight port host'.">
                <Textarea value={values} onChange={({ detail }) => setValues(detail.value)} rows={3} />
              </FormField>
              <Button
                variant="primary"
                disabled={!name || !values.trim()}
                onClick={() => act(async () => {
                  await api.createRecordset(id, { name, type: rtype.value, records: values.split('\n').map(s => s.trim()).filter(Boolean) })
                  setName('')
                  setValues('')
                })}
              >
                Add record
              </Button>
            </SpaceBetween>
          </Container>
        )}

        {editable && zone.scope === 'public' && (
          <Table
            trackBy="id"
            header={
              <Header
                variant="h2"
                description="Servers allowed to transfer (AXFR) this zone. CloudX only acts as primary."
                actions={
                  <SpaceBetween direction="horizontal" size="xs">
                    <Input value={address} onChange={({ detail }) => setAddress(detail.value)} placeholder="203.0.113.5" />
                    <Button disabled={!address} onClick={() => act(async () => { await api.addSecondary(id, { address }); setAddress('') })}>Add secondary</Button>
                  </SpaceBetween>
                }
              >
                Secondary servers
              </Header>
            }
            items={secondaries}
            empty={<TableEmpty title="No secondaries" subtitle="Zone transfers are refused until you add one." />}
            columnDefinitions={[
              { id: 'address', header: 'Address', cell: s => s.address },
              { id: 'actions', header: '', cell: s => <Button variant="inline-link" onClick={() => act(() => api.deleteSecondary(id, s.id))}>Remove</Button> },
            ]}
          />
        )}
      </SpaceBetween>
    </ContentLayout>
  )
}