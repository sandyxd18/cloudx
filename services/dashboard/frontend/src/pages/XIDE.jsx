import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Badge from '@cloudscape-design/components/badge'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

export default function XIDEPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [workspaces, setWorkspaces] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState([])

  const load = useCallback(() => {
    setLoading(true)
    api.getXIDEWorkspaces()
      .then(r => setWorkspaces(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} workspace(s)?`)) return
    for (const w of selected) {
      try { await api.deleteXIDEWorkspace(w.id) } catch { /* continue */ }
    }
    setSelected([])
    toast(`${selected.length} workspace(s) deleted`, 'success')
    load()
  }

  const openIDE = (ws) => {
    // Open code-server with the workspace path preset
    const wsPath = encodeURIComponent(`/home/coder/${ws.owner}/${ws.name}`)
    const win = window.open(`/xide-proxy/?folder=/home/coder/${encodeURIComponent(ws.owner)}/${encodeURIComponent(ws.name)}`, '_blank')
    if (!win) toast('Pop-up blocked. Please allow pop-ups for this site.', 'warning')
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? workspaces.filter(w => w.name?.toLowerCase().includes(q) || w.description?.toLowerCase().includes(q))
    : workspaces

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(workspaces.length, selected.length)}
          description="Cloud-based VS Code IDE environments with isolated workspaces"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh workspaces" />
              <Button disabled={!selected.length} onClick={deleteSelected}>Delete</Button>
              <Button variant="primary" onClick={() => navigate('/xide/create')} data-testid="xide-create-btn">
                Create environment
              </Button>
            </SpaceBetween>
          }
        >
          XIDE environments
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading workspaces"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Environment name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <Box fontWeight="bold">{item.name}</Box>
                <Box variant="small" color="text-body-secondary">{item.description || '—'}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'id', header: 'ID', cell: item => shortId(item.id, 12) },
          {
            id: 'profile',
            header: 'Instance profile',
            cell: item => <Badge color="blue">{item.profile || 'default'}</Badge>,
          },
          {
            id: 'auto_stop',
            header: 'Auto-stop',
            cell: item => item.auto_stop || '—',
          },
          {
            id: 'created',
            header: 'Created',
            cell: item => {
              if (!item.created_at) return '—'
              return new Date(item.created_at).toLocaleString()
            },
            sortingField: 'created_at',
          },
          {
            id: 'actions',
            header: '',
            cell: item => (
              <Button variant="primary" onClick={() => openIDE(item)} data-testid={`xide-open-${item.id}`}>
                Open IDE
              </Button>
            ),
          },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find environments"
            filteringAriaLabel="Filter environments"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No XIDE environments"
                description="Create a cloud IDE environment to start coding in your browser."
                action={<Button variant="primary" onClick={() => navigate('/xide/create')}>Create environment</Button>}
              />
        }
      />
    </ContentLayout>
  )
}