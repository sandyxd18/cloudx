import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function BgpPeersPage() {
  const toast = useToast()
  const [peers, setPeers] = useState([])
  const [routers, setRouters] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState([])

  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState({
    name: '',
    peer_ip: '',
    remote_as: '65001',
    local_as: '65000',
    router: null,
  })

  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([
      api.getBgpPeers(),
      api.getRouters(),
    ]).then(([p, r]) => {
      if (p.status === 'fulfilled') setPeers(p.value?.items || (Array.isArray(p.value) ? p.value : []))
      if (r.status === 'fulfilled') setRouters(r.value?.items || (Array.isArray(r.value) ? r.value : []))
    }).catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!form.name.trim() || !form.peer_ip.trim() || !form.router) {
      setCreateError('Name, Peer IP, and Router are required.')
      return
    }
    setCreating(true); setCreateError('')
    try {
      await api.createBgpPeer({
        name: form.name.trim(),
        peer_ip: form.peer_ip.trim(),
        remote_as: parseInt(form.remote_as, 10) || 65001,
        local_as: parseInt(form.local_as, 10) || 65000,
        router_id: form.router.value,
      })
      toast('BGP Peer created successfully', 'success')
      setShowCreate(false)
      setForm({ name: '', peer_ip: '', remote_as: '65001', local_as: '65000', router: null })
      load()
    } catch (e) {
      setCreateError(e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleSyncConfig = async (peer) => {
    try {
      await api.syncBgpPeerConfig(peer.id)
      toast(`FRRouting BGP configuration synced for ${peer.name}`, 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const handleDelete = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const p of selected) {
      try { await api.deleteBgpPeer(p.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} BGP Peer(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? peers.filter(p => p.name?.toLowerCase().includes(q) || p.peer_ip?.toLowerCase().includes(q))
    : peers

  const routerMap = Object.fromEntries(routers.map(r => [r.id, r.name]))

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(peers.length, selected.length)}
          description="FRR BGP dynamic routing peers for hybrid WAN and datacenter interconnects"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh BGP peers" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)}>
                Delete
              </Button>
              <Button variant="primary" onClick={() => setShowCreate(true)}>
                Create BGP peer
              </Button>
            </SpaceBetween>
          }
        >
          BGP Peers
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="id"
        columnDefinitions={[
          { id: 'name', header: 'Peer Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
          { id: 'peer_ip', header: 'Neighbor IP', cell: item => item.peer_ip },
          { id: 'remote_as', header: 'Remote AS', cell: item => item.remote_as },
          { id: 'local_as', header: 'Local AS', cell: item => item.local_as },
          { id: 'router', header: 'Router', cell: item => routerMap[item.router_id] || item.router_id },
          {
            id: 'actions',
            header: 'Actions',
            cell: item => (
              <Button variant="inline-link" onClick={() => handleSyncConfig(item)}>
                Sync FRR Config
              </Button>
            ),
          },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading BGP peers"
        filter={
          <TextFilter
            filteringText={filter}
            onChange={({ detail }) => setFilter(detail.filteringText)}
            filteringPlaceholder="Filter BGP peers"
          />
        }
        empty={
          filter ? (
            <TableNoMatch onClear={() => setFilter('')} />
          ) : (
            <TableEmpty
              title="No BGP peers"
              subtitle="Configure BGP dynamic routing peers for hybrid and upstream interconnects."
            />
          )
        }
      />

      {showCreate && (
        <Modal
          visible
          onDismiss={() => setShowCreate(false)}
          header="Create BGP Peer"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreate(false)}>Cancel</Button>
                <Button variant="primary" loading={creating} onClick={handleCreate}>
                  Create peer
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {createError && <Box color="text-status-error">{createError}</Box>}
            <FormField label="Peer Name">
              <Input
                value={form.name}
                onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
                placeholder="upstream-router-bgp"
                autoFocus
              />
            </FormField>
            <FormField label="Neighbor Peer IP">
              <Input
                value={form.peer_ip}
                onChange={({ detail }) => setForm(f => ({ ...f, peer_ip: detail.value }))}
                placeholder="192.168.100.1"
              />
            </FormField>
            <SpaceBetween direction="horizontal" size="m">
              <FormField label="Remote AS">
                <Input
                  value={form.remote_as}
                  onChange={({ detail }) => setForm(f => ({ ...f, remote_as: detail.value }))}
                  placeholder="65001"
                />
              </FormField>
              <FormField label="Local AS">
                <Input
                  value={form.local_as}
                  onChange={({ detail }) => setForm(f => ({ ...f, local_as: detail.value }))}
                  placeholder="65000"
                />
              </FormField>
            </SpaceBetween>
            <FormField label="Router">
              <Select
                selectedOption={form.router}
                onChange={({ detail }) => setForm(f => ({ ...f, router: detail.selectedOption }))}
                options={routers.map(r => ({ value: r.id, label: r.name }))}
                placeholder="Select router"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete BGP Peer"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
