/**
 * TopologyLinks.jsx
 *
 * Orthogonal stepped SVG links with rounded corners for CloudX network topology.
 */
import React from 'react'
import { getOrthogonalRoundedPath } from '../../utils/topologyLayout.js'

const LINK_STYLES = {
  gateway:   { stroke: '#f97316', width: 2.2, opacity: 0.85 },
  interface: { stroke: '#0284c7', width: 2.0, opacity: 0.8 },
  port:      { stroke: '#22c55e', width: 1.8, opacity: 0.8 },
}

export function TopologyLink({ link, nodeMap, isHighlighted }) {
  const sourceNode = nodeMap.get(link.source)
  const targetNode = nodeMap.get(link.target)
  if (!sourceNode || !targetNode) return null

  // Calculate connection anchors: bottom of source card to top of target card
  const x1 = sourceNode.x
  const y1 = sourceNode.y + (sourceNode.height || 50) / 2

  const x2 = targetNode.x
  const y2 = targetNode.y - (targetNode.height || 50) / 2

  const pathData = getOrthogonalRoundedPath(x1, y1, x2, y2, 10)
  const style = LINK_STYLES[link.type] || LINK_STYLES.port

  return (
    <g className={`topology-link topology-link-${link.type}`}>
      {/* Background shadow/glow if highlighted */}
      {isHighlighted && (
        <path
          d={pathData}
          fill="none"
          stroke={style.stroke}
          strokeWidth={style.width + 4}
          strokeOpacity={0.4}
        />
      )}

      {/* Main connector stepped orthogonal line with rounded corners */}
      <path
        d={pathData}
        fill="none"
        stroke={isHighlighted ? '#38bdf8' : style.stroke}
        strokeWidth={isHighlighted ? style.width + 1 : style.width}
        strokeOpacity={isHighlighted ? 1 : style.opacity}
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Connection endpoint dots */}
      <circle cx={x1} cy={y1} r={3} fill={style.stroke} />
      <circle cx={x2} cy={y2} r={3} fill={style.stroke} />
    </g>
  )
}
