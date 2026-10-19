import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { Component, useEffect } from 'react'
import { ToastProvider, AuthProvider, useAuth, useToast } from './context.jsx'
import { ThemeProvider } from './theme.jsx'
import { XShellProvider } from './components/XShellContext.jsx'
import XShellPanel from './components/XShellPanel.jsx'
import Layout from './components/Layout.jsx'
import LoginPage from './pages/Login.jsx'
import OverviewPage from './pages/Overview.jsx'
import ServersPage from './pages/Servers.jsx'
import ServerLaunchPage from './pages/ServerLaunch.jsx'
import ServerDetailPage from './pages/ServerDetail.jsx'
import FlavorsPage from './pages/Flavors.jsx'
import FlavorCreatePage from './pages/FlavorCreate.jsx'
import KeypairsPage from './pages/Keypairs.jsx'
import KeypairCreatePage from './pages/KeypairCreate.jsx'
import KeypairImportPage from './pages/KeypairImport.jsx'
import NetworksPage from './pages/Networks.jsx'
import NetworkCreatePage from './pages/NetworkCreate.jsx'
import NetworkDetailPage from './pages/NetworkDetail.jsx'
import SubnetCreatePage from './pages/SubnetCreate.jsx'
import RoutersPage from './pages/Routers.jsx'
import RouterCreatePage from './pages/RouterCreate.jsx'
import RouterDetailPage from './pages/RouterDetail.jsx'
import FloatingIPsPage from './pages/FloatingIPs.jsx'
import FloatingIPAllocatePage from './pages/FloatingIPAllocate.jsx'
import FloatingIPAssociatePage from './pages/FloatingIPAssociate.jsx'
import SecurityGroupsPage from './pages/SecurityGroups.jsx'
import SecurityGroupCreatePage from './pages/SecurityGroupCreate.jsx'
import SecurityGroupDetailPage from './pages/SecurityGroupDetail.jsx'
import ImagesPage from './pages/Images.jsx'
import ImageCreatePage from './pages/ImageCreate.jsx'
import ImageDetailPage from './pages/ImageDetail.jsx'
import VolumesPage from './pages/Volumes.jsx'
import VolumeCreatePage from './pages/VolumeCreate.jsx'
import VolumeDetailPage from './pages/VolumeDetail.jsx'
import SnapshotsPage from './pages/Snapshots.jsx'
import SnapshotCreatePage from './pages/SnapshotCreate.jsx'
import SnapshotDetailPage from './pages/SnapshotDetail.jsx'
import VolumeTypesPage from './pages/VolumeTypes.jsx'
import ObjectStorePage from './pages/ObjectStore.jsx'
import BucketCreatePage from './pages/BucketCreate.jsx'
import BucketDetailPage from './pages/BucketDetail.jsx'
import DnsPage from './pages/Dns.jsx'
import FileStorePage from './pages/FileStore.jsx'
import ContainersPage from './pages/Containers.jsx'
import DatabasesPage from './pages/Databases.jsx'
import DnsZoneDetailPage from './pages/DnsZoneDetail.jsx'
import LoadBalancersPage from './pages/LoadBalancers.jsx'
import LoadBalancerCreatePage from './pages/LoadBalancerCreate.jsx'
import LoadBalancerDetailPage from './pages/LoadBalancerDetail.jsx'
import VolumeTypeCreatePage from './pages/VolumeTypeCreate.jsx'
import BucketTypesPage from './pages/BucketTypes.jsx'
import K8sEnginePage from './pages/K8sEngine.jsx'
import ScalingGroupsPage from './pages/ScalingGroups.jsx'
import SecretsPage from './pages/Secrets.jsx'
import VpcPeeringPage from './pages/VpcPeering.jsx'
import VpnGatewaysPage from './pages/VpnGateways.jsx'
import BgpPeersPage from './pages/BgpPeers.jsx'
import TelemetryPage from './pages/Telemetry.jsx'
import BillingPage from './pages/Billing.jsx'
import EventLogsPage from './pages/EventLogs.jsx'
import HypervisorsPage from './pages/Hypervisors.jsx'
import ServerGroupsPage from './pages/ServerGroups.jsx'
import ServerGroupCreatePage from './pages/ServerGroupCreate.jsx'
import ServerGroupDetailPage from './pages/ServerGroupDetail.jsx'
import AdminPage from './pages/Admin.jsx'
import TopologyPage from './pages/Topology.jsx'
import ConsolePage from './pages/Console.jsx'
import RolesPage from './pages/Roles.jsx'
import RoleAssignmentsPage from './pages/RoleAssignments.jsx'
import StacksPage from './pages/Stacks.jsx'
import AlarmsPage from './pages/Alarms.jsx'
import XIDEPage from './pages/XIDE.jsx'
import XIDECreatePage from './pages/XIDECreate.jsx'

class GlobalErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }
  componentDidCatch(error, errorInfo) {
    console.error('Application error caught by GlobalErrorBoundary:', error, errorInfo)
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, fontFamily: 'system-ui, -apple-system, sans-serif', maxWidth: 600, margin: '60px auto', background: '#fff', borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
          <h2 style={{ marginTop: 0, color: '#d13212' }}>Something went wrong</h2>
          <p style={{ color: '#545b64', fontSize: 14 }}>
            An unexpected error occurred while loading this page:
          </p>
          <pre style={{ background: '#f2f3f3', padding: 12, borderRadius: 4, fontSize: 12, overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <div style={{ marginTop: 20 }}>
            <button
              onClick={() => {
                localStorage.clear()
                window.location.href = '/login'
              }}
              style={{
                backgroundColor: '#0972d3',
                color: '#fff',
                border: 'none',
                padding: '8px 16px',
                borderRadius: 4,
                cursor: 'pointer',
                fontWeight: 'bold'
              }}
            >
              Sign out & return to Login
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

class XShellErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  componentDidCatch(error) {
    console.error('XShell panel crashed, hiding:', error)
  }
  render() {
    if (this.state.hasError) return null
    return this.props.children
  }
}

function PrivateRoute({ children }) {
  const { user } = useAuth()
  return user ? children : <Navigate to="/login" replace />
}

// Listens for the cloudx:session-expired event dispatched by api.js
// and redirects to /login with a notification instead of a hard refresh.
function SessionExpiredHandler() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()

  useEffect(() => {
    const handler = () => {
      logout()
      if (window.location.pathname !== '/login') {
        toast('Session expired. Please sign in again.', 'error')
        navigate('/login', { replace: true })
      }
    }
    window.addEventListener('cloudx:session-expired', handler)
    return () => window.removeEventListener('cloudx:session-expired', handler)
  }, [logout, navigate, toast])

  return null
}

function AppRoutes() {
  const { user } = useAuth()
  return (
    <>
      <SessionExpiredHandler />
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
        <Route path="/" element={<PrivateRoute><Layout /></PrivateRoute>}>
          <Route index element={<OverviewPage />} />
          <Route path="servers" element={<ServersPage />} />
          <Route path="servers/create" element={<ServerLaunchPage />} />
          <Route path="servers/:id" element={<ServerDetailPage />} />
          <Route path="flavors" element={<FlavorsPage />} />
          <Route path="flavors/create" element={<FlavorCreatePage />} />
          <Route path="keypairs" element={<KeypairsPage />} />
          <Route path="keypairs/create" element={<KeypairCreatePage />} />
          <Route path="keypairs/import" element={<KeypairImportPage />} />
          <Route path="server-groups" element={<ServerGroupsPage />} />
          <Route path="server-groups/create" element={<ServerGroupCreatePage />} />
          <Route path="server-groups/:id" element={<ServerGroupDetailPage />} />
          <Route path="scaling-groups" element={<ScalingGroupsPage />} />
          <Route path="k8s-engine" element={<K8sEnginePage />} />
          <Route path="secrets" element={<SecretsPage />} />
          <Route path="vpc-peering" element={<VpcPeeringPage />} />
          <Route path="vpn-gateways" element={<VpnGatewaysPage />} />
          <Route path="bgp-peers" element={<BgpPeersPage />} />
          <Route path="telemetry" element={<TelemetryPage />} />
          <Route path="billing" element={<BillingPage />} />
          <Route path="event-logs" element={<EventLogsPage />} />
          <Route path="hypervisors" element={<HypervisorsPage />} />
          <Route path="networks" element={<NetworksPage />} />
          <Route path="networks/create" element={<NetworkCreatePage />} />
          <Route path="networks/:id" element={<NetworkDetailPage />} />
          <Route path="subnets/create" element={<SubnetCreatePage />} />
          <Route path="routers" element={<RoutersPage />} />
          <Route path="routers/create" element={<RouterCreatePage />} />
          <Route path="routers/:id" element={<RouterDetailPage />} />
          <Route path="floating-ips" element={<FloatingIPsPage />} />
          <Route path="floating-ips/allocate" element={<FloatingIPAllocatePage />} />
          <Route path="floating-ips/associate" element={<FloatingIPAssociatePage />} />
          <Route path="security-groups" element={<SecurityGroupsPage />} />
          <Route path="security-groups/create" element={<SecurityGroupCreatePage />} />
          <Route path="security-groups/:id" element={<SecurityGroupDetailPage />} />
          <Route path="images" element={<ImagesPage />} />
          <Route path="images/create" element={<ImageCreatePage />} />
          <Route path="images/:id" element={<ImageDetailPage />} />
          <Route path="volumes" element={<VolumesPage />} />
          <Route path="volumes/create" element={<VolumeCreatePage />} />
          <Route path="volumes/:id" element={<VolumeDetailPage />} />
          <Route path="volume-types" element={<VolumeTypesPage />} />
          <Route path="volume-types/create" element={<VolumeTypeCreatePage />} />
          <Route path="bucket-types" element={<BucketTypesPage />} />
          <Route path="snapshots" element={<SnapshotsPage />} />
          <Route path="snapshots/create" element={<SnapshotCreatePage />} />
          <Route path="snapshots/:id" element={<SnapshotDetailPage />} />
          <Route path="object-store" element={<ObjectStorePage />} />
          <Route path="object-store/create" element={<BucketCreatePage />} />
          <Route path="object-store/buckets/:name" element={<BucketDetailPage />} />
          <Route path="dns" element={<DnsPage />} />
          <Route path="file-store" element={<FileStorePage />} />
          <Route path="containers" element={<ContainersPage />} />
        <Route path="databases" element={<DatabasesPage />} />
          <Route path="dns/:id" element={<DnsZoneDetailPage />} />
          <Route path="load-balancers" element={<LoadBalancersPage />} />
          <Route path="load-balancers/create" element={<LoadBalancerCreatePage />} />
          <Route path="load-balancers/:id" element={<LoadBalancerDetailPage />} />
          <Route path="topology" element={<TopologyPage />} />
          <Route path="console/:id" element={<ConsolePage />} />
          <Route path="roles" element={<RolesPage />} />
          <Route path="role-assignments" element={<RoleAssignmentsPage />} />
          <Route path="stacks" element={<StacksPage />} />
          <Route path="alarms" element={<AlarmsPage />} />
          <Route path="admin" element={<AdminPage />} />
          <Route path="xide" element={<XIDEPage />} />
          <Route path="xide/create" element={<XIDECreatePage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

export default function App() {
  return (
    <GlobalErrorBoundary>
      <ThemeProvider>
        <BrowserRouter>
          <AuthProvider>
            <ToastProvider>
              <XShellProvider>
                <AppRoutes />
                <XShellErrorBoundary>
                  <XShellPanel />
                </XShellErrorBoundary>
              </XShellProvider>
            </ToastProvider>
          </AuthProvider>
        </BrowserRouter>
      </ThemeProvider>
    </GlobalErrorBoundary>
  )
}
