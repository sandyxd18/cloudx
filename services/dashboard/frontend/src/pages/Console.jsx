import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import Alert from '@cloudscape-design/components/alert'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import Container from '@cloudscape-design/components/container'
import ContentLayout from '@cloudscape-design/components/content-layout'
import Header from '@cloudscape-design/components/header'
import SpaceBetween from '@cloudscape-design/components/space-between'
import Spinner from '@cloudscape-design/components/spinner'
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs'
import { api } from '../api.js'
import { ComputeStatus } from '../components/common.jsx'

export default function ConsolePage() {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const serverName = searchParams.get('name') || id

  const [consoleUrl, setConsoleUrl] = useState(null)
  const [serverInfo, setServerInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const containerRef = useRef(null)

  const load = useCallback(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    setConsoleUrl(null)
    Promise.allSettled([api.getServer(id), api.getRemoteConsole(id)])
      .then(([serverRes, consoleRes]) => {
        if (serverRes.status === 'fulfilled') setServerInfo(serverRes.value)
        if (consoleRes.status === 'fulfilled') {
          const url = consoleRes.value?.remote_console?.url
          if (url) setConsoleUrl(url)
          else setError('Console URL not available. The instance must be ACTIVE and the noVNC proxy running.')
        } else {
          setError(consoleRes.reason?.message || 'Failed to obtain a console URL.')
        }
        setLoading(false)
      })
  }, [id])

  useEffect(() => { load() }, [load])

  const goFullscreen = () => containerRef.current?.requestFullscreen?.()

  const status = serverInfo?.status || 'UNKNOWN'

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description={`Browser-based VNC session for ${serverInfo?.name || serverName}`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => navigate('/servers')}>Back to instances</Button>
              <Button iconName="refresh" onClick={load} loading={loading}>Reconnect</Button>
              <Button variant="primary" onClick={goFullscreen} disabled={!consoleUrl}>Full screen</Button>
            </SpaceBetween>
          }
        >
          Instance console
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Container header={<Header variant="h2">Session</Header>}>
          <KeyValuePairs
            columns={3}
            items={[
              { label: 'Instance', value: serverInfo?.name || serverName },
              { label: 'State', value: <ComputeStatus status={status} /> },
              { label: 'Endpoint', value: consoleUrl ? new URL(consoleUrl).host : '—' },
            ]}
          />
        </Container>

        <Container header={<Header variant="h2">Console</Header>} disableContentPaddings>
          <div ref={containerRef}>
            {loading ? (
              <Box textAlign="center" padding="xxxl"><SpaceBetween size="s" alignItems="center"><Spinner size="large" /><Box>Connecting to console…</Box></SpaceBetween></Box>
            ) : error ? (
              <Box padding="l">
                <SpaceBetween size="m">
                  <Alert type="error" header="Console unavailable">{error}</Alert>
                  {status !== 'ACTIVE' && (
                    <Alert type="warning" header="Instance is not running">
                      The instance must be ACTIVE to open a console. Current state: {status}.
                    </Alert>
                  )}
                  <Button onClick={load}>Try again</Button>
                </SpaceBetween>
              </Box>
            ) : (
              <iframe
                src={consoleUrl}
                title={`Console — ${serverInfo?.name || serverName}`}
                className="console-frame console-frame-tall"
                allow="clipboard-read; clipboard-write; fullscreen"
              />
            )}
          </div>
        </Container>

        <Container header={<Header variant="h2">Tips</Header>}>
          <Box variant="ul">
            <li>Click inside the console to capture keyboard input.</li>
            <li>Use Ctrl+Alt+Delete to send a reset sequence to the guest.</li>
            <li>For shell access over SSH, associate a floating IP or use a jump host.</li>
            <li>The session is proxied over WebSocket through the noVNC proxy on port 6080.</li>
          </Box>
        </Container>
      </SpaceBetween>
    </ContentLayout>
  )
}
