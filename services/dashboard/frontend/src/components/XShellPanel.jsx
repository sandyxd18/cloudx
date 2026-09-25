import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import Button from '@cloudscape-design/components/button'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { useXShell } from './XShellContext.jsx'

export default function XShellPanel() {
  const { open, setOpen, height, setHeight, mode, setMode } = useXShell()

  const terminalEl = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const wsRef = useRef(null)
  const [termStatus, setTermStatus] = useState('idle')
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const safeSetTermStatus = useCallback((s) => {
    if (mountedRef.current) setTermStatus(s)
  }, [])

  // Drag-to-resize handle
  const startDrag = useCallback((e) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = height
    const onMove = (ev) => {
      setHeight(startH + (startY - ev.clientY))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'row-resize'
  }, [height, setHeight])

  // Terminal lifecycle
  const spawnTerminal = useCallback(() => {
    const container = terminalEl.current
    if (!container) return

    // Clean up any previous terminal / websocket
    if (wsRef.current) {
      try { wsRef.current.close() } catch {}
      wsRef.current = null
    }
    if (termRef.current) {
      try { termRef.current.dispose() } catch {}
      termRef.current = null
    }
    if (fitRef.current) {
      fitRef.current = null
    }

    let term
    try {
      term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: '"JetBrains Mono", "Fira Code", Menlo, Consolas, monospace',
        theme: {
          background: '#0f1117',
          foreground: '#d1d5db',
          cursor: '#7aa2f7',
        },
        scrollback: 5000,
        allowProposedApi: true,
        rows: 24,
        cols: 80,
      })
    } catch (err) {
      console.error('XShell: failed to create Terminal', err)
      safeSetTermStatus('error')
      return
    }

    let fit
    try {
      fit = new FitAddon()
      term.loadAddon(fit)
      term.open(container)
    } catch (err) {
      console.error('XShell: failed to open terminal', err)
      try { term.dispose() } catch {}
      safeSetTermStatus('error')
      return
    }

    try {
      fit.fit()
    } catch {
      // container might not be sized yet — default 80x24 is fine
    }

    termRef.current = term
    fitRef.current = fit

    safeSetTermStatus('connecting')
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/xshell`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      safeSetTermStatus('ready')
      try { fit.fit() } catch {}
      try { term.focus() } catch {}
    }

    ws.onmessage = (e) => {
      try {
        if (e.data instanceof ArrayBuffer) {
          term.write(new Uint8Array(e.data))
        } else if (typeof e.data === 'string') {
          term.write(e.data)
        }
      } catch (err) {
        console.error('XShell: term.write failed', err)
      }
    }

    ws.onerror = () => safeSetTermStatus('error')
    ws.onclose = () => {
      if (mountedRef.current) {
        safeSetTermStatus('error')
      }
    }

    const dataListener = (data) => {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(data)
      } catch {}
    }
    term.onData(dataListener)

    let resizeObserver
    try {
      resizeObserver = new ResizeObserver(() => {
        try {
          if (fitRef.current && termRef.current) {
            fitRef.current.fit()
            if (ws.readyState === WebSocket.OPEN) {
              const cols = termRef.current.cols
              const rows = termRef.current.rows
              if (cols > 0 && rows > 0) {
                ws.send(JSON.stringify({ type: 'resize', cols, rows }))
              }
            }
          }
        } catch {}
      })
      resizeObserver.observe(container)
    } catch {}

    return () => {
      try { resizeObserver && resizeObserver.disconnect() } catch {}
      try { term.dispose() } catch {}
      termRef.current = null
      fitRef.current = null
      try { ws.close() } catch {}
      wsRef.current = null
    }
  }, [safeSetTermStatus])

  // Spawn the terminal when the panel opens in XShell mode.
  useEffect(() => {
    if (!open || mode !== 'xshell') {
      safeSetTermStatus('idle')
      return
    }
    const cleanup = spawnTerminal()
    return typeof cleanup === 'function' ? cleanup : undefined
  }, [open, mode, spawnTerminal, safeSetTermStatus])

  // Auto-scroll viewport when panel opens so user doesn't have to manually scroll.
  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => {
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
    }, 80)
    return () => clearTimeout(timer)
  }, [open])

  if (!open) return null

  const panelHeight = typeof height === 'number' ? `${height}px` : height

  return (
    <div
      data-testid="xshell-panel"
      style={{
        position: 'fixed',
        left: 0,
        right: 0,
        bottom: 0,
        height: panelHeight,
        zIndex: 2000,
        background: '#0f1117',
        borderTop: '1px solid #2a2f3a',
        boxShadow: '0 -4px 16px rgba(0,0,0,0.4)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Header bar */}
      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '6px 10px', borderBottom: '1px solid #2a2f3a', cursor: 'row-resize',
        }}
        onMouseDown={startDrag}
      >
        <SpaceBetween direction="horizontal" size="m" alignItems="center">
          <span style={{ color: '#7aa2f7', fontWeight: 600, fontSize: 13 }}>☁</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <Button
              variant={mode === 'xshell' ? 'primary' : 'normal'}
              onClick={() => setMode('xshell')}
              data-testid="xshell-switch-terminal"
            >
              Terminal
            </Button>
            <Button
              variant={mode === 'xide' ? 'primary' : 'normal'}
              onClick={() => setMode('xide')}
              data-testid="xshell-switch-ide"
            >
              IDE
            </Button>
          </div>
        </SpaceBetween>

        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
          {mode === 'xshell' && termStatus === 'connecting' && (
            <span style={{ color: '#9ca3af', fontSize: 12 }}>Connecting…</span>
          )}
          {mode === 'xshell' && termStatus === 'error' && (
            <span style={{ color: '#f87171', fontSize: 12 }}>Disconnected — click Close and reopen</span>
          )}
          <Button iconName="close" variant="icon" onClick={() => setOpen(false)} ariaLabel="Close panel" />
        </SpaceBetween>
      </div>

      {/* Body */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <div
          ref={terminalEl}
          data-testid="xshell-terminal"
          style={{
            position: 'absolute', inset: 0, padding: 8,
            display: mode === 'xshell' ? 'block' : 'none',
            background: '#0f1117',
          }}
        />
        {mode === 'xide' && (
          <iframe
            src="/xide-proxy/"
            title="XIDE"
            data-testid="xshell-ide"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 'none' }}
            allow="clipboard-read; clipboard-write; fullscreen"
          />
        )}
      </div>
    </div>
  )
}