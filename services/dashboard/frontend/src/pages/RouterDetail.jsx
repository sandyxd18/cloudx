import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import FormField from '@cloudscape-design/components/form-field'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty, ResourceStatus } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function RouterDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [router, setRouter] = useState(null)
  const [subnets, setSubnets] = useState([])
  const [networks, setNetworks] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAddIfModal, setShowAddIfModal] = useState(false)
  const [selectedSubnet, setSelectedSubnet] = useState(null)
  const [addingIf, setAddingIf] = useState(false)
  const [addIfError, setAddIfError] = useState('')
  const [showGatewayModal, setShowGatewayModal] = useState(false)
  const [selectedExtNet, setSelectedExtNet] = useState(null)
  const [settingGateway, setSettingGateway] = useState(false)
  const [gatewayError, setGatewayError] = useState('')
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [rData, sData, nData] = await Promise.all([
        api.getRouter(id),
        api.getSubnets(),
        api.getNetworks(),
      ])
      setRouter(rData)
      setSubnets(sData?.items || [])
      setNetworks(nData?.items || [])
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const handleAddInterface = async () => {
    if (!selectedSubnet) { setAddIfError('Please choose a subnet.'); return }
    setAddingIf(true)
    setAddIfError('')
    try {
      await api.addRouterInterface(router.id, { subnet: selectedSubnet.value })
      toast('Router interface added', 'success')
      setShowAddIfModal(false)
      load()
    } catch (e) {
      setAddIfError(e.message)
    } finally {
      setAddingIf(false)
    }
  }

  const handleRemoveInterface = async (subnetId) => {
    try {
      await api.removeRouterInterface(router.id, { subnet: subnetId })
      toast('Router interface removed', 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const handleSetGateway = async () => {
    if (!selectedExtNet) { setGatewayError('Please choose an external network.'); return }
    setSettingGateway(true)
    setGatewayError('')
    try {
      await api.setRouterGateway(router.id, { network: selectedExtNet.value })
      toast('External gateway connected', 'success')
      setShowGatewayModal(false)
      load()
    } catch (e) {
      setGatewayError(e.message)
    } finally {
      setSettingGateway(false)
    }
  }

  const handleClearGateway = async () => {
    try {
      await api.clearRouterGateway(router.id)
      toast('External gateway disconnected', 'success')
      load()
    } catch (e) {
      toast(e.message, 'error')
    }
  }

  const handleDeleteRouter = async () => {
    setDeleting(true)
    try {
      await api.deleteRouter(router.id)
      toast('Router deleted', 'success')
      navigate('/routers')
    } catch (e) {
      toast(e.message, 'error')
      setDeleting(false)
      setDeleteModal(false)
    }
  }

  if (loading && !router) return <PageLoading text="Loading router details" />
  if (!router) return <Box variant="h2">Router not found</Box>

  const extNetInfo = router.external_gateway_info
  const extNetName = extNetInfo?.network_id ? (networks.find(n => n.id === extNetInfo.network_id)?.name || extNetInfo.network_id) : 'None'

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh router" />
              {extNetInfo ? (
                <Button onClick={handleClearGateway}>Clear gateway</Button>
              ) : (
                <Button onClick={() => setShowGatewayModal(true)}>Set gateway</Button>
              )}
              <Button onClick={() => setShowAddIfModal(true)}>Add interface</Button>
              <Button variant="primary" onClick={() => setDeleteModal(true)}>Delete router</Button>
            </SpaceBetween>
          }
        >
          Router: {router.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Router Overview</Header>}>
          <SpaceBetween size="m" direction="horizontal">
            <Box>
              <Box variant="awsui-key-label">Name</Box>
              <Box fontWeight="bold">{router.name}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">ID</Box>
              <Box>{router.id}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Status</Box>
              <ResourceStatus status={router.status} />
            </Box>
            <Box>
              <Box variant="awsui-key-label">External Gateway Network</Box>
              <Box>{extNetName}</Box>
            </Box>
          </SpaceBetween>
        </Container>

        <Table
          columnDefinitions={[
            { id: 'name', header: 'Subnet Name', cell: item => item.name || item.id },
            { id: 'cidr', header: 'CIDR', cell: item => item.cidr || '-' },
            { id: 'gateway', header: 'Gateway IP', cell: item => item.gateway_ip || item.gateway || '-' },
            {
              id: 'actions',
              header: 'Actions',
              cell: item => (
                <Button variant="icon" iconName="remove" onClick={() => handleRemoveInterface(item.id)} ariaLabel="Remove interface" />
              ),
            },
          ]}
          items={router.interfaces || router.subnets || []}
          empty={<TableEmpty title="No connected interfaces" subtitle="Click Add Interface to connect subnets to this router." />}
          header={<Header variant="h2">Connected Interfaces</Header>}
        />
      </SpaceBetween>

      {showAddIfModal && (
        <Modal
          visible
          onDismiss={() => setShowAddIfModal(false)}
          header={`Add Interface to ${router.name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowAddIfModal(false)}>Cancel</Button>
                <Button variant="primary" loading={addingIf} onClick={handleAddInterface}>Add interface</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {addIfError && <Box color="text-status-error">{addIfError}</Box>}
            <FormField label="Subnet" description="Choose tenant subnet to connect.">
              <Select
                selectedOption={selectedSubnet}
                onChange={({ detail }) => setSelectedSubnet(detail.selectedOption)}
                options={subnets.map(s => ({ label: `${s.name} (${s.cidr})`, value: s.name }))}
                placeholder="Select subnet"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {showGatewayModal && (
        <Modal
          visible
          onDismiss={() => setShowGatewayModal(false)}
          header={`Set External Gateway for ${router.name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowGatewayModal(false)}>Cancel</Button>
                <Button variant="primary" loading={settingGateway} onClick={handleSetGateway}>Set gateway</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {gatewayError && <Box color="text-status-error">{gatewayError}</Box>}
            <FormField label="External Network">
              <Select
                selectedOption={selectedExtNet}
                onChange={({ detail }) => setSelectedExtNet(detail.selectedOption)}
                options={networks.filter(n => n.external || n['router:external'] === 'External').map(n => ({ label: n.name, value: n.name }))}
                placeholder="Select external network"
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {deleteModal && (
        <DeleteModal
          visible
          itemName={router.name}
          title="Delete Router"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDeleteRouter}
        />
      )}
    </ContentLayout>
  )
}
