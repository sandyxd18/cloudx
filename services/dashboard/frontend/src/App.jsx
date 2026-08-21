import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import { ToastProvider, AuthProvider, useAuth, useToast } from './context.jsx'
import Layout from './components/Layout.jsx'
import LoginPage from './pages/Login.jsx'
import OverviewPage from './pages/Overview.jsx'
import ServersPage from './pages/Servers.jsx'
import ServerDetailPage from './pages/ServerDetail.jsx'
import FlavorsPage from './pages/Flavors.jsx'
import KeypairsPage from './pages/Keypairs.jsx'
import NetworksPage from './pages/Networks.jsx'
import NetworkDetailPage from './pages/NetworkDetail.jsx'
import RoutersPage from './pages/Routers.jsx'
import FloatingIPsPage from './pages/FloatingIPs.jsx'
import SecurityGroupsPage from './pages/SecurityGroups.jsx'
import ImagesPage from './pages/Images.jsx'
import VolumesPage from './pages/Volumes.jsx'
import VolumeDetailPage from './pages/VolumeDetail.jsx'
import SnapshotsPage from './pages/Snapshots.jsx'
import VolumeTypesPage from './pages/VolumeTypes.jsx'
import ServerGroupsPage from './pages/ServerGroups.jsx'
import AdminPage from './pages/Admin.jsx'
import TopologyPage from './pages/Topology.jsx'
import ConsolePage from './pages/Console.jsx'
import RolesPage from './pages/Roles.jsx'

function PrivateRoute({ children }) {
  const { user } = useAuth()
  return user ? children : <Navigate to="/login" replace />
}

// Listens for the cloudx:session-expired event dispatched by api.js
// and redirects to /login with a toast message instead of a hard refresh.
function SessionExpiredHandler() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()

  useEffect(() => {
    const handler = () => {
      logout()
      toast('Session expired. Please log in again.', 'error')
      navigate('/login', { replace: true })
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
          <Route path="servers/:id" element={<ServerDetailPage />} />
          <Route path="flavors" element={<FlavorsPage />} />
          <Route path="keypairs" element={<KeypairsPage />} />
          <Route path="server-groups" element={<ServerGroupsPage />} />
          <Route path="networks" element={<NetworksPage />} />
          <Route path="networks/:id" element={<NetworkDetailPage />} />
          <Route path="routers" element={<RoutersPage />} />
          <Route path="floating-ips" element={<FloatingIPsPage />} />
          <Route path="security-groups" element={<SecurityGroupsPage />} />
          <Route path="images" element={<ImagesPage />} />
          <Route path="volumes" element={<VolumesPage />} />
          <Route path="volumes/:id" element={<VolumeDetailPage />} />
          <Route path="volume-types" element={<VolumeTypesPage />} />
          <Route path="snapshots" element={<SnapshotsPage />} />
          <Route path="topology" element={<TopologyPage />} />
          <Route path="console/:id" element={<ConsolePage />} />
          <Route path="roles" element={<RolesPage />} />
          <Route path="admin" element={<AdminPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <AppRoutes />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
