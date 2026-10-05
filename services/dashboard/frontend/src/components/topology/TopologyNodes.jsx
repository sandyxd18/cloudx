/**
 * TopologyNodes.jsx
 *
 * Skyline-style SVG card node components for CloudX network topology.
 */
import React from 'react'

export function ServerNode({ node, isSelected, onClick }) {
  const { x, y, width = 160, height = 46, label } = node
  const halfW = width / 2
  const halfH = height / 2

  return (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
      className="topology-node topology-node-server"
    >
      {/* Selection Glow / Shadow */}
      {isSelected && (
        <rect
          x={-halfW - 3}
          y={-halfH - 3}
          width={width + 6}
          height={height + 6}
          rx={10}
          fill="none"
          stroke="#0972d3"
          strokeWidth={2.5}
          strokeOpacity={0.8}
        />
      )}

      {/* Card Body */}
      <rect
        x={-halfW}
        y={-halfH}
        width={width}
        height={height}
        rx={8}
        fill="#1e293b"
        stroke={isSelected ? '#38bdf8' : '#334155'}
        strokeWidth={1.5}
        filter="drop-shadow(0 2px 4px rgba(0,0,0,0.3))"
      />

      {/* Left Accent Bar */}
      <rect
        x={-halfW}
        y={-halfH}
        width={4}
        height={height}
        rx={2}
        fill="#22c55e"
      />

      {/* Server Icon (SVG) */}
      <g transform={`translate(${-halfW + 14}, ${-9})`} fill="none" stroke="#22c55e" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
        <rect x="0" y="0" width="16" height="8" rx="1.5" />
        <rect x="0" y="10" width="16" height="8" rx="1.5" />
        <line x1="4" y1="4" x2="4.01" y2="4" strokeWidth={2.5} />
        <line x1="4" y1="14" x2="4.01" y2="14" strokeWidth={2.5} />
      </g>

      {/* Server Label (Name only, clean and uncluttered) */}
      <text
        x={-halfW + 38}
        y={4}
        fill="#f8fafc"
        fontSize="12px"
        fontWeight="600"
        fontFamily="system-ui, -apple-system, sans-serif"
      >
        {label.length > 15 ? label.slice(0, 14) + '…' : label}
      </text>
    </g>
  )
}

export function RouterNode({ node, isSelected, onClick }) {
  const { x, y, width = 180, height = 50, label } = node
  const halfW = width / 2
  const halfH = height / 2

  return (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
      className="topology-node topology-node-router"
    >
      {isSelected && (
        <rect
          x={-halfW - 3}
          y={-halfH - 3}
          width={width + 6}
          height={height + 6}
          rx={10}
          fill="none"
          stroke="#f97316"
          strokeWidth={2.5}
          strokeOpacity={0.8}
        />
      )}

      <rect
        x={-halfW}
        y={-halfH}
        width={width}
        height={height}
        rx={8}
        fill="#1e293b"
        stroke={isSelected ? '#fb923c' : '#475569'}
        strokeWidth={1.5}
        filter="drop-shadow(0 2px 4px rgba(0,0,0,0.3))"
      />

      {/* Left Accent Bar */}
      <rect
        x={-halfW}
        y={-halfH}
        width={4}
        height={height}
        rx={2}
        fill="#f97316"
      />

      {/* Router Icon */}
      <g transform={`translate(${-halfW + 14}, ${-10})`} fill="none" stroke="#f97316" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
        <rect x="0" y="2" width="18" height="14" rx="2" />
        <line x1="3" y1="9" x2="15" y2="9" />
        <line x1="9" y1="5" x2="9" y2="13" />
      </g>

      <text
        x={-halfW + 42}
        y={-2}
        fill="#f8fafc"
        fontSize="12px"
        fontWeight="600"
        fontFamily="system-ui, -apple-system, sans-serif"
      >
        {label.length > 16 ? label.slice(0, 15) + '…' : label}
      </text>
      <text
        x={-halfW + 42}
        y={14}
        fill="#94a3b8"
        fontSize="10px"
        fontWeight="500"
        fontFamily="system-ui, -apple-system, sans-serif"
      >
        Virtual Router
      </text>
    </g>
  )
}

export function SubnetNode({ node, isSelected, onClick }) {
  const { x, y, width = 190, height = 50, label, cidr } = node
  const halfW = width / 2
  const halfH = height / 2

  return (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
      className="topology-node topology-node-subnet"
    >
      {isSelected && (
        <rect
          x={-halfW - 3}
          y={-halfH - 3}
          width={width + 6}
          height={height + 6}
          rx={10}
          fill="none"
          stroke="#0284c7"
          strokeWidth={2.5}
          strokeOpacity={0.8}
        />
      )}

      <rect
        x={-halfW}
        y={-halfH}
        width={width}
        height={height}
        rx={8}
        fill="#1e293b"
        stroke={isSelected ? '#38bdf8' : '#334155'}
        strokeWidth={1.5}
        filter="drop-shadow(0 2px 4px rgba(0,0,0,0.3))"
      />

      {/* Left Accent Bar */}
      <rect
        x={-halfW}
        y={-halfH}
        width={4}
        height={height}
        rx={2}
        fill="#0284c7"
      />

      {/* Network / Subnet Icon */}
      <g transform={`translate(${-halfW + 14}, ${-10})`} fill="none" stroke="#38bdf8" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
        <rect x="0" y="0" width="18" height="12" rx="2" />
        <line x1="9" y1="12" x2="9" y2="18" />
        <line x1="3" y1="18" x2="15" y2="18" />
      </g>

      <text
        x={-halfW + 42}
        y={-2}
        fill="#f8fafc"
        fontSize="12px"
        fontWeight="600"
        fontFamily="system-ui, -apple-system, sans-serif"
      >
        {label.length > 16 ? label.slice(0, 15) + '…' : label}
      </text>
      <text
        x={-halfW + 42}
        y={14}
        fill="#38bdf8"
        fontSize="10px"
        fontWeight="500"
        fontFamily="monospace"
      >
        {cidr || 'Private Network'}
      </text>
    </g>
  )
}

export function ExternalNetworkNode({ node, isSelected, onClick }) {
  const { x, y, width = 210, height = 50, label, cidr } = node
  const halfW = width / 2
  const halfH = height / 2

  return (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
      className="topology-node topology-node-external"
    >
      {isSelected && (
        <rect
          x={-halfW - 3}
          y={-halfH - 3}
          width={width + 6}
          height={height + 6}
          rx={10}
          fill="none"
          stroke="#a855f7"
          strokeWidth={2.5}
          strokeOpacity={0.8}
        />
      )}

      <rect
        x={-halfW}
        y={-halfH}
        width={width}
        height={height}
        rx={8}
        fill="#1e293b"
        stroke={isSelected ? '#c084fc' : '#475569'}
        strokeWidth={1.5}
        filter="drop-shadow(0 2px 4px rgba(0,0,0,0.3))"
      />

      {/* Left Accent Bar */}
      <rect
        x={-halfW}
        y={-halfH}
        width={4}
        height={height}
        rx={2}
        fill="#a855f7"
      />

      {/* Globe Icon */}
      <g transform={`translate(${-halfW + 14}, ${-10})`} fill="none" stroke="#c084fc" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
        <circle cx="9" cy="9" r="8" />
        <line x1="1" y1="9" x2="17" y2="9" />
        <ellipse cx="9" cy="9" rx="3.5" ry="8" />
      </g>

      <text
        x={-halfW + 42}
        y={-2}
        fill="#f8fafc"
        fontSize="12px"
        fontWeight="600"
        fontFamily="system-ui, -apple-system, sans-serif"
      >
        {label.length > 17 ? label.slice(0, 16) + '…' : label}
      </text>
      <text
        x={-halfW + 42}
        y={14}
        fill="#c084fc"
        fontSize="10px"
        fontWeight="500"
        fontFamily="monospace"
      >
        {cidr || 'External Gateway'}
      </text>
    </g>
  )
}
