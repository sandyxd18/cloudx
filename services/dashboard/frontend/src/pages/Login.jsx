import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import Form from '@cloudscape-design/components/form'
import FormField from '@cloudscape-design/components/form-field'
import Header from '@cloudscape-design/components/header'
import Input from '@cloudscape-design/components/input'
import Alert from '@cloudscape-design/components/alert'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useAuth, useToast } from '../context.jsx'

export default function LoginPage() {
  const [form, setForm] = useState({ username: '', password: '', project: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const { checkSession } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()

  useEffect(() => {
    toast.clear?.()
  }, [toast])

  const handleSubmit = async (e) => {
    e?.preventDefault?.()
    setError('')
    toast.clear?.()
    setLoading(true)
    try {
      await api.login({
        username: form.username,
        password: form.password,
        project: form.project?.trim() || undefined,
      })
      await checkSession()
      toast('Signed in successfully', 'success')
      navigate('/')
    } catch (err) {
      setError(err.message || 'Sign-in failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Box padding={{ top: 'xxxl' }}>
      <Box margin={{ top: 'xxl' }} padding="l">
        <div style={{ maxWidth: 420, margin: '0 auto' }}>
          <SpaceBetween size="l">
            <Box textAlign="center">
              <Box variant="h1">CloudX Console</Box>
              <Box variant="p" color="text-body-secondary">Private cloud management</Box>
            </Box>

            <Container header={<Header variant="h2">Sign in</Header>}>
              <form onSubmit={handleSubmit}>
                <Form
                  actions={
                    <Button variant="primary" loading={loading} formAction="submit" data-testid="login-submit">
                      {loading ? 'Signing in' : 'Sign in'}
                    </Button>
                  }
                >
                  <SpaceBetween size="m">
                    {error && <Alert type="error" statusIconAriaLabel="Error">{error}</Alert>}

                    <FormField label="Username">
                      <Input
                        value={form.username}
                        onChange={({ detail }) => setForm(f => ({ ...f, username: detail.value }))}
                        placeholder="admin"
                        autoFocus
                        controlId="login-username"
                      />
                    </FormField>

                    <FormField label="Password">
                      <Input
                        type="password"
                        value={form.password}
                        onChange={({ detail }) => setForm(f => ({ ...f, password: detail.value }))}
                        placeholder="••••••••"
                        controlId="login-password"
                      />
                    </FormField>

                    <FormField label="Project" description="Optional — defaults to your primary project scope">
                      <Input
                        value={form.project}
                        onChange={({ detail }) => setForm(f => ({ ...f, project: detail.value }))}
                        placeholder="default"
                        controlId="login-project"
                      />
                    </FormField>
                  </SpaceBetween>
                </Form>
              </form>
            </Container>

            <Box textAlign="center" color="text-body-secondary" fontSize="body-s">
              CloudX private cloud dashboard
            </Box>
          </SpaceBetween>
        </div>
      </Box>
    </Box>
  )
}
