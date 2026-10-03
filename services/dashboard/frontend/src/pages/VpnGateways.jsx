import { useEffect, useState, useCallback } from 'react'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { ResourceStatus, TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function VpnGatewaysPage() {
  const toast = useToast()
  const [activeTab, setActiveTab] = useState('connections')

  // Connections
  const [connections, setConnections] = useState([])
  const [loadingConn, setLoadingConn] = useState(true)
  const [connFilter, setConnFilter] = useState('')
  const [selectedConn, setSelectedConn] = useState([])
  const [showCreateConn, setShowCreateConn] = useState(false)
  const [creatingConn, setCreatingConn] = useState(false)
  const [connError, setConnError] = useState('')
  const [deleteConnModal, setDeleteConnModal] = useState(false)
  const [deletingConn, setDeletingConn] = useState(false)

  // IKE Policies
  const [ikePolicies, setIkePolicies] = useState([])
  const [loadingIke, setLoadingIke] = useState(true)
  const [showCreateIke, setShowCreateIke] = useState(false)
  const [creatingIke, setCreatingIke] = useState(false)
  const [ikeForm, setIkeForm] = useState({ name: '', auth_algorithm: 'sha256', encryption_algorithm: 'aes-256', pfs: 'group14', ike_version: 'v2' })

  // IPsec Policies
  const [ipsecPolicies, setIpsecPolicies] = useState([])
  const [loadingIpsec, setLoadingIpsec] = useState(true)
  const [showCreateIpsec, setShowCreateIpsec] = useState(false)
  const [creatingIpsec, setCreatingIpsec] = useState(false)
  const [ipsecForm, setIpsecForm] = useState({ name: '', auth_algorithm: 'sha256', encryption_algorithm: 'aes-256', pfs: 'group14', transform_protocol: 'esp' })

  // Routers
  const [routers, setRouters] = useState([])

  // Connection form state
  const [connForm, setConnForm] = useState({
    name: '',
    peer_address: '',
    peer_cidrs: '10.0.0.0/24',
    psk: '',
    router: null,
    ikePolicy: null,
    ipsecPolicy: null,
  })

  const loadConn = useCallback(() => {
    setLoadingConn(true)
    api.getVpnSiteConnections()
      .then(r => setConnections(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingConn(false))
  }, [toast])

  const loadIke = useCallback(() => {
    setLoadingIke(true)
    api.getVpnIkePolicies()
      .then(r => setIkePolicies(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingIke(false))
  }, [toast])

  const loadIpsec = useCallback(() => {
    setLoadingIpsec(true)
    api.getVpnIpsecPolicies()
      .then(r => setIpsecPolicies(r?.items || (Array.isArray(r) ? r : [])))
      .catch(e => toast(e.message, 'error'))
      .finally(() => setLoadingIpsec(false))
  }, [toast])

  const loadRouters = useCallback(() => {
    api.getRouters()
      .then(r => setRouters(r?.items || (Array.isArray(r) ? r : [])))
      .catch(() => {})
  }, [])

  useEffect(() => {
    loadConn()
    loadIke()
    loadIpsec()
    loadRouters()
  }, [loadConn, loadIke, loadIpsec, loadRouters])

  const handleCreateConn = async () => {
    if (!connForm.name.trim() || !connForm.peer_address.trim() || !connForm.psk.trim() || !connForm.router) {
      setConnError('Name, Peer Address, PSK, and Router are required.')
      return
    }
    setCreatingConn(true); setConnError('')
    try {
      await api.createVpnSiteConnection({
        name: connForm.name.trim(),
        peer_address: connForm.peer_address.trim(),
        peer_cidrs: connForm.peer_cidrs.split(',').map(s => s.trim()).filter(Boolean),
        psk: connForm.psk.trim(),
        router_id: connForm.router.value,
        ikepolicy_id: connForm.ikePolicy?.value || undefined,
        ipsecpolicy_id: connForm.ipsecPolicy?.value || undefined,
      })
      toast('IPSec VPN site connection created successfully', 'success')
      setShowCreateConn(false)
      setConnForm({ name: '', peer_address: '', peer_cidrs: '10.0.0.0/24', psk: '', router: null, ikePolicy: null, ipsecPolicy: null })
      loadConn()
    } catch (e) {
      setConnError(e.message)
    } finally {
      setCreatingConn(false)
    }
  }

  const handleCreateIke = async () => {
    if (!ikeForm.name.trim()) return
    setCreatingIke(true)
    try {
      await api.createVpnIkePolicy({
        name: ikeForm.name.trim(),
        auth_algorithm: ikeForm.auth_algorithm,
        encryption_algorithm: ikeForm.encryption_algorithm,
        pfs: ikeForm.pfs,
        ike_version: ikeForm.ike_version,
      })
      toast('IKE Policy created', 'success')
      setShowCreateIke(false)
      loadIke()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setCreatingIke(false)
    }
  }

  const handleCreateIpsec = async () => {
    if (!ipsecForm.name.trim()) return
    setCreatingIpsec(true)
    try {
      await api.createVpnIpsecPolicy({
        name: ipsecForm.name.trim(),
        auth_algorithm: ipsecForm.auth_algorithm,
        encryption_algorithm: ipsecForm.encryption_algorithm,
        pfs: ipsecForm.pfs,
        transform_protocol: ipsecForm.transform_protocol,
      })
      toast('IPsec Policy created', 'success')
      setShowCreateIpsec(false)
      loadIpsec()
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setCreatingIpsec(false)
    }
  }

  const handleDeleteConn = async () => {
    if (!selectedConn.length) return
    setDeletingConn(true)
    for (const c of selectedConn) {
      try { await api.deleteVpnSiteConnection(c.id) } catch (e) { toast(e.message, 'error') }
    }
    setDeletingConn(false)
    setDeleteConnModal(false)
    setSelectedConn([])
    toast(`${selectedConn.length} VPN site connection(s) deleted`, 'success')
    loadConn()
  }

  const cq = connFilter.trim().toLowerCase()
  const filteredConn = cq
    ? connections.filter(c => c.name?.toLowerCase().includes(cq) || c.peer_address?.toLowerCase().includes(cq))
    : connections

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="IPSec Site-to-Site VPN Gateways, IKE Phase 1 policies, and IPsec Phase 2 profiles"
        >
          VPN Gateways
        </Header>
      }
    >
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          {
            id: 'connections',
            label: 'Site Connections',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                selectionType="multi"
                selectedItems={selectedConn}
                onSelectionChange={({ detail }) => setSelectedConn(detail.selectedItems)}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(connections.length, selectedConn.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button iconName="refresh" onClick={loadConn} ariaLabel="Refresh VPN connections" />
                        <Button disabled={!selectedConn.length} onClick={() => setDeleteConnModal(true)}>
                          Delete
                        </Button>
                        <Button variant="primary" onClick={() => setShowCreateConn(true)}>
                          Create connection
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    VPN Site Connections
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Connection Name', cell: item => <Box fontWeight="bold">{item.name}</Box>, sortingField: 'name' },
                  { id: 'status', header: 'Status', cell: item => <ResourceStatus status={item.status || 'ACTIVE'} /> },
                  { id: 'peer_address', header: 'Peer Endpoint IP', cell: item => item.peer_address },
                  { id: 'peer_cidrs', header: 'Remote CIDRs', cell: item => (item.peer_cidrs || []).join(', ') || '—' },
                  { id: 'mtu', header: 'MTU', cell: item => item.mtu || 1500 },
                  { id: 'dpd', header: 'DPD Action', cell: item => item.dpd_action || 'hold' },
                ]}
                items={filteredConn}
                loading={loadingConn}
                loadingText="Loading VPN connections"
                filter={
                  <TextFilter
                    filteringText={connFilter}
                    onChange={({ detail }) => setConnFilter(detail.filteringText)}
                    filteringPlaceholder="Filter VPN connections"
                  />
                }
                empty={
                  connFilter ? (
                    <TableNoMatch onClear={() => setConnFilter('')} />
                  ) : (
                    <TableEmpty
                      title="No VPN site connections"
                      subtitle="Establish an encrypted IPSec tunnel to an external branch or on-premises gateway."
                    />
                  )
                }
              />
            ),
          },
          {
            id: 'ike',
            label: 'IKE Policies',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                header={
                  <Header
                    variant="h2"
                    counter={String(ikePolicies.length)}
                    actions={
                      <Button variant="primary" onClick={() => setShowCreateIke(true)}>
                        Create IKE policy
                      </Button>
                    }
                  >
                    IKE Phase 1 Policies
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Policy Name', cell: item => <Box fontWeight="bold">{item.name}</Box> },
                  { id: 'version', header: 'IKE Version', cell: item => item.ike_version || 'v2' },
                  { id: 'auth', header: 'Auth Algorithm', cell: item => item.auth_algorithm || 'sha256' },
                  { id: 'enc', header: 'Encryption Algorithm', cell: item => item.encryption_algorithm || 'aes-256' },
                  { id: 'pfs', header: 'PFS', cell: item => item.pfs || 'group14' },
                ]}
                items={ikePolicies}
                loading={loadingIke}
                empty={<TableEmpty title="No IKE policies" subtitle="Create custom Phase 1 cryptographic policies." />}
              />
            ),
          },
          {
            id: 'ipsec',
            label: 'IPsec Policies',
            content: (
              <Table
                variant="full-page"
                stickyHeader
                header={
                  <Header
                    variant="h2"
                    counter={String(ipsecPolicies.length)}
                    actions={
                      <Button variant="primary" onClick={() => setShowCreateIpsec(true)}>
                        Create IPsec policy
                      </Button>
                    }
                  >
                    IPsec Phase 2 Policies
                  </Header>
                }
                columnDefinitions={[
                  { id: 'name', header: 'Policy Name', cell: item => <Box fontWeight="bold">{item.name}</Box> },
                  { id: 'protocol', header: 'Transform Protocol', cell: item => item.transform_protocol || 'esp' },
                  { id: 'auth', header: 'Auth Algorithm', cell: item => item.auth_algorithm || 'sha256' },
                  { id: 'enc', header: 'Encryption Algorithm', cell: item => item.encryption_algorithm || 'aes-256' },
                  { id: 'pfs', header: 'PFS', cell: item => item.pfs || 'group14' },
                ]}
                items={ipsecPolicies}
                loading={loadingIpsec}
                empty={<TableEmpty title="No IPsec policies" subtitle="Create custom Phase 2 cryptographic policies." />}
              />
            ),
          },
        ]}
      />

      {/* Modal: Create Connection */}
      {showCreateConn && (
        <Modal
          visible
          onDismiss={() => setShowCreateConn(false)}
          header="Create IPSec VPN Site Connection"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateConn(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingConn} onClick={handleCreateConn}>
                  Create connection
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {connError && <Box color="text-status-error">{connError}</Box>}
            <FormField label="Connection Name">
              <Input
                value={connForm.name}
                onChange={({ detail }) => setConnForm(f => ({ ...f, name: detail.value }))}
                placeholder="branch-office-vpn"
                autoFocus
              />
            </FormField>
            <FormField label="Peer Public IP Address">
              <Input
                value={connForm.peer_address}
                onChange={({ detail }) => setConnForm(f => ({ ...f, peer_address: detail.value }))}
                placeholder="203.0.113.1"
              />
            </FormField>
            <FormField label="Remote Peer CIDRs" description="Comma-separated subnets on remote network">
              <Input
                value={connForm.peer_cidrs}
                onChange={({ detail }) => setConnForm(f => ({ ...f, peer_cidrs: detail.value }))}
                placeholder="10.10.0.0/24, 10.20.0.0/24"
              />
            </FormField>
            <FormField label="Pre-Shared Key (PSK)">
              <Input
                type="password"
                value={connForm.psk}
                onChange={({ detail }) => setConnForm(f => ({ ...f, psk: detail.value }))}
                placeholder="Enter pre-shared secret key"
              />
            </FormField>
            <FormField label="Router Gateway">
              <Select
                selectedOption={connForm.router}
                onChange={({ detail }) => setConnForm(f => ({ ...f, router: detail.selectedOption }))}
                options={routers.map(r => ({ value: r.id, label: r.name }))}
                placeholder="Select router"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Create IKE */}
      {showCreateIke && (
        <Modal
          visible
          onDismiss={() => setShowCreateIke(false)}
          header="Create IKE Policy"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateIke(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingIke} onClick={handleCreateIke}>
                  Create policy
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <FormField label="Policy Name">
              <Input
                value={ikeForm.name}
                onChange={({ detail }) => setIkeForm(f => ({ ...f, name: detail.value }))}
                placeholder="ike-sha256-aes256"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Modal: Create IPsec */}
      {showCreateIpsec && (
        <Modal
          visible
          onDismiss={() => setShowCreateIpsec(false)}
          header="Create IPsec Policy"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowCreateIpsec(false)}>Cancel</Button>
                <Button variant="primary" loading={creatingIpsec} onClick={handleCreateIpsec}>
                  Create policy
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <FormField label="Policy Name">
              <Input
                value={ipsecForm.name}
                onChange={({ detail }) => setIpsecForm(f => ({ ...f, name: detail.value }))}
                placeholder="ipsec-esp-aes256"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {/* Delete Modal */}
      {deleteConnModal && (
        <DeleteModal
          visible
          itemCount={selectedConn.length}
          itemName={selectedConn.length === 1 ? selectedConn[0].name : undefined}
          title="Delete VPN Site Connection"
          loading={deletingConn}
          onDismiss={() => setDeleteConnModal(false)}
          onConfirm={handleDeleteConn}
        />
      )}
    </ContentLayout>
  )
}
