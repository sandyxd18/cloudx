import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Container from '@cloudscape-design/components/container'
import FormField from '@cloudscape-design/components/form-field'
import Select from '@cloudscape-design/components/select'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { api } from '../api.js'
import { useToast } from '../context.jsx'
import FormPage from '../components/FormPage.jsx'

export default function FloatingIPAssociatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [searchParams] = useSearchParams()
  const fipId = searchParams.get('id')

  const [fip, setFip] = useState(null)
  const [servers, setServers] = useState([])
  const [server, setServer] = useState(null)
  const [routers, setRouters] = useState([])
  const [router, setRouter] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.allSettled([api.getFloatingIPs(), api.getServers(), api.getRouters()])
      .then(([f, s, r]) => {
        if (f.status === 'fulfilled') {
          const list = f.value?.items || []
          const match = list.find(item => item.id === fipId) || list[0]
          setFip(match || null)
        }
        if (s.status === 'fulfilled') setServers(s.value?.items || [])
        if (r.status === 'fulfilled') {
          const rList = r.value?.items || []
          setRouters(rList)
          if (rList.length === 1) setRouter({ value: rList[0].name, label: rList[0].name })
        }
      })
  }, [fipId])

  const back = () => navigate('/floating-ips')

  const submit = async () => {
    if (!fip) { setError('No floating IP selected.'); return }
    if (!server) { setError('Select a server instance.'); return }
    if (!router) { setError('Select a router.'); return }
    setLoading(true); setError('')
    try {
      await api.associateFloatingIP(fip.id, { server: server.value, router: router.value })
      toast(`Floating IP ${fip.floating_ip_address || fip.floating_ip} associated`, 'success')
      back()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <FormPage
      title="Associate floating IP"
      description="Program OVN DNAT forwarding to route public traffic to an instance."
      error={error}
      loading={loading}
      submitText="Associate floating IP"
      submitTestId="fip-assoc-submit"
      submitDisabled={!fip}
      onCancel={back}
      onSubmit={submit}
    >
      <Container>
        <SpaceBetween size="l">
          {fip && (
            <Alert type="info">
              Floating IP: <b>{fip.floating_ip_address || fip.floating_ip}</b>
            </Alert>
          )}

          <FormField label="Instance" description="The server instance to receive the floating IP.">
            <Select
              selectedOption={server}
              onChange={({ detail }) => setServer(detail.selectedOption)}
              options={servers.map(s => ({ value: s.name, label: s.name, description: `Fixed IP: ${s.ip_address || '—'}` }))}
              placeholder="Choose an instance"
              empty="No instances"
              controlId="fip-assoc-server"
            />
          </FormField>

          <FormField label="Router" description="The router connecting the private and external networks.">
            <Select
              selectedOption={router}
              onChange={({ detail }) => setRouter(detail.selectedOption)}
              options={routers.map(r => ({ value: r.name, label: r.name }))}
              placeholder="Choose a router"
              empty="No routers"
              controlId="fip-assoc-router"
            />
          </FormField>
        </SpaceBetween>
      </Container>
    </FormPage>
  )
}
