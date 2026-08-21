import { useState, useEffect } from 'react'
import { Shield, Users, Plus, Trash2, RefreshCw, ChevronDown, ChevronRight, CheckCircle } from 'lucide-react'
import { api } from '../api.js'
import { useAuth } from '../context.jsx'

// Role badge color map
const roleBadge = {
  admin:  { bg: '#ef444422', color: '#ef4444', label: 'Admin' },
  member: { bg: '#6366f122', color: '#6366f1', label: 'Member' },
}

function UserRoleRow({ user, onChangeRole }) {
  const badge = roleBadge[user.role] || { bg: '#ffffff11', color: '#94a3b8', label: user.role }
  const [expanded, setExpanded] = useState(false)

  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '12px 16px', cursor: 'pointer',
          transition: 'background 0.15s',
        }}
        className="table-row-hover"
        onClick={() => setExpanded(e => !e)}
      >
        {expanded ? <ChevronDown size={14} color="var(--text-muted)" /> : <ChevronRight size={14} color="var(--text-muted)" />}
        <div style={{
          width: 34, height: 34, borderRadius: '50%',
          background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 13, fontWeight: 700, color: '#fff', flexShrink: 0,
        }}>
          {user.username[0].toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>{user.username}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{user.email}</div>
        </div>
        <span style={{
          fontSize: 11, fontWeight: 600,
          background: badge.bg, color: badge.color,
          padding: '2px 10px', borderRadius: 20,
        }}>
          {badge.label}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {new Date(user.created_at).toLocaleDateString()}
        </span>
      </div>

      {/* Expanded role assignment panel */}
      {expanded && (
        <div style={{
          background: 'var(--bg-elevated)',
          borderTop: '1px solid var(--border)',
          padding: '14px 20px',
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Assign role:</span>
          {['admin', 'member'].map(role => (
            <button
              key={role}
              onClick={e => { e.stopPropagation(); onChangeRole(user, role) }}
              style={{
                padding: '4px 14px',
                borderRadius: 20,
                border: `1px solid ${user.role === role ? roleBadge[role].color : 'var(--border)'}`,
                background: user.role === role ? roleBadge[role].bg : 'transparent',
                color: user.role === role ? roleBadge[role].color : 'var(--text-secondary)',
                fontSize: 12, fontWeight: 600,
                cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                transition: 'all 0.15s',
              }}
            >
              {user.role === role && <CheckCircle size={12} />}
              {role.charAt(0).toUpperCase() + role.slice(1)}
            </button>
          ))}
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            Project: <strong>{user.project_id}</strong>
          </span>
        </div>
      )}
    </div>
  )
}

export default function RolesPage() {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [toast, setToast] = useState(null)
  const [filter, setFilter] = useState('')
  const [roleFilter, setRoleFilter] = useState('all')

  const showToast = (msg, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const load = () => {
    setLoading(true)
    api.getUsers()
      .then(res => setUsers(res?.items || []))
      .catch(() => showToast('Failed to load users', 'error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleChangeRole = async (user, newRole) => {
    if (user.role === newRole) return
    setSaving(user.id)
    try {
      await api.updateUser(user.id, { role: newRole })
      showToast(`Role updated: ${user.username} → ${newRole}`)
      setUsers(us => us.map(u => u.id === user.id ? { ...u, role: newRole } : u))
    } catch (e) {
      showToast(e.message || 'Failed to update role', 'error')
    } finally {
      setSaving(null)
    }
  }

  const filtered = users.filter(u => {
    const matchesText = !filter || u.username.toLowerCase().includes(filter.toLowerCase()) || u.email.toLowerCase().includes(filter.toLowerCase())
    const matchesRole = roleFilter === 'all' || u.role === roleFilter
    return matchesText && matchesRole
  })

  const adminCount  = users.filter(u => u.role === 'admin').length
  const memberCount = users.filter(u => u.role === 'member').length

  if (currentUser?.role !== 'admin') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 400, gap: 12 }}>
        <Shield size={48} style={{ color: '#ef4444', opacity: 0.6 }} />
        <p style={{ fontSize: 16, color: 'var(--text-secondary)' }}>Admin access required</p>
        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>Only administrators can manage roles.</p>
      </div>
    )
  }

  return (
    <div>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 20, right: 20, zIndex: 9999,
          padding: '12px 20px', borderRadius: 8,
          background: toast.type === 'error' ? '#ef4444' : '#10b981',
          color: '#fff', fontSize: 13, fontWeight: 500,
          boxShadow: '0 8px 32px rgba(0,0,0,.3)',
          animation: 'fadeIn 0.2s',
        }}>
          {toast.msg}
        </div>
      )}

      <div className="page-header">
        <div>
          <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Shield size={20} style={{ color: '#ef4444' }} />
            Role Management
          </h1>
          <p className="page-subtitle">Manage user role assignments across projects</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={loading ? { animation: 'spin 1s linear infinite' } : {}} />
          Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div className="stat-grid" style={{ marginBottom: 16 }}>
        {[
          { label: 'Total Users',    value: users.length, color: '#6366f1', icon: <Users size={16}/> },
          { label: 'Administrators', value: adminCount,   color: '#ef4444', icon: <Shield size={16}/> },
          { label: 'Members',        value: memberCount,  color: '#10b981', icon: <Users size={16}/> },
        ].map(s => (
          <div key={s.label} className="stat-card" style={{ padding: 16 }}>
            <div className="stat-card-icon" style={{ background: s.color + '22', color: s.color }}>
              {s.icon}
            </div>
            <div className="stat-card-value">{s.value}</div>
            <div className="stat-card-label">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-body" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', padding: '12px 16px' }}>
          <input
            type="text"
            className="form-input"
            placeholder="Search by username or email…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            style={{ flex: 1, minWidth: 200, maxWidth: 340 }}
          />
          <div style={{ display: 'flex', gap: 6 }}>
            {['all', 'admin', 'member'].map(r => (
              <button
                key={r}
                onClick={() => setRoleFilter(r)}
                style={{
                  padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: `1px solid ${roleFilter === r ? '#6366f1' : 'var(--border)'}`,
                  background: roleFilter === r ? '#6366f122' : 'transparent',
                  color: roleFilter === r ? '#6366f1' : 'var(--text-secondary)',
                  transition: 'all 0.15s',
                }}
              >
                {r.charAt(0).toUpperCase() + r.slice(1)}
              </button>
            ))}
          </div>
          <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            {filtered.length} user{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* User list */}
      <div className="card">
        <div className="card-header">
          <span className="card-title"><Users size={15} />Users & Role Assignments</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Click a row to expand role assignment</span>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
            <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite', marginBottom: 10 }} />
            <p style={{ fontSize: 13 }}>Loading users…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
            <Users size={36} style={{ opacity: 0.4, marginBottom: 10 }} />
            <p style={{ fontSize: 13 }}>No users found</p>
          </div>
        ) : (
          <div>
            {filtered.map(u => (
              <UserRoleRow
                key={u.id}
                user={u}
                onChangeRole={handleChangeRole}
              />
            ))}
          </div>
        )}
      </div>

      {/* Role descriptions */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-header">
          <span className="card-title"><Shield size={15} />Role Definitions</span>
        </div>
        <div className="card-body">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
            {[
              { role: 'admin', desc: 'Full access to all resources across all projects. Can manage users, roles, quotas, and service catalog.', color: '#ef4444' },
              { role: 'member', desc: 'Standard project member. Can create and manage resources within their own project. Cannot manage other users.', color: '#6366f1' },
            ].map(r => (
              <div key={r.role} style={{
                padding: 14, borderRadius: 8,
                border: `1px solid ${r.color}33`,
                background: r.color + '0a',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <Shield size={14} color={r.color} />
                  <strong style={{ fontSize: 13, color: r.color }}>
                    {r.role.charAt(0).toUpperCase() + r.role.slice(1)}
                  </strong>
                </div>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>{r.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
