import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

export default function FlavorsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [flavors, setFlavors] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter]   = useState('')
  const [selected, setSelected] = useState([])

  const load = useCallback(() => {
    setLoading(true)
    api.getFlavors()
      .then(r => setFlavors(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} flavor(s)?`)) return
    for (const f of selected) {
      try { await api.deleteFlavor(f.id) } catch { /* continue */ }
    }
    setSelected([])
    toast(`${selected.length} flavor(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? flavors.filter(f => f.name?.toLowerCase().includes(q)) : flavors

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(flavors.length, selected.length)}
          description="Instance types defining vCPU, memory, and root disk size"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh flavors" />
              <Button disabled={!selected.length} onClick={deleteSelected}>Delete</Button>
              <Button variant="primary" onClick={() => navigate('/flavors/create')} data-testid="create-flavor-btn">Create flavor</Button>
            </SpaceBetween>
          }
        >
          Flavors
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading flavors"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          { id: 'name', header: 'Flavor name', cell: item => <Box fontWeight="bold">{item.name}</Box>, isRowHeader: true, sortingField: 'name' },
          { id: 'vcpus', header: 'vCPUs', cell: item => item.vcpus, sortingField: 'vcpus' },
          {
            id: 'ram',
            header: 'Memory',
            cell: item => (item.ram_mb >= 1024 ? `${(item.ram_mb / 1024).toFixed(1)} GB` : `${item.ram_mb} MB`),
            sortingField: 'ram_mb',
          },
          { id: 'disk', header: 'Root disk', cell: item => `${item.disk_gb} GB`, sortingField: 'disk_gb' },
          { id: 'public', header: 'Visibility', cell: item => <BoolStatus value={item.is_public} trueText="Public" falseText="Private" /> },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find flavors"
            filteringAriaLabel="Filter flavors"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No flavors"
                description="Create a flavor to define the compute profile available to instances."
                action={<Button variant="primary" onClick={() => navigate('/flavors/create')}>Create flavor</Button>}
              />
        }
      />
    </ContentLayout>
  )
}
