import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import RadioGroup from '@cloudscape-design/components/radio-group'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage, { downloadTextFile } from '../components/FormPage.jsx'

const KEY_TYPES = [
  { value: 'rsa',     label: 'RSA' },
  { value: 'ed25519', label: 'ED25519' },
]

const FILE_FORMATS = [
  { value: 'pem', label: '.pem', description: 'For use with OpenSSH' },
  { value: 'ppk', label: '.ppk', description: 'For use with PuTTY' },
]

export default function KeypairCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')
  const [keyType, setKeyType] = useState('rsa')
  const [fileFormat, setFileFormat] = useState('pem')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/keypairs')

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    setLoading(true); setError('')
    try {
      const result = await api.createKeypair({ name: name.trim(), key_type: keyType })
      const privateKey = result?.private_key
      if (privateKey) {
        downloadTextFile(`${name.trim()}.${fileFormat}`, privateKey)
      }
      toast(`Key pair "${name.trim()}" created`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Create key pair"
      description="Key pairs allow you to securely connect to your instances via SSH."
      error={error}
      loading={loading}
      submitText="Create key pair"
      submitTestId="keypair-create-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Name" description="A descriptive name for the key pair.">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-key-pair" controlId="keypair-name" autoFocus />
          </FormField>

          <FormField label="Key pair type">
            <RadioGroup
              value={keyType}
              onChange={({ detail }) => setKeyType(detail.value)}
              items={KEY_TYPES}
            />
          </FormField>

          <FormField label="Private key file format" description="If you plan to use PuTTY to connect, choose .ppk. Otherwise choose .pem.">
            <RadioGroup
              value={fileFormat}
              onChange={({ detail }) => setFileFormat(detail.value)}
              items={FILE_FORMATS}
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
