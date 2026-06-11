// src/pages/MonitoringPage.tsx
import { useEffect, useState } from 'react'
import { Server, Database, Cpu } from 'lucide-react'
import { healthApi } from '../lib/api'

const SERVICES = [
  { label: 'Domain Service', path: '/domains' },
  { label: 'Ingestion Service', path: '/ingest' },
  { label: 'Generation Service', path: '/generate' },
  { label: 'Evaluation Service', path: '/evaluate' },
]

// Mock metrics - replace with real polled endpoints when available
const MOCK_METRICS = {
  queueDepth: 3,
  activeWorkers: 2,
  vectorLatencyMs: 42,
  bm25LatencyMs: 18,
  avgFusionScore: 0.78,
  cacheHits: 124,
  cacheMisses: 31,
  cacheMemoryMB: 56,
  llmRouteApi: 68,
  llmRouteLocal: 32,
}

export default function MonitoringPage() {
  const [statuses, setStatuses] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let mounted = true
    async function check() {
      const results: Record<string, boolean> = {}
      for (const s of SERVICES) results[s.label] = await healthApi.check(s.path)
      if (mounted) setStatuses(results)
    }
    check()
    const id = setInterval(check, 15000)
    return () => {
      mounted = false
      clearInterval(id)
    }
  }, [])

  const cacheTotal = MOCK_METRICS.cacheHits + MOCK_METRICS.cacheMisses
  const hitRate = ((MOCK_METRICS.cacheHits / cacheTotal) * 100).toFixed(1)
  const routeTotal = MOCK_METRICS.llmRouteApi + MOCK_METRICS.llmRouteLocal

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="glass rounded-lg p-5">
        <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
          <Server size={16} /> Infrastructure Status
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {SERVICES.map((s) => (
            <div key={s.label} className="rounded-md border border-border p-3 flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{s.label}</span>
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <span className={`h-2 w-2 rounded-full ${statuses[s.label] ? 'bg-status-success' : 'bg-status-error'}`} />
                {statuses[s.label] ? 'Online' : 'Offline'}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="glass rounded-lg p-5">
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <Cpu size={16} /> Queue Monitor
          </h3>
          <Metric label="Queue Depth" value={MOCK_METRICS.queueDepth} />
          <Metric label="Active Celery Workers" value={MOCK_METRICS.activeWorkers} />
        </div>

        <div className="glass rounded-lg p-5">
          <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
            <Database size={16} /> Retrieval Analytics
          </h3>
          <Metric label="Vector Search Latency" value={`${MOCK_METRICS.vectorLatencyMs} ms`} />
          <Metric label="BM25 Latency" value={`${MOCK_METRICS.bm25LatencyMs} ms`} />
          <Metric label="Avg Fusion Score" value={MOCK_METRICS.avgFusionScore} />
        </div>

        <div className="glass rounded-lg p-5">
          <h3 className="font-semibold text-sm mb-3">Cache Dashboard</h3>
          <Metric label="Hit Rate" value={`${hitRate}%`} />
          <div className="w-full h-2 rounded-full bg-muted overflow-hidden mt-1 mb-3">
            <div className="h-full bg-status-success" style={{ width: `${hitRate}%` }} />
          </div>
          <Metric label="Memory Consumption" value={`${MOCK_METRICS.cacheMemoryMB} MB`} />
        </div>

        <div className="glass rounded-lg p-5">
          <h3 className="font-semibold text-sm mb-3">LLM Provider Distribution</h3>
          <Metric label="API (Groq)" value={`${MOCK_METRICS.llmRouteApi} req`} />
          <Metric label="Local (Ollama)" value={`${MOCK_METRICS.llmRouteLocal} req`} />
          <div className="w-full h-2 rounded-full bg-muted overflow-hidden mt-1 flex">
            <div className="h-full bg-primary" style={{ width: `${(MOCK_METRICS.llmRouteApi / routeTotal) * 100}%` }} />
            <div className="h-full bg-accent-foreground/40" style={{ width: `${(MOCK_METRICS.llmRouteLocal / routeTotal) * 100}%` }} />
          </div>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Note: queue, retrieval and cache metrics above are mock placeholders pending dedicated metrics endpoints.
      </p>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between text-sm py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}
