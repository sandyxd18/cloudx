import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Badge from '@cloudscape-design/components/badge'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const POLICIES = [
  { value: 'anti-affinity',      label: 'Anti-affinity',      description: 'Place instances on different hosts' },
  { value: 'affinity',           label: 'Affinity',           description: 'Place instances on the same host' },
  { value: 'soft-anti-affinity', label: 'Soft anti-affinity', description: 'Prefer different hosts (best effort)' },
  { value: 'soft-affinity',      label: 'Soft affinity',      description: 'Prefer the same host (best effort)' },
]

export default function ServerGroupsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [groups, setGroups]   = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getServerGroups()
      .then(r => setGroups(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const g of selected) {
      try { await api.deleteServerGroup(g.id) } catch { /* continue */ }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} server group(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? groups.filter(g => g.name?.toLowerCase().includes(q) || g.policy?.toLowerCase().includes(q)) : groups

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(groups.length, selected.length)}
          description="Affinity and anti-affinity placement policies for instances"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh server groups" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)}>Delete</Button>
              <Button variant="primary" onClick={() => navigate('/server-groups/create')} data-testid="create-sg-btn">Create server group</Button>
            </SpaceBetween>
          }
        >
          Server groups
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading server groups"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Name',
            cell: item => (
              <Link href={`/server-groups/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/server-groups/${item.id}`) }}>
                {item.name}
              </Link>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'id', header: 'Group ID', cell: item => shortId(item.id, 16) },
          {
            id: 'policy',
            header: 'Policy',
            cell: item => <Badge color={(item.policy || '').includes('anti') ? 'blue' : 'green'}>{item.policy || 'affinity'}</Badge>,
            sortingField: 'policy',
          },
          {
            id: 'members',
            header: 'Members',
            cell: item => {
              const members = Array.isArray(item.members)
                ? item.members
                : (typeof item.members === 'string' ? item.members.split(',').filter(Boolean) : [])
              return members.length ? `${members.length} instance(s)` : '—'
            },
          },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find server groups"
            filteringAriaLabel="Filter server groups"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No server groups"
                description="Create a server group to control how instances are placed across hosts."
                action={<Button variant="primary" onClick={() => navigate('/server-groups/create')}>Create server group</Button>}
              />
        }
      />
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Server Group"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
