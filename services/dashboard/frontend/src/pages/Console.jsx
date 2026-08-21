import { useState, useEffect, useRef } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Monitor, RefreshCw, Maximize2, AlertCircle, Loader2 } from 'lucide-react'
import { api } from '../api.js'

export default function ConsolePage() {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  // also allow passing server name via ?name=...
  const serverName = searchParams.get('name') || id

  const [consoleUrl, setConsoleUrl] = useState(null)
  const [serverInfo, setServerInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [fullscreen, setFullscreen] = useState(false)
  const iframeRef = useRef(null)
  const containerRef = useRef(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)

    // Fetch server info and console URL in parallel
    Promise.allSettled([
      api.getServer(id),
      api.getRemoteConsole(id),
    ]).then(([serverRes, consoleRes]) => {
      if (serverRes.status === 'fulfilled') {
        setServerInfo(serverRes.value)
      }
      if (consoleRes.status === 'fulfilled') {
        const url = consoleRes.value?.remote_console?.url
        if (url) {
          setConsoleUrl(url)
        } else {
          setError('Console URL not available. Make sure the instance is ACTIVE and noVNC proxy is running.')
        }
      } else {
        const detail = consoleRes.reason?.message || 'Failed to get console URL.'
        setError(detail)
      }
      setLoading(false)
    })
  }, [id])

  const handleRefresh = () => {
    setConsoleUrl(null)
    setLoading(true)
    setError(null)
    api.getRemoteConsole(id)
      .then(res => {
        const url = res?.remote_console?.url
        if (url) {
          setConsoleUrl(url)
        } else {
          setError('Console URL not returned by server.')
        }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  const handleFullscreen = () => {
    if (!fullscreen) {
      containerRef.current?.requestFullscreen?.()
      setFullscreen(true)
    } else {
      document.exitFullscreen?.()
      setFullscreen(false)
    }
  }

  const status = serverInfo?.status || 'UNKNOWN'
  const statusColor = {
    ACTIVE: '#10b981', SHUTOFF: '#6b7280', BUILD: '#f59e0b', ERROR: '#ef4444',
  }[status] || '#6b7280'

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Link to="/servers" style={{ display: 'flex', alignItems: 'center', color: 'var(--text-muted)', textDecoration: 'none' }}>
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Monitor size={20} style={{ color: '#6366f1' }} />
              Instance Console
            </h1>
            <p className="page-subtitle">
              {serverInfo?.name || serverName}
              {serverInfo && (
                <span style={{ marginLeft: 8, fontSize: 11, color: statusColor, background: statusColor + '22', padding: '1px 6px', borderRadius: 4 }}>
                  {status}
                </span>
              )}
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-secondary" onClick={handleRefresh} disabled={loading}>
            <RefreshCw size={14} style={loading ? { animation: 'spin 1s linear infinite' } : {}} />
            Reconnect
          </button>
          <button className="btn-secondary" onClick={handleFullscreen} title="Toggle fullscreen">
            <Maximize2 size={14} />
            Fullscreen
          </button>
        </div>
      </div>

      <div ref={containerRef} className="card" style={{ overflow: 'hidden', position: 'relative' }}>
        {/* Header bar */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 16px',
          background: 'var(--bg-elevated)',
          borderBottom: '1px solid var(--border)',
        }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#ef4444' }} />
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b' }} />
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#10b981' }} />
          <span style={{ flex: 1, textAlign: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
            {consoleUrl ? new URL(consoleUrl).host : 'noVNC — CloudX Console'}
          </span>
        </div>

        {/* Console area */}
        <div style={{
          background: '#0a0a0a',
          minHeight: 560,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
        }}>
          {loading && (
            <div style={{ textAlign: 'center', color: '#64748b' }}>
              <Loader2 size={36} style={{ animation: 'spin 1s linear infinite', marginBottom: 12, color: '#6366f1' }} />
              <p style={{ fontSize: 13 }}>Connecting to console…</p>
            </div>
          )}

          {!loading && error && (
            <div style={{ textAlign: 'center', maxWidth: 420 }}>
              <AlertCircle size={40} style={{ color: '#ef4444', marginBottom: 12 }} />
              <p style={{ fontSize: 14, color: '#ef4444', marginBottom: 8 }}>Console Unavailable</p>
              <p style={{ fontSize: 12, color: '#64748b', marginBottom: 20, lineHeight: 1.6 }}>{error}</p>
              {status !== 'ACTIVE' && (
                <div style={{
                  background: '#f59e0b11', border: '1px solid #f59e0b44',
                  borderRadius: 8, padding: '10px 16px', marginBottom: 16,
                }}>
                  <p style={{ fontSize: 12, color: '#f59e0b' }}>
                    ⚠ Instance must be ACTIVE to open a console (current: <strong>{status}</strong>)
                  </p>
                </div>
              )}
              <button className="btn-secondary" onClick={handleRefresh} style={{ fontSize: 12 }}>
                <RefreshCw size={12} /> Try Again
              </button>
            </div>
          )}

          {!loading && !error && consoleUrl && (
            <iframe
              ref={iframeRef}
              src={consoleUrl}
              title={`Console: ${serverInfo?.name || serverName}`}
              style={{
                width: '100%',
                height: 560,
                border: 'none',
                display: 'block',
              }}
              allow="clipboard-read; clipboard-write"
            />
          )}
        </div>

        {/* Footer info */}
        {consoleUrl && (
          <div style={{
            padding: '8px 16px',
            background: 'var(--bg-elevated)',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981' }} />
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Connected via noVNC WebSocket proxy (port 6080)</span>
          </div>
        )}
      </div>

      {/* Tips */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-header">
          <span className="card-title">Console Tips</span>
        </div>
        <div className="card-body">
          <ul style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 2, paddingLeft: 16 }}>
            <li>Click inside the console to capture keyboard input.</li>
            <li>Use <kbd style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', padding: '0 4px', borderRadius: 3 }}>Ctrl+Alt+Delete</kbd> to send a reset sequence.</li>
            <li>For SSH access, use a floating IP or configure a jump host.</li>
            <li>The console connects via WebSocket to the noVNC proxy on port 6080.</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
