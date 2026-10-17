import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ColumnLayout from '@cloudscape-design/components/column-layout'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { PageLoading, TableEmpty } from '../components/common.jsx'
import L7Policies from '../components/L7Policies.jsx'

const STATUS_MAP = {
  ACTIVE: 'success', PENDING_CREATE: 'pending', ERROR: 'error',
  ONLINE: 'success', OFFLINE: 'stopped',
}

function Val({ label, children }) {
  return <div><Box variant="awsui-key-label">{label}</Box><div>{children}</div></div>
}

export default function LoadBalancerDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [lb, setLb] = useState(null)
  const [listeners, setListeners] = useState([])
  const [pools, setPools] = useState({})

  async function load() {
    setLoading(true)
    try {
      const [lbData, lsData] = await Promise.all([
        api.getLoadBalancer(id),
        api.getListeners(id),
      ])
      setLb(lbData)
      const lsList = lsData.listeners || []
      setListeners(lsList)

      const poolMap = {}
      for (const ls of lsList) {
        if (ls.default_pool_id) {
          try {
            const pool = await api.getPool(ls.default_pool_id)
            poolMap[ls.id] = pool
          } catch { /* ignore */ }
        }
      }
      setPools(poolMap)
    } catch { /* ignore */ } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [id])

  async function handleSync(poolId) {
    try {
      await api.syncPoolMembers(poolId)
      load()
    } catch { /* ignore */ }
  }

  if (loading) return <PageLoading text="Loading load balancer" />
  if (!lb) return <Box variant="h2">Load balancer not found</Box>

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={load}>Refresh</Button>
              <Button onClick={() => navigate('/load-balancers')}>Back</Button>
            </SpaceBetween>
          }
        >{lb.name}</Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Details</Header>}>
          <ColumnLayout columns={3} variant="text-grid">
            <Val label="Type">{lb.lb_type === 'ALB' ? 'Application (L7)' : 'Network (L4)'}</Val>
            <Val label="VIP Address">{lb.vip_address || '-'}</Val>
            <Val label="VIP Network">{lb.vip_network_id || '-'}</Val>
            <Val label="Provisioning">
              <StatusIndicator type={STATUS_MAP[lb.provisioning_status] || 'info'}>
                {lb.provisioning_status}
              </StatusIndicator>
            </Val>
            <Val label="Operating">
              <StatusIndicator type={STATUS_MAP[lb.operating_status] || 'info'}>
                {lb.operating_status}
              </StatusIndicator>
            </Val>
            <Val label="Created">{lb.created_at}</Val>
          </ColumnLayout>
        </Container>

        {listeners.map(ls => {
          const pool = pools[ls.id]
          const members = pool?.members || []
          return (
            <Container key={ls.id} header={
              <Header variant="h2"
                actions={pool?.server_group_id && (
                  <Button onClick={() => handleSync(pool.id)}>Sync members</Button>
                )}
              >
                Listener: {ls.name} ({ls.protocol}:{ls.protocol_port})
              </Header>
            }>
              <SpaceBetween size="m">
                {pool && (
                  <Box>
                    <Box variant="awsui-key-label">Pool: {pool.name}</Box>
                    <Box variant="small">
                      Algorithm: {pool.lb_algorithm} | Server Group: {pool.server_group_id || 'manual'}
                    </Box>
                  </Box>
                )}
                <Table
                  columnDefinitions={[
                    { id: 'address', header: 'Address', cell: m => m.address },
                    { id: 'port', header: 'Port', cell: m => m.protocol_port },
                    { id: 'weight', header: 'Weight', cell: m => m.weight },
                    { id: 'instance', header: 'Instance', cell: m => m.instance_id || '-' },
                    { id: 'status', header: 'Status', cell: m => (
                      <StatusIndicator type={STATUS_MAP[m.operating_status] || 'info'}>
                        {m.operating_status}
                      </StatusIndicator>
                    )},
                  ]}
                  items={members}
                  empty={<TableEmpty title="No members" subtitle="Members will be synced from the server group." />}
                  header={<Header variant="h3" counter={`(${members.length})`}>Members</Header>}
                />
                {lb.lb_type === 'ALB' && ['HTTP', 'HTTPS'].includes(ls.protocol) && <L7Policies listenerId={ls.id} />}
              </SpaceBetween>
            </Container>
          )
        })}
      </SpaceBetween>
    </ContentLayout>
  )
}
