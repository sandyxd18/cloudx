import { useState, useEffect, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import TextFilter from '@cloudscape-design/components/text-filter'
import { api } from '../api.js'
import { useAuth, useToast } from '../context.jsx'
import { TableEmpty, TableNoMatch, counterText } from '../components/common.jsx'
import DeleteModal from '../components/DeleteModal.jsx'

const ROLES = ['admin', 'member', 'reader']

export default function RoleAssignmentsPage() {
  const { user: me } = useAuth()
  const toast = useToast()
  const [items, setItems] = useState([])
  const [users, setUsers] = useState([])
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ project: null, user: null, role: { value: 'member', label: 'member' } })
  const [error, setError] = useState('')
  const [showDelete, setShowDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // A system admin may grant every role; a domain admin only inside its own domain and never `admin`.
  const isSystem = me?.role === 'admin'
  const allowed = isSystem || me?.domain_admin

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getRoleAssignments(), api.getUsers(), api.getProjects()])
      .then(([a, u, p]) => {
        if (a.status === 'fulfilled') setItems(a.value?.items || [])
        else toast(a.reason?.message || 'Failed to load role assignments', 'error')
        if (u.status === 'fulfilled') setUsers(u.value?.items || [])
        if (p.status === 'fulfilled') setProjects(p.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [toast])

  useEffect(() => { if (allowed) load() }, [allowed, load])

  if (!allowed) {
    return (
      <ContentLayout header={<Header variant="h1">Role assignments</Header>}>
        <Alert type="error" header="Administrator access required">
          Only system admins and domain admins can view and change role assignments.
        </Alert>
      </ContentLayout>
    )
  }

  const userName = id => users.find(u => u.id === id)?.username || id
  const projectName = id => projects.find(p => p.id === id)?.name || id
  const rows = items.map(a => ({ ...a, key: `${a.project_id}|${a.user_id}|${a.role}`, username: userName(a.user_id), project: projectName(a.project_id) }))
  const q = filter.trim().toLowerCase()
  const filtered = q ? rows.filter(r => r.username?.toLowerCase().includes(q) || r.project?.toLowerCase().includes(q) || r.role.includes(q)) : rows
  const roleOptions = ROLES.filter(r => isSystem || r !== 'admin').map(r => ({ value: r, label: r }))

  const handleAdd = async () => {
    if (!form.project || !form.user) { setError('Project and user are required.'); return }
    setSaving(true); setError('')
    try {
      await api.assignRole(form.project.value, form.user.value, form.role.value)
      toast('Role assigned', 'success')
      setShowAdd(false)
      setForm(f => ({ ...f, project: null, user: null }))
      load()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    for (const r of selected) {
      try { await api.unassignRole(r.project_id, r.user_id, r.role) } catch (e) { toast(e.message, 'error') }
    }
    setDeleting(false); setShowDelete(false); setSelected([])
    load()
  }

  return (
    <ContentLayout
      header={
        <Header variant="h1" description="Which user holds which role in which project">
          Role assignments
        </Header>
      }
    >
      <Table
        variant="full-page"
        stickyHeader
        selectionType="multi"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="key"
        header={
          <Header
            variant="h2"
            counter={counterText(rows.length, selected.length)}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={load} ariaLabel="Refresh role assignments" />
                <Button disabled={!selected.length} onClick={() => setShowDelete(true)}>Remove</Button>
                <Button variant="primary" onClick={() => { setError(''); setShowAdd(true) }}>Assign role</Button>
              </SpaceBetween>
            }
          >
            Assignments
          </Header>
        }
        columnDefinitions={[
          { id: 'user', header: 'User', cell: r => <Box fontWeight="bold">{r.username}</Box>, sortingField: 'username' },
          { id: 'project', header: 'Project', cell: r => r.project, sortingField: 'project' },
          { id: 'role', header: 'Role', cell: r => r.role, sortingField: 'role' },
        ]}
        items={filtered}
        loading={loading}
        loadingText="Loading role assignments"
        filter={
          <TextFilter filteringText={filter} onChange={({ detail }) => setFilter(detail.filteringText)} filteringPlaceholder="Filter by user, project or role" />
        }
        empty={
          filter
            ? <TableNoMatch onClear={() => setFilter('')} />
            : <TableEmpty title="No role assignments" description="Assign a role to give a user access to another project." />
        }
      />

      {showAdd && (
        <Modal
          visible
          onDismiss={() => setShowAdd(false)}
          header="Assign role"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={() => setShowAdd(false)}>Cancel</Button>
                <Button variant="primary" loading={saving} onClick={handleAdd}>Assign</Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Project" description="A user can only be assigned to a project of its own domain.">
              <Select
                selectedOption={form.project}
                onChange={({ detail }) => setForm(f => ({ ...f, project: detail.selectedOption }))}
                options={projects.map(p => ({ value: p.id, label: p.name }))}
                placeholder="Select project"
                filteringType="auto"
              />
            </FormField>
            <FormField label="User">
              <Select
                selectedOption={form.user}
                onChange={({ detail }) => setForm(f => ({ ...f, user: detail.selectedOption }))}
                options={users.map(u => ({ value: u.id, label: u.username }))}
                placeholder="Select user"
                filteringType="auto"
              />
            </FormField>
            <FormField label="Role">
              <Select
                selectedOption={form.role}
                onChange={({ detail }) => setForm(f => ({ ...f, role: detail.selectedOption }))}
                options={roleOptions}
              />
            </FormField>
          </SpaceBetween>
        </Modal>
      )}

      {showDelete && (
        <DeleteModal
          visible
          itemCount={selected.length}
          itemName={selected.length === 1 ? `${selected[0].username} / ${selected[0].role}` : undefined}
          title="Remove role assignment"
          confirmButtonText="Remove"
          loading={deleting}
          onDismiss={() => setShowDelete(false)}
          onConfirm={handleDelete}
        />
      )}
    </ContentLayout>
  )
}
