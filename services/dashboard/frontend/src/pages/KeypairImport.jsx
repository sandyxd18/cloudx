import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Input from '@cloudscape-design/components/input'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Textarea from '@cloudscape-design/components/textarea'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function KeypairImportPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const fileInputRef = useRef(null)
  const [name, setName] = useState('')
  const [publicKey, setPublicKey] = useState('')
  const [fileName, setFileName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const back = () => navigate('/keypairs')

  const browseFile = () => fileInputRef.current?.click()

  const handleFileSelected = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    if (!name) setName(file.name.replace(/\.pub$/i, ''))
    const reader = new FileReader()
    reader.onload = () => setPublicKey(String(reader.result || '').trim())
    reader.readAsText(file)
  }

  const submit = async () => {
    if (!name.trim()) { setError('Name is required.'); return }
    if (!publicKey.trim()) { setError('Public key content is required.'); return }
    setLoading(true); setError('')
    try {
      await api.importKeypair({ name: name.trim(), public_key: publicKey.trim() })
      toast(`Key pair "${name.trim()}" imported`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Import key pair"
      description="Import the public key portion of a key pair you already own."
      error={error}
      loading={loading}
      submitText="Import key pair"
      submitTestId="keypair-import-submit"
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          <FormField label="Name" description="A descriptive name for the key pair.">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-imported-key" controlId="keypair-import-name" autoFocus />
          </FormField>

          <FormField label="Key pair file" description="Select a public key file from your computer (.pub).">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={browseFile}>Browse</Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pub,.txt,text/plain"
                style={{ display: 'none' }}
                onChange={handleFileSelected}
              />
              {fileName && <Button variant="link" disabled>{fileName}</Button>}
            </SpaceBetween>
          </FormField>

          <FormField label="Public key content" description="Contents of id_rsa.pub or id_ed25519.pub — paste directly, or use Browse above.">
            <Textarea
              value={publicKey}
              onChange={({ detail }) => setPublicKey(detail.value)}
              placeholder="ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB..."
              rows={8}
              controlId="keypair-import-content"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
