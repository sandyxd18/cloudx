// API client — all requests go through FastAPI proxy at /api/*

const BASE = '/api'

async function request(method, path, body = null, opts = {}) {
  const headers = { 'Content-Type': 'application/json' }

  const config = {
    method,
    headers,
    // ensure cookies are sent with requests to /api
    // although not strictly needed for same-origin if not explicitly set to 'omit'
    // but good practice.
    credentials: 'same-origin',
    ...opts,
  }
  if (body !== null) {
    config.body = JSON.stringify(body)
  }

  const resp = await fetch(`${BASE}${path}`, config)
  if (resp.status === 401) {
    // Dispatch a custom event so the app can handle it gracefully
    // (show a toast + redirect) rather than forcing a hard navigation
    // in the middle of an unrelated component render.
    window.dispatchEvent(new CustomEvent('cloudx:session-expired'))
    // Return a rejected promise so callers can catch and show their own error
    throw new Error('Session expired. Please log in again.')
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
  login: (body) => request('POST', '/auth/session/login', body),
  logout: () => request('POST', '/auth/session/logout'),
  getSessionMe: () => request('GET', '/auth/session/me'),
  getProjects: () => request('GET', '/auth/projects'),
  createProject: (b) => request('POST', '/auth/projects', b),
  deleteProject: (id) => request('DELETE', `/auth/projects/${id}`),
  getUsers: () => request('GET', '/auth/users'),
  createUser: (b) => request('POST', '/auth/users', b),
  deleteUser: (id) => request('DELETE', `/auth/users/${id}`),
  switchProject: (projectId) => request('POST', '/auth/session/switch-project', { project_id: projectId }),

  // Auth — Access Keys
  getAccessKeys: () => request('GET', '/auth/access-keys'),
  createAccessKey: (b) => request('POST', '/auth/access-keys', b),
  deleteAccessKey: (id) => request('DELETE', `/auth/access-keys/${id}`),

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
  getSecurityGroup: (id) => request('GET', `/networking/security-groups/${id}`),
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

  // Volume Quotas
  getQuota: (projectId, withUsage = true) =>
    request('GET', `/block-store/os-quota-sets/${projectId}${withUsage ? '?usage=true' : ''}`),
  updateQuota: (projectId, b) => request('PUT', `/block-store/os-quota-sets/${projectId}`, b),

  // Service Catalog
  getServiceCatalog: () => request('GET', '/auth/v3/auth/catalog'),

  // Image upload with progress (returns a Promise that resolves to the image object)
  uploadImageWithProgress(formData, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/image/images`)
      xhr.withCredentials = true
      xhr.upload.onprogress = (evt) => {
        if (evt.lengthComputable && onProgress) {
          onProgress(Math.round((evt.loaded / evt.total) * 100))
        }
      }
      xhr.onload = () => {
        if (xhr.status === 401) {
          window.dispatchEvent(new CustomEvent('cloudx:session-expired'))
          reject(new Error('Session expired.'))
          return
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)) } catch { resolve({}) }
        } else {
          let detail = `HTTP ${xhr.status}`
          try { detail = JSON.parse(xhr.responseText).detail || detail } catch { /* noop */ }
          reject(new Error(detail))
        }
      }
      xhr.onerror = () => reject(new Error('Network error'))
      xhr.send(formData)
    })
  },
}
