/**
 * XShellPanel.jsx — the bottom dockable panel hosting XShell terminal
 * (xterm.js over WebSocket) and XIDE (code-server iframe). A header bar lets
 * the user switch between terminal and IDE, minimize/maximize, and close —
 * mirroring the AWS CloudShell dock.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import SpaceBetween from '@cloudscape-design/components/space-between'
import { useXShell } from './XShellContext.jsx'

const XIDE_PORT = 8085

export default function XShellPanel() {
  const { open, setOpen, height, setHeight, mode, setMode } = useXShell()

  const terminalEl = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const wsRef = useRef(null)
  const [termStatus, setTermStatus] = useState('connecting') // connecting | ready | error

  // ── Drag-to-resize handle ────────────────────────────────────────────────────
  const startDrag = useCallback((e) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = height
    const onMove = (ev) => {
      // height grows upward → invert the delta.
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

  // ── Terminal lifecycle ──────────────────────────────────────────────────────
  const spawnTerminal = useCallback(() => {
    if (!terminalEl.current) return
    if (termRef.current) { termRef.current.dispose(); termRef.current = null }

    const term = new Terminal({
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
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(terminalEl.current)
    fit.fit()
    termRef.current = term
    fitRef.current = fit

    setTermStatus('connecting')
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/xshell`)
    wsRef.current = ws

    ws.onopen = () => setTermStatus('ready')
    ws.onmessage = (e) => term.write(e.data)
    ws.onerror = () => setTermStatus('error')
    ws.onclose = () => setTermStatus('error')

    const dataListener = (data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data)
    }
    term.onData(dataListener)

    const resizeObserver = new ResizeObserver(() => {
      fit.fit()
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    })
    resizeObserver.observe(terminalEl.current)

    return () => {
      resizeObserver.disconnect()
      term.dispose()
      termRef.current = null
      ws.close()
      wsRef.current = null
    }
  }, [])

  // Spawn the terminal when the panel opens in XShell mode.
  useEffect(() => {
    if (!open || mode !== 'xshell') return
    const cleanup = spawnTerminal()
    return cleanup
  }, [open, mode, spawnTerminal])

  if (!open) return null

  return (
    <Box
      padding="xxs"
      data-testid="xshell-panel"
      style={{
        position: 'fixed',
        left: 0,
        right: 0,
        bottom: 0,
        height,
        zIndex: 1000,
        background: '#0f1117',
        borderTop: '1px solid #2a2f3a',
        boxShadow: '0 -4px 16px rgba(0,0,0,0.4)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* ── Header bar ────────────────────────────────────────────────────── */}
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

      {/* ── Body ──────────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {/* XShell terminal */}
        <div
          ref={terminalEl}
          data-testid="xshell-terminal"
          style={{
            position: 'absolute', inset: 0, padding: 8,
            display: mode === 'xshell' ? 'block' : 'none',
            background: '#0f1117',
          }}
        />
        {/* XIDE code-server iframe */}
        {mode === 'xide' && (
          <iframe
            src={`${window.location.protocol}//${window.location.hostname}:${XIDE_PORT}/`}
            title="XIDE"
            data-testid="xshell-ide"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 'none' }}
            allow="clipboard-read; clipboard-write; fullscreen"
          />
        )}
      </div>
    </Box>
  )
}
