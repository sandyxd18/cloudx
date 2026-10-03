/**
 * common.jsx — shared Cloudscape helpers used across CloudX pages.
 */
import Box from '@cloudscape-design/components/box'
import Button from '@cloudscape-design/components/button'
import SpaceBetween from '@cloudscape-design/components/space-between'
import StatusIndicator from '@cloudscape-design/components/status-indicator'
import Spinner from '@cloudscape-design/components/spinner'

// ── Status mapping ───────────────────────────────────────────────────────────
// Cloudscape StatusIndicator types: success | error | warning | info |
// stopped | pending | in-progress | loading

const COMPUTE_STATUS = {
  ACTIVE: 'success',
  SHUTOFF: 'stopped',
  ERROR: 'error',
  BUILD: 'in-progress',
  PAUSED: 'warning',
  SUSPENDED: 'warning',
  RESCUE: 'warning',
  VERIFY_RESIZE: 'warning',
  SHELVED_OFFLOADED: 'stopped',
  STARTING: 'in-progress',
  STOPPING: 'in-progress',
  REBOOT: 'in-progress',
  PAUSING: 'in-progress',
  RESUMING: 'in-progress',
  SUSPENDING: 'in-progress',
  DELETING: 'in-progress',
  REBUILD: 'in-progress',
  RESIZE: 'in-progress',
  UNRESCUING: 'in-progress',
  SHELVING: 'in-progress',
  UNSHELVING: 'in-progress',
}

const RESOURCE_STATUS = {
  available: 'success',
  'in-use': 'info',
  in_use: 'info',
  active: 'success',
  error: 'error',
  creating: 'in-progress',
  extending: 'in-progress',
  deleting: 'in-progress',
  restoring: 'in-progress',
  queued: 'pending',
  saving: 'in-progress',
  deactivated: 'stopped',
  down: 'stopped',
}

/** Status indicator for compute instance states (uppercase Nova statuses). */
export function ComputeStatus({ status }) {
  const type = COMPUTE_STATUS[status] || 'info'
  return <StatusIndicator type={type}>{status || '—'}</StatusIndicator>
}

/** Status indicator for volumes, images, snapshots, ports (lowercase states). */
export function ResourceStatus({ status }) {
  if (!status) return <Box color="text-status-inactive">—</Box>
  const type = RESOURCE_STATUS[String(status).toLowerCase()] || 'info'
  return <StatusIndicator type={type}>{status}</StatusIndicator>
}

/** Yes/no indicator used for boolean columns (shared, external, bootable…). */
export function BoolStatus({ value, trueText = 'Yes', falseText = 'No' }) {
  return value
    ? <StatusIndicator type="success">{trueText}</StatusIndicator>
    : <StatusIndicator type="stopped">{falseText}</StatusIndicator>
}

// ── Table state slots ────────────────────────────────────────────────────────

/** Empty-state block for Cloudscape tables. */
export function TableEmpty({ title, description, action }) {
  return (
    <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
      <SpaceBetween size="m">
        <b>{title}</b>
        {description && <Box variant="p" color="inherit">{description}</Box>}
        {action}
      </SpaceBetween>
    </Box>
  )
}

/** No-match block shown when client-side filtering yields nothing. */
export function TableNoMatch({ onClear }) {
  return (
    <Box margin={{ vertical: 'xs' }} textAlign="center" color="inherit">
      <SpaceBetween size="m">
        <b>No matches</b>
        <Box variant="p" color="inherit">No resources matched the filter.</Box>
        {onClear && <Button onClick={onClear}>Clear filter</Button>}
      </SpaceBetween>
    </Box>
  )
}

/** Centered loading state for detail pages. */
export function PageLoading({ text = 'Loading' }) {
  return (
    <Box textAlign="center" padding={{ vertical: 'xxxl' }}>
      <SpaceBetween size="s" alignItems="center">
        <Spinner size="large" />
        <Box variant="p" color="text-body-secondary">{text}…</Box>
      </SpaceBetween>
    </Box>
  )
}

// ── Formatting ───────────────────────────────────────────────────────────────

export function fmtBytes(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function parseUtcDate(dt) {
  if (!dt) return null
  if (typeof dt === 'string' && !dt.endsWith('Z') && !dt.includes('+')) {
    return new Date(dt + 'Z')
  }
  return new Date(dt)
}

export function fmtDate(dt) {
  const d = parseUtcDate(dt)
  if (!d || isNaN(d.getTime())) return '—'
  return d.toLocaleString()
}

export function timeAgo(dt) {
  const d = parseUtcDate(dt)
  if (!d || isNaN(d.getTime())) return '—'
  const diff = Math.floor((Date.now() - d.getTime()) / 1000)
  if (diff <= 0) return 'just now'
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export function shortId(id, len = 12) {
  if (!id) return '—'
  return id.length > len ? `${id.slice(0, len)}…` : id
}

/** Standard "N resources" / "N of M" table counter. */
export function counterText(total, selectedCount = 0) {
  return selectedCount ? `(${selectedCount}/${total})` : `(${total})`
}
