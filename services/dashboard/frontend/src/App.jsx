import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ToastProvider, AuthProvider, useAuth } from './context.jsx'
import Layout from './components/Layout.jsx'
import LoginPage from './pages/Login.jsx'
import OverviewPage from './pages/Overview.jsx'
import ServersPage from './pages/Servers.jsx'
import FlavorsPage from './pages/Flavors.jsx'
import KeypairsPage from './pages/Keypairs.jsx'
import NetworksPage from './pages/Networks.jsx'
import RoutersPage from './pages/Routers.jsx'
import FloatingIPsPage from './pages/FloatingIPs.jsx'
import SecurityGroupsPage from './pages/SecurityGroups.jsx'
import ImagesPage from './pages/Images.jsx'
import VolumesPage from './pages/Volumes.jsx'
import SnapshotsPage from './pages/Snapshots.jsx'
import VolumeTypesPage from './pages/VolumeTypes.jsx'
import ServerGroupsPage from './pages/ServerGroups.jsx'
import AdminPage from './pages/Admin.jsx'

function PrivateRoute({ children }) {
  const { user } = useAuth()
  return user ? children : <Navigate to="/login" replace />
}

function AppRoutes() {
  const { user } = useAuth()
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/" element={<PrivateRoute><Layout /></PrivateRoute>}>
        <Route index element={<OverviewPage />} />
        <Route path="servers" element={<ServersPage />} />
        <Route path="flavors" element={<FlavorsPage />} />
        <Route path="keypairs" element={<KeypairsPage />} />
        <Route path="server-groups" element={<ServerGroupsPage />} />
        <Route path="networks" element={<NetworksPage />} />
        <Route path="routers" element={<RoutersPage />} />
        <Route path="floating-ips" element={<FloatingIPsPage />} />
        <Route path="security-groups" element={<SecurityGroupsPage />} />
        <Route path="images" element={<ImagesPage />} />
        <Route path="volumes" element={<VolumesPage />} />
        <Route path="volume-types" element={<VolumeTypesPage />} />
        <Route path="snapshots" element={<SnapshotsPage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
