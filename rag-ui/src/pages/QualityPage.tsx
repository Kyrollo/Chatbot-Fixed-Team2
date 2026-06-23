// src/pages/QualityPage.tsx
// Quality Dashboard — system_admin only
// Shows: Judge LLM scores, audit log, moderation queue

import { useEffect, useState } from 'react'
import { BarChart2, ShieldAlert, RefreshCw, CheckCircle, XCircle, Clock, Activity } from 'lucide-react'
import { api } from '../lib/api'
import { useAuthStore } from '../store/authStore'
import { cn } from '../lib/utils'

// ── Types ────────────────────────────────────────────────────────────────────

interface EvalLog {
  id: string
  query_id: number
  model_used: string
  overall_score: number | null
  faithfulness_score: number | null
  relevance_score: number | null
  completeness_score: number | null
  evaluated_at: string
}

interface ModerationItem {
  id: string
  query_id: number
  status: string
  created_at: string
  overall_score: number | null
  faithfulness_score: number | null
  relevance_score: number | null
  query: string
  answer: string
}

interface AuditEntry {
  id: string
  event_type: string
  actor: string | null
  query_id: number | null
  details: Record<string, any> | null
  created_at: string
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function scoreColor(score: number | null): string {
  if (score === null) return 'text-muted-foreground'
  if (score >= 0.8) return 'text-emerald-500'
  if (score >= 0.6) return 'text-amber-400'
  return 'text-red-400'
}

function scoreBg(score: number | null): string {
  if (score === null) return 'bg-muted/30'
  if (score >= 0.8) return 'bg-emerald-500/10 border-emerald-500/20'
  if (score >= 0.6) return 'bg-amber-400/10 border-amber-400/20'
  return 'bg-red-400/10 border-red-400/20'
}

function fmt(score: number | null): string {
  if (score === null) return '—'
  return `${(score * 100).toFixed(0)}%`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function eventBadge(type: string) {
  const map: Record<string, { label: string; cls: string }> = {
    live_evaluation:    { label: 'Live Eval',  cls: 'bg-blue-500/10 text-blue-400 border-blue-500/20' },
    moderation_decision:{ label: 'Decision',   cls: 'bg-purple-500/10 text-purple-400 border-purple-500/20' },
    batch_run:          { label: 'Batch Run',  cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' },
  }
  const m = map[type] ?? { label: type, cls: 'bg-muted/30 text-muted-foreground border-border' }
  return (
    <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase tracking-wide', m.cls)}>
      {m.label}
    </span>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function QualityPage() {
  const isSystemAdmin = useAuthStore((s) => s.isSystemAdmin)

  const [evalLogs, setEvalLogs]       = useState<EvalLog[]>([])
  const [modItems, setModItems]       = useState<ModerationItem[]>([])
  const [auditLogs, setAuditLogs]     = useState<AuditEntry[]>([])
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState('')
  const [modFilter, setModFilter]     = useState<'pending' | 'all'>('pending')
  const [auditFilter, setAuditFilter] = useState<string>('all')

  async function fetchAll() {
    if (!isSystemAdmin) return
    setLoading(true)
    setError('')
    try {
      const [evalRes, modRes, auditRes] = await Promise.all([
        api.get<{ logs: EvalLog[] }>('/evaluate/logs'),
        api.get<{ count: number; items: ModerationItem[] }>('/moderation/queue'),
        api.get<{ logs: AuditEntry[] }>('/moderation/audit'),
      ])
      setEvalLogs(evalRes.logs ?? [])
      setModItems(modRes.items ?? [])
      setAuditLogs(auditRes.logs ?? [])
    } catch (e: any) {
      setError(e.message ?? 'Failed to load quality data')
    } finally {
      setLoading(false)
    }
  }

  async function submitDecision(itemId: string, decision: 'approved' | 'rejected') {
    try {
      await api.post(`/moderation/${itemId}/decide`, {
        decision,
        reviewer: useAuthStore.getState().userId ?? 'system_admin',
        notes: null,
      })
      await fetchAll()
    } catch (e: any) {
      alert(`Decision failed: ${e.message}`)
    }
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSystemAdmin])

  // ── Derived stats ──────────────────────────────────────────────────────────
  const totalEvals    = evalLogs.length
  const avgScore      = totalEvals > 0
    ? evalLogs.reduce((s, r) => s + (r.overall_score ?? 0), 0) / totalEvals
    : null
  const pendingCount  = modItems.filter((m) => m.status === 'pending').length
  const batchRuns     = auditLogs.filter((a) => a.event_type === 'batch_run').length

  const visibleMod    = modFilter === 'pending'
    ? modItems.filter((m) => m.status === 'pending')
    : modItems

  const visibleAudit  = auditFilter === 'all'
    ? auditLogs
    : auditLogs.filter((a) => a.event_type === auditFilter)

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6 max-w-5xl pb-12">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground font-sans">Quality Dashboard</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Judge LLM scores, moderation queue, and audit trail.
          </p>
        </div>
        <button
          onClick={fetchAll}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-card/50 hover:bg-card text-xs transition"
        >
          <RefreshCw size={14} className={cn(loading && 'animate-spin')} /> Refresh
        </button>
      </div>

      {/* Non-admin guard */}
      {!isSystemAdmin && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 max-w-2xl">
          <ShieldAlert className="text-amber-500 shrink-0 mt-0.5" size={18} />
          <div>
            <h4 className="text-sm font-semibold text-foreground">System Admin Only</h4>
            <p className="text-xs text-muted-foreground mt-1">
              Quality metrics and audit logs are restricted to System Administrators.
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg p-3">
          {error}
        </div>
      )}

      {isSystemAdmin && (
        <>
          {/* ── KPI Cards ── */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <KpiCard label="Total Evaluations" value={totalEvals} icon={<BarChart2 size={16} />} />
            <KpiCard
              label="Avg Overall Score"
              value={fmt(avgScore)}
              icon={<Activity size={16} />}
              valueClass={scoreColor(avgScore)}
            />
            <KpiCard label="Pending Review" value={pendingCount} icon={<Clock size={16} />}
              valueClass={pendingCount > 0 ? 'text-amber-400' : 'text-emerald-500'} />
            <KpiCard label="Batch Runs" value={batchRuns} icon={<RefreshCw size={16} />} />
          </div>

          {/* ── Recent Evaluation Logs ── */}
          <Section title="Recent Judge Evaluations" icon={<BarChart2 size={16} className="text-primary" />}>
            {evalLogs.length === 0 ? (
              <Empty text="No evaluations yet. Run a batch job or call POST /evaluate." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-muted-foreground border-b border-border/40">
                      <th className="pb-2 pr-4 font-semibold">Query ID</th>
                      <th className="pb-2 pr-4 font-semibold">Judge</th>
                      <th className="pb-2 pr-4 font-semibold">Overall</th>
                      <th className="pb-2 pr-4 font-semibold">Faith.</th>
                      <th className="pb-2 pr-4 font-semibold">Relev.</th>
                      <th className="pb-2 pr-4 font-semibold">Complet.</th>
                      <th className="pb-2 font-semibold">Evaluated At</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/20">
                    {evalLogs.slice(0, 20).map((row) => (
                      <tr key={row.id} className="hover:bg-muted/10 transition">
                        <td className="py-2 pr-4 font-mono text-muted-foreground">{row.query_id}</td>
                        <td className="py-2 pr-4">
                          <span className={cn(
                            'px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase',
                            row.model_used === 'ragas'
                              ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                              : 'bg-purple-500/10 text-purple-400 border-purple-500/20'
                          )}>
                            {row.model_used}
                          </span>
                        </td>
                        <td className={cn('py-2 pr-4 font-bold', scoreColor(row.overall_score))}>
                          {fmt(row.overall_score)}
                        </td>
                        <td className={cn('py-2 pr-4', scoreColor(row.faithfulness_score))}>
                          {fmt(row.faithfulness_score)}
                        </td>
                        <td className={cn('py-2 pr-4', scoreColor(row.relevance_score))}>
                          {fmt(row.relevance_score)}
                        </td>
                        <td className={cn('py-2 pr-4', scoreColor(row.completeness_score))}>
                          {fmt(row.completeness_score)}
                        </td>
                        <td className="py-2 text-muted-foreground">{fmtDate(row.evaluated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          {/* ── Moderation Queue ── */}
          <Section
            title="Moderation Queue"
            icon={<ShieldAlert size={16} className="text-primary" />}
            action={
              <select
                value={modFilter}
                onChange={(e) => setModFilter(e.target.value as any)}
                className="text-xs bg-card border border-border rounded-lg px-2 py-1"
              >
                <option value="pending">Pending</option>
                <option value="all">All</option>
              </select>
            }
          >
            {visibleMod.length === 0 ? (
              <Empty text="No items in the moderation queue." />
            ) : (
              <div className="space-y-3">
                {visibleMod.map((item) => (
                  <div
                    key={item.id}
                    className={cn('rounded-xl border p-4 space-y-2', scoreBg(item.overall_score))}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-muted-foreground">#{item.query_id}</span>
                        <span className={cn(
                          'text-[10px] font-bold px-2 py-0.5 rounded-full border uppercase',
                          item.status === 'pending'
                            ? 'bg-amber-400/10 text-amber-400 border-amber-400/20'
                            : item.status === 'approved'
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              : 'bg-red-400/10 text-red-400 border-red-400/20'
                        )}>
                          {item.status}
                        </span>
                      </div>
                      <span className={cn('text-sm font-bold', scoreColor(item.overall_score))}>
                        Score: {fmt(item.overall_score)}
                      </span>
                    </div>

                    <div className="text-xs space-y-1">
                      <p className="text-muted-foreground font-semibold">Q: <span className="text-foreground font-normal">{item.query}</span></p>
                      <p className="text-muted-foreground font-semibold">A: <span className="text-foreground font-normal line-clamp-2">{item.answer}</span></p>
                    </div>

                    <div className="flex gap-2 text-[10px] text-muted-foreground">
                      <span>Faith: {fmt(item.faithfulness_score)}</span>
                      <span>·</span>
                      <span>Relev: {fmt(item.relevance_score)}</span>
                      <span>·</span>
                      <span>{fmtDate(item.created_at)}</span>
                    </div>

                    {item.status === 'pending' && (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => submitDecision(item.id, 'approved')}
                          className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20 text-emerald-400 text-xs font-semibold transition"
                        >
                          <CheckCircle size={12} /> Approve
                        </button>
                        <button
                          onClick={() => submitDecision(item.id, 'rejected')}
                          className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-400/10 hover:bg-red-400/20 border border-red-400/20 text-red-400 text-xs font-semibold transition"
                        >
                          <XCircle size={12} /> Reject
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* ── Audit Log ── */}
          <Section
            title="Audit Log"
            icon={<Activity size={16} className="text-primary" />}
            action={
              <select
                value={auditFilter}
                onChange={(e) => setAuditFilter(e.target.value)}
                className="text-xs bg-card border border-border rounded-lg px-2 py-1"
              >
                <option value="all">All Events</option>
                <option value="live_evaluation">Live Evaluations</option>
                <option value="moderation_decision">Decisions</option>
                <option value="batch_run">Batch Runs</option>
              </select>
            }
          >
            {visibleAudit.length === 0 ? (
              <Empty text="No audit events yet." />
            ) : (
              <div className="space-y-2">
                {visibleAudit.slice(0, 30).map((entry) => (
                  <div key={entry.id} className="flex items-start gap-3 py-2 border-b border-border/20 last:border-b-0">
                    <div className="shrink-0 mt-0.5">{eventBadge(entry.event_type)}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {entry.actor && (
                          <span className="text-xs font-semibold text-foreground">{entry.actor}</span>
                        )}
                        {entry.query_id && (
                          <span className="text-[10px] text-muted-foreground font-mono">query #{entry.query_id}</span>
                        )}
                        {entry.details && (
                          <span className="text-[10px] text-muted-foreground">
                            {entry.event_type === 'batch_run' &&
                              `evaluated: ${entry.details.evaluated}, flagged: ${entry.details.flagged}, cursor: ${entry.details.cursor}`}
                            {entry.event_type === 'live_evaluation' &&
                              `score: ${fmt(entry.details.score)}, model: ${entry.details.model}`}
                            {entry.event_type === 'moderation_decision' &&
                              `decision: ${entry.details.decision}`}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="shrink-0 text-[10px] text-muted-foreground whitespace-nowrap">
                      {fmtDate(entry.created_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────

function KpiCard({
  label, value, icon, valueClass,
}: {
  label: string
  value: string | number
  icon: React.ReactNode
  valueClass?: string
}) {
  return (
    <div className="glass rounded-xl p-4 border border-border space-y-2">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-semibold">
        {icon} {label}
      </div>
      <p className={cn('text-2xl font-bold text-foreground', valueClass)}>{value}</p>
    </div>
  )
}

function Section({
  title, icon, action, children,
}: {
  title: string
  icon: React.ReactNode
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="glass rounded-xl p-5 border border-border space-y-4">
      <div className="flex items-center justify-between border-b border-border/40 pb-3">
        <h3 className="font-bold text-sm flex items-center gap-2">{icon} {title}</h3>
        {action}
      </div>
      {children}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <p className="text-xs text-muted-foreground text-center py-6">{text}</p>
}