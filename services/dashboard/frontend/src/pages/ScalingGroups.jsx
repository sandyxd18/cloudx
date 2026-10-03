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

export default function ScalingGroupsPage() {
  const toast = useToast()
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState([])

  // Modal create
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState({
    name: '',
    description: '',
    image: null,
    flavor: null,
    network: null,
    min_instances: '1',
    max_instances: '5',
    target_cpu_percent: '70',
    cooldown_seconds: '300',
  })

  // Modal view details (instances & history)
  const [viewDetails, setViewDetails] = useState(null)
  const [instances, setInstances] = useState([])
  const [history, setHistory] = useState([])
  const [loadingDetails, setLoadingDetails] = useState(false)

  // Delete modal
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Aux data
  const [images, setImages] = useState([])
  const [flavors, setFlavors] = useState([])
  const [networks, setNetworks] = useState([])

  const load = useCallback(() => {
    setLoading(true)
    api.getScalingGroups()
      .then(r => setGroups(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  const loadAux = useCallback(() => {
    Promise.allSettled([
      api.getImages(),
      api.getFlavors(),
      api.getNetworks(),
    ]).then(([im, fl, nw]) => {
      if (im.status === 'fulfilled') setImages(im.value?.items || [])
      if (fl.status === 'fulfilled') setFlavors(fl.value?.items || [])
      if (nw.status === 'fulfilled') setNetworks((nw.value?.items || []).filter(n => !n.external))
    })
  }, [])

  useEffect(() => {
    load()
    loadAux()
  }, [load, loadAux])

  const handleCreate = async () => {
    if (!form.name.trim() || !form.image || !form.flavor || !form.network) {
      setCreateError('Name, OS Image, Flavor, and Network are required.')
      return
    }
    setCreating(true); setCreateError('')
    try {
      await api.createScalingGroup({
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        image_id: form.image.value,
        flavor_id: form.flavor.value,
        network_id: form.network.value,
        min_instances: parseInt(form.min_instances, 10) || 1,
        max_instances: parseInt(form.max_instances, 10) || 5,
        target_cpu_percent: parseInt(form.target_cpu_percent, 10) || 70,
        cooldown_seconds: parseInt(form.cooldown_seconds, 10) || 300,
        scale_in_cooldown_seconds: 600,
      })
      toast('Auto-scaling group created successfully', 'success')
      setShowCreate(false)
      setForm({
        name: '',
        description: '',
        image: null,
        flavor: null,
        network: null,
        min_instances: '1',
        max_instances: '5',
        target_cpu_percent: '70',
        cooldown_seconds: '300',
      })
      load()
    } catch (e) {
      setCreateError(e.message)
    } finally {
      setCreating(false)
    }
  }

  const handleScaleOut = async (group) => {
    try {
      await api.scaleOutGroup(group.id)
      toast(`Manual scale-out triggered for ${group.name}`, 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const handleScaleIn = async (group) => {
    try {
      await api.scaleInGroup(group.id)
      toast(`Manual scale-in triggered for ${group.name}`, 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const handleOpenDetails = async (group) => {
    setViewDetails(group)
    setLoadingDetails(true)
    try {
      const [instRes, histRes] = await Promise.allSettled([
        api.getScalingGroupInstances(group.id),
        api.getScalingGroupHistory(group.id),
      ])
      if (instRes.status === 'fulfilled') {
        setInstances(instRes.value?.items || (Array.isArray(instRes.value) ? instRes.value : []))
      }
      if (histRes.status === 'fulfilled') {
        setHistory(histRes.value?.items || (Array.isArray(histRes.value) ? histRes.value : []))
      }
    } catch (e) {
      toast(`Error loading group details: ${e.message}`, 'error')
    } finally {
      setLoadingDetails(false)
    }
  }

  const handleDelete = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const g of selected) {
      try { await api.deleteScalingGroup(g.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} scaling group(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? groups.filter(g => g.name?.toLowerCase().includes(q) || g.status?.toLowerCase().includes(q))
    : groups

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(groups.length, selected.length)}
          description="Automated compute elasticity, min/max instance policies, and target metric scaling"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh scaling groups" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)}>
                Delete
              </Button>
              <Button variant="primary" onClick={() => setShowCreate(true)}>
                Create scaling group
              </Button>
            </SpaceBetween>
          }
        >
          Auto Scaling Groups
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
          {
            id: 'name',
            header: 'Group Name',
            cell: item => (
              <Button variant="inline-link" onClick={() => handleOpenDetails(item)}>
                {item.name}
              </Button>
            ),
            sortingField: 'name',
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status} /> },
          { id: 'min_max', header: 'Capacity (Min / Max)', cell: item => `${item.min_instances} / ${item.max_instances}` },
          { id: 'target_cpu', header: 'Target CPU', cell: item => `${item.target_cpu_percent}%` },
          { id: 'cooldown', header: 'Cooldown', cell: item => `${item.cooldown_seconds}s` },
          {
            id: 'actions',
            header: 'Manual Scale',
            cell: item => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" iconName="add-plus" onClick={() => handleScaleOut(item)}>
                  Scale Out
                </Button>
                <Button variant="inline-link" iconName="remove" onClick={() => handleScaleIn(item)}>
                  Scale In
                </Button>
              </SpaceBetween>
            ),
          },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading scaling groups"
        filter={
          <TextFilter
            filteringText={filter}
            onChange={({ detail }) => setFilter(detail.filteringText)}
            filteringPlaceholder="Filter scaling groups"
          />
        }
        empty={
          filter ? (
            <TableNoMatch onClear={() => setFilter('')} />
          ) : (
            <TableEmpty
              title="No auto-scaling groups"
              subtitle="Create an auto-scaling group to manage cluster elasticity automatically."
            />
          )
        }
      />

      {/* Modal: Create Scaling Group */}
      {showCreate && (
        <Modal
          visible
          onDismiss={() => setShowCreate(false)}
          header="Create Auto Scaling Group"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreate(false)}>Cancel</Button>
                <Button variant="primary" loading={creating} onClick={handleCreate}>
                  Create group
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {createError && <Box color="text-status-error">{createError}</Box>}
            <FormField label="Group Name">
              <Input
                value={form.name}
                onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))}
                placeholder="web-server-asg"
                autoFocus
              />
            </FormField>
            <FormField label="Description (Optional)">
              <Input
                value={form.description}
                onChange={({ detail }) => setForm(f => ({ ...f, description: detail.value }))}
                placeholder="Production web tier auto-scaling"
              />
            </FormField>
            <FormField label="Base Node Image">
              <Select
                selectedOption={form.image}
                onChange={({ detail }) => setForm(f => ({ ...f, image: detail.selectedOption }))}
                options={images.map(im => ({ value: im.id, label: im.name }))}
                placeholder="Select VM image"
              />
            </FormField>
            <FormField label="Instance Flavor">
              <Select
                selectedOption={form.flavor}
                onChange={({ detail }) => setForm(f => ({ ...f, flavor: detail.selectedOption }))}
                options={flavors.map(fl => ({ value: fl.name, label: fl.name, description: `${fl.vcpus} vCPU · ${fl.ram_mb} MB RAM` }))}
                placeholder="Select instance flavor"
              />
            </FormField>
            <FormField label="Network">
              <Select
                selectedOption={form.network}
                onChange={({ detail }) => setForm(f => ({ ...f, network: detail.selectedOption }))}
                options={networks.map(nw => ({ value: nw.name, label: nw.name }))}
                placeholder="Select network"
              />
            </FormField>
            <SpaceBetween direction="horizontal" size="m">
              <FormField label="Min Instances">
                <Input
                  type="number"
                  value={form.min_instances}
                  onChange={({ detail }) => setForm(f => ({ ...f, min_instances: detail.value }))}
                />
              </FormField>
              <FormField label="Max Instances">
                <Input
                  type="number"
                  value={form.max_instances}
                  onChange={({ detail }) => setForm(f => ({ ...f, max_instances: detail.value }))}
                />
              </FormField>
            </SpaceBetween>
            <SpaceBetween direction="horizontal" size="m">
              <FormField label="Target CPU (%)">
                <Input
                  type="number"
                  value={form.target_cpu_percent}
                  onChange={({ detail }) => setForm(f => ({ ...f, target_cpu_percent: detail.value }))}
                />
              </FormField>
              <FormField label="Cooldown (seconds)">
                <Input
                  type="number"
                  value={form.cooldown_seconds}
                  onChange={({ detail }) => setForm(f => ({ ...f, cooldown_seconds: detail.value }))}
                />
              </FormField>
            </SpaceBetween>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: View Details (Instances & History) */}
      {viewDetails && (
        <Modal
          visible
          size="large"
          onDismiss={() => setViewDetails(null)}
          header={`Scaling Group: ${viewDetails.name}`}
          footer={
            <Box float="right">
              <Button onClick={() => setViewDetails(null)}>Close</Button>
            </Box>
          }
        >
          <SpaceBetween size="l">
            <Header variant="h3">Current Instances ({instances.length})</Header>
            <Table
              columnDefinitions={[
                { id: 'name', header: 'Server Name', cell: item => item.server_name || item.server_id },
                { id: 'ip', header: 'IP Address', cell: item => item.ip_address || '—' },
                { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
              ]}
              items={instances}
              loading={loadingDetails}
              empty={<Box textAlign="center" color="text-status-inactive">No active instances in this group</Box>}
            />

            <Header variant="h3">Scaling History</Header>
            <Table
              columnDefinitions={[
                { id: 'time', header: 'Timestamp', cell: item => item.created_at || '—' },
                { id: 'action', header: 'Action', cell: item => item.action },
                { id: 'delta', header: 'Delta', cell: item => (item.delta > 0 ? `+${item.delta}` : item.delta) },
                { id: 'reason', header: 'Reason', cell: item => item.reason || 'Threshold reached' },
              ]}
              items={history}
              loading={loadingDetails}
              empty={<Box textAlign="center" color="text-status-inactive">No scaling operations recorded yet</Box>}
            />
          </SpaceBetween>
        </Modal>
      )}

      {/* Delete Modal */}
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Scaling Group"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
