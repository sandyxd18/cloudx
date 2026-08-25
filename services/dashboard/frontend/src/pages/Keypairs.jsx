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
import Select from '@cloudscape-design/components/select'
import Alert from '@cloudscape-design/components/alert'
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'

const KEY_TYPES = [
  { value: 'ssh',  label: 'SSH key' },
  { value: 'x509', label: 'X.509 certificate' },
]

function ImportModal({ onDismiss, onCreate }) {
  const [form, setForm] = useState({ name: '', public_key: '', type: KEY_TYPES[0] })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!form.name || !form.public_key) { setError('Name and public key are required.'); return }
    setLoading(true); setError('')
    try {
      await onCreate({ name: form.name, public_key: form.public_key, type: form.type.value })
      onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Import key pair"
      size="medium"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="keypair-create-submit">Import key pair</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Key pair name">
            <Input value={form.name} onChange={({ detail }) => setForm(f => ({ ...f, name: detail.value }))} placeholder="my-key" controlId="keypair-name" />
          </FormField>
          <FormField label="Key type">
            <Select selectedOption={form.type} onChange={({ detail }) => setForm(f => ({ ...f, type: detail.selectedOption }))} options={KEY_TYPES} />
          </FormField>
          <FormField label="Public key" description="Paste the contents of id_rsa.pub or id_ed25519.pub.">
            <Textarea
              value={form.public_key}
              onChange={({ detail }) => setForm(f => ({ ...f, public_key: detail.value }))}
              placeholder="ssh-rsa AAAA..."
              rows={5}
              controlId="keypair-pubkey"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

export default function KeypairsPage() {
  const toast = useToast()
  const [keypairs, setKeypairs] = useState([])
  const [loading, setLoading]   = useState(true)
  const [filter, setFilter]     = useState('')
  const [selected, setSelected] = useState([])
  const [showImport, setShowImport] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getKeypairs()
      .then(r => setKeypairs(r?.items || []))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const deleteSelected = async () => {
    if (!selected.length) return
    if (!confirm(`Delete ${selected.length} key pair(s)?`)) return
    for (const k of selected) {
      try { await api.deleteKeypair(k.name) } catch { /* continue */ }
    }
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
              <Button disabled={!selected.length} onClick={deleteSelected}>Delete</Button>
              <Button variant="primary" onClick={() => setShowImport(true)} data-testid="create-keypair-btn">Import key pair</Button>
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
          { id: 'type', header: 'Type', cell: item => item.key_type || 'ssh' },
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
                description="Import an SSH public key to enable password-less access to your instances."
                action={<Button variant="primary" onClick={() => setShowImport(true)}>Import key pair</Button>}
              />
        }
      />

      {showImport && (
        <ImportModal
          onDismiss={() => setShowImport(false)}
          onCreate={async (payload) => { await api.createKeypair(payload); toast('Key pair imported', 'success'); load() }}
        />
      )}
    </ContentLayout>
  )
}
