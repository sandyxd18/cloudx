import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Textarea from '@cloudscape-design/components/textarea'
import Checkbox from '@cloudscape-design/components/checkbox'
import ColumnLayout from '@cloudscape-design/components/column-layout'
import Alert from '@cloudscape-design/components/alert'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, shortId, counterText } from '../components/common.jsx'

import DeleteModal from '../components/DeleteModal.jsx'

function CreatePoolModal({ onDismiss, onDone }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [prefixes, setPrefixes] = useState('')
  const [minLen, setMinLen] = useState('8')
  const [defLen, setDefLen] = useState('24')
  const [maxLen, setMaxLen] = useState('32')
  const [shared, setShared] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    const list = prefixes.split(/[\s,]+/).map(s => s.trim()).filter(Boolean)
    if (!list.length) { setError('Enter at least one prefix, for example 10.0.0.0/8.'); return }
    const bad = list.find(p => !/^[0-9a-fA-F:.]+\/\d{1,3}$/.test(p))
    if (bad) { setError(`"${bad}" is not a CIDR prefix.`); return }
    const lens = { min: Number(minLen), def: Number(defLen), max: Number(maxLen) }
    if (Object.values(lens).some(n => !Number.isInteger(n) || n < 0 || n > 128)) {
      setError('Prefix lengths must be whole numbers between 0 and 128.'); return
    }
    if (!(lens.min <= lens.def && lens.def <= lens.max)) {
      setError('Prefix lengths must satisfy min <= default <= max.'); return
    }
    setLoading(true); setError('')
    try {
      await api.createSubnetPool({
        name: name.trim(),
        prefixes: list,
        min_prefixlen: lens.min,
        default_prefixlen: lens.def,
        max_prefixlen: lens.max,
        shared,
      })
      toast(`Subnet pool ${name.trim()} created`, 'success')
      onDone(); onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      size="medium"
      header="Create subnet pool"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="subnet-pool-create-submit">Create pool</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="private-pool" data-testid="subnet-pool-name" />
          </FormField>
          <FormField label="Prefixes" description="One CIDR per line, or separated by commas.">
            <Textarea
              value={prefixes}
              onChange={({ detail }) => setPrefixes(detail.value)}
              placeholder={'10.0.0.0/8\n192.168.0.0/16'}
              rows={3}
              data-testid="subnet-pool-prefixes"
            />
          </FormField>
          <ColumnLayout columns={3}>
            <FormField label="Min prefix length">
              <Input type="number" inputMode="numeric" value={minLen} onChange={({ detail }) => setMinLen(detail.value)} />
            </FormField>
            <FormField label="Default prefix length">
              <Input type="number" inputMode="numeric" value={defLen} onChange={({ detail }) => setDefLen(detail.value)} />
            </FormField>
            <FormField label="Max prefix length">
              <Input type="number" inputMode="numeric" value={maxLen} onChange={({ detail }) => setMaxLen(detail.value)} />
            </FormField>
          </ColumnLayout>
          <Checkbox checked={shared} onChange={({ detail }) => setShared(detail.checked)} description="Other projects can allocate subnets from this pool.">
            Shared
          </Checkbox>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function SubnetPoolsPage() {
  const toast = useToast()
  const [pools, setPools]       = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [createOpen, setCreateOpen] = useState(false)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getSubnetPools()
      .then(r => setPools(r?.subnet_pools || []))
      .catch(e => toast(`Could not load subnet pools: ${e.message}`, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    setDeleting(true)
    for (const p of selected) {
      try { await api.deleteSubnetPool(p.id) } catch (e) { toast(`${p.name}: ${e.message}`, 'error') }
    }
    setDeleting(false)
    setDeleteModal(false)
    setSelected([])
    load()
  }

  const q = filter.trim().toLowerCase()
  const filtered = q ? pools.filter(p => p.name?.toLowerCase().includes(q)) : pools

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          counter={counterText(pools.length, selected.length)}
          description="Address ranges that subnets can be allocated from"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh subnet pools" />
              <Button disabled={!selected.length} onClick={() => setDeleteModal(true)} data-testid="delete-subnet-pool-btn">Delete</Button>
              <Button variant="primary" onClick={() => setCreateOpen(true)} data-testid="create-subnet-pool-btn">Create subnet pool</Button>
            </SpaceBetween>
          }
        >
          Subnet pools
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        loading={loading}
        loadingText="Loading subnet pools"
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        items={filtered}
        trackBy="id"
        columnDefinitions={[
          {
            id: 'name',
            header: 'Pool name',
            cell: item => (
              <SpaceBetween size="xxxs">
                <span>{item.name}</span>
                <Box variant="small" color="text-body-secondary">{shortId(item.id, 16)}</Box>
              </SpaceBetween>
            ),
            isRowHeader: true,
            sortingField: 'name',
          },
          { id: 'prefixes', header: 'Prefixes', cell: item => item.prefixes?.length ? item.prefixes.join(', ') : '—' },
          { id: 'min', header: 'Min prefix length', cell: item => item.min_prefixlen },
          { id: 'default', header: 'Default prefix length', cell: item => item.default_prefixlen },
          { id: 'max', header: 'Max prefix length', cell: item => item.max_prefixlen },
          { id: 'shared', header: 'Shared', cell: item => <BoolStatus value={!!item.shared} /> },
        ]}
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find subnet pools"
            filteringAriaLabel="Filter subnet pools"
            countText={`${filtered.length} matches`}
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty
                title="No subnet pools"
                description="Create a pool to allocate subnets from a set of prefixes."
                action={<Button variant="primary" onClick={() => setCreateOpen(true)}>Create subnet pool</Button>}
              />
        }
      />

      {createOpen && <CreatePoolModal onDismiss={() => setCreateOpen(false)} onDone={load} />}
      {deleteModal && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? selected[0].name : undefined}
          title="Delete Subnet Pool"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={deleteSelected}
        />
      )}
    </ContentLayout>
  )
}
