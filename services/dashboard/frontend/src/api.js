// API client — all requests go through FastAPI proxy at /api/*

const BASE = '/api'

function getToken() {
  return localStorage.getItem('cloudx_token')
}

async function request(method, path, body = null, opts = {}) {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const config = {
    method,
    headers,
    ...opts,
  }
  if (body !== null) {
    config.body = JSON.stringify(body)
  }

  const resp = await fetch(`${BASE}${path}`, config)
  if (resp.status === 401) {
    localStorage.removeItem('cloudx_token')
    localStorage.removeItem('cloudx_user')
    window.location.href = '/login'
    return
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const err = await resp.json()
      detail = err.detail || JSON.stringify(err)
    } catch { /* noop */ }
    throw new Error(detail)
  }
  if (resp.status === 204) return null
  return resp.json()
}

export const api = {
  // Auth
  login: (body) => request('POST', '/auth/auth/tokens', body),
  getProjects: () => request('GET', '/auth/projects'),
  createProject: (b) => request('POST', '/auth/projects', b),
  deleteProject: (id) => request('DELETE', `/auth/projects/${id}`),
  getUsers: () => request('GET', '/auth/users'),
  createUser: (b) => request('POST', '/auth/users', b),
  deleteUser: (id) => request('DELETE', `/auth/users/${id}`),

  // Compute — Servers
  getServers: () => request('GET', '/compute/servers'),
  createServer: (b) => request('POST', '/compute/servers', b),
  getServer: (id) => request('GET', `/compute/servers/${id}`),
  deleteServer: (id) => request('DELETE', `/compute/servers/${id}`),
  serverAction: (id, action, body={}) => request('POST', `/compute/servers/${id}/${action}`, body),
  getServerLogs: (id) => request('POST', `/compute/servers/${id}/logs`, {}),

  // Compute — Flavors
  getFlavors: () => request('GET', '/compute/flavors'),
  createFlavor: (b) => request('POST', '/compute/flavors', b),
  deleteFlavor: (id) => request('DELETE', `/compute/flavors/${id}`),

  // Compute — Keypairs
  getKeypairs: () => request('GET', '/compute/keypairs'),
  createKeypair: (b) => request('POST', '/compute/keypairs', b),
  deleteKeypair: (name) => request('DELETE', `/compute/keypairs/${name}`),

  // Compute — Server Groups
  getServerGroups: () => request('GET', '/compute/server-groups'),
  createServerGroup: (b) => request('POST', '/compute/server-groups', b),
  deleteServerGroup: (id) => request('DELETE', `/compute/server-groups/${id}`),

  // Networking — Networks
  getNetworks: () => request('GET', '/networking/networks'),
  createNetwork: (b) => request('POST', '/networking/networks', b),
  deleteNetwork: (id) => request('DELETE', `/networking/networks/${id}`),

  // Networking — Subnets
  getSubnets: () => request('GET', '/networking/subnets'),
  createSubnet: (b) => request('POST', '/networking/subnets', b),
  deleteSubnet: (id) => request('DELETE', `/networking/subnets/${id}`),

  // Networking — Routers
  getRouters: () => request('GET', '/networking/routers'),
  createRouter: (b) => request('POST', '/networking/routers', b),
  deleteRouter: (id) => request('DELETE', `/networking/routers/${id}`),
  setRouterGateway: (id, b) => request('POST', `/networking/routers/${id}/gateway`, b),
  addRouterInterface: (id, b) => request('POST', `/networking/routers/${id}/interfaces`, b),

  // Networking — Floating IPs
  getFloatingIPs: () => request('GET', '/networking/floating-ips'),
  createFloatingIP: (b) => request('POST', '/networking/floating-ips', b),
  deleteFloatingIP: (id) => request('DELETE', `/networking/floating-ips/${id}`),
  associateFloatingIP: (id, b) => request('POST', `/networking/floating-ips/${id}/associate`, b),
  disassociateFloatingIP: (id) => request('POST', `/networking/floating-ips/${id}/disassociate`, {}),

  // Networking — Security Groups
  getSecurityGroups: () => request('GET', '/networking/security-groups'),
  createSecurityGroup: (b) => request('POST', '/networking/security-groups', b),
  deleteSecurityGroup: (id) => request('DELETE', `/networking/security-groups/${id}`),
  createSecurityGroupRule: (id, b) => request('POST', `/networking/security-groups/${id}/rules`, b),
  deleteSecurityGroupRule: (ruleId) => request('DELETE', `/networking/security-group-rules/${ruleId}`),

  // Images
  getImages: () => request('GET', '/image/images'),
  deleteImage: (id) => request('DELETE', `/image/images/${id}`),
  deactivateImage: (id) => request('POST', `/image/images/${id}/deactivate`, {}),
  reactivateImage: (id) => request('POST', `/image/images/${id}/reactivate`, {}),
  updateImage: (id, b) => request('PATCH', `/image/images/${id}`, b),

  // Block Store — Volumes
  getVolumes: () => request('GET', '/block-store/volumes'),
  createVolume: (b) => request('POST', '/block-store/volumes', b),
  deleteVolume: (id) => request('DELETE', `/block-store/volumes/${id}`),
  extendVolume: (id, b) => request('POST', `/block-store/volumes/${id}/extend`, b),
  attachVolume: (id, b) => request('POST', `/block-store/volumes/${id}/attach`, b),
  detachVolume: (id) => request('POST', `/block-store/volumes/${id}/detach`, {}),

  // Block Store — Snapshots
  getSnapshots: () => request('GET', '/block-store/snapshots'),
  createSnapshot: (volId, b) => request('POST', `/block-store/volumes/${volId}/snapshots`, b),
  deleteSnapshot: (id) => request('DELETE', `/block-store/snapshots/${id}`),

  // Block Store — Volume Types
  getVolumeTypes:    () => request('GET', '/block-store/volume-types'),
  createVolumeType:  (b) => request('POST', '/block-store/volume-types', b),
  updateVolumeType:  (ref, b) => request('PATCH', `/block-store/volume-types/${ref}`, b),
  deleteVolumeType:  (ref) => request('DELETE', `/block-store/volume-types/${ref}`),

  // Placement
  getUsages: () => request('GET', '/placement/usages'),
  getProviders: () => request('GET', '/placement/resource-providers'),
}
