// API client — all requests go through FastAPI proxy at /api/*

const BASE = '/api'

async function request(method, path, body = null, opts = {}, skipJson = false) {
  const headers = skipJson ? {} : { 'Content-Type': 'application/json' }

  // Inject Bearer token from localStorage for resilience in Incognito/Private Browsing modes
  const token = localStorage.getItem('cloudx_token')
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const config = {
    method,
    headers,
    credentials: 'include',
    ...opts,
  }
  if (body !== null) {
    config.body = skipJson ? body : JSON.stringify(body)
  }

  const resp = await fetch(`${BASE}${path}`, config)
  if (resp.status === 401) {
    if (!path.startsWith('/auth/session')) {
      localStorage.removeItem('cloudx_token')
      window.dispatchEvent(new CustomEvent('cloudx:session-expired'))
    }
    let detail = 'Session expired. Please log in again.'
    try {
      const err = await resp.json()
      if (err.detail) detail = err.detail
    } catch { /* noop */ }
    throw new Error(detail)
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
  login: async (body) => {
    const res = await request('POST', '/auth/session/login', body)
    if (res?.token) {
      localStorage.setItem('cloudx_token', res.token)
    }
    return res
  },
  logout: async () => {
    try {
      await request('POST', '/auth/session/logout')
    } finally {
      localStorage.removeItem('cloudx_token')
    }
  },
  getSessionMe: () => request('GET', '/auth/session/me'),
  getProjects: () => request('GET', '/auth/projects'),
  createProject: (b) => request('POST', '/auth/projects', b),
  deleteProject: (id) => request('DELETE', `/auth/projects/${id}`),
  getUsers: () => request('GET', '/auth/users'),
  createUser: (b) => request('POST', '/auth/users', b),
  updateUser: (id, b) => request('PUT', `/auth/users/${id}`, b),
  deleteUser: (id) => request('DELETE', `/auth/users/${id}`),
  getAuditLogs: () => request('GET', '/auth/audit-logs'),
  switchProject: (projectId) => request('POST', '/auth/session/switch-project', { project_id: projectId }),

  // Auth — Access Keys
  getAccessKeys: () => request('GET', '/auth/access-keys'),
  createAccessKey: (b) => request('POST', '/auth/access-keys', b),
  deleteAccessKey: (id) => request('DELETE', `/auth/access-keys/${id}`),

  // Auth — Domains (Keystone v3)
  getDomains: () => request('GET', '/auth/v3/domains'),
  createDomain: (b) => request('POST', '/auth/v3/domains', b),
  deleteDomain: (id) => request('DELETE', `/auth/v3/domains/${id}`),

  // Auth — Application Credentials (Keystone v3)
  getAppCredentials: (userId) => request('GET', `/auth/v3/users/${userId}/application_credentials`),
  createAppCredential: (userId, b) => request('POST', `/auth/v3/users/${userId}/application_credentials`, b),
  deleteAppCredential: (userId, credId) => request('DELETE', `/auth/v3/users/${userId}/application_credentials/${credId}`),
  loginWithAppCredential: (b) => request('POST', '/auth/tokens/application-credential', b),

  // Compute — Servers
  getServers: () => request('GET', '/compute/servers'),
  createServer: (b) => request('POST', '/compute/servers', b),
  getServer: (id) => request('GET', `/compute/servers/${id}`),
  deleteServer: (id) => request('DELETE', `/compute/servers/${id}`),
  serverAction: (id, action, body={}) => request('POST', `/compute/servers/${id}/${action}`, body),
  getServerLogs: (id) => request('POST', `/compute/servers/${id}/logs`, {}),

  // Compute — Console (noVNC)
  getRemoteConsole: (id) => request('POST', `/compute/servers/${id}/remote-consoles`, {
    remote_console: { type: 'novnc', protocol: 'vnc' }
  }),

  // Compute — Shelve / Unshelve
  shelveServer: (id) => request('POST', `/compute/servers/${id}/shelve`, {}),
  unshelveServer: (id) => request('POST', `/compute/servers/${id}/unshelve`, {}),

  // Compute — Instance monitoring (EC2-style "Monitoring" tab)
  getServerMetrics: (id, timeRange = '1h') => request('GET', `/compute/servers/${id}/metrics?time_range=${timeRange}`),

  // Compute — Host capacity & cluster allocation trend (Overview page)
  getHost: () => request('GET', '/compute/host'),
  getAllocationHistory: (hours = 24) => request('GET', `/compute/allocation-history?hours=${hours}`),

  // Compute — Resize confirm / revert
  confirmResize: (id) => request('POST', `/compute/servers/${id}/confirmResize`, {}),
  revertResize: (id) => request('POST', `/compute/servers/${id}/revertResize`, {}),

  // Compute — Flavors
  getFlavors: () => request('GET', '/compute/flavors'),
  createFlavor: (b) => request('POST', '/compute/flavors', b),
  deleteFlavor: (id) => request('DELETE', `/compute/flavors/${id}`),

  // Compute — Keypairs
  getKeypairs: () => request('GET', '/compute/keypairs'),
  createKeypair: (b) => request('POST', '/compute/keypairs', b),
  importKeypair: (b) => request('POST', '/compute/keypairs/import', b),
  deleteKeypair: (name) => request('DELETE', `/compute/keypairs/${name}`),

  // Compute — Server Groups
  getServerGroups: () => request('GET', '/compute/server-groups'),
  getServerGroup: (id) => request('GET', `/compute/server-groups/${id}`),
  createServerGroup: (b) => request('POST', '/compute/server-groups', b),
  deleteServerGroup: (id) => request('DELETE', `/compute/server-groups/${id}`),

  // Networking — Networks
  getNetworks: () => request('GET', '/networking/networks'),
  getNetwork: (id) => request('GET', `/networking/networks/${id}`),
  createNetwork: (b) => request('POST', '/networking/networks', b),
  deleteNetwork: (id) => request('DELETE', `/networking/networks/${id}`),

  // Networking — Subnets
  getSubnets: () => request('GET', '/networking/subnets'),
  createSubnet: (b) => request('POST', '/networking/subnets', b),
  deleteSubnet: (id) => request('DELETE', `/networking/subnets/${id}`),

  // Networking — Ports
  getPorts: () => request('GET', '/networking/ports'),

  // Networking — Routers
  getRouters: () => request('GET', '/networking/routers'),
  getRouter: (id) => request('GET', `/networking/routers/${id}`),
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
  getSecurityGroupPresets: () => request('GET', '/networking/security-groups/presets'),
  applySecurityGroupPreset: (id, preset) => request('POST', `/networking/security-groups/${id}/apply-preset`, { preset }),
  createSecurityGroup: (b) => request('POST', '/networking/security-groups', b),
  deleteSecurityGroup: (id) => request('DELETE', `/networking/security-groups/${id}`),
  createSecurityGroupRule: (id, b) => request('POST', `/networking/security-groups/${id}/rules`, b),
  deleteSecurityGroupRule: (ruleId) => request('DELETE', `/networking/security-group-rules/${ruleId}`),

  // Networking — QoS Policies
  getQosPolicies: () => request('GET', '/networking/qos/policies'),
  createQosPolicy: (b) => request('POST', '/networking/qos/policies', b),
  deleteQosPolicy: (id) => request('DELETE', `/networking/qos/policies/${id}`),
  createQosBwRule: (policyId, b) => request('POST', `/networking/qos/policies/${policyId}/bandwidth_limit_rules`, b),

  // Networking — Subnet Pools
  getSubnetPools: () => request('GET', '/networking/subnetpools'),
  createSubnetPool: (b) => request('POST', '/networking/subnetpools', b),
  deleteSubnetPool: (id) => request('DELETE', `/networking/subnetpools/${id}`),

  // Network Topology (aggregated client-side)
  async getNetworkTopology() {
    const [networks, subnets, routers, ports, floatingIPs, servers] = await Promise.allSettled([
      request('GET', '/networking/networks'),
      request('GET', '/networking/subnets'),
      request('GET', '/networking/routers'),
      request('GET', '/networking/ports'),  // may 404 if no port list endpoint
      request('GET', '/networking/floating-ips'),
      request('GET', '/compute/servers'),
    ])
    return {
      networks: networks.status === 'fulfilled' ? (networks.value.items || networks.value.networks || []) : [],
      subnets: subnets.status === 'fulfilled' ? (subnets.value.items || subnets.value.subnets || []) : [],
      routers: routers.status === 'fulfilled' ? (routers.value.items || routers.value.routers || []) : [],
      ports: ports.status === 'fulfilled' ? (ports.value.items || ports.value.ports || []) : [],
      floatingIPs: floatingIPs.status === 'fulfilled' ? (floatingIPs.value.items || floatingIPs.value.floating_ips || []) : [],
      servers: servers.status === 'fulfilled' ? (servers.value.items || []) : [],
    }
  },

  // Images
  getImages: () => request('GET', '/image/images'),
  getImage: (id) => request('GET', `/image/images/${id}`),
  deleteImage: (id) => request('DELETE', `/image/images/${id}`),
  deactivateImage: (id) => request('POST', `/image/images/${id}/deactivate`, {}),
  reactivateImage: (id) => request('POST', `/image/images/${id}/reactivate`, {}),
  updateImage: (id, b) => request('PATCH', `/image/images/${id}`, b),

  // Glance Tasks (async image import)
  createImageTask: (b) => request('POST', '/image/v2/tasks', b),
  getImageTask: (id) => request('GET', `/image/v2/tasks/${id}`),
  getImageTasks: () => request('GET', '/image/v2/tasks'),

  // Block Store — Volumes
  getVolumes: () => request('GET', '/block-store/volumes'),
  getVolume: (id) => request('GET', `/block-store/volumes/${id}`),
  createVolume: (b) => request('POST', '/block-store/volumes', b),
  deleteVolume: (id) => request('DELETE', `/block-store/volumes/${id}`),
  extendVolume: (id, b) => request('POST', `/block-store/volumes/${id}/extend`, b),
  attachVolume: (id, b) => request('POST', `/block-store/volumes/${id}/attach`, b),
  detachVolume: (id) => request('POST', `/block-store/volumes/${id}/detach`, {}),

  // Block Store — Snapshots
  getSnapshots: () => request('GET', '/block-store/snapshots'),
  getSnapshot: (id) => request('GET', `/block-store/snapshots/${id}`),
  createSnapshot: (volId, b) => request('POST', `/block-store/volumes/${volId}/snapshots`, b),
  deleteSnapshot: (id) => request('DELETE', `/block-store/snapshots/${id}`),

  // Block Store — Volume backups to object storage (Fase 5)
  getVolumeBackups: () => request('GET', '/block-store/backups'),
  createVolumeBackup: (volId, b) => request('POST', `/block-store/volumes/${volId}/backup`, b),
  deleteVolumeBackup: (id) => request('DELETE', `/block-store/backups/${id}`),
  restoreVolumeBackup: (id, b) => request('POST', `/block-store/backups/${id}/restore`, b),

  // Block Store — Volume Types
  getVolumeTypes:    () => request('GET', '/block-store/volume-types'),
  createVolumeType:  (b) => request('POST', '/block-store/volume-types', b),
  updateVolumeType:  (ref, b) => request('PATCH', `/block-store/volume-types/${ref}`, b),
  deleteVolumeType:  (ref) => request('DELETE', `/block-store/volume-types/${ref}`),

  // Object Store
  getBuckets: () => request('GET', '/object-store/buckets'),
  getBucket: (name) => request('GET', `/object-store/buckets/${name}`),
  createBucket: (b) => request('POST', '/object-store/buckets', b),
  updateBucket: (name, b) => request('PATCH', `/object-store/buckets/${name}`, b),
  deleteBucket: (name) => request('DELETE', `/object-store/buckets/${name}`),
  getBucketObjects: (name) => request('GET', `/object-store/buckets/${name}/objects`),
  uploadObject: (name, key, file) => {
    const form = new FormData()
    form.append('file', file)
    return request('PUT', `/object-store/buckets/${name}/objects/${key}`, form, {}, true)
  },
  deleteObject: (name, key) => request('DELETE', `/object-store/buckets/${name}/objects/${key}`),
  getBucketTypes: () => request('GET', '/object-store/admin/bucket-types'),
  createBucketType: (b) => request('POST', '/object-store/admin/bucket-types', b),
  deleteBucketType: (ref) => request('DELETE', `/object-store/admin/bucket-types/${ref}`),

  // Load Balancer
  getLoadBalancers: () => request('GET', '/load-balancer/load-balancers'),
  getLoadBalancer: (id) => request('GET', `/load-balancer/load-balancers/${id}`),
  createLoadBalancer: (b) => request('POST', '/load-balancer/load-balancers', b),
  deleteLoadBalancer: (id) => request('DELETE', `/load-balancer/load-balancers/${id}`),
  getListeners: (lbId) => request('GET', `/load-balancer/load-balancers/${lbId}/listeners`),
  createListener: (lbId, b) => request('POST', `/load-balancer/load-balancers/${lbId}/listeners`, b),
  createPool: (listenerId, b) => request('POST', `/load-balancer/listeners/${listenerId}/pools`, b),
  getPool: (poolId) => request('GET', `/load-balancer/pools/${poolId}`),
  syncPoolMembers: (poolId) => request('POST', `/load-balancer/pools/${poolId}/sync`),
  createHealthMonitor: (poolId, b) => request('POST', `/load-balancer/pools/${poolId}/health-monitors`, b),

  // Placement
  getUsages: () => request('GET', '/placement/usages'),
  getProviders: () => request('GET', '/placement/resource-providers'),
  getAllocationCandidates: (resources, opts = {}) => {
    const params = new URLSearchParams({ resources })
    if (opts.required) params.set('required', opts.required)
    if (opts.forbidden) params.set('forbidden', opts.forbidden)
    return request('GET', `/placement/allocation_candidates?${params.toString()}`)
  },
  // Placement — Custom Resource Classes
  getResourceClasses: () => request('GET', '/placement/resource_classes'),
  createResourceClass: (name) => request('PUT', `/placement/resource_classes/${name}`, {}),
  deleteResourceClass: (name) => request('DELETE', `/placement/resource_classes/${name}`),

  // Volume Quotas
  getQuota: (projectId, withUsage = true) =>
    request('GET', `/block-store/os-quota-sets/${projectId}${withUsage ? '?usage=true' : ''}`),
  updateQuota: (projectId, b) => request('PUT', `/block-store/os-quota-sets/${projectId}`, b),

  // XIDE — Cloud IDE workspaces
  getXIDEWorkspaces: () => request('GET', '/xide/workspaces'),
  createXIDEWorkspace: (b) => request('POST', '/xide/workspaces', b),
  deleteXIDEWorkspace: (id) => request('DELETE', `/xide/workspaces/${id}`),

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

  // Managed Kubernetes Engine
  getK8sTemplates: () => request('GET', '/k8s-engine/cluster-templates'),
  createK8sTemplate: (b) => request('POST', '/k8s-engine/cluster-templates', b),
  deleteK8sTemplate: (id) => request('DELETE', `/k8s-engine/cluster-templates/${id}`),
  getK8sClusters: () => request('GET', '/k8s-engine/clusters'),
  getK8sCluster: (id) => request('GET', `/k8s-engine/clusters/${id}`),
  createK8sCluster: (b) => request('POST', '/k8s-engine/clusters', b),
  scaleK8sCluster: (id, b) => request('POST', `/k8s-engine/clusters/${id}/scale`, b),
  deleteK8sCluster: (id) => request('DELETE', `/k8s-engine/clusters/${id}`),
  getKubeconfig: (id) => request('GET', `/k8s-engine/clusters/${id}/kubeconfig`),

  // Orchestration & Auto Scaling
  getScalingGroups: () => request('GET', '/orchestration/scaling-groups'),
  getScalingGroup: (id) => request('GET', `/orchestration/scaling-groups/${id}`),
  createScalingGroup: (b) => request('POST', '/orchestration/scaling-groups', b),
  deleteScalingGroup: (id) => request('DELETE', `/orchestration/scaling-groups/${id}`),
  scaleOutGroup: (id) => request('POST', `/orchestration/scaling-groups/${id}/scale-out`, {}),
  scaleInGroup: (id) => request('POST', `/orchestration/scaling-groups/${id}/scale-in`, {}),
  getScalingGroupInstances: (id) => request('GET', `/orchestration/scaling-groups/${id}/instances`),
  getScalingGroupHistory: (id) => request('GET', `/orchestration/scaling-groups/${id}/history`),

  // Key Manager / Secrets Vault
  getSecrets: () => request('GET', '/key-manager/secrets'),
  getSecret: (id) => request('GET', `/key-manager/secrets/${id}`),
  createSecret: (b) => request('POST', '/key-manager/secrets', b),
  deleteSecret: (id) => request('DELETE', `/key-manager/secrets/${id}`),
  getSecretContainers: () => request('GET', '/key-manager/containers'),
  createSecretContainer: (b) => request('POST', '/key-manager/containers', b),
  deleteSecretContainer: (id) => request('DELETE', `/key-manager/containers/${id}`),

  // Advanced Networking — VPC Peering
  getVpcPeerings: () => request('GET', '/networking/vpc-peerings'),
  createVpcPeering: (routerId, b) => request('POST', `/networking/routers/${routerId}/peering`, b),
  deleteVpcPeering: (id) => request('DELETE', `/networking/vpc-peerings/${id}`),

  // Advanced Networking — BGP Peers
  getBgpPeers: () => request('GET', '/networking/bgp-peers'),
  createBgpPeer: (b) => request('POST', '/networking/bgp-peers', b),
  deleteBgpPeer: (id) => request('DELETE', `/networking/bgp-peers/${id}`),
  syncBgpPeerConfig: (id) => request('POST', `/networking/bgp-peers/${id}/sync-config`, {}),

  // Advanced Networking — VPN Gateways & Policies
  getVpnSiteConnections: () => request('GET', '/networking/vpn/site-connections'),
  createVpnSiteConnection: (b) => request('POST', '/networking/vpn/site-connections', b),
  deleteVpnSiteConnection: (id) => request('DELETE', `/networking/vpn/site-connections/${id}`),
  getVpnIkePolicies: () => request('GET', '/networking/vpn/ikepolicies'),
  createVpnIkePolicy: (b) => request('POST', '/networking/vpn/ikepolicies', b),
  deleteVpnIkePolicy: (id) => request('DELETE', `/networking/vpn/ikepolicies/${id}`),
  getVpnIpsecPolicies: () => request('GET', '/networking/vpn/ipsecpolicies'),
  createVpnIpsecPolicy: (b) => request('POST', '/networking/vpn/ipsecpolicies', b),
  deleteVpnIpsecPolicy: (id) => request('DELETE', `/networking/vpn/ipsecpolicies/${id}`),

  // Telemetry & Metrics
  getTelemetryMetrics: (instanceId) => request('GET', `/telemetry/metrics${instanceId ? `?instance_id=${instanceId}` : ''}`),
  getTelemetryStats: () => request('GET', '/telemetry/stats'),
  getInstanceStats: (instanceId) => request('GET', `/telemetry/instances/${instanceId}/stats`),

  // Billing & Invoices
  getBillingSummary: (projectId) => request('GET', `/billing/summary${projectId ? `/${projectId}` : ''}`),
  getRatingSheets: () => request('GET', '/billing/rating-sheets'),
  createRatingSheet: (b) => request('POST', '/billing/rating-sheets', b),
  getInvoices: () => request('GET', '/billing/invoices'),
  getInvoice: (id) => request('GET', `/billing/invoices/${id}`),
  createInvoice: (b) => request('POST', '/billing/invoices', b),
  downloadInvoicePdf: (id) => request('POST', `/billing/invoices/${id}/pdf`),

  // Audit Event Logs & Subscriptions
  getEventLogs: (limit = 100) => request('GET', `/event/events/logs?limit=${limit}`),
  getEventTypes: () => request('GET', '/event/events/types'),
  getEventSubscriptions: () => request('GET', '/event/events/subscriptions'),
  createEventSubscription: (b) => request('POST', '/event/events/subscriptions', b),
  deleteEventSubscription: (id) => request('DELETE', `/event/events/subscriptions/${id}`),

  // Hypervisors & Compute Nodes
  getComputeNodes: () => request('GET', '/placement/compute_nodes'),
}



