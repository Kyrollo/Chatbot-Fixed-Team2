// src/pages/MonitoringPage.tsx
import { useEffect, useState } from 'react'
import { Server, Database, Cpu, HardDrive, RefreshCw, BarChart2, ShieldAlert } from 'lucide-react'
import { healthApi, monitoringApi } from '../lib/api'
import { useAuthStore } from '../store/authStore'
import { cn } from '../lib/utils'

const SERVICES = [
  { label: 'Domain Service', path: '/domains' },
  { label: 'Ingestion Service', path: '/ingest' },
  { label: 'Generation Service', path: '/generate' },
  { label: 'Evaluation Service', path: '/evaluate' },
]

interface Metrics {
  queue: {
    depth: number
    active_workers: number
  }
  retrieval: {
    vector_latency_ms: number
    bm25_latency_ms: number
    avg_fusion_score: number
  }
  cache: {
    hits: number
    max_memory?: number
    misses: number
    memory_mb: number
  }
  llm: {
    api_requests: number
    local_requests: number
  }
  services: Record<string, string>
  documents: {
    total: number
    processing: number
    failed: number
  }
}

export default function MonitoringPage() {
  const isSystemAdmin = useAuthStore((state) => state.isSystemAdmin)
  const [statuses, setStatuses] = useState<Record<string, boolean>>({})
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [metricsLoading, setMetricsLoading] = useState(false)
  const [metricsError, setMetricsError] = useState('')

  // 1. Basic Health Check for Services
  async function checkHealth() {
    const results: Record<string, boolean> = {}
    for (const s of SERVICES) {
      results[s.label] = await healthApi.check(s.path)
    }
    setStatuses(results)
  }

  // 2. Fetch Detailed Metrics (requires system_admin)
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

  useEffect(() => {
    checkHealth()
    const healthInterval = setInterval(checkHealth, 15000)

    if (isSystemAdmin) {
      setMetricsLoading(true)
      fetchMetrics().finally(() => setMetricsLoading(false))
      const metricsInterval = setInterval(fetchMetrics, 15000)
      return () => {
        clearInterval(healthInterval)
        clearInterval(metricsInterval)
      }
    }

    return () => {
      clearInterval(healthInterval)
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
        <button
          onClick={async () => {
            checkHealth()
            if (isSystemAdmin) {
              setMetricsLoading(true)
              await fetchMetrics().finally(() => setMetricsLoading(false))
            }
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-card/50 hover:bg-card text-xs transition"
        >
          <RefreshCw size={14} className={cn(metricsLoading && 'animate-spin')} /> Manual Refresh
        </button>
      </div>

      {/* Basic Infrastructure Status */}
      <div className="glass rounded-xl p-5 border border-border">
        <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
          <Server size={16} className="text-primary" /> Gateway Status & Health checks
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
              Infrastructure metrics (Celery queue depth, search latencies, redis cache memory, and document stats) are restricted to System Administrators.
              Login with a System Admin account to view detailed analytics.
            </p>
          </div>
        </div>
      )}

      {/* Admin Metrics Dashboard */}
      {isSystemAdmin && (
        <>
          {metricsError && (
            <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg p-3">
              {metricsError}
            </div>
          )}

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
