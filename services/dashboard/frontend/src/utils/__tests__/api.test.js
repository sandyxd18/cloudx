import test from 'node:test'
import assert from 'node:assert/strict'

// api.js reads localStorage and window on every request
globalThis.localStorage = { getItem: () => null, removeItem() {} }
globalThis.window = { location: { pathname: '/' }, dispatchEvent() {} }
const calls = []
globalThis.fetch = async (url, cfg) => {
  calls.push([cfg.method, url, cfg.body ? JSON.parse(cfg.body) : null])
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({}), text: async () => '{}' }
}
const { api } = await import('../../api.js')

// each call the v0.7.20/21 pages make must hit a route the backend serves
const cases = [
  [() => api.previewStack({ name: 's' }), 'POST', '/api/orchestration/stacks/preview'],
  [() => api.updateStack('s1', { template: 't' }), 'PUT', '/api/orchestration/stacks/s1'],
  [() => api.getStackTemplate('s1'), 'GET', '/api/orchestration/stacks/s1/template'],
  [() => api.createL7Policy('l1', {}), 'POST', '/api/load-balancer/listeners/l1/l7policies'],
  [() => api.updateL7Policy('p1', {}), 'PUT', '/api/load-balancer/l7policies/p1'],
  [() => api.deleteL7Policy('p1'), 'DELETE', '/api/load-balancer/l7policies/p1'],
  [() => api.getDomains(), 'GET', '/api/auth/v3/domains'],
  [() => api.createProject({ name: 'p', domain_id: 'd1' }), 'POST', '/api/auth/projects'],
]

for (const [fn, method, url] of cases) {
  test(`${method} ${url}`, async () => {
    calls.length = 0
    await fn()
    assert.deepEqual(calls[0].slice(0, 2), [method, url])
  })
}
