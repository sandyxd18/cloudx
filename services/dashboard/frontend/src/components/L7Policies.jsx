import { useState, useEffect, useCallback } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Checkbox from '@cloudscape-design/components/checkbox'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Table from '@cloudscape-design/components/table'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { TableEmpty } from './common.jsx'

const opts = a => a.map(v => ({ value: v, label: v }))
const ACTIONS = opts(['REDIRECT_TO_POOL', 'REDIRECT_TO_URL', 'REDIRECT_PREFIX', 'REJECT'])
const TYPES = opts(['PATH', 'HOST_NAME', 'FILE_TYPE', 'HEADER', 'COOKIE'])
const COMPARES = opts(['STARTS_WITH', 'ENDS_WITH', 'EQUAL_TO', 'CONTAINS', 'REGEX'])
const TARGET = { REDIRECT_TO_POOL: 'redirect_pool_id', REDIRECT_TO_URL: 'redirect_url', REDIRECT_PREFIX: 'redirect_prefix' }
const P0 = { name: '', action: ACTIONS[0], target: '', code: '', position: '1' }
const R0 = { type: TYPES[0], compare: COMPARES[0], value: '', key: '', invert: false }

export default function L7Policies({ listenerId }) {
  const toast = useToast()
  const [policies, setPolicies] = useState([])
  const [loading, setLoading] = useState(true)
  const [pform, setPform] = useState(null)
  const [rform, setRform] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api.getL7Policies(listenerId).then(d => setPolicies(d?.l7policies || []))
      .catch(e => toast(e.message || 'Failed to load L7 policies', 'error'))
      .finally(() => setLoading(false))
  }, [listenerId, toast])

  useEffect(() => { load() }, [load])

  const run = async fn => {
    setError('')
    try { await fn(); setPform(null); setRform(null); load() } catch (e) { setError(e.message); toast(e.message, 'error') }
  }

  const savePolicy = () => run(() => {
    const f = pform
    const body = { name: f.name, action: f.action.value, position: Number(f.position) }
    if (TARGET[body.action]) body[TARGET[body.action]] = f.target
    if (f.code) body.redirect_http_code = Number(f.code)
    return f.id ? api.updateL7Policy(f.id, body) : api.createL7Policy(listenerId, body)
  })

  const editPolicy = p => {
    const action = ACTIONS.find(a => a.value === p.action) || ACTIONS[0]
    setError('')
    setPform({ id: p.id, name: p.name, action, position: String(p.position),
      target: p[TARGET[p.action]] || '', code: p.redirect_http_code ? String(p.redirect_http_code) : '' })
  }

  const createRule = () => run(() => {
    const f = rform
    const body = { type: f.type.value, compare_type: f.compare.value, value: f.value, invert: f.invert }
    if (f.key) body.key = f.key
    return api.createL7Rule(f.policyId, body)
  })

  const needsKey = rform && ['HEADER', 'COOKIE'].includes(rform.type.value)
  const redirectUrl = pform && ['REDIRECT_TO_URL', 'REDIRECT_PREFIX'].includes(pform.action.value)

  return (
    <SpaceBetween size="s">
      <Header variant="h3" counter={`(${policies.length})`}
        actions={<Button onClick={() => { setError(''); setPform(P0) }}>Add L7 policy</Button>}>
        L7 policies
      </Header>
      <Box variant="small">Rules inside one policy are AND-ed; a redirect or reject is applied before any pool routing.</Box>
      <Table
        loading={loading}
        loadingText="Loading L7 policies"
        items={policies}
        columnDefinitions={[
          { id: 'pos', header: '#', cell: p => p.position },
          { id: 'name', header: 'Name', cell: p => p.name },
          { id: 'action', header: 'Action', cell: p => p.action },
          { id: 'target', header: 'Target', cell: p => p.redirect_pool_id || p.redirect_url || p.redirect_prefix || '-' },
          { id: 'rules', header: 'Rules', cell: p => (
            <SpaceBetween size="xxs">
              {(p.rules || []).map(r => (
                <span key={r.id}>
                  {r.invert ? 'NOT ' : ''}{r.type}{r.key ? `[${r.key}]` : ''} {r.compare_type} {r.value}{' '}
                  <Button variant="inline-link" onClick={() => run(() => api.deleteL7Rule(p.id, r.id))}>remove</Button>
                </span>
              ))}
              {!(p.rules || []).length && '-'}
            </SpaceBetween>
          )},
          { id: 'on', header: 'Enabled', cell: p => (
            <Checkbox checked={p.admin_state_up} onChange={({ detail }) => run(() => api.updateL7Policy(p.id, { admin_state_up: detail.checked }))} />
          )},
          { id: 'act', header: 'Actions', cell: p => (
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => editPolicy(p)}>Edit</Button>
              <Button onClick={() => { setError(''); setRform({ ...R0, policyId: p.id }) }}>Add rule</Button>
              <Button onClick={() => run(() => api.deleteL7Policy(p.id))}>Delete</Button>
            </SpaceBetween>
          )},
        ]}
        empty={<TableEmpty title="No L7 policies" />}
      />

      {pform && (
        <Modal visible onDismiss={() => setPform(null)} header={pform.id ? 'Edit L7 policy' : 'Add L7 policy'}
          footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setPform(null)}>Cancel</Button>
            <Button variant="primary" disabled={!pform.name} onClick={savePolicy}>{pform.id ? 'Save' : 'Create'}</Button>
          </SpaceBetween></Box>}>
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Name"><Input value={pform.name} onChange={({ detail }) => setPform(f => ({ ...f, name: detail.value }))} /></FormField>
            <FormField label="Action"><Select selectedOption={pform.action} options={ACTIONS} onChange={({ detail }) => setPform(f => ({ ...f, action: detail.selectedOption, target: '' }))} /></FormField>
            {TARGET[pform.action.value] && (
              <FormField label={pform.action.value === 'REDIRECT_TO_POOL' ? 'Pool ID' : 'URL (https://...)'}>
                <Input value={pform.target} onChange={({ detail }) => setPform(f => ({ ...f, target: detail.value }))} />
              </FormField>
            )}
            {redirectUrl && (
              <FormField label="HTTP code" description="301, 302 (default), 303, 307 or 308">
                <Input type="number" value={pform.code} onChange={({ detail }) => setPform(f => ({ ...f, code: detail.value }))} />
              </FormField>
            )}
            <FormField label="Position"><Input type="number" value={pform.position} onChange={({ detail }) => setPform(f => ({ ...f, position: detail.value }))} /></FormField>
          </SpaceBetween>
        </Modal>
      )}

      {rform && (
        <Modal visible onDismiss={() => setRform(null)} header="Add L7 rule"
          footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setRform(null)}>Cancel</Button>
            <Button variant="primary" disabled={!rform.value || (needsKey && !rform.key)} onClick={createRule}>Create</Button>
          </SpaceBetween></Box>}>
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Type"><Select selectedOption={rform.type} options={TYPES} onChange={({ detail }) => setRform(f => ({ ...f, type: detail.selectedOption }))} /></FormField>
            {needsKey && <FormField label="Key" description="Header or cookie name"><Input value={rform.key} onChange={({ detail }) => setRform(f => ({ ...f, key: detail.value }))} /></FormField>}
            <FormField label="Compare"><Select selectedOption={rform.compare} options={COMPARES} onChange={({ detail }) => setRform(f => ({ ...f, compare: detail.selectedOption }))} /></FormField>
            <FormField label="Value"><Input value={rform.value} onChange={({ detail }) => setRform(f => ({ ...f, value: detail.value }))} /></FormField>
            <Checkbox checked={rform.invert} onChange={({ detail }) => setRform(f => ({ ...f, invert: detail.checked }))}>Invert (match when the condition is false)</Checkbox>
          </SpaceBetween>
        </Modal>
      )}
    </SpaceBetween>
  )
}
