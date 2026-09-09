import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import Link from '@cloudscape-design/components/link'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Tabs from '@cloudscape-design/components/tabs'
import Table from '@cloudscape-design/components/table'
import Badge from '@cloudscape-design/components/badge'
import Alert from '@cloudscape-design/components/alert'
import Spinner from '@cloudscape-design/components/spinner'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ComputeStatus, PageLoading, TableEmpty, fmtDate, timeAgo, shortId } from '../components/common.jsx'
import InstanceMonitoring from '../components/InstanceMonitoring.jsx'

// Attach-volume modal — picks an existing 'available' volume to hotplug.
function AttachVolumeModal({ server, onDismiss, onAttach }) {
  const [volume, setVolume] = useState(null)
  const [candidates, setCandidates] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getVolumes()
      .then(r => setCandidates((r?.items || []).filter(v => v.status === 'available')))
      .catch(e => setError(e.message))
  }, [])

  const submit = async () => {
    if (!volume) { setError('Select a volume.'); return }
    setLoading(true); setError('')
    try {
      await onAttach(volume.value)
      onDismiss()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header={`Attach volume — ${server.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} disabled={!volume} onClick={submit} data-testid="attach-vol-submit">
              Attach volume
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
        {candidates.length === 0 ? (
          <Alert type="info" header="No available volumes">
            Create a volume first — only unattached (available) volumes can be attached to an instance.
          </Alert>
        ) : (
          <Select
            selectedOption={volume}
            onChange={({ detail }) => setVolume(detail.selectedOption)}
            options={candidates.map(v => ({
              value: v.id,
              label: v.name,
              description: `${v.size_gb || v.size || 0} GiB · ${v.volume_type || v.backend || '—'}`,
            }))}
            placeholder="Search and choose a volume"
            filteringType="auto"
            controlId="attach-vol-select"
          />
        )}
      </SpaceBetween>
    </Modal>
  )
}

export default function ServerDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [server, setServer]   = useState(null)
  const [volumes, setVolumes] = useState([])
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [logs, setLogs]       = useState('')
  const [showLogs, setShowLogs] = useState(false)
  const [showAttach, setShowAttach] = useState(false)
  const [consoleUrl, setConsoleUrl] = useState(null)
  const [consoleError, setConsoleError] = useState('')
  const [activeTab, setActiveTab] = useState('details')

  const load = useCallback(async () => {
    setLoading(true)
    const [srv, vols, prjs] = await Promise.allSettled([api.getServer(id), api.getVolumes(), api.getProjects()])
    if (srv.status === 'fulfilled') setServer(srv.value)
    else toast('Failed to load instance: ' + (srv.reason?.message || 'unknown error'), 'error')
    if (vols.status === 'fulfilled') {
      setVolumes((vols.value?.items || []).filter(v => v.attached_to_server_id === id))
    }
    if (prjs.status === 'fulfilled') setProjects(prjs.value?.items || [])
    setLoading(false)
  }, [id, toast])

  useEffect(() => { load() }, [load])

  // Lazily fetch the console URL when the Console tab is opened.
  useEffect(() => {
    if (activeTab !== 'console' || consoleUrl || consoleError) return
    api.getRemoteConsole(id)
      .then(data => {
        const url = data?.remote_console?.url
        if (url) setConsoleUrl(url)
        else setConsoleError('No console URL was returned.')
      })
      .catch(e => setConsoleError(e.message))
  }, [activeTab, id, consoleUrl, consoleError])

  const doAction = async (action) => {
    try {
      if (action === 'delete') {
        if (!confirm(`Delete instance "${server.name}"?`)) return
        await api.deleteServer(id)
        toast('Instance deletion started', 'success')
        navigate('/servers')
        return
      }
      if (action === 'shelve')        { await api.shelveServer(id) }
      else if (action === 'unshelve') { await api.unshelveServer(id) }
      else if (action === 'confirmResize') { await api.confirmResize(id) }
      else if (action === 'revertResize')  { await api.revertResize(id) }
      else { await api.serverAction(id, action) }
      toast(`Action "${action}" sent`, 'success')
      setTimeout(load, 1500)
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const attachVolume = async (volumeId) => {
    await api.attachVolume(volumeId, { server: id })
    toast('Volume attach started', 'success')
    setTimeout(load, 1500)
  }

  const detachVolume = async (volume) => {
    if (!confirm(`Detach volume "${volume.name}" from "${server.name}"?`)) return
    try {
      await api.detachVolume(volume.id)
      toast('Volume detach started', 'success')
      setTimeout(load, 1500)
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const loadLogs = async () => {
    try {
      const res = await api.getServerLogs(id)
      setLogs(res?.output || 'No logs available')
      setShowLogs(true)
    } catch (e) {
      toast('Could not load logs: ' + e.message, 'error')
    }
  }

  if (loading) return <PageLoading text="Loading instance" />
  if (!server) return <Alert type="error" header="Instance not found">The requested instance does not exist or is not visible in this project.</Alert>

  const isActive  = server.status === 'ACTIVE'
  const isShutoff = server.status === 'SHUTOFF'
  const isPaused  = server.status === 'PAUSED'
  const isShelved = server.status === 'SHELVED_OFFLOADED'
  const isVerify  = server.status === 'VERIFY_RESIZE' || server.status === 'RESIZE'

  const addresses = server.addresses || {}
  const allIPs = Object.values(addresses).flat()

  const projectName = projects.find(p => p.id === server.project_id)?.name

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={server.id}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh" />
              <Button onClick={loadLogs}>Console log</Button>
              <ButtonDropdown
                items={[
                  { id: 'start',   text: 'Start',   disabled: !isShutoff },
                  { id: 'stop',    text: 'Stop',    disabled: !isActive },
                  { id: 'reboot',  text: 'Reboot',  disabled: !isActive },
                  { id: 'pause',   text: 'Pause',   disabled: !isActive },
                  { id: 'resume',  text: 'Resume',   disabled: !isPaused },
                  { id: 'suspend', text: 'Suspend', disabled: !isActive },
                  { id: 'rescue',  text: 'Rescue',  disabled: !isActive },
                  { id: 'shelve',   text: 'Shelve',   disabled: !(isActive || isShutoff) },
                  { id: 'unshelve', text: 'Unshelve', disabled: !isShelved },
                  { id: 'confirmResize', text: 'Confirm resize', disabled: !isVerify },
                  { id: 'revertResize',  text: 'Revert resize',  disabled: !isVerify },
                  { id: 'lock',   text: 'Lock' },
                  { id: 'unlock', text: 'Unlock' },
                  { id: 'delete', text: 'Delete' },
                ]}
                onItemClick={({ detail }) => doAction(detail.id)}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" disabled={!isActive} onClick={() => navigate(`/console/${id}?name=${encodeURIComponent(server.name)}`)}>
                Connect
              </Button>
            </SpaceBetween>
          }
        >
          {server.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Instance summary</Header>}>
          <KeyValuePairs
            columns={4}
            items={[
              { label: 'Instance state', value: <ComputeStatus status={server.status} /> },
              { label: 'Private IP', value: server.ip_address || allIPs[0]?.addr || '—' },
              { label: 'Flavor', value: server.flavor_name || server.flavor || '—' },
              { label: 'Launch time', value: timeAgo(server.created_at) },
            ]}
          />
        </Container>

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId)}
          tabs={[
            {
              id: 'details',
              label: 'Details',
              content: (
                <SpaceBetween size="l">
                  <Container header={<Header variant="h2">Instance details</Header>}>
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Instance ID', value: server.id },
                        { label: 'Name', value: server.name },
                        { label: 'State', value: <ComputeStatus status={server.status} /> },
                        {
                          label: 'Image',
                          value: (
                            <SpaceBetween size="xxxs" direction="horizontal">
                              {server.image_name
                                ? <Link href="/images" onFollow={(e) => { e.preventDefault(); navigate('/images') }}>{server.image_name}</Link>
                                : '—'}
                              {server.image_id
                                ? <> / <Link href={`/images`} onFollow={(e) => { e.preventDefault(); navigate('/images') }}>{shortId(server.image_id, 12)}</Link></>
                                : null}
                            </SpaceBetween>
                          ),
                        },
                        { label: 'Flavor', value: server.flavor_name || server.flavor || '—' },
                        { label: 'Locked', value: server.locked ? <Badge color="red">Locked</Badge> : 'No' },
                        { label: 'Created', value: fmtDate(server.created_at) },
                        {
                          label: 'Project',
                          value: (
                            <SpaceBetween size="xxxs" direction="horizontal">
                              {projectName
                                ? <Link href="/admin" onFollow={(e) => { e.preventDefault(); navigate('/admin') }}>{projectName}</Link>
                                : '—'}
                              {server.project_id
                                ? <> / <Link href="/admin" onFollow={(e) => { e.preventDefault(); navigate('/admin') }}>{shortId(server.project_id, 12)}</Link></>
                                : null}
                            </SpaceBetween>
                          ),
                        },
                        { label: 'Availability zone', value: server.availability_zone || 'nova' },
                        { label: 'Host', value: server.host || '—' },
                        { label: 'Hypervisor', value: server.libvirt_domain_name || '—' },
                        { label: 'Error', value: server.error_message || '—' },
                      ]}
                    />
                  </Container>

                  <Container header={<Header variant="h2">Resources</Header>}>
                    <KeyValuePairs
                      columns={4}
                      items={[
                        { label: 'vCPUs', value: server.vcpus ?? '—' },
                        { label: 'Memory', value: server.ram_mb ? `${server.ram_mb} MB` : '—' },
                        { label: 'Root disk', value: server.disk_gb ? `${server.disk_gb} GB` : '—' },
                        { label: 'Boot source', value: server.boot_source || 'volume' },
                      ]}
                    />
                  </Container>

                  <Container header={<Header variant="h2">Security and access</Header>}>
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Key pair', value: server.keypair_name || server.keypair || '—' },
                        {
                          label: 'Security groups',
                          value: server.security_groups?.length
                            ? <SpaceBetween size="xxs" direction="horizontal">{server.security_groups.map(sg => <Badge key={sg}>{sg}</Badge>)}</SpaceBetween>
                            : '—',
                        },
                        {
                          label: 'Tags',
                          value: server.tags?.length
                            ? <SpaceBetween size="xxs" direction="horizontal">{server.tags.map(t => <Badge key={t} color="green">{t}</Badge>)}</SpaceBetween>
                            : '—',
                        },
                      ]}
                    />
                  </Container>
                </SpaceBetween>
              ),
            },
            {
              id: 'monitoring',
              label: 'Monitoring',
              content: <InstanceMonitoring serverId={id} isActive={isActive} />,
            },
            {
              id: 'networking',
              label: 'Networking',
              content: (
                <Container header={<Header variant="h2">Network interfaces</Header>}>
                  <KeyValuePairs
                    columns={3}
                    items={[
                      { label: 'Private IP', value: server.ip_address || '—' },
                      { label: 'Floating IP', value: server.floating_ip || '—' },
                      { label: 'MAC address', value: server.mac_address || '—' },
                      { label: 'Port ID', value: server.port_id || '—' },
                      { label: 'Network ID', value: server.network_id || '—' },
                      { label: 'Extra ports', value: server.extra_port_ids || '—' },
                    ]}
                  />
                </Container>
              ),
            },
            {
              id: 'storage',
              label: 'Storage',
              content: (
                <Table
                  variant="container"
                  header={
                    <Header
                      variant="h2"
                      counter={`(${volumes.length})`}
                      actions={<Button variant="primary" onClick={() => setShowAttach(true)}>Attach volume</Button>}
                    >
                      Attached volumes
                    </Header>
                  }
                  items={volumes}
                  trackBy="id"
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Volume',
                      cell: v => (
                        <Link href={`/volumes/${v.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/volumes/${v.id}`) }}>
                          {v.name}
                        </Link>
                      ),
                      isRowHeader: true,
                    },
                    { id: 'size', header: 'Size', cell: v => `${v.size_gb || v.size || 0} GiB` },
                    { id: 'device', header: 'Device', cell: v => v.target_device ? `/dev/${v.target_device}` : '—' },
                    { id: 'bootable', header: 'Bootable', cell: v => (v.bootable ? 'Yes' : 'No') },
                    { id: 'backend', header: 'Backend', cell: v => v.backend || '—' },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: v => v.bootable
                        ? <Box variant="small" color="text-body-secondary">Root volume</Box>
                        : <Button variant="inline-link" onClick={() => detachVolume(v)}>Detach</Button>,
                    },
                  ]}
                  empty={<TableEmpty title="No attached volumes" description="This instance has no additional block devices." />}
                />
              ),
            },
            {
              id: 'console',
              label: 'Console',
              content: (
                <Container header={<Header variant="h2" description="Browser-based VNC session via the noVNC proxy">Instance console</Header>}>
                  {consoleError ? (
                    <Alert type="error" header="Console unavailable">
                      {consoleError} The instance must be ACTIVE and the noVNC proxy running.
                    </Alert>
                  ) : !consoleUrl ? (
                    <Box textAlign="center" padding="xxl"><Spinner size="large" /> Connecting…</Box>
                  ) : (
                    <iframe src={consoleUrl} title={`Console — ${server.name}`} className="console-frame" allow="clipboard-read; clipboard-write" />
                  )}
                </Container>
              ),
            },
          ]}
        />
      </SpaceBetween>

      {showAttach && (
        <AttachVolumeModal
          server={server}
          onDismiss={() => setShowAttach(false)}
          onAttach={attachVolume}
        />
      )}
      {showLogs && (
        <Modal
          visible
          onDismiss={() => setShowLogs(false)}
          size="large"
          header={`Console log — ${server.name}`}
          footer={<Box float="right"><Button variant="primary" onClick={() => setShowLogs(false)}>Close</Button></Box>}
        >
          <pre className="log-output">{logs}</pre>
        </Modal>
      )}
    </ContentLayout>
  )
}
