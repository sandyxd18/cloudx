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
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function KeypairsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [keypairs, setKeypairs] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getKeypairs()
      .then(r => setKeypairs(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const handleConfirmDelete = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const k of selected) {
      try { await api.deleteKeypair(k.name) } catch { /* continue */ }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    toast(`${selected.length} key pair(s) deleted`, 'success')
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? keypairs.filter(k => k.name?.toLowerCase().includes(q)) : keypairs

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(keypairs.length, selected.length)}
          description="SSH key pairs injected into instances via cloud-init"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh key pairs" />
              <ButtonDropdown
                items={[
                  { id: 'import', text: 'Import key pair' },
                  { id: 'delete', text: 'Delete', disabled: !selected.length },
                ]}
                onItemClick={({ detail }) => {
                  if (detail.id === 'import') navigate('/keypairs/import')
                  else if (detail.id === 'delete') setDeleteModal(true)
                }}
              >
                Actions
              </ButtonDropdown>
              <Button variant="primary" onClick={() => navigate('/keypairs/create')} data-testid="create-keypair-btn">
                Create key pair
              </Button>
            </SpaceBetween>
          }
        >
          Key pairs
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading key pairs"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="name"
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, isRowHeader: true, sortingField: 'name' },
          { id: 'type', header: 'Type', cell: item => (item.key_type || 'rsa').toUpperCase() },
          { id: 'fingerprint', header: 'Fingerprint', cell: item => item.fingerprint || '—' },
          {
            id: 'public_key',
            header: 'Public key',
            cell: item => item.public_key
              ? <CopyToClipboard
                  variant="icon"
                  textToCopy={item.public_key}
                  copyButtonAriaLabel={`Copy public key for ${item.name}`}
                  copySuccessText="Public key copied"
                  copyErrorText="Could not copy public key"
                />
              : '—',
          },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find key pairs"
            filteringAriaLabel="Filter key pairs"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No key pairs"
                subtitle="Create or import an SSH key pair to access your instances."
                action={
                  <Button onClick={() => navigate('/keypairs/create')}>
                    Create key pair
                  </Button>
                }
              />
        }
      />

      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Key Pair"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </ContentLayout>
  )
}
