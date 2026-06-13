import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Server, Cpu, Key, Users2,
  Network, Globe, ArrowLeftRight, Shield,
  HardDrive, Database, Camera, LogOut,
  ChevronRight, Settings, Layers, Layers2
} from 'lucide-react'
import { useAuth } from '../context.jsx'

const NAV = [
  { section: 'Overview' },
  { to: '/',               icon: LayoutDashboard, label: 'Dashboard' },
  { section: 'Compute' },
  { to: '/servers',        icon: Server,           label: 'Instances'     },
  { to: '/flavors',        icon: Cpu,              label: 'Flavors'       },
  { to: '/keypairs',       icon: Key,              label: 'Key Pairs'     },
  { to: '/server-groups',  icon: Layers,           label: 'Server Groups' },
  { section: 'Networking' },
  { to: '/networks',       icon: Network,          label: 'Networks'      },
  { to: '/routers',        icon: Globe,            label: 'Routers'       },
  { to: '/floating-ips',   icon: ArrowLeftRight,   label: 'Floating IPs'  },
  { to: '/security-groups',icon: Shield,           label: 'Security Groups'},
  { section: 'Storage' },
  { to: '/images',         icon: HardDrive,        label: 'Images'        },
  { to: '/volumes',        icon: Database,         label: 'Volumes'       },
  { to: '/volume-types',   icon: Layers2,          label: 'Volume Types'  },
  { to: '/snapshots',      icon: Camera,           label: 'Snapshots'     },
  { section: 'Admin' },
  { to: '/admin',          icon: Settings,         label: 'Admin'         },
]

export default function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const currentLabel = NAV.find(n => n.to && (
    n.to === '/' ? location.pathname === '/' : location.pathname.startsWith(n.to)
  ))?.label || 'Dashboard'

  const initials = (user?.username || 'U').slice(0, 2).toUpperCase()

  return (
    <div className="layout">
      {/* Sidebar */}
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
            if (item.section) {
              return <div key={i} className="sidebar-section">{item.section}</div>
            }
            const Icon = item.icon
            const isExact = item.to === '/'
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={isExact}
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

      {/* Main */}
      <div className="main-content">
        <header className="header">
          <div className="header-breadcrumb">
            <span>cloudx</span>
            <ChevronRight size={13} />
            <span className="header-breadcrumb-current">{currentLabel}</span>
          </div>
          <div className="header-actions">
            {user?.project_name && (
              <div className="header-project-badge">
                <span>◆</span>
                {user.project_name}
              </div>
            )}
          </div>
        </header>

        <div className="page-content">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
