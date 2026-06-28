// src/pages/MonitoringPage.tsx
import { useEffect, useState } from 'react'
import { Server, Database, Cpu, HardDrive, RefreshCw, BarChart2, ShieldAlert, Download, Trash2, ExternalLink, AlertTriangle, Activity } from 'lucide-react'
import { healthApi, monitoringApi } from '../lib/api'
import { useAuthStore } from '../store/authStore'
import { cn } from '../lib/utils'

const SERVICES = [
  { label: 'Domain Service', path: '/domains' },
  { label: 'Ingestion Service', path: '/ingest' },
  { label: 'Generation Service', path: '/generate' },
  { label: 'Evaluation Service', path: '/evaluate' },
]

// ── External monitoring URLs ──────────────────────────────────────────────────
const GRAFANA_DASHBOARDS = [
  {
    label: 'Service Health',
    description: 'Request rate, error rate, and latency per service',
    url: 'http://localhost:3000/d/rag-service-health',
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/10 border-emerald-500/20',
  },
  {
    label: 'Evaluation Quality',
    description: 'Judge scores, eval latency, and failure rate',
    url: 'http://localhost:3000/d/rag-eval-quality',
    color: 'text-violet-400',
    bg: 'bg-violet-500/10 border-violet-500/20',
  },
  {
    label: 'Infra Overview',
    description: 'Celery workers, task throughput, and 24h totals',
    url: 'http://localhost:3000/d/rag-infra-overview',
    color: 'text-sky-400',
    bg: 'bg-sky-500/10 border-sky-500/20',
  },
]

interface Metrics {
  queue: { depth: number; active_workers: number }
  retrieval: { vector_latency_ms: number; bm25_latency_ms: number; avg_fusion_score: number }
  cache: { hits: number; max_memory?: number; misses: number; memory_mb: number }
  llm: { api_requests: number; local_requests: number }
  services: Record<string, string>
  documents: { total: number; processing: number; failed: number }
}

export default function MonitoringPage() {
  const isSystemAdmin = useAuthStore((state) => state.isSystemAdmin)
  const [statuses, setStatuses] = useState<Record<string, boolean>>({})
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [metricsError, setMetricsError] = useState('')
  const [prometheusUp, setPrometheusUp] = useState<boolean | null>(null)
  const [alertsCount, setAlertsCount] = useState<number | null>(null)

  // Check if Prometheus is reachable
  async function checkPrometheus() {
    try {
      const res = await fetch('http://localhost:9091/-/healthy', { signal: AbortSignal.timeout(3000) })
      setPrometheusUp(res.ok)
    } catch {
      setPrometheusUp(false)
    }
  }

  // Check Alertmanager for firing alerts
  async function checkAlerts() {
    try {
      const res = await fetch('http://localhost:9093/api/v2/alerts?active=true', { signal: AbortSignal.timeout(3000) })
      if (res.ok) {
        const data = await res.json()
        setAlertsCount(Array.isArray(data) ? data.length : 0)
      }
    } catch {
      setAlertsCount(null)
    }
  }

  async function checkHealth() {
    const results: Record<string, boolean> = {}
    for (const s of SERVICES) {
      results[s.label] = await healthApi.check(s.path)
    }
    setStatuses(results)
  }

  async function fetchMetrics() {
    if (!isSystemAdmin) return
    setMetricsError('')
    try {
      const data = await monitoringApi.metrics()
      setMetrics(data)
    } catch (err) {
      console.error('Failed to fetch monitoring metrics:', err)
      setMetricsError('Could not fetch infrastructure metrics.')
    }
  }

  function exportMetrics() {
    if (!metrics) return
    const blob = new Blob([JSON.stringify(metrics, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `monitoring_metrics_${new Date().toISOString().slice(0, 10)}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  async function resetMetrics() {
    if (!window.confirm('Are you sure you want to reset all monitoring metrics?')) return
    setMetricsLoading(true)
    try {
      await monitoringApi.reset()
      alert('Metrics reset successfully.')
      await fetchMetrics()
    } catch (err: any) {
      alert(`Reset failed: ${err.message}`)
    } finally {
      setMetricsLoading(false)
    }
  }

  useEffect(() => {
    checkHealth()
    checkPrometheus()
    checkAlerts()
    const healthInterval = setInterval(checkHealth, 15000)
    const promInterval = setInterval(() => { checkPrometheus(); checkAlerts() }, 30000)

    if (isSystemAdmin) {
      setMetricsLoading(true)
      fetchMetrics().finally(() => setMetricsLoading(false))
      const metricsInterval = setInterval(fetchMetrics, 15000)
      return () => {
        clearInterval(healthInterval)
        clearInterval(metricsInterval)
        clearInterval(promInterval)
      }
    }

    return () => {
      clearInterval(healthInterval)
      clearInterval(promInterval)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSystemAdmin])

  const cacheTotal = metrics ? metrics.cache.hits + metrics.cache.misses : 0
  const hitRate = metrics && cacheTotal > 0 ? ((metrics.cache.hits / cacheTotal) * 100).toFixed(1) : '0.0'
  const routeTotal = metrics ? metrics.llm.api_requests + metrics.llm.local_requests : 0

  return (
    <div className="space-y-6 max-w-5xl pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground font-sans">System Monitoring</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Real-time status of microservices, database, and system resource load.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isSystemAdmin && metrics && (
            <button
              onClick={exportMetrics}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-card/50 hover:bg-card text-xs transition text-muted-foreground hover:text-foreground"
            >
              <Download size={14} /> Export Metrics
            </button>
          )}
          {isSystemAdmin && (
            <button
              onClick={resetMetrics}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-red-500/20 bg-red-500/5 hover:bg-red-500/15 text-xs transition text-red-400 font-semibold"
            >
              <Trash2 size={14} /> Reset Metrics
            </button>
          )}
          <button
            onClick={async () => {
              checkHealth()
              checkPrometheus()
              checkAlerts()
              if (isSystemAdmin) {
                setMetricsLoading(true)
                await fetchMetrics().finally(() => setMetricsLoading(false))
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-card/50 hover:bg-card text-xs transition"
          >
            <RefreshCw size={14} className={cn(metricsLoading && 'animate-spin')} /> Refresh
          </button>
        </div>
      </div>

      {/* Service Health */}
      <div className="glass rounded-xl p-5 border border-border">
        <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
          <Server size={16} className="text-primary" /> Gateway Status & Health Checks
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {SERVICES.map((s) => (
            <div key={s.label} className="rounded-xl border border-border/80 p-3.5 flex flex-col gap-1.5 bg-card/20 hover:bg-card/40 transition">
              <span className="text-xs text-muted-foreground font-medium">{s.label}</span>
              <span className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                <span className={cn('h-2 w-2 rounded-full', statuses[s.label] ? 'bg-emerald-500 animate-pulse' : 'bg-red-500')} />
                {statuses[s.label] ? 'Online' : 'Offline'}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Non-Admin Banner */}
      {!isSystemAdmin && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 max-w-2xl">
          <ShieldAlert className="text-amber-500 shrink-0 mt-0.5" size={18} />
          <div>
            <h4 className="text-sm font-semibold text-foreground">Elevated Privilege Required</h4>
            <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
              Infrastructure metrics and Grafana dashboards are restricted to System Administrators.
            </p>
          </div>
        </div>
      )}

      {/* ── SYSTEM ADMIN ONLY SECTION ─────────────────────────────────────── */}
      {isSystemAdmin && (
        <>
          {metricsError && (
            <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg p-3">
              {metricsError}
            </div>
          )}

          {/* ── Grafana Dashboards ── */}
          <div className="glass rounded-xl p-5 border border-border space-y-4">
            <div className="flex items-center justify-between border-b border-border/40 pb-3">
              <h3 className="font-bold text-sm flex items-center gap-2">
                <BarChart2 size={16} className="text-primary" /> Grafana Dashboards
              </h3>
              <div className="flex items-center gap-2">
                <span className={cn(
                  'flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full border',
                  prometheusUp === true
                    ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                    : prometheusUp === false
                    ? 'text-red-400 bg-red-500/10 border-red-500/20'
                    : 'text-muted-foreground bg-muted/20 border-border'
                )}>
                  <span className={cn('h-1.5 w-1.5 rounded-full', prometheusUp === true ? 'bg-emerald-500 animate-pulse' : prometheusUp === false ? 'bg-red-500' : 'bg-muted-foreground')} />
                  Prometheus {prometheusUp === true ? 'UP' : prometheusUp === false ? 'DOWN' : 'Checking...'}
                </span>
                <a
                  href="http://localhost:3000"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition px-2 py-0.5 rounded border border-border hover:bg-card"
                >
                  Open Grafana <ExternalLink size={11} />
                </a>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {GRAFANA_DASHBOARDS.map((d) => (
                <a
                  key={d.label}
                  href={d.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(
                    'group flex flex-col gap-1.5 rounded-xl border p-4 transition hover:scale-[1.02] hover:shadow-md',
                    d.bg
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className={cn('text-sm font-bold', d.color)}>{d.label}</span>
                    <ExternalLink size={13} className={cn('opacity-0 group-hover:opacity-100 transition', d.color)} />
                  </div>
                  <span className="text-xs text-muted-foreground leading-relaxed">{d.description}</span>
                </a>
              ))}
            </div>
          </div>

          {/* ── Prometheus & Alertmanager ── */}
          <div className="glass rounded-xl p-5 border border-border space-y-4">
            <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-3">
              <Activity size={16} className="text-primary" /> Prometheus & Alertmanager
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {/* Prometheus */}
              <a
                href="http://localhost:9091/targets"
                target="_blank"
                rel="noopener noreferrer"
                className="group flex flex-col gap-2 rounded-xl border border-border p-4 bg-card/20 hover:bg-card/40 transition hover:scale-[1.01]"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold text-foreground">Prometheus</span>
                  <ExternalLink size={13} className="text-muted-foreground opacity-0 group-hover:opacity-100 transition" />
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn('h-2 w-2 rounded-full', prometheusUp === true ? 'bg-emerald-500 animate-pulse' : prometheusUp === false ? 'bg-red-500' : 'bg-muted-foreground')} />
                  <span className="text-xs text-muted-foreground">
                    {prometheusUp === true ? 'Scraping metrics every 15s — click to view targets' : prometheusUp === false ? 'Not reachable — is the monitoring stack running?' : 'Checking...'}
                  </span>
                </div>
                <span className="text-[10px] text-muted-foreground/60 font-mono">localhost:9091</span>
              </a>

              {/* Alertmanager */}
              <a
                href="http://localhost:9093"
                target="_blank"
                rel="noopener noreferrer"
                className="group flex flex-col gap-2 rounded-xl border border-border p-4 bg-card/20 hover:bg-card/40 transition hover:scale-[1.01]"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold text-foreground">Alertmanager</span>
                  <ExternalLink size={13} className="text-muted-foreground opacity-0 group-hover:opacity-100 transition" />
                </div>
                <div className="flex items-center gap-2">
                  {alertsCount === null ? (
                    <span className="text-xs text-muted-foreground">Could not reach Alertmanager</span>
                  ) : alertsCount === 0 ? (
                    <>
                      <span className="h-2 w-2 rounded-full bg-emerald-500" />
                      <span className="text-xs text-emerald-400 font-semibold">No active alerts</span>
                    </>
                  ) : (
                    <>
                      <AlertTriangle size={14} className="text-red-400" />
                      <span className="text-xs text-red-400 font-semibold">{alertsCount} active alert{alertsCount > 1 ? 's' : ''} firing</span>
                    </>
                  )}
                </div>
                <span className="text-[10px] text-muted-foreground/60 font-mono">localhost:9093</span>
              </a>
            </div>
          </div>

          {/* ── Existing infra metrics ── */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Queue Monitor */}
            <div className="glass rounded-xl p-5 border border-border space-y-3">
              <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-2">
                <Cpu size={16} className="text-primary" /> Queue & Worker Monitor
              </h3>
              <div className="space-y-1">
                <Metric label="Ingestion Queue Depth" value={metrics ? `${metrics.queue.depth} tasks` : '0 tasks'} />
                <Metric label="Active Celery Workers" value={metrics ? metrics.queue.active_workers : 0} />
              </div>
            </div>

            {/* Retrieval Analytics */}
            <div className="glass rounded-xl p-5 border border-border space-y-3">
              <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-2">
                <Database size={16} className="text-primary" /> Search & Retrieval Analytics
              </h3>
              <div className="space-y-1">
                <Metric label="Vector search (Qdrant) latency" value={metrics ? `${metrics.retrieval.vector_latency_ms} ms` : '—'} />
                <Metric label="Keyword (Postgres BM25) latency" value={metrics ? `${metrics.retrieval.bm25_latency_ms} ms` : '—'} />
                <Metric label="Avg RRF Fusion Score" value={metrics ? metrics.retrieval.avg_fusion_score : '—'} />
              </div>
            </div>

            {/* Cache Dashboard */}
            <div className="glass rounded-xl p-5 border border-border space-y-4">
              <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-2 mb-2">
                <HardDrive size={16} className="text-primary" /> Redis Cache Dashboard
              </h3>
              <div className="space-y-2">
                <Metric label="Cache Hits" value={metrics ? metrics.cache.hits : 0} />
                <Metric label="Cache Misses" value={metrics ? metrics.cache.misses : 0} />
                <Metric label="Hit Rate" value={`${hitRate}%`} />
                <div className="w-full h-2 rounded-full bg-muted overflow-hidden mt-1.5">
                  <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${hitRate}%` }} />
                </div>
                <Metric label="Memory Consumption" value={metrics ? `${metrics.cache.memory_mb} MB` : '—'} />
              </div>
            </div>

            {/* LLM Provider Distribution */}
            <div className="glass rounded-xl p-5 border border-border space-y-4">
              <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-2 mb-2">
                <BarChart2 size={16} className="text-primary" /> LLM Routing Distribution
              </h3>
              <div className="space-y-2">
                <Metric label="API Gateway (Groq/API)" value={metrics ? `${metrics.llm.api_requests} req` : '0 req'} />
                <Metric label="Local Service (Ollama)" value={metrics ? `${metrics.llm.local_requests} req` : '0 req'} />
                {routeTotal > 0 && metrics ? (
                  <>
                    <div className="w-full h-2 rounded-full bg-muted overflow-hidden mt-1.5 flex">
                      <div className="h-full bg-primary" style={{ width: `${(metrics.llm.api_requests / routeTotal) * 100}%` }} />
                      <div className="h-full bg-sky-400" style={{ width: `${(metrics.llm.local_requests / routeTotal) * 100}%` }} />
                    </div>
                    <div className="flex justify-between text-[10px] text-muted-foreground font-semibold">
                      <span>Groq: {((metrics.llm.api_requests / routeTotal) * 100).toFixed(0)}%</span>
                      <span>Ollama: {((metrics.llm.local_requests / routeTotal) * 100).toFixed(0)}%</span>
                    </div>
                  </>
                ) : (
                  <div className="w-full h-2 rounded-full bg-muted mt-1.5" />
                )}
              </div>
            </div>

            {/* Document stats */}
            <div className="glass rounded-xl p-5 border border-border md:col-span-2 space-y-3">
              <h3 className="font-bold text-sm flex items-center gap-2 border-b border-border/40 pb-2">
                <FileText size={16} className="text-primary" /> Global Document Pipeline Stats
              </h3>
              <div className="grid grid-cols-3 gap-4 text-center mt-2">
                <div className="p-3 bg-muted/20 border border-border/60 rounded-xl">
                  <span className="text-xs text-muted-foreground font-semibold">Total Documents</span>
                  <p className="text-xl font-bold mt-1 text-foreground">{metrics ? metrics.documents.total : 0}</p>
                </div>
                <div className="p-3 bg-muted/20 border border-border/60 rounded-xl">
                  <span className="text-xs text-muted-foreground font-semibold">In Progress</span>
                  <p className="text-xl font-bold mt-1 text-amber-400">{metrics ? metrics.documents.processing : 0}</p>
                </div>
                <div className="p-3 bg-muted/20 border border-border/60 rounded-xl">
                  <span className="text-xs text-muted-foreground font-semibold">Failed Pipeline</span>
                  <p className="text-xl font-bold mt-1 text-red-400">{metrics ? metrics.documents.failed : 0}</p>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between text-xs py-1.5 border-b border-border/20 last:border-b-0">
      <span className="text-muted-foreground font-medium">{label}</span>
      <span className="font-semibold text-foreground">{value}</span>
    </div>
  )
}

function FileText({ size, className }: { size: number; className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      <path d="M10 9H8" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
    </svg>
  )
}