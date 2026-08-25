import { useEffect, useState, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import Modal from '@cloudscape-design/components/modal'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import Tabs from '@cloudscape-design/components/tabs'
import TextFilter from '@cloudscape-design/components/text-filter'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Select from '@cloudscape-design/components/select'
import Badge from '@cloudscape-design/components/badge'
import CopyToClipboard from '@cloudscape-design/components/copy-to-clipboard'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { BoolStatus, TableEmpty, TableNoMatch, fmtDate, shortId, counterText } from '../components/common.jsx'

const ROLES = [
  { value: 'member', label: 'member' },
  { value: 'admin',  label: 'admin' },
]

// ── Create project ───────────────────────────────────────────────────────────
function ProjectModal({ onDismiss, onCreate }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    if (!name) { setError('Project name is required.'); return }
    setLoading(true); setError('')
    try { await onCreate({ name, description: desc }); onDismiss() }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create project"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="project-create-submit">Create project</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Project name">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-project" controlId="project-name" />
          </FormField>
          <FormField label="Description">
            <Input value={desc} onChange={({ detail }) => setDesc(detail.value)} placeholder="Optional description" controlId="project-desc" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Create user ──────────────────────────────────────────────────────────────
function UserModal({ onDismiss, onCreate, projects }) {
  const [form, setForm] = useState({ username: '', email: '', password: '', role: ROLES[0], project: null })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (projects.length && !form.project) {
      setForm(f => ({ ...f, project: { value: projects[0].id, label: projects[0].name } }))
    }
  }, [projects, form.project])

  const submit = async () => {
    if (!form.username || !form.email || !form.password) { setError('Username, email, and password are required.'); return }
    if (!form.project) { setError('Select a project.'); return }
    setLoading(true); setError('')
    try {
      await onCreate({
        username: form.username,
        email: form.email,
        password: form.password,
        role: form.role.value,
        project_id: form.project.value,
      })
      onDismiss()
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Create user"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="user-create-submit">Create user</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Username">
            <Input value={form.username} onChange={({ detail }) => setForm(f => ({ ...f, username: detail.value }))} placeholder="jane.doe" controlId="user-username" />
          </FormField>
          <FormField label="Email">
            <Input value={form.email} onChange={({ detail }) => setForm(f => ({ ...f, email: detail.value }))} type="email" placeholder="jane@example.com" controlId="user-email" />
          </FormField>
          <FormField label="Password">
            <Input value={form.password} onChange={({ detail }) => setForm(f => ({ ...f, password: detail.value }))} type="password" controlId="user-password" />
          </FormField>
          <FormField label="Role">
            <Select selectedOption={form.role} onChange={({ detail }) => setForm(f => ({ ...f, role: detail.selectedOption }))} options={ROLES} controlId="user-role" />
          </FormField>
          <FormField label="Project">
            <Select
              selectedOption={form.project}
              onChange={({ detail }) => setForm(f => ({ ...f, project: detail.selectedOption }))}
              options={projects.map(p => ({ value: p.id, label: p.name }))}
              placeholder="Choose a project"
              empty="No projects"
              controlId="user-project"
            />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Generate access key ──────────────────────────────────────────────────────
function AccessKeyModal({ onDismiss, onGenerate }) {
  const [name, setName] = useState('default')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  const submit = async () => {
    setLoading(true); setError('')
    try { setResult(await onGenerate({ name })) }
    catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  if (result) {
    return (
      <Modal
        visible
        onDismiss={onDismiss}
        header="Access key generated"
        footer={<Box float="right"><Button variant="primary" onClick={onDismiss}>Done</Button></Box>}
      >
        <SpaceBetween size="m">
          <Alert type="warning" header="Save the secret access key now">
            The secret access key is shown only once and cannot be retrieved later.
          </Alert>
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'Access key ID',
                value: (
                  <CopyToClipboard
                    variant="inline"
                    textToCopy={result.access_key_id}
                    copyButtonAriaLabel="Copy access key ID"
                    copySuccessText="Access key ID copied"
                    copyErrorText="Could not copy access key ID"
                  />
                ),
              },
              {
                label: 'Secret access key',
                value: (
                  <CopyToClipboard
                    variant="inline"
                    textToCopy={result.secret_access_key}
                    copyButtonAriaLabel="Copy secret access key"
                    copySuccessText="Secret access key copied"
                    copyErrorText="Could not copy secret access key"
                  />
                ),
              },
            ]}
          />
        </SpaceBetween>
      </Modal>
    )
  }

  return (
    <Modal
      visible
      onDismiss={onDismiss}
      header="Generate access key"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit} data-testid="key-generate-submit">Generate key</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <Form>
        <SpaceBetween size="m">
          {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}
          <FormField label="Key name" description='A friendly label, for example "cli" or "ci-pipeline".'>
            <Input value={name} onChange={({ detail }) => setName(detail.value)} controlId="key-name" />
          </FormField>
        </SpaceBetween>
      </Form>
    </Modal>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function AdminPage() {
  const toast = useToast()
  const [projects, setProjects] = useState([])
  const [users, setUsers]       = useState([])
  const [keys, setKeys]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [tab, setTab]           = useState('projects')
  const [filter, setFilter]     = useState('')
  const [selProjects, setSelProjects] = useState([])
  const [selUsers, setSelUsers] = useState([])
  const [selKeys, setSelKeys]   = useState([])
  const [showProject, setShowProject] = useState(false)
  const [showUser, setShowUser] = useState(false)
  const [showKey, setShowKey]   = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.allSettled([api.getProjects(), api.getUsers(), api.getAccessKeys()])
      .then(([p, u, k]) => {
        if (p.status === 'fulfilled') setProjects(p.value?.items || [])
        if (u.status === 'fulfilled') setUsers(u.value?.items || [])
        if (k.status === 'fulfilled') setKeys(k.value?.items || [])
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const q = filter.trim().toLowerCase()

  const deleteProjects = async () => {
    if (!confirm(`Delete ${selProjects.length} project(s)?`)) return
    for (const p of selProjects) {
      try { await api.deleteProject(p.id) } catch (e) { toast(`${p.name}: ${e.message}`, 'error') }
    }
    setSelProjects([]); load()
  }

  const deleteUsers = async () => {
    if (!confirm(`Delete ${selUsers.length} user(s)?`)) return
    for (const u of selUsers) {
      try { await api.deleteUser(u.id) } catch (e) { toast(`${u.username}: ${e.message}`, 'error') }
    }
    setSelUsers([]); load()
  }

  const revokeKeys = async () => {
    if (!confirm(`Revoke ${selKeys.length} access key(s)?`)) return
    for (const k of selKeys) {
      try { await api.deleteAccessKey(k.id) } catch (e) { toast(e.message, 'error') }
    }
    setSelKeys([]); load()
  }

  const filteredProjects = q ? projects.filter(p => p.name?.toLowerCase().includes(q)) : projects
  const filteredUsers = q ? users.filter(u => u.username?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q)) : users
  const filteredKeys = q ? keys.filter(k => k.name?.toLowerCase().includes(q) || k.access_key_id?.toLowerCase().includes(q)) : keys

  const searchFilter = (label) => (
    <TextFilter
      filteringText={filter}
      filteringPlaceholder={`Find ${label}`}
      filteringAriaLabel={`Filter ${label}`}
      onChange={({ detail }) => setFilter(detail.filteringText)}
    />
  )

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Identity administration — projects, users, and programmatic credentials"
          actions={<Button iconName="refresh" onClick={load} ariaLabel="Refresh" />}
        >
          Administration
        </Header>
      }
    >
      <Tabs
        activeTabId={tab}
        onChange={({ detail }) => { setTab(detail.activeTabId); setFilter('') }}
        tabs={[
          {
            id: 'projects',
            label: `Projects (${projects.length})`,
            content: (
              <Table
                variant="container"
                loading={loading}
                loadingText="Loading projects"
                selectionType="multi"
                selectedItems={selProjects}
                onSelectionChange={({ detail }) => setSelProjects(detail.selectedItems)}
                items={filteredProjects}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(projects.length, selProjects.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button disabled={!selProjects.length} onClick={deleteProjects}>Delete</Button>
                        <Button variant="primary" onClick={() => setShowProject(true)} data-testid="create-project-btn">Create project</Button>
                      </SpaceBetween>
                    }
                  >
                    Projects
                  </Header>
                }
                columnDefinitions={[
                  {
                    id: 'name',
                    header: 'Project name',
                    cell: p => (
                      <SpaceBetween size="xxxs">
                        <Box fontWeight="bold">{p.name}</Box>
                        <Box variant="small" color="text-body-secondary">{shortId(p.id, 16)}</Box>
                      </SpaceBetween>
                    ),
                    isRowHeader: true,
                  },
                  { id: 'description', header: 'Description', cell: p => p.description || '—' },
                  { id: 'enabled', header: 'Status', cell: p => <BoolStatus value={p.enabled !== false} trueText="Enabled" falseText="Disabled" /> },
                  { id: 'users', header: 'Users', cell: p => users.filter(u => u.project_id === p.id).length },
                ]}
                filter={searchFilter('projects')}
                empty={q ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No projects" description="Create a project to scope resources and users." action={<Button variant="primary" onClick={() => setShowProject(true)}>Create project</Button>} />}
              />
            ),
          },
          {
            id: 'users',
            label: `Users (${users.length})`,
            content: (
              <Table
                variant="container"
                loading={loading}
                loadingText="Loading users"
                selectionType="multi"
                selectedItems={selUsers}
                onSelectionChange={({ detail }) => setSelUsers(detail.selectedItems)}
                items={filteredUsers}
                trackBy="id"
                header={
                  <Header
                    variant="h2"
                    counter={counterText(users.length, selUsers.length)}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button disabled={!selUsers.length} onClick={deleteUsers}>Delete</Button>
                        <Button variant="primary" onClick={() => setShowUser(true)} data-testid="create-user-btn">Create user</Button>
                      </SpaceBetween>
                    }
                  >
                    Users
                  </Header>
                }
                columnDefinitions={[
                  { id: 'username', header: 'Username', cell: u => <Box fontWeight="bold">{u.username}</Box>, isRowHeader: true },
                  { id: 'email', header: 'Email', cell: u => u.email || '—' },
                  { id: 'project', header: 'Project', cell: u => projects.find(p => p.id === u.project_id)?.name || shortId(u.project_id, 12) },
                  { id: 'role', header: 'Role', cell: u => <Badge color={u.role === 'admin' ? 'red' : 'blue'}>{u.role}</Badge> },
                  { id: 'enabled', header: 'Status', cell: u => <BoolStatus value={u.enabled !== false} trueText="Active" falseText="Disabled" /> },
                  { id: 'created', header: 'Created', cell: u => fmtDate(u.created_at) },
                ]}
                filter={searchFilter('users')}
                empty={q ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No users" description="Create a user to grant access to a project." action={<Button variant="primary" onClick={() => setShowUser(true)}>Create user</Button>} />}
              />
            ),
          },
          {
            id: 'keys',
            label: `Access keys (${keys.length})`,
            content: (
              <SpaceBetween size="l">
                <Alert type="info">
                  Access keys authenticate the CLI and API without a password. Generate a separate key per user or
                  automation pipeline so it can be revoked independently.
                </Alert>
                <Table
                  variant="container"
                  loading={loading}
                  loadingText="Loading access keys"
                  selectionType="multi"
                  selectedItems={selKeys}
                  onSelectionChange={({ detail }) => setSelKeys(detail.selectedItems)}
                  items={filteredKeys}
                  trackBy="id"
                  header={
                    <Header
                      variant="h2"
                      counter={counterText(keys.length, selKeys.length)}
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button disabled={!selKeys.length} onClick={revokeKeys}>Revoke</Button>
                          <Button variant="primary" onClick={() => setShowKey(true)} data-testid="generate-key-btn">Generate access key</Button>
                        </SpaceBetween>
                      }
                    >
                      Access keys
                    </Header>
                  }
                  columnDefinitions={[
                    { id: 'name', header: 'Name', cell: k => <Box fontWeight="bold">{k.name || 'default'}</Box>, isRowHeader: true },
                    {
                      id: 'access_key_id',
                      header: 'Access key ID',
                      cell: k => (
                        <CopyToClipboard
                          variant="inline"
                          textToCopy={k.access_key_id}
                          copyButtonAriaLabel="Copy access key ID"
                          copySuccessText="Access key ID copied"
                          copyErrorText="Could not copy access key ID"
                        />
                      ),
                    },
                    { id: 'owner', header: 'Owner', cell: k => users.find(u => u.id === k.user_id)?.username || shortId(k.user_id, 12) },
                    { id: 'enabled', header: 'Status', cell: k => <BoolStatus value={k.enabled} trueText="Active" falseText="Disabled" /> },
                    { id: 'created', header: 'Created', cell: k => fmtDate(k.created_at) },
                  ]}
                  filter={searchFilter('access keys')}
                  empty={q ? <TableNoMatch onClear={() => setFilter('')} /> : <TableEmpty title="No access keys" description="Generate an access key to authenticate the CLI without a password." action={<Button variant="primary" onClick={() => setShowKey(true)}>Generate access key</Button>} />}
                />
              </SpaceBetween>
            ),
          },
        ]}
      />

      {showProject && (
        <ProjectModal
          onDismiss={() => setShowProject(false)}
          onCreate={async (b) => { await api.createProject(b); toast('Project created', 'success'); load() }}
        />
      )}
      {showUser && (
        <UserModal
          projects={projects}
          onDismiss={() => setShowUser(false)}
          onCreate={async (b) => { await api.createUser(b); toast('User created', 'success'); load() }}
        />
      )}
      {showKey && (
        <AccessKeyModal
          onDismiss={() => { setShowKey(false); load() }}
          onGenerate={(b) => api.createAccessKey(b)}
        />
      )}
    </ContentLayout>
  )
}
