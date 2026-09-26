import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ButtonDropdown from '@cloudscape-design/components/button-dropdown'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function FloatingIPsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [fips, setFips]         = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [releasing, setReleasing] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getFloatingIPs(), api.getNetworks()])
      .then(([f, n]) => {
        if (f.status === 'fulfilled') setFips(f.value?.items || [])
        if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const one = selected.length === 1 ? selected[0] : null
  const oneAssociated = !!(one?.port_id || one?.fixed_ip_address || one?.fixed_ip)

  const handleReleaseConfirmed = async () => {
    if (!selected.length) return
    setReleasing(true)
    for (const f of selected) {
      try { await api.deleteFloatingIP(f.id) } catch (e) { toast(e.message, 'error') }
    }
    setReleasing(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} floating IP(s) released`, 'success')
    load()
  }

  const disassociate = async (fip) => {
    try { await api.disassociateFloatingIP(fip.id); toast('Floating IP disassociated', 'success'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const q = filter.trim().toLowerCase()
  const filtered = q
    ? fips.filter(f => (f.floating_ip_address || f.floating_ip || '').toLowerCase().includes(q))
    : fips

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(fips.length, selected.length)}
          description="Public addresses mapped to instance ports via OVN DNAT/SNAT"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh floating IPs" />
              <ButtonDropdown
                disabled={!selected.length}
                items={[
                  { id: 'associate',    text: 'Associate',    disabled: !one || oneAssociated },
                  { id: 'disassociate', text: 'Disassociate', disabled: !one || !oneAssociated },
                  { id: 'release',      text: 'Release floating IP' },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'release') setDeleteModal(true)
                  else if (detail.id === 'associate' && one) navigate(`/floating-ips/associate?id=${one.id}`)
                  else if (detail.id === 'disassociate' && one) disassociate(one)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/floating-ips/allocate')} data-testid="allocate-fip-btn">
                Allocate floating IP
              </Button>
            </SpaceBetween>
          }
        >
          Floating IPs
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading floating IPs"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'ip',
            header: 'Floating IP',
            cell: item => <Box fontWeight="bold">{item.floating_ip_address || item.floating_ip || '—'}</Box>,
            isRowHeader: true,
          },
          {
            id: 'status',
            header: 'Status',
            cell: item => <ResourceStatus status={item.status || (item.port_id ? 'active' : 'down')} />,
          },
          { id: 'fixed', header: 'Mapped fixed IP', cell: item => item.fixed_ip_address || item.fixed_ip || '—' },
          { id: 'port', header: 'Port', cell: item => (item.port_id ? shortId(item.port_id, 12) : '—') },
          { id: 'pool', header: 'Pool', cell: item => (item.floating_network_id ? shortId(item.floating_network_id, 12) : '—') },
          { id: 'router', header: 'Router', cell: item => (item.router_id ? shortId(item.router_id, 12) : '—') },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find floating IPs"
            filteringAriaLabel="Filter floating IPs"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No floating IPs"
                description="Allocate a floating IP from an external network pool to expose an instance."
                action={<Button variant="primary" onClick={() => navigate('/floating-ips/allocate')}>Allocate floating IP</Button>}
              />
        }
      />
      {deleteModal && (
        <DeleteModal
          visible
          title="Release Floating IP"
          confirmButtonText="Release"
          itemCount={selected.length}
          itemName={selected.length === 1 ? (selected[0].floating_ip_address || selected[0].floating_ip) : undefined}
          requireText={false}
          description="Releasing a floating IP unmaps it from instances and returns it to the public pool."
          loading={releasing}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleReleaseConfirmed}
        />
      )}
    </ContentLayout>
  )
}
