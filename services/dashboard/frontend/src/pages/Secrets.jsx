import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Multiselect from '@cloudscape-design/components/multiselect'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import Textarea from '@cloudscape-design/components/textarea'
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const SECRET_TYPE_OPTIONS = [
  { value: 'passphrase', label: 'Passphrase' },
  { value: 'symmetric', label: 'Symmetric Key' },
  { value: 'certificate', label: 'Certificate / Public Key' },
  { value: 'opaque', label: 'Opaque Data' },
]

const CONTAINER_TYPE_OPTIONS = [
  { value: 'generic', label: 'Generic Container' },
  { value: 'certificate', label: 'Certificate Bundle' },
  { value: 'rsa', label: 'RSA Keypair' },
]

export default function SecretsPage() {
  const toast = useToast()
  const [activeTab, setActiveTab] = useState('secrets')

  // Secrets state
  const [secrets, setSecrets] = useState([])
  const [loadingSecrets, setLoadingSecrets] = useState(true)
  const [secretFilter, setSecretFilter] = useState('')
  const [selectedSecrets, setSelectedSecrets] = useState([])
  const [showCreateSecret, setShowCreateSecret] = useState(false)
  const [secretForm, setSecretForm] = useState({
    name: '',
    secret_type: SECRET_TYPE_OPTIONS[0],
    payload: '',
    algorithm: 'aes',
    bit_length: '256',
  })
  const [creatingSecret, setCreatingSecret] = useState(false)
  const [secretError, setSecretError] = useState('')
  const [viewSecret, setViewSecret] = useState(null)
  const [deleteSecretModal, setDeleteSecretModal] = useState(false)
  const [deletingSecret, setDeletingSecret] = useState(false)

  // Containers state
  const [containers, setContainers] = useState([])
  const [loadingContainers, setLoadingContainers] = useState(true)
  const [containerFilter, setContainerFilter] = useState('')
  const [selectedContainers, setSelectedContainers] = useState([])
  const [showCreateContainer, setShowCreateContainer] = useState(false)
  const [containerForm, setContainerForm] = useState({
    name: '',
    container_type: CONTAINER_TYPE_OPTIONS[0],
    selectedSecrets: [],
  })
  const [creatingContainer, setCreatingContainer] = useState(false)
  const [containerError, setContainerError] = useState('')
  const [deleteContainerModal, setDeleteContainerModal] = useState(false)
  const [deletingContainer, setDeletingContainer] = useState(false)

  const loadSecrets = useCallback(() => {
    setLoadingSecrets(true)
    api.getSecrets()
      .then(r => setSecrets(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingSecrets(false))
  }, [toast])

  const loadContainers = useCallback(() => {
    setLoadingContainers(true)
    api.getSecretContainers()
      .then(r => setContainers(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingContainers(false))
  }, [toast])

  useEffect(() => {
    loadSecrets()
    loadContainers()
  }, [loadSecrets, loadContainers])

  const handleCreateSecret = async () => {
    if (!secretForm.name.trim() || !secretForm.payload.trim()) {
      setSecretError('Secret name and payload are required.')
      return
    }
    setCreatingSecret(true); setSecretError('')
    try {
      await api.createSecret({
        name: secretForm.name.trim(),
        secret_type: secretForm.secret_type.value,
        payload: secretForm.payload.trim(),
        algorithm: secretForm.algorithm.trim() || undefined,
        bit_length: parseInt(secretForm.bit_length, 10) || undefined,
      })
      toast('Secret created in Key Manager vault', 'success')
      setShowCreateSecret(false)
      setSecretForm({
        name: '',
        secret_type: SECRET_TYPE_OPTIONS[0],
        payload: '',
        algorithm: 'aes',
        bit_length: '256',
      })
      loadSecrets()
    } catch (e) {
      setSecretError(e.message)
    } finally {
      setCreatingSecret(false)
    }
  }

  const handleCreateContainer = async () => {
    if (!containerForm.name.trim()) {
      setContainerError('Container name is required.')
      return
    }
    setCreatingContainer(true); setContainerError('')
    try {
      const refs = containerForm.selectedSecrets.map(s => s.value)
      await api.createSecretContainer({
        name: containerForm.name.trim(),
        container_type: containerForm.container_type.value,
        secret_refs: refs,
      })
      toast('Secret container created successfully', 'success')
      setShowCreateContainer(false)
      setContainerForm({
        name: '',
        container_type: CONTAINER_TYPE_OPTIONS[0],
        selectedSecrets: [],
      })
      loadContainers()
    } catch (e) {
      setContainerError(e.message)
    } finally {
      setCreatingContainer(false)
    }
  }

  const handleDeleteSecrets = async () => {
    if (!selectedSecrets.length) return
    setDeletingSecret(true)
    for (const s of selectedSecrets) {
      try { await api.deleteSecret(s.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeletingSecret(false)
    setDeleteSecretModal(false)
    setSelectedSecrets([])
    toast(`${selectedSecrets.length} secret(s) deleted`, 'success')
    loadSecrets()
  }

  const handleDeleteContainers = async () => {
    if (!selectedContainers.length) return
    setDeletingContainer(true)
    for (const c of selectedContainers) {
      try { await api.deleteSecretContainer(c.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeletingContainer(false)
    setDeleteContainerModal(false)
    setSelectedContainers([])
    toast(`${selectedContainers.length} container(s) deleted`, 'success')
    loadContainers()
  }

  const sq = secretFilter.trim().toLowerCase()
  const filteredSecrets = sq
    ? secrets.filter(s => s.name?.toLowerCase().includes(sq) || s.secret_type?.toLowerCase().includes(sq))
    : secrets

  const cq = containerFilter.trim().toLowerCase()
  const filteredContainers = cq
    ? containers.filter(c => c.name?.toLowerCase().includes(cq) || c.container_type?.toLowerCase().includes(cq))
    : containers

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Barbican-compatible Key Manager vault for symmetric keys, passphrases, and certificates"
        >
          Key Manager & Secrets
        </Header>
      }
    >
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          {
            id: 'secrets',
            label: 'Secrets',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selectedSecrets}
                onSelectionChange={({ detail }) => setSelectedSecrets(detail.selectedItems)}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(secrets.length, selectedSecrets.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button iconName="refresh" onClick={loadSecrets} ariaLabel="Refresh secrets" />
                        <Button disabled={!selectedSecrets.length} onClick={() => setDeleteSecretModal(true)}>
                          Delete
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreateSecret(true)}>
                          Store secret
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Encrypted Secrets
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Secret Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
                  { id: 'type', header: 'Type', cell: item => item.secret_type?.toUpperCase() || 'OPAQUE' },
                  { id: 'algorithm', header: 'Algorithm', cell: item => item.algorithm || '—' },
                  { id: 'bit_length', header: 'Bit Length', cell: item => item.bit_length || '—' },
                  { id: 'created_at', header: 'Created', cell: item => item.created_at || '—' },
                  {
                    id: 'actions',
                    header: 'Actions',
                    cell: item => (
                      <Button variant="inline-link" onClick={() => setViewSecret(item)}>
                        View secret
                      </Button>
                    ),
                  },
                ]}
                items={filteredSecrets}
                loading={loadingSecrets}
                loadingText="Loading secrets"
                filter={
                  <TextFilter
                    filteringText={secretFilter}
                    onChange={({ detail }) => setSecretFilter(detail.filteringText)}
                    filteringPlaceholder="Filter secrets"
                  />
                }
                empty={
                  secretFilter ? (
                    <TableNoMatch onClear={() => setSecretFilter('')} />
                  ) : (
                    <TableEmpty title="No secrets stored" subtitle="Store keys, certificates, or passphrases securely in the vault." />
                  )
                }
              />
            ),
          },
          {
            id: 'containers',
            label: 'Secret Containers',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selectedContainers}
                onSelectionChange={({ detail }) => setSelectedContainers(detail.selectedItems)}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(containers.length, selectedContainers.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button iconName="refresh" onClick={loadContainers} ariaLabel="Refresh containers" />
                        <Button disabled={!selectedContainers.length} onClick={() => setDeleteContainerModal(true)}>
                          Delete
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreateContainer(true)}>
                          Create container
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Secret Containers
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Container Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
                  { id: 'type', header: 'Type', cell: item => item.container_type?.toUpperCase() || 'GENERIC' },
                  { id: 'secrets', header: 'Associated Secrets', cell: item => (item.secret_refs?.length || 0) + ' secret(s)' },
                  { id: 'created_at', header: 'Created', cell: item => item.created_at || '—' },
                ]}
                items={filteredContainers}
                loading={loadingContainers}
                loadingText="Loading containers"
                filter={
                  <TextFilter
                    filteringText={containerFilter}
                    onChange={({ detail }) => setContainerFilter(detail.filteringText)}
                    filteringPlaceholder="Filter containers"
                  />
                }
                empty={
                  containerFilter ? (
                    <TableNoMatch onClear={() => setContainerFilter('')} />
                  ) : (
                    <TableEmpty title="No secret containers" subtitle="Group secrets into bundles such as TLS/SSL certificates or RSA pairs." />
                  )
                }
              />
            ),
          },
        ]}
      />

      {/* Modal: Store Secret */}
      {showCreateSecret && (
        <Modal
          visible
          onDismiss={() => setShowCreateSecret(false)}
          header="Store Secret in Vault"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateSecret(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingSecret} onClick={handleCreateSecret}>
                  Save secret
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {secretError && <Box color="text-status-error">{secretError}</Box>}
            <FormField label="Secret Name">
              <Input
                value={secretForm.name}
                onChange={({ detail }) => setSecretForm(f => ({ ...f, name: detail.value }))}
                placeholder="db-root-password"
                autoFocus
              />
            </FormField>
            <FormField label="Secret Type">
              <Select
                selectedOption={secretForm.secret_type}
                onChange={({ detail }) => setSecretForm(f => ({ ...f, secret_type: detail.selectedOption }))}
                options={SECRET_TYPE_OPTIONS}
              />
            </FormField>
            <FormField label="Payload / Secret Value">
              <Textarea
                value={secretForm.payload}
                onChange={({ detail }) => setSecretForm(f => ({ ...f, payload: detail.value }))}
                placeholder="Enter password, secret key, or certificate contents"
                rows={5}
              />
            </FormField>
            <SpaceBetween direction="horizontal" size="m">
              <FormField label="Algorithm (Optional)">
                <Input
                  value={secretForm.algorithm}
                  onChange={({ detail }) => setSecretForm(f => ({ ...f, algorithm: detail.value }))}
                />
              </FormField>
              <FormField label="Bit Length (Optional)">
                <Input
                  value={secretForm.bit_length}
                  onChange={({ detail }) => setSecretForm(f => ({ ...f, bit_length: detail.value }))}
                />
              </FormField>
            </SpaceBetween>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: View Secret Payload */}
      {viewSecret && (
        <Modal
          visible
          onDismiss={() => setViewSecret(null)}
          header={`Secret: ${viewSecret.name}`}
          footer={
            <Box float="right">
              <Button onClick={() => setViewSecret(null)}>Close</Button>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <div>
              <Box variant="awsui-key-label">Secret Type</Box>
              <Box>{viewSecret.secret_type}</Box>
            </div>
            <div>
              <Box variant="awsui-key-label">Payload</Box>
              <SpaceBetween direction="horizontal" size="xs">
                <Textarea value={viewSecret.payload || '—'} readOnly rows={4} />
                {viewSecret.payload && (
                  <CopyToClipboard
                    textToCopy={viewSecret.payload}
                    copyButtonAriaLabel="Copy secret payload"
                    copySuccessText="Payload copied"
                    copyErrorText="Failed to copy"
                  />
                )}
              </SpaceBetween>
            </div>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Create Container */}
      {showCreateContainer && (
        <Modal
          visible
          onDismiss={() => setShowCreateContainer(false)}
          header="Create Secret Container"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateContainer(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingContainer} onClick={handleCreateContainer}>
                  Create container
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {containerError && <Box color="text-status-error">{containerError}</Box>}
            <FormField label="Container Name">
              <Input
                value={containerForm.name}
                onChange={({ detail }) => setContainerForm(f => ({ ...f, name: detail.value }))}
                placeholder="tls-ssl-bundle"
                autoFocus
              />
            </FormField>
            <FormField label="Container Type">
              <Select
                selectedOption={containerForm.container_type}
                onChange={({ detail }) => setContainerForm(f => ({ ...f, container_type: detail.selectedOption }))}
                options={CONTAINER_TYPE_OPTIONS}
              />
            </FormField>
            <FormField label="Select Secrets to Group">
              <Multiselect
                selectedOptions={containerForm.selectedSecrets}
                onChange={({ detail }) => setContainerForm(f => ({ ...f, selectedSecrets: detail.selectedOptions }))}
                options={secrets.map(s => ({ value: s.id, label: s.name, description: s.secret_type }))}
                placeholder="Choose secrets"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Delete Modals */}
      {deleteSecretModal && (
        <DeleteModal
          visible
          itemCount={selectedSecrets.length}
          itemName={selectedSecrets.length === 1 ? selectedSecrets[0].name : undefined}
          title="Delete Secret"
          loading={deletingSecret}
          onDismiss={() => setDeleteSecretModal(false)}
          onConfirm={handleDeleteSecrets}
        />
      )}

      {deleteContainerModal && (
        <DeleteModal
          visible
          itemCount={selectedContainers.length}
          itemName={selectedContainers.length === 1 ? selectedContainers[0].name : undefined}
          title="Delete Secret Container"
          loading={deletingContainer}
          onDismiss={() => setDeleteContainerModal(false)}
          onConfirm={handleDeleteContainers}
        />
      )}
    </ContentLayout>
  )
}
