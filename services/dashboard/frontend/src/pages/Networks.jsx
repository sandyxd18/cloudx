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
import { ResourceStatus, BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

export default function NetworksPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [networks, setNetworks] = useState([])
  const [subnets, setSubnets]   = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getNetworks(), api.getSubnets()])
      .then(([n, s]) => {
        if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
        if (s.status === 'fulfilled') setSubnets(s.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const subnetsFor = (netId) => subnets.filter(s => s.network_id === netId)

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} network(s)?`)) return
    for (const n of selected) {
      try { await api.deleteNetwork(n.id) } catch (e) { toast(`${n.name}: ${e.message}`, 'error') }
    }
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? networks.filter(n => n.name?.toLowerCase().includes(q)) : networks

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(networks.length, selected.length)}
          description={`Virtual networks backed by OVN — ${subnets.length} subnet(s) defined`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh networks" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'detail', text: 'View details', disabled: selected.length !== 1 },
                  { id: 'delete', text: 'Delete network' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'delete') deleteSelected()
                  else if (detail.id === 'detail' && selected[0]) navigate(`/networks/${selected[0].id}`)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/networks/create')} data-testid="create-network-btn">Create network</Button>
            </SpaceBetween>
          }
        >
          Networks
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading networks"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Network name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Link href={`/networks/${item.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/networks/${item.id}`) }}>
                  {item.name}
                </Link>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          {
            id: 'subnets',
            header: 'Associated subnets',
            cell: item => {
              const list = subnetsFor(item.id)
              if (!list.length) return '—'
              return (
                <SpaceBetween size="xxxs">
                  {list.map(s => <Box key={s.id} variant="small">{s.name} · {s.cidr}</Box>)}
                </SpaceBetween>
              )
            },
          },
          { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'active'} /> },
          { id: 'external', header: 'External', cell: item => <BoolStatus value={!!(item.external || item['router:external'] === 'External')} /> },
          { id: 'shared', header: 'Shared', cell: item => <BoolStatus value={!!item.shared} /> },
          { id: 'admin', header: 'Admin state', cell: item => <BoolStatus value={item.admin_state_up !== false} trueText="UP" falseText="DOWN" /> },
          { id: 'az', header: 'Availability zone', cell: item => item.availability_zone || 'nova' },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find networks"
            filteringAriaLabel="Filter networks"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No networks"
                description="Create a network and subnet to give instances connectivity."
                action={<Button variant="primary" onClick={() => navigate('/networks/create')}>Create network</Button>}
              />
        }
      />
    </ContentLayout>
  )
}
