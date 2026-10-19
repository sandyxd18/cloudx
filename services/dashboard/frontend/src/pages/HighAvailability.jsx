import { useEffect, useState, useCallback } from 'react'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty } from '../components/common.jsx'

const hostType = s => (s === 'up' ? 'success' : s === 'fenced' ? 'error' : 'pending')
const recType = s => (s === 'done' ? 'success' : s === 'failed' ? 'error' : s === 'skipped' ? 'warning' : 'in-progress')

export default function HighAvailabilityPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [hosts, setHosts] = useState([])
  const [prot, setProt] = useState([])
  const [recs, setRecs] = useState([])
  const [servers, setServers] = useState([])
  const [pick, setPick] = useState(null)
  const [selHost, setSelHost] = useState([])
  const [selProt, setSelProt] = useState([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [h, p, r, s] = await Promise.all([api.getHaHosts(), api.getHaProtected(), api.getHaRecoveries(), api.getServers()])
      setHosts(h.items || [])
      setProt(p.items || [])
      setRecs(r.items || [])
      setServers(s.servers || s.items || s || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { load() }, [load])

  const act = async fn => {
    try { await fn(); await load() } catch (e) { toast(e.message, 'error') }
  }

  if (loading && !hosts.length && !prot.length) return <PageLoading text="Loading high availability" />

  const names = Object.fromEntries(servers.map(s => [s.id, s.name]))
  const free = servers.filter(s => !prot.some(p => p.server_id === s.id))

  return (
    <ContentLayout header={
      <Header variant="h1" description="A protected server is evacuated to another host if its host stops sending heartbeats. Servers with a volume on their own host cannot be protected."
        actions={<Button iconName="refresh" onClick={load} ariaLabel="Refresh" />}>
        High availability
      </Header>}>
      <SpaceBetween size="l">
        <Table variant="container" selectionType="single" trackBy="host" selectedItems={selHost}
          onSelectionChange={({ detail }) => setSelHost(detail.selectedItems)}
          header={<Header variant="h2" actions={
            <Button disabled={!selHost.length || selHost[0].state === 'up'}
              onClick={() => act(() => api.resetHaHost(selHost[0].host))}>Reset host</Button>}>Hosts</Header>}
          columnDefinitions={[
            { id: 'host', header: 'Host', cell: h => h.host },
            { id: 'state', header: 'State', cell: h => <StatusIndicator type={hostType(h.state)}>{h.state}</StatusIndicator> },
            { id: 'seen', header: 'Last heartbeat', cell: h => h.last_seen || '-' },
            { id: 'fails', header: 'Fence failures', cell: h => h.fails },
          ]}
          items={hosts} empty={<TableEmpty title="No hosts" description="Compute workers report in every 10 seconds." />} />
        <Table variant="container" selectionType="multi" trackBy="server_id" selectedItems={selProt}
          onSelectionChange={({ detail }) => setSelProt(detail.selectedItems)}
          header={<Header variant="h2" actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Select selectedOption={pick} onChange={({ detail }) => setPick(detail.selectedOption)} placeholder="Choose a server"
                options={free.map(s => ({ value: s.id, label: s.name }))} />
              <Button disabled={!pick} onClick={() => act(async () => { await api.protectServer(pick.value); setPick(null) })}>Protect</Button>
              <Button disabled={!selProt.length} onClick={() => act(async () => { for (const p of selProt) await api.unprotectServer(p.server_id); setSelProt([]) })}>Unprotect</Button>
            </SpaceBetween>}>Protected servers</Header>}
          columnDefinitions={[
            { id: 'name', header: 'Server', cell: p => names[p.server_id] || p.server_id },
            { id: 'since', header: 'Protected since', cell: p => p.created_at || '-' },
          ]}
          items={prot} empty={<TableEmpty title="No protected servers" description="Pick a server above and press Protect." />} />
        <Table variant="container"
          header={<Header variant="h2">Recoveries</Header>}
          columnDefinitions={[
            { id: 'host', header: 'Host', cell: r => r.host },
            { id: 'server', header: 'Server', cell: r => (r.server_id ? names[r.server_id] || r.server_id : '-') },
            { id: 'status', header: 'Status', cell: r => <StatusIndicator type={recType(r.status)}>{r.status}</StatusIndicator> },
            { id: 'msg', header: 'Message', cell: r => r.message || '-' },
            { id: 'at', header: 'Started', cell: r => r.started_at || '-' },
          ]}
          items={recs} empty={<TableEmpty title="No recoveries" description="Nothing has failed over." />} />
      </SpaceBetween>
    </ContentLayout>
  )
}
