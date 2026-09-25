import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { PageLoading, TableEmpty } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

export default function ServerGroupDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [group, setGroup] = useState(null)
  const [members, setMembers] = useState([])
  const [loading, setLoading] = useState(true)
  const [deleteModal, setDeleteModal] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [gData, sData] = await Promise.all([
        api.getServerGroup(id),
        api.getServers(),
      ])
      setGroup(gData)
      const allServers = sData?.items || []
      const rawMembers = gData?.members || []
      const memberIds = Array.isArray(rawMembers) ? rawMembers : []
      const matched = allServers.filter(s => memberIds.includes(s.id))
      setMembers(matched)
    } catch (e) {
      toast(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [id, toast])

  useEffect(() => { load() }, [load])

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await api.deleteServerGroup(group.id)
      toast('Server group deleted', 'success')
      navigate('/server-groups')
    } catch (e) {
      toast(e.message, 'error')
      setDeleting(false)
      setDeleteModal(false)
    }
  }

  if (loading && !group) return <PageLoading text="Loading server group details" />
  if (!group) return <Box variant="h2">Server group not found</Box>

  const policyName = group.policy || (Array.isArray(group.policies) ? group.policies.join(', ') : '-')

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={load} ariaLabel="Refresh server group" />
              <Button variant="primary" onClick={() => setDeleteModal(true)}>Delete group</Button>
            </SpaceBetween>
          }
        >
          Server Group: {group.name}
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Group Overview</Header>}>
          <SpaceBetween size="m" direction="horizontal">
            <Box>
              <Box variant="awsui-key-label">Name</Box>
              <Box fontWeight="bold">{group.name}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">ID</Box>
              <Box>{group.id}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Policy</Box>
              <Box>{policyName}</Box>
            </Box>
            <Box>
              <Box variant="awsui-key-label">Total Members</Box>
              <Box>{members.length}</Box>
            </Box>
          </SpaceBetween>
        </Container>

        <Table
          columnDefinitions={[
            { id: 'name', header: 'Member Instance Name', cell: item => item.name, isRowHeader: true },
            { id: 'id', header: 'Instance ID', cell: item => item.id },
            { id: 'status', header: 'Status', cell: item => item.status || 'ACTIVE' },
            { id: 'ip', header: 'IP Address', cell: item => item.ip_address || '-' },
          ]}
          items={members}
          empty={<TableEmpty title="No member instances" subtitle="Launch instances in this server group to see them here." />}
          header={<Header variant="h2" counter={`(${members.length})`}>Group Members</Header>}
        />
      </SpaceBetween>

      {deleteModal && (
        <DeleteModal
          visible
          itemName={group.name}
          title="Delete Server Group"
          loading={deleting}
          onDismiss={() => setDeleteModal(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
