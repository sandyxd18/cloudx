import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Server, Network, Database, HardDrive, Activity, TrendingUp } from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api.js'

// Custom tooltip for chart
const ChartTooltip = ({ active, payload, label }) => {
  if (active && payload?.length) {
    return (
      <div style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px' }}>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</p>
        {payload.map((p, i) => (
          <p key={i} style={{ fontSize: 12, color: p.color }}>
            {p.name}: <strong>{p.value}</strong>
          </p>
        ))}
      </div>
    )
  }
  return null
}

// Fake time-series data for sparkline (in real life this would come from placement history)
function makeTimeData(max, points = 12) {
  const data = []
  const now = Date.now()
  for (let i = points; i >= 0; i--) {
    const t = new Date(now - i * 5 * 60 * 1000)
    data.push({
      time: t.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit' }),
      value: Math.max(0, Math.round(max * (0.4 + Math.random() * 0.4))),
    })
  }
  return data
}

export default function OverviewPage() {
  const [servers, setServers] = useState([])
  const [networks, setNetworks] = useState([])
  const [volumes, setVolumes] = useState([])
  const [images, setImages]   = useState([])
  const [usages, setUsages]   = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.allSettled([
      api.getServers(),
      api.getNetworks(),
      api.getVolumes(),
      api.getImages(),
      api.getUsages(),
    ]).then(([s, n, v, im, u]) => {
      if (s.status === 'fulfilled') setServers(s.value?.items || [])
      if (n.status === 'fulfilled') setNetworks(n.value?.items || [])
      if (v.status === 'fulfilled') setVolumes(v.value?.items || [])
      if (im.status === 'fulfilled') setImages(im.value?.items || [])
      if (u.status === 'fulfilled') setUsages(u.value)
      setLoading(false)
    })
  }, [])

  const activeServers  = servers.filter(s => s.status === 'ACTIVE').length
  const totalDisk      = volumes.reduce((a, v) => a + (v.size_gb || 0), 0)
  const vcpusUsed      = usages?.usages?.VCPU || 0
  const ramUsedMb      = usages?.usages?.MEMORY_MB || 0
  const vcpusTotal     = usages?.inventories?.VCPU?.total || 0
  const ramTotal       = usages?.inventories?.MEMORY_MB?.total || 0

  const cpuData = makeTimeData(vcpusTotal || 8)
  const ramData = makeTimeData(ramTotal || 16384)

  const stats = [
    {
      icon: <Server size={18} />,
      label: 'Instances',
      value: servers.length,
      sub: `${activeServers} active`,
      color: '#6366f1',
      to: '/servers',
    },
    {
      icon: <Network size={18} />,
      label: 'Networks',
      value: networks.length,
      sub: 'total networks',
      color: '#22d3ee',
      to: '/networks',
    },
    {
      icon: <Database size={18} />,
      label: 'Volumes',
      value: volumes.length,
      sub: `${totalDisk} GB total`,
      color: '#10b981',
      to: '/volumes',
    },
    {
      icon: <HardDrive size={18} />,
      label: 'Images',
      value: images.length,
      sub: 'available images',
      color: '#f59e0b',
      to: '/images',
    },
  ]

  if (loading) {
    return (
      <div className="loading-overlay">
        <span className="spinner" />
        <span>Loading overview…</span>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-subtitle">Private cloud resource overview</p>
        </div>
      </div>

      {/* Stat cards */}
      <div className="stat-grid">
        {stats.map(s => (
          <Link key={s.label} to={s.to} style={{ textDecoration: 'none' }}>
            <div className="stat-card">
              <div className="stat-card-icon" style={{ background: s.color + '22', color: s.color }}>
                {s.icon}
              </div>
              <div className="stat-card-value">{s.value}</div>
              <div className="stat-card-label">{s.label}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>{s.sub}</div>
            </div>
          </Link>
        ))}
      </div>

      {/* Charts + resource bars */}
      <div className="overview-grid">
        {/* CPU chart */}
        <div>
          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-header">
              <span className="card-title"><Activity size={15} />CPU Usage Trend</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {vcpusUsed} / {vcpusTotal} vCPUs
              </span>
            </div>
            <div className="card-body" style={{ paddingTop: 8 }}>
              <div className="chart-container" style={{ height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={cpuData} margin={{ top: 5, right: 5, bottom: 0, left: -20 }}>
                    <defs>
                      <linearGradient id="cpuGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} axisLine={false} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="value" name="vCPUs" stroke="#6366f1" strokeWidth={2} fill="url(#cpuGrad)" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* RAM chart */}
          <div className="card">
            <div className="card-header">
              <span className="card-title"><TrendingUp size={15} />RAM Usage Trend</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {Math.round(ramUsedMb / 1024)}GB / {Math.round(ramTotal / 1024)}GB
              </span>
            </div>
            <div className="card-body" style={{ paddingTop: 8 }}>
              <div className="chart-container" style={{ height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={ramData} margin={{ top: 5, right: 5, bottom: 0, left: -20 }}>
                    <defs>
                      <linearGradient id="ramGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.3}/>
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0}/>
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: '#475569' }} tickLine={false} axisLine={false} />
                    <Tooltip content={<ChartTooltip />} />
                    <Area type="monotone" dataKey="value" name="MB RAM" stroke="#22d3ee" strokeWidth={2} fill="url(#ramGrad)" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </div>

        {/* Resource utilization */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Resource Utilization</span>
          </div>
          <div className="card-body">
            {[
              { label: 'vCPU', used: vcpusUsed, total: vcpusTotal || 1, unit: 'cores', color: '#6366f1' },
              { label: 'RAM', used: Math.round(ramUsedMb / 1024), total: Math.round(ramTotal / 1024) || 1, unit: 'GB', color: '#22d3ee' },
              { label: 'Storage', used: totalDisk, total: Math.max(totalDisk, 500), unit: 'GB', color: '#10b981' },
              { label: 'Instances', used: servers.length, total: Math.max(servers.length, 20), unit: '', color: '#8b5cf6' },
            ].map(r => (
              <div key={r.label} className="resource-bar-item">
                <div className="resource-bar-header">
                  <span>{r.label}</span>
                  <span style={{ color: r.color }}>{r.used}{r.unit && ' ' + r.unit} / {r.total}{r.unit && ' ' + r.unit}</span>
                </div>
                <div className="resource-bar-track">
                  <div
                    className="resource-bar-fill"
                    style={{ width: `${Math.min(100, (r.used / r.total) * 100).toFixed(1)}%`, background: r.color }}
                  />
                </div>
              </div>
            ))}

            {/* Recent instances */}
            <div style={{ marginTop: 24 }}>
              <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '.5px' }}>
                Recent Instances
              </p>
              {servers.slice(-4).reverse().map(s => (
                <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: s.status === 'ACTIVE' ? 'var(--status-active)' : 'var(--text-muted)', flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.name}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.status}</span>
                </div>
              ))}
              {servers.length === 0 && <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>No instances yet</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
