import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Link from '@cloudscape-design/components/link'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function SecurityGroupsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [sgs, setSgs]         = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

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
    setDeleting(true)
    for (const sg of selected) {
      try { await api.deleteSecurityGroup(sg.id) } catch (e) { toast(`${sg.name}: ${e.message}`, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    load()
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
                  if (detail.id === 'delete') setDeleteModal(true)
                  else if (detail.id === 'rules' && one) navigate(`/security-groups/${one.id}`)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/security-groups/create')} data-testid="create-sg-btn">
                Create security group
              </Button>
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
                <Link href={`/security-groups/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/security-groups/${item.id}`) }}>
                  {item.name}
                </Link>
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
                action={<Button variant="primary" onClick={() => navigate('/security-groups/create')}>Create security group</Button>}
              />
        }
      />
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Security Group"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
