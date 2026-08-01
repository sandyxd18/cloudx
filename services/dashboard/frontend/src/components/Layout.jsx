import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Server, Cpu, Key,
  Network, Globe, ArrowLeftRight, Shield,
  HardDrive, Database, Camera, LogOut,
  ChevronRight, Settings, Layers, Layers2,
  ChevronDown, ChevronUp, FolderOpen, GitBranch
} from 'lucide-react'
import { useAuth } from '../context.jsx'
import { useState, useRef, useEffect } from 'react'

const NAV = [
  { section: 'Project' },
  { to: '/', icon: LayoutDashboard, label: 'Overview' },
  {
    group: 'Compute', icon: Server, children: [
      { to: '/servers',       icon: Server,   label: 'Instances'     },
      { to: '/images',        icon: HardDrive,label: 'Images'        },
      { to: '/keypairs',      icon: Key,      label: 'Key Pairs'     },
      { to: '/server-groups', icon: Layers,   label: 'Server Groups' },
    ]
  },
  {
    group: 'Volumes', icon: Database, children: [
      { to: '/volumes',   icon: Database, label: 'Volumes'   },
      { to: '/snapshots', icon: Camera,   label: 'Snapshots' },
    ]
  },
  {
    group: 'Network', icon: Network, children: [
      { to: '/networks',       icon: Network,        label: 'Networks'       },
      { to: '/routers',        icon: Globe,          label: 'Routers'        },
      { to: '/security-groups',icon: Shield,         label: 'Security Groups'},
      { to: '/floating-ips',   icon: ArrowLeftRight, label: 'Floating IPs'  },
      { to: '/topology',       icon: GitBranch,      label: 'Topology'       },
    ]
  },
  { section: 'Admin' },
  { to: '/admin',       icon: Settings, label: 'Admin Panel'  },
  { to: '/flavors',     icon: Cpu,      label: 'Flavors'      },
  { to: '/volume-types',icon: Layers2,  label: 'Volume Types' },
]

function NavGroup({ group, icon: Icon, children, location }) {
  const isActive = children.some(c => c.to && (
    c.to === '/' ? location.pathname === '/' : location.pathname.startsWith(c.to)
  ))
  const [open, setOpen] = useState(isActive)

  return (
    <div className="sidebar-group">
      <button
        className={`sidebar-group-btn${isActive ? ' active' : ''}`}
        onClick={() => setOpen(o => !o)}
      >
        <Icon size={14} />
        <span>{group}</span>
        {open
          ? <ChevronUp size={11} style={{ marginLeft: 'auto' }} />
          : <ChevronDown size={11} style={{ marginLeft: 'auto' }} />
        }
      </button>
      {open && (
        <div className="sidebar-group-children">
          {children.map(item => {
            const CIcon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `sidebar-item sidebar-child${isActive ? ' active' : ''}`}
              >
                <CIcon size={13} />
                {item.label}
              </NavLink>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Project Selector Dropdown ────────────────────────────────────────────────
function ProjectSelector({ user, projects, switchProject }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (!projects || projects.length <= 1) {
    // Just show the badge, no dropdown
    return (
      <div className="header-project-badge">
        <FolderOpen size={13} />
        <span>{user?.project_name || user?.project_id || 'Project'}</span>
      </div>
    )
  }

  return (
    <div className="project-selector" ref={ref}>
      <button
        className="header-project-badge header-project-btn"
        onClick={() => setOpen(o => !o)}
        title="Switch project"
      >
        <FolderOpen size={13} />
        <span>{user?.project_name || user?.project_id || 'Project'}</span>
        <ChevronDown size={11} />
      </button>
      {open && (
        <div className="project-dropdown">
          <div className="project-dropdown-label">Switch Project</div>
          {projects.map(p => (
            <button
              key={p.id}
              className={`project-dropdown-item${p.id === user?.project_id ? ' active' : ''}`}
              onClick={() => {
                setOpen(false)
                if (p.id !== user?.project_id) switchProject(p.id)
              }}
            >
              <FolderOpen size={12} />
              <span>{p.name}</span>
              {p.id === user?.project_id && <span className="project-check">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Layout() {
  const { user, logout, projects, switchProject } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const allItems = NAV.flatMap(n => n.children || (n.to ? [n] : []))
  const currentLabel = allItems.find(n => n.to && (
    n.to === '/' ? location.pathname === '/' : location.pathname.startsWith(n.to)
  ))?.label || 'Overview'

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">☁</div>
          <div>
            <div className="sidebar-logo-text">cloudx</div>
            <div className="sidebar-logo-sub">Private Cloud</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map((item, i) => {
            if (item.section) return <div key={i} className="sidebar-section">{item.section}</div>
            if (item.group) return <NavGroup key={item.group} group={item.group} icon={item.icon} children={item.children} location={location} />
            const Icon = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) => `sidebar-item${isActive ? ' active' : ''}`}
              >
                <Icon size={15} />
                {item.label}
              </NavLink>
            )
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user" onClick={handleLogout} title="Logout">
            <div className="sidebar-avatar">{initials}</div>
            <div className="sidebar-user-info">
              <div className="sidebar-user-name">{user?.username || 'User'}</div>
              <div className="sidebar-user-role">{user?.role || 'member'} · logout</div>
            </div>
            <LogOut size={14} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          </div>
        </div>
      </aside>

      <div className="main-content">
        <header className="header">
          <div className="header-breadcrumb">
            <span>cloudx</span>
            <ChevronRight size={13} />
            <span className="header-breadcrumb-current">{currentLabel}</span>
          </div>
          <div className="header-actions">
            <ProjectSelector
              user={user}
              projects={projects}
              switchProject={switchProject}
            />
          </div>
        </header>
        <div className="page-content">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
