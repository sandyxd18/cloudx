/**
 * Create-in-place modals for the Launch instance form. They only create the
 * resource; the form's refresh button reloads the dropdown afterwards.
 */
import { useState } from 'react'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import Modal from '@cloudscape-design/components/modal'
import RadioGroup from '@cloudscape-design/components/radio-group'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import { downloadTextFile } from './FormPage.jsx'

function QuickModal({ title, submitText, onClose, onSubmit, children }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setLoading(true); setError('')
    try {
      await onSubmit()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      visible
      onDismiss={onClose}
      header={title}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onClose}>Cancel</Button>
            <Button variant="primary" loading={loading} onClick={submit}>{submitText}</Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}
        {children}
      </SpaceBetween>
    </Modal>
  )
}

export function KeypairModal({ onClose }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [keyType, setKeyType] = useState('rsa')

  const create = async () => {
    const n = name.trim()
    if (!n) throw new Error('Name is required.')
    const res = await api.createKeypair({ name: n, key_type: keyType })
    if (res?.private_key) downloadTextFile(`${n}.pem`, res.private_key)
    toast(`Key pair "${n}" created. Click refresh next to the key pair list.`, 'success')
  }

  return (
    <QuickModal title="Create key pair" submitText="Create key pair" onClose={onClose} onSubmit={create}>
      <FormField label="Name">
        <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-key-pair" autoFocus />
      </FormField>
      <FormField label="Key pair type" description="The private key (.pem) is downloaded once, right after creation.">
        <RadioGroup
          value={keyType}
          onChange={({ detail }) => setKeyType(detail.value)}
          items={[{ value: 'rsa', label: 'RSA' }, { value: 'ed25519', label: 'ED25519' }]}
        />
      </FormField>
    </QuickModal>
  )
}

export function SecurityGroupModal({ onClose }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const create = async () => {
    const n = name.trim()
    if (!n) throw new Error('Name is required.')
    await api.createSecurityGroup({ name: n, description })
    toast(`Security group "${n}" created. Click refresh next to the security group list.`, 'success')
  }

  return (
    <QuickModal title="Create security group" submitText="Create security group" onClose={onClose} onSubmit={create}>
      <FormField label="Security group name">
        <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="web-servers" autoFocus />
      </FormField>
      <FormField label="Description">
        <Input value={description} onChange={({ detail }) => setDescription(detail.value)} placeholder="Allows HTTP and SSH" />
      </FormField>
    </QuickModal>
  )
}
