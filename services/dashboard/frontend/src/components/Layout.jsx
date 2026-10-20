/**
 * Layout.jsx — CloudX console shell built on the Cloudscape Design System.
 *
 * Mirrors the AWS Management Console structure:
 *   TopNavigation  → identity, global search, project switcher, theme toggle, user menu
 *   AppLayout      → side navigation, breadcrumbs, notifications (Flashbar), content
 */
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useMemo, useState } from 'react'
import AppLayout from '@cloudscape-design/components/app-layout'
import TopNavigation from '@cloudscape-design/components/top-navigation'
import SideNavigation from '@cloudscape-design/components/side-navigation'
import BreadcrumbGroup from '@cloudscape-design/components/breadcrumb-group'
import Flashbar from '@cloudscape-design/components/flashbar'
import Autosuggest from '@cloudscape-design/components/autosuggest'
import { Moon, Sun, Terminal } from 'lucide-react'
import { useAuth, useFlashItems } from '../context.jsx'
import { useTheme } from '../theme.jsx'
import { useXShell } from './XShellContext.jsx'

// ── Navigation model ─────────────────────────────────────────────────────────
const NAV_ITEMS = [
  {
    type: 'section',
    text: 'Compute',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Instances',      href: '/servers' },
      { type: 'link', text: 'Images',         href: '/images' },
      { type: 'link', text: 'Key pairs',      href: '/keypairs' },
      { type: 'link', text: 'Server groups',  href: '/server-groups' },
      { type: 'link', text: 'Scaling groups', href: '/scaling-groups' },
      { type: 'link', text: 'Stacks',         href: '/stacks' },
      { type: 'link', text: 'Queues',         href: '/queues' },
      { type: 'link', text: 'Reservations',   href: '/reservations' },
      { type: 'link', text: 'XIDE',           href: '/xide' },
    ],
  },
  {
    type: 'section',
    text: 'Containers',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Containers',        href: '/containers' },
      { type: 'link', text: 'Databases',         href: '/databases' },
    { type: 'link', text: 'High availability', href: '/ha' },
      { type: 'link', text: 'Kubernetes Engine', href: '/k8s-engine' },
    ],
  },
  {
    type: 'section',
    text: 'Storage',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Volumes',      href: '/volumes' },
      { type: 'link', text: 'Snapshots',    href: '/snapshots' },
      { type: 'link', text: 'Object Store', href: '/object-store' },
      { type: 'link', text: 'File Store',   href: '/file-store' },
    ],
  },
  {
    type: 'section',
    text: 'Networking',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Networks',        href: '/networks' },
      { type: 'link', text: 'Routers',         href: '/routers' },
      { type: 'link', text: 'Security groups', href: '/security-groups' },
      { type: 'link', text: 'Floating IPs',    href: '/floating-ips' },
      { type: 'link', text: 'Load Balancers',  href: '/load-balancers' },
      { type: 'link', text: 'DNS',  href: '/dns' },
      { type: 'link', text: 'VPC Peering',     href: '/vpc-peering' },
      { type: 'link', text: 'VPN Gateways',    href: '/vpn-gateways' },
      { type: 'link', text: 'BGP Peers',       href: '/bgp-peers' },
      { type: 'link', text: 'Topology',        href: '/topology' },
    ],
  },
  {
    type: 'section',
    text: 'Security',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Key Manager (Vault)', href: '/secrets' },
    ],
  },
  {
    type: 'section',
    text: 'Observability & Cost',
    defaultExpanded: true,
    items: [
      { type: 'link', text: 'Telemetry & Metrics', href: '/telemetry' },
      { type: 'link', text: 'Alarms',              href: '/alarms' },
      { type: 'link', text: 'Usage & Billing',     href: '/billing' },
      { type: 'link', text: 'Audit Event Logs',    href: '/event-logs' },
    ],
  },
  { type: 'divider' },
  {
    type: 'section',
    text: 'Administration',
    defaultExpanded: false,
    items: [
      { type: 'link', text: 'Projects & users', href: '/admin' },
      { type: 'link', text: 'Roles',            href: '/roles' },
      { type: 'link', text: 'Role assignments', href: '/role-assignments' },
      { type: 'link', text: 'Hypervisors',      href: '/hypervisors' },
      { type: 'link', text: 'Flavors',          href: '/flavors' },
      { type: 'link', text: 'Volume types',     href: '/volume-types' },
      { type: 'link', text: 'Bucket types',     href: '/bucket-types' },
    ],
  },
]

// Route → breadcrumb trail. Detail routes append the resource id.
const CRUMBS = {
  '/':                 [{ text: 'Dashboard', href: '/' }],
  '/servers':          [{ text: 'Compute', href: '/servers' }, { text: 'Instances', href: '/servers' }],
  '/servers/create':   [{ text: 'Compute', href: '/servers' }, { text: 'Instances', href: '/servers' }, { text: 'Launch instance', href: '/servers/create' }],
  '/images':           [{ text: 'Compute', href: '/servers' }, { text: 'Images', href: '/images' }],
  '/images/create':    [{ text: 'Compute', href: '/servers' }, { text: 'Images', href: '/images' }, { text: 'Upload image', href: '/images/create' }],
  '/keypairs':         [{ text: 'Compute', href: '/servers' }, { text: 'Key pairs', href: '/keypairs' }],
  '/keypairs/create':  [{ text: 'Compute', href: '/servers' }, { text: 'Key pairs', href: '/keypairs' }, { text: 'Create key pair', href: '/keypairs/create' }],
  '/keypairs/import':  [{ text: 'Compute', href: '/servers' }, { text: 'Key pairs', href: '/keypairs' }, { text: 'Import key pair', href: '/keypairs/import' }],
  '/server-groups':        [{ text: 'Compute', href: '/servers' }, { text: 'Server groups', href: '/server-groups' }],
  '/server-groups/create': [{ text: 'Compute', href: '/servers' }, { text: 'Server groups', href: '/server-groups' }, { text: 'Create server group', href: '/server-groups/create' }],
  '/scaling-groups':       [{ text: 'Compute', href: '/servers' }, { text: 'Scaling groups', href: '/scaling-groups' }],
  '/stacks':               [{ text: 'Compute', href: '/servers' }, { text: 'Stacks', href: '/stacks' }],
  '/k8s-engine':           [{ text: 'Containers', href: '/k8s-engine' }, { text: 'Kubernetes Engine', href: '/k8s-engine' }],
  '/secrets':              [{ text: 'Security', href: '/secrets' }, { text: 'Key Manager (Vault)', href: '/secrets' }],
  '/vpc-peering':          [{ text: 'Networking', href: '/networks' }, { text: 'VPC Peering', href: '/vpc-peering' }],
  '/vpn-gateways':         [{ text: 'Networking', href: '/networks' }, { text: 'VPN Gateways', href: '/vpn-gateways' }],
  '/bgp-peers':            [{ text: 'Networking', href: '/networks' }, { text: 'BGP Peers', href: '/bgp-peers' }],
  '/telemetry':            [{ text: 'Observability & Cost', href: '/telemetry' }, { text: 'Telemetry & Metrics', href: '/telemetry' }],
  '/billing':              [{ text: 'Observability & Cost', href: '/billing' }, { text: 'Usage & Billing', href: '/billing' }],
  '/event-logs':           [{ text: 'Observability & Cost', href: '/event-logs' }, { text: 'Audit Event Logs', href: '/event-logs' }],
  '/hypervisors':          [{ text: 'Administration', href: '/admin' }, { text: 'Hypervisors', href: '/hypervisors' }],
  '/volumes':          [{ text: 'Storage', href: '/volumes' }, { text: 'Volumes', href: '/volumes' }],
  '/volumes/create':   [{ text: 'Storage', href: '/volumes' }, { text: 'Volumes', href: '/volumes' }, { text: 'Create volume', href: '/volumes/create' }],
  '/snapshots':        [{ text: 'Storage', href: '/volumes' }, { text: 'Snapshots', href: '/snapshots' }],
  '/snapshots/create': [{ text: 'Storage', href: '/volumes' }, { text: 'Snapshots', href: '/snapshots' }, { text: 'Create snapshot', href: '/snapshots/create' }],
  '/volume-types':        [{ text: 'Administration', href: '/admin' }, { text: 'Volume types', href: '/volume-types' }],
  '/volume-types/create': [{ text: 'Administration', href: '/admin' }, { text: 'Volume types', href: '/volume-types' }, { text: 'Create volume type', href: '/volume-types/create' }],
  '/bucket-types':        [{ text: 'Administration', href: '/admin' }, { text: 'Bucket types', href: '/bucket-types' }],
  '/object-store':     [{ text: 'Storage', href: '/volumes' }, { text: 'Object Store', href: '/object-store' }],
  '/containers':       [{ text: 'Containers', href: '/containers' }],
  '/queues':           [{ text: 'Compute', href: '/servers' }, { text: 'Queues', href: '/queues' }],
  '/reservations':     [{ text: 'Compute', href: '/servers' }, { text: 'Reservations', href: '/reservations' }],
  '/databases':        [{ text: 'Containers', href: '/containers' }, { text: 'Databases', href: '/databases' }],
  '/ha':               [{ text: 'High availability', href: '/ha' }],
  '/file-store':       [{ text: 'Storage', href: '/volumes' }, { text: 'File Store', href: '/file-store' }],
  '/networks':         [{ text: 'Networking', href: '/networks' }, { text: 'Networks', href: '/networks' }],
  '/networks/create':  [{ text: 'Networking', href: '/networks' }, { text: 'Networks', href: '/networks' }, { text: 'Create network', href: '/networks/create' }],
  '/subnets/create':   [{ text: 'Networking', href: '/networks' }, { text: 'Networks', href: '/networks' }, { text: 'Create subnet', href: '/subnets/create' }],
  '/routers':          [{ text: 'Networking', href: '/networks' }, { text: 'Routers', href: '/routers' }],
  '/routers/create':   [{ text: 'Networking', href: '/networks' }, { text: 'Routers', href: '/routers' }, { text: 'Create router', href: '/routers/create' }],
  '/security-groups':        [{ text: 'Networking', href: '/networks' }, { text: 'Security groups', href: '/security-groups' }],
  '/security-groups/create': [{ text: 'Networking', href: '/networks' }, { text: 'Security groups', href: '/security-groups' }, { text: 'Create security group', href: '/security-groups/create' }],
  '/floating-ips':           [{ text: 'Networking', href: '/networks' }, { text: 'Floating IPs', href: '/floating-ips' }],
  '/floating-ips/allocate':  [{ text: 'Networking', href: '/networks' }, { text: 'Floating IPs', href: '/floating-ips' }, { text: 'Allocate floating IP', href: '/floating-ips/allocate' }],
  '/floating-ips/associate': [{ text: 'Networking', href: '/networks' }, { text: 'Floating IPs', href: '/floating-ips' }, { text: 'Associate floating IP', href: '/floating-ips/associate' }],
  '/topology':         [{ text: 'Networking', href: '/networks' }, { text: 'Topology', href: '/topology' }],
  '/dns':   [{ text: 'Networking', href: '/networks' }, { text: 'DNS', href: '/dns' }],
  '/load-balancers':   [{ text: 'Networking', href: '/networks' }, { text: 'Load Balancers', href: '/load-balancers' }],
  '/load-balancers/create': [{ text: 'Networking', href: '/networks' }, { text: 'Load Balancers', href: '/load-balancers' }, { text: 'Create load balancer', href: '/load-balancers/create' }],
  '/admin':            [{ text: 'Administration', href: '/admin' }, { text: 'Projects & users', href: '/admin' }],
  '/roles':            [{ text: 'Administration', href: '/admin' }, { text: 'Roles', href: '/roles' }],
  '/role-assignments': [{ text: 'Administration', href: '/admin' }, { text: 'Role assignments', href: '/role-assignments' }],
  '/alarms':           [{ text: 'Observability & Cost', href: '/telemetry' }, { text: 'Alarms', href: '/alarms' }],
  '/flavors':          [{ text: 'Administration', href: '/admin' }, { text: 'Flavors', href: '/flavors' }],
  '/flavors/create':   [{ text: 'Administration', href: '/admin' }, { text: 'Flavors', href: '/flavors' }, { text: 'Create flavor', href: '/flavors/create' }],
  '/xide':             [{ text: 'Compute', href: '/servers' }, { text: 'XIDE environments', href: '/xide' }],
  '/xide/create':      [{ text: 'Compute', href: '/servers' }, { text: 'XIDE environments', href: '/xide' }, { text: 'Create environment', href: '/xide/create' }],
}

// Global search targets — jump straight to a service page.
const SEARCH_TARGETS = [
  { value: 'Instances', href: '/servers' },
  { value: 'Images', href: '/images' },
  { value: 'Key pairs', href: '/keypairs' },
  { value: 'Server groups', href: '/server-groups' },
  { value: 'Volumes', href: '/volumes' },
  { value: 'Snapshots', href: '/snapshots' },
  { value: 'Volume types', href: '/volume-types' },
  { value: 'Networks', href: '/networks' },
  { value: 'Routers', href: '/routers' },
  { value: 'Security groups', href: '/security-groups' },
  { value: 'Floating IPs', href: '/floating-ips' },
  { value: 'Topology', href: '/topology' },
  { value: 'Projects & users', href: '/admin' },
  { value: 'Roles', href: '/roles' },
  { value: 'Role assignments', href: '/role-assignments' },
  { value: 'Stacks', href: '/stacks' },
  { value: 'Alarms', href: '/alarms' },
  { value: 'Flavors', href: '/flavors' },
  { value: 'XIDE environments', href: '/xide' },
]

function buildCrumbs(pathname) {
  const base = { text: 'CloudX', href: '/' }
  if (CRUMBS[pathname]) return [base, ...CRUMBS[pathname]]

  // Detail routes: /servers/:id, /volumes/:id, /networks/:id, /console/:id
  const [, section, id] = pathname.split('/')
  const parentKey = `/${section}`
  const short = id ? `${id.slice(0, 8)}…` : ''
  if (section === 'console') {
    return [base, { text: 'Compute', href: '/servers' }, { text: 'Instances', href: '/servers' }, { text: `Console ${short}`, href: pathname }]
  }
  if (CRUMBS[parentKey]) {
    return [base, ...CRUMBS[parentKey], { text: short, href: pathname }]
  }
  return [base]
}

export default function Layout() {
  const { user, logout, projects, switchProject } = useAuth()
  const { isDark, toggleMode } = useTheme()
  const flashItems = useFlashItems()
  const { open: xshellOpen, setOpen: setXShellOpen } = useXShell()
  const navigate = useNavigate()
  const location = useLocation()
  const [navOpen, setNavOpen] = useState(true)
  const [search, setSearch] = useState('')

  const crumbs = useMemo(() => buildCrumbs(location.pathname), [location.pathname])

  const navItems = useMemo(() => {
    if (user?.role === 'admin') return NAV_ITEMS
    // a domain admin gets the identity pages only; the rest of Administration is system-wide
    const idPages = ['/admin', '/role-assignments']
    return NAV_ITEMS.map(s => s.text !== 'Administration' ? s
      : user?.domain_admin ? { ...s, items: s.items.filter(i => idPages.includes(i.href)) } : null).filter(Boolean)
  }, [user?.role, user?.domain_admin])

  // Highlight the deepest matching nav link.
  const activeHref = useMemo(() => {
    if (location.pathname === '/') return '/'
    const flat = navItems.flatMap(i => (i.items ? i.items : i.type === 'link' ? [i] : []))
    const match = flat
      .filter(i => location.pathname.startsWith(i.href) && i.href !== '/')
      .sort((a, b) => b.href.length - a.href.length)[0]
    return match?.href || location.pathname
  }, [location.pathname, navItems])

  const handleFollow = (event) => {
    if (event.detail.external) return
    event.preventDefault()
    navigate(event.detail.href)
  }

  const projectItems = (projects || []).map(p => ({
    id: p.id,
    text: p.name,
    iconName: p.id === user?.project_id ? 'check' : undefined,
  }))

  const utilities = [
    // Project (account) switcher — mirrors the AWS account/region menus.
    {
      type: 'menu-dropdown',
      iconName: 'folder',
      text: user?.project_name || user?.project_id || 'Project',
      description: 'Active project scope',
      ariaLabel: 'Project switcher',
      items: projectItems.length
        ? [{ id: 'projects', text: 'Switch project', items: projectItems }]
        : [{ id: 'none', text: 'No other projects', disabled: true }],
      onItemClick: ({ detail }) => {
        if (detail.id && detail.id !== user?.project_id && detail.id !== 'none') {
          switchProject(detail.id)
        }
      },
    },
    // XShell / XIDE docked panel toggle — the AWS CloudShell terminal icon.
    {
      type: 'button',
      iconSvg: <Terminal size={16} />,
      text: xshellOpen ? 'XShell' : 'XShell',
      ariaLabel: xshellOpen ? 'Close XShell' : 'Open XShell',
      title: xshellOpen ? 'Close XShell' : 'Open XShell',
      onClick: () => setXShellOpen(!xshellOpen),
    },
    // Dark / light visual mode toggle.
    {
      type: 'button',
      iconSvg: isDark ? <Sun size={16} /> : <Moon size={16} />,
      text: isDark ? 'Light' : 'Dark',
      ariaLabel: isDark ? 'Switch to light mode' : 'Switch to dark mode',
      title: isDark ? 'Switch to light mode' : 'Switch to dark mode',
      onClick: toggleMode,
    },
    {
      type: 'menu-dropdown',
      text: user?.username || 'User',
      description: user?.role ? `Role: ${user.role}` : undefined,
      iconName: 'user-profile',
      items: [
        { id: 'api-access', text: 'API access & endpoints' },
        ...(user?.role === 'admin' ? [
          { id: 'keys', text: 'Access keys' },
          { id: 'roles', text: 'Role management' },
        ] : []),
        { id: 'signout', text: 'Sign out' },
      ],
      onItemClick: async ({ detail }) => {
        if (detail.id === 'signout') {
          await logout()
          navigate('/login')
        } else if (detail.id === 'api-access') {
          navigate('/')
        } else if (detail.id === 'keys') {
          navigate('/admin')
        } else if (detail.id === 'roles') {
          navigate('/roles')
        }
      },
    },
  ]

  return (
    <>
      <div
        id="cloudx-top-nav"
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 2001,
          background: 'var(--awsui-color-background-layout-main)',
        }}
      >
        <TopNavigation
          identity={{
            href: '/',
            title: 'CloudX Console',
            onFollow: (e) => { e.preventDefault(); navigate('/') },
          }}
          search={
            <Autosuggest
              value={search}
              onChange={({ detail }) => setSearch(detail.value)}
              onSelect={({ detail }) => {
                const target = SEARCH_TARGETS.find(t => t.value === detail.value)
                if (target) {
                  setSearch('')
                  navigate(target.href)
                }
              }}
              options={SEARCH_TARGETS.map(t => ({ value: t.value }))}
              enteredTextLabel={v => `Search for "${v}"`}
              ariaLabel="Search CloudX services"
              placeholder="Search services"
              empty="No matching service"
            />
          }
          utilities={utilities}
        />
      </div>
      <AppLayout
        headerSelector="#cloudx-top-nav"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigation={
          <SideNavigation
            header={{ href: '/', text: 'Dashboard' }}
            activeHref={activeHref}
            items={navItems}
            onFollow={handleFollow}
          />
        }
        breadcrumbs={
          <BreadcrumbGroup items={crumbs} onFollow={handleFollow} ariaLabel="Breadcrumbs" />
        }
        notifications={<Flashbar items={flashItems} stackItems />}
        toolsHide
        content={<Outlet />}
      />
    </>
  )
}
