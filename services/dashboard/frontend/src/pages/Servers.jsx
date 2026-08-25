import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import Modal from '@cloudscape-design/components/modal'
import Pagination from '@cloudscape-design/components/pagination'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Alert from '@cloudscape-design/components/alert'
import Checkbox from '@cloudscape-design/components/checkbox'
import Spinner from '@cloudscape-design/components/spinner'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import useEventStream from '../hooks/useEventStream.js'
import { ComputeStatus, TableEmpty, TableNoMatch, timeAgo, shortId, counterText } from '../components/common.jsx'

const PAGE_SIZE = 15

function powerState(s) {
  if (s === 'ACTIVE') return 'Running'
  if (s === 'SHUTOFF') return 'Shutdown'
  if (s === 'PAUSED') return 'Paused'
  if (s === 'SUSPENDED') return 'Suspended'
  return '—'
}

function taskState(s) {
  const tasks = {
    BUILD: 'Spawning', STARTING: 'Powering on', STOPPING: 'Powering off',
    REBOOT: 'Rebooting', PAUSING: 'Pausing', RESUMING: 'Resuming',
    SUSPENDING: 'Suspending', DELETING: 'Deleting', RESCUE: 'Rescuing',
    REBUILD: 'Rebuilding', RESIZE: 'Resizing', UNRESCUING: 'Unrescuing',
    SHELVING: 'Shelving', UNSHELVING: 'Unshelving',
  }
  return tasks[s] || '—'
}

// ── Launch instance modal ────────────────────────────────────────────────────
function LaunchModal({ onDismiss, onCreate }) {
  const [form, setForm] = useState({ name: '', flavor: null, image: null, keypair: null, network: null })
  const [flavors, setFlavors]   = useState([])
  const [images, setImages]     = useState([])
  const [keypairs, setKeypairs] = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    Promise.allSettled([api.getFlavors(), api.getImages(), api.getKeypairs(), api.getNetworks()])
      .then(([f, im, k, n]) => {
        if (f.status === 'fulfilled')  setFlavors(f.value?.items || [])
        if (im.status === 'fulfilled') setImages((im.value?.items || []).filter(i => i.status === 'ACTIVE'))
        if (k.status === 'fulfilled')  setKeypairs(k.value?.items || [])
        if (n.status === 'fulfilled')  setNetworks(n.value?.items || [])
      })
  }, [])

  const submit = async () => {
    if (!form.name || !form.flavor || !form.image || !form.network) {
      setError('Name, flavor, image, and network are required.')
      return
    }
    setLoading(true); setError('')
    try {
      await onCreate({
        name: form.name,
        flavor: form.flavor.value,
        image: form.image.value,
        network: form.network.value,
        keypair: form.keypair?.value || undefined,
      })
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
      header="Launch instance"
      size="medium"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="server-create-submit">
              Launch instance
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}

          <FormField label="Instance name" description="Must be unique within the project.">
            <Input
              value={form.name}
              onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
              placeholder="my-instance"
              controlId="server-name"
            />
          </FormField>

          <FormField label="Flavor" description="Compute, memory, and root disk profile.">
            <Select
              selectedOption={form.flavor}
              onChange={({ detail }) => setForm(f => ({ ...f, flavor: detail.selectedOption }))}
              options={flavors.map(fl => ({
                value: fl.name,
                label: fl.name,
                description: `${fl.vcpus} vCPU · ${fl.ram_mb} MB RAM · ${fl.disk_gb} GB disk`,
              }))}
              placeholder="Choose a flavor"
              empty="No flavors defined"
              controlId="server-flavor"
            />
          </FormField>

          <FormField label="Image" description="Boot image (root volume is created from it).">
            <Select
              selectedOption={form.image}
              onChange={({ detail }) => setForm(f => ({ ...f, image: detail.selectedOption }))}
              options={images.map(im => ({ value: im.name, label: im.name, description: im.disk_format }))}
              placeholder="Choose an image"
              empty="No active images"
              controlId="server-image"
            />
          </FormField>

          <FormField label="Network">
            <Select
              selectedOption={form.network}
              onChange={({ detail }) => setForm(f => ({ ...f, network: detail.selectedOption }))}
              options={networks.map(n => ({ value: n.name, label: n.name }))}
              placeholder="Choose a network"
              empty="No networks"
              controlId="server-network"
            />
          </FormField>

          <FormField label="Key pair" description="Optional — injected via cloud-init for SSH access.">
            <Select
              selectedOption={form.keypair}
              onChange={({ detail }) => setForm(f => ({ ...f, keypair: detail.selectedOption }))}
              options={keypairs.map(k => ({ value: k.name, label: k.name }))}
              placeholder="Proceed without a key pair"
              empty="No key pairs"
              controlId="server-keypair"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Console log modal ────────────────────────────────────────────────────────
function LogsModal({ server, onDismiss }) {
  const [logs, setLogs] = useState('')
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const fetchLogs = useCallback(() => {
    return api.getServerLogs(server.id)
      .then(r => setLogs(r?.output || '(no output)'))
      .catch(e => setLogs('Error: ' + e.message))
      .finally(() => setLoading(false))
  }, [server.id])

  useEffect(() => { fetchLogs() }, [fetchLogs])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(fetchLogs, 2000)
    return () => clearInterval(id)
  }, [autoRefresh, fetchLogs])

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="large"
      header={`Console log — ${server.name}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" onClick={fetchLogs}>Refresh</Button>
            <Button variant="primary" onClick={onDismiss}>Close</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="s">
        <Checkbox checked={autoRefresh} onChange={({ detail }) => setAutoRefresh(detail.checked)}>
          Auto-refresh every 2 seconds
        </Checkbox>
        {loading ? <Box textAlign="center" padding="l"><Spinner /></Box> : <pre className="log-output">{logs || '(empty)'}</pre>}
      </SpaceBetween>
    </Modal>
  )
}

// ── noVNC console modal ──────────────────────────────────────────────────────
function ConsoleModal({ server, onDismiss }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getRemoteConsole(server.id)
      .then(data => {
        const consoleUrl = data?.remote_console?.url
        if (consoleUrl) setUrl(consoleUrl)
        else setError('No console URL was returned.')
      })
      .catch(e => setError(e.message))
  }, [server.id])

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="max"
      header={`Console — ${server.name}`}
      footer={<Box float="right"><Button variant="primary" onClick={onDismiss}>Close</Button></Box>}
    >
      {error ? (
        <Alert type="error" header="Console unavailable">
          {error} Ensure the instance is ACTIVE and the noVNC proxy is running.
        </Alert>
      ) : !url ? (
        <Box textAlign="center" padding="xxl"><Spinner size="large" /> Connecting to console…</Box>
      ) : (
        <iframe src={url} title={`Console — ${server.name}`} className="console-frame console-frame-tall" allow="fullscreen" />
      )}
    </Modal>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function ServersPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [page, setPage]       = useState(1)
  const [selected, setSelected] = useState([])
  const [showLaunch, setShowLaunch] = useState(false)
  const [showLogs, setShowLogs] = useState(null)
  const [showConsole, setShowConsole] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getServers()
      .then(r => setServers(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  // Live refresh on compute events pushed over the WebSocket.
  const { lastEvent } = useEventStream()
  useEffect(() => {
    if (lastEvent?.type?.startsWith('server.')) load()
  }, [lastEvent, load])

  const runAction = async (server, action) => {
    if (action === 'delete') {
      if (!confirm(`Delete instance "${server.name}"?`)) return
      try { await api.deleteServer(server.id); toast('Instance deletion started', 'success'); load() }
      catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'logs')         { setShowLogs(server); return }
    if (action === 'open-console') { setShowConsole(server); return }
    if (action === 'detail')       { navigate(`/servers/${server.id}`); return }
    if (action === 'shelve') {
      try { await api.shelveServer(server.id); toast('Shelving instance', 'success'); setTimeout(load, 2000) }
      catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'unshelve') {
      try { await api.unshelveServer(server.id); toast('Unshelving instance', 'success'); setTimeout(load, 2000) }
      catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'confirmResize') {
      try { await api.confirmResize(server.id); toast('Resize confirmed', 'success'); setTimeout(load, 1500) }
      catch (e) { toast(e.message, 'error') }
      return
    }
    if (action === 'revertResize') {
      try { await api.revertResize(server.id); toast('Resize reverted', 'success'); setTimeout(load, 1500) }
      catch (e) { toast(e.message, 'error') }
      return
    }
    try {
      await api.serverAction(server.id, action)
      toast(`Action "${action}" sent`, 'success')
      setTimeout(load, 1500)
    } catch (e) { toast(e.message, 'error') }
  }

  const bulkDelete = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} instance(s)?`)) return
    for (const s of selected) {
      try { await api.deleteServer(s.id) } catch { /* continue */ }
    }
    setSelected([])
    toast(`${selected.length} instance(s) queued for deletion`, 'success')
    load()
  }

  const bulkAction = async (action) => {
    if (!selected.length) return
    for (const s of selected) {
      try { await api.serverAction(s.id, action) } catch { /* continue */ }
    }
    toast(`Action "${action}" sent to ${selected.length} instance(s)`, 'success')
    setTimeout(load, 1500)
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? servers.filter(s =>
        s.name?.toLowerCase().includes(q) ||
        s.status?.toLowerCase().includes(q) ||
        s.ip_address?.toLowerCase().includes(q) ||
        s.flavor_name?.toLowerCase().includes(q))
    : servers

  const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const one = selected.length === 1 ? selected[0] : null
  const isActive  = one?.status === 'ACTIVE'
  const isShutoff = one?.status === 'SHUTOFF'
  const isPaused  = one?.status === 'PAUSED'
  const isShelved = one?.status === 'SHELVED_OFFLOADED'
  const isVerify  = one?.status === 'VERIFY_RESIZE' || one?.status === 'RESIZE'

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(servers.length, selected.length)}
          description="Virtual machines backed by libvirt/KVM with boot-from-volume root disks"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} data-testid="refresh-servers" ariaLabel="Refresh instances" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'start',  text: 'Start',       disabled: !selected.length },
                  { id: 'stop',   text: 'Stop',        disabled: !selected.length },
                  { id: 'reboot', text: 'Reboot',      disabled: !selected.length },
                  { id: 'delete', text: 'Delete',      disabled: !selected.length },
                ]}
                onItemClick={({ detail }) => detail.id === 'delete' ? bulkDelete() : bulkAction(detail.id)}
              >
                Instance state
              </ButtonDropdown>
              <ButtonDropdown
                disabled={!one}
                items={[
                  { id: 'detail',        text: 'View details' },
                  { id: 'open-console',  text: 'Open console', disabled: !isActive },
                  { id: 'logs',          text: 'View console log' },
                  { id: 'sep1', text: '—', disabled: true },
                  { id: 'pause',    text: 'Pause',    disabled: !isActive },
                  { id: 'resume',   text: 'Resume',   disabled: !isPaused },
                  { id: 'suspend',  text: 'Suspend',  disabled: !isActive },
                  { id: 'rescue',   text: 'Rescue',   disabled: !isActive },
                  { id: 'sep2', text: '—', disabled: true },
                  { id: 'shelve',   text: 'Shelve',   disabled: !(isActive || isShutoff) },
                  { id: 'unshelve', text: 'Unshelve', disabled: !isShelved },
                  { id: 'confirmResize', text: 'Confirm resize', disabled: !isVerify },
                  { id: 'revertResize',  text: 'Revert resize',  disabled: !isVerify },
                  { id: 'sep3', text: '—', disabled: true },
                  { id: 'lock',   text: 'Lock' },
                  { id: 'unlock', text: 'Unlock' },
                ]}
                onItemClick={({ detail }) => one && !detail.id.startsWith('sep') && runAction(one, detail.id)}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => setShowLaunch(true)} data-testid="create-server-btn">
                Launch instance
              </Button>
            </SpaceBetween>
          }
        >
          Instances
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading instances"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={pageItems}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Instance name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Link href={`/servers/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/servers/${item.id}`) }}>
                  {item.name}
                </Link>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            sortingField: 'name',
            isRowHeader: true,
          },
          { id: 'status', header: 'Instance state', cell: item => <ComputeStatus status={item.status} />, sortingField: 'status' },
          { id: 'image', header: 'Image', cell: item => item.image_name || item.image || '—' },
          { id: 'ip', header: 'Private IP', cell: item => item.ip_address || item.fixed_ip || '—' },
          { id: 'flavor', header: 'Flavor', cell: item => item.flavor_name || item.flavor || '—' },
          { id: 'keypair', header: 'Key pair', cell: item => item.keypair || '—' },
          { id: 'az', header: 'Availability zone', cell: item => item.availability_zone || 'nova' },
          { id: 'task', header: 'Task', cell: item => taskState(item.status) },
          { id: 'power', header: 'Power state', cell: item => powerState(item.status) },
          { id: 'age', header: 'Launch time', cell: item => timeAgo(item.created_at) },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find instances"
            filteringAriaLabel="Filter instances"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => { setFilter(detail.filteringText); setPage(1) }}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={page}
            pagesCount={Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}
            onChange={({ detail }) => setPage(detail.currentPageIndex)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No instances"
                description="Launch your first instance to get started."
                action={<Button variant="primary" onClick={() => setShowLaunch(true)}>Launch instance</Button>}
              />
        }
      />

      {showLaunch && (
        <LaunchModal
          onDismiss={() => setShowLaunch(false)}
          onCreate={async (payload) => {
            await api.createServer(payload)
            toast('Instance launch started', 'success')
            load()
          }}
        />
      )}
      {showLogs && <LogsModal server={showLogs} onDismiss={() => setShowLogs(null)} />}
      {showConsole && <ConsoleModal server={showConsole} onDismiss={() => setShowConsole(null)} />}
    </ContentLayout>
  )
}
