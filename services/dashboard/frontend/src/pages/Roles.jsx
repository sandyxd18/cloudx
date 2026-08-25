import { useState, useEffect, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Grid from '@cloudscape-design/components/grid'
import Header from '@cloudscape-design/components/header'
import SegmentedControl from '@cloudscape-design/components/segmented-control'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import Badge from '@cloudscape-design/components/badge'
import Select from '@cloudscape-design/components/select'
import { api } from '../api.js'
import { useAuth, useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, fmtDate, counterText } from '../components/common.jsx'

const ROLES = [
  { value: 'admin',  label: 'admin' },
  { value: 'member', label: 'member' },
]

const ROLE_DESCRIPTIONS = [
  {
    role: 'admin',
    color: 'red',
    text: 'Full access to every resource across all projects. Can manage users, roles, quotas, volume types, and the service catalog.',
  },
  {
    role: 'member',
    color: 'blue',
    text: 'Standard project member. Can create and manage resources inside the assigned project, but cannot manage identity or global configuration.',
  },
]

export default function RolesPage() {
  const { user: currentUser } = useAuth()
  const toast = useToast()
  const [users, setUsers]     = useState([])
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(null)
  const [filter, setFilter]   = useState('')
  const [roleFilter, setRoleFilter] = useState('all')

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getUsers(), api.getProjects()])
      .then(([u, p]) => {
        if (u.status === 'fulfilled') setUsers(u.value?.items || [])
        else toast('Failed to load users', 'error')
        if (p.status === 'fulfilled') setProjects(p.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { load() }, [load])

  const changeRole = async (user, newRole) => {
    if (user.role === newRole) return
    setSaving(user.id)
    try {
      await api.updateUser(user.id, { role: newRole })
      setUsers(list => list.map(u => (u.id === user.id ? { ...u, role: newRole } : u)))
      toast(`${user.username} is now ${newRole}`, 'success')
    } catch (e) {
      toast(e.message || 'Failed to update role', 'error')
    } finally {
      setSaving(null)
    }
  }

  if (currentUser?.role !== 'admin') {
    return (
      <ContentLayout header={<Header variant="h1">Role management</Header>}>
        <Alert type="error" header="Administrator access required">
          Only users with the admin role can view and modify role assignments.
        </Alert>
      </ContentLayout>
    )
  }

  const q = filter.trim().toLowerCase()
  const filtered = users.filter(u => {
    const matchText = !q || u.username?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q)
    const matchRole = roleFilter === 'all' || u.role === roleFilter
    return matchText && matchRole
  })

  const adminCount  = users.filter(u => u.role === 'admin').length
  const memberCount = users.filter(u => u.role === 'member').length

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Assign roles that determine what each user can do inside their project"
          actions={<Button iconName="refresh" onClick={load} loading={loading}>Refresh</Button>}
        >
          Role management
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Grid gridDefinition={[{ colspan: { default: 12, xs: 4 } }, { colspan: { default: 12, xs: 4 } }, { colspan: { default: 12, xs: 4 } }]}>
          <Container>
            <SpaceBetween size="xxs">
              <Box variant="awsui-key-label">Total users</Box>
              <Box variant="awsui-value-large" fontWeight="bold">{users.length}</Box>
            </SpaceBetween>
          </Container>
          <Container>
            <SpaceBetween size="xxs">
              <Box variant="awsui-key-label">Administrators</Box>
              <Box variant="awsui-value-large" fontWeight="bold">{adminCount}</Box>
            </SpaceBetween>
          </Container>
          <Container>
            <SpaceBetween size="xxs">
              <Box variant="awsui-key-label">Members</Box>
              <Box variant="awsui-value-large" fontWeight="bold">{memberCount}</Box>
            </SpaceBetween>
          </Container>
        </Grid>

        <Table
          variant="container"
          loading={loading}
          loadingText="Loading users"
          items={filtered}
          trackBy="id"
          header={
            <Header variant="h2" counter={counterText(users.length)} description="Change a role using the selector in the Role column.">
              Users and role assignments
            </Header>
          }
          columnDefinitions={[
            { id: 'username', header: 'Username', cell: u => <Box fontWeight="bold">{u.username}</Box>, isRowHeader: true, sortingField: 'username' },
            { id: 'email', header: 'Email', cell: u => u.email || '—' },
            { id: 'project', header: 'Project', cell: u => projects.find(p => p.id === u.project_id)?.name || u.project_id || '—' },
            {
              id: 'role',
              header: 'Role',
              cell: u => (
                <Select
                  selectedOption={ROLES.find(r => r.value === u.role) || { value: u.role, label: u.role }}
                  onChange={({ detail }) => changeRole(u, detail.selectedOption.value)}
                  options={ROLES}
                  disabled={saving === u.id}
                  expandToViewport
                  ariaLabel={`Role for ${u.username}`}
                />
              ),
            },
            { id: 'current', header: 'Current role', cell: u => <Badge color={u.role === 'admin' ? 'red' : 'blue'}>{u.role}</Badge> },
            { id: 'enabled', header: 'Status', cell: u => <BoolStatus value={u.enabled !== false} trueText="Active" falseText="Disabled" /> },
            { id: 'created', header: 'Created', cell: u => fmtDate(u.created_at) },
          ]}
          filter={
            <SpaceBetween direction="horizontal" size="xs">
              <TextFilter
                filteringText={filter}
                filteringPlaceholder="Find users"
                filteringAriaLabel="Filter users"
                countText={`${filtered.length} matches`}
                onChange={({ detail }) => setFilter(detail.filteringText)}
              />
              <SegmentedControl
                selectedId={roleFilter}
                onChange={({ detail }) => setRoleFilter(detail.selectedId)}
                label="Filter by role"
                options={[
                  { id: 'all', text: 'All' },
                  { id: 'admin', text: 'Admin' },
                  { id: 'member', text: 'Member' },
                ]}
              />
            </SpaceBetween>
          }
          empty={q ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No users" description="Create users from the Administration page." />}
        />

        <Container header={<Header variant="h2">Role definitions</Header>}>
          <SpaceBetween size="m">
            {ROLE_DESCRIPTIONS.map(r => (
              <Box key={r.role}>
                <SpaceBetween size="xxs">
                  <Badge color={r.color}>{r.role}</Badge>
                  <Box variant="p" color="text-body-secondary">{r.text}</Box>
                </SpaceBetween>
              </Box>
            ))}
          </SpaceBetween>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  )
}
