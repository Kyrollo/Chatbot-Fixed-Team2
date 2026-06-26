// src/components/quality/QueryDetailDrawer.tsx
// Opens when a Query ID row is clicked in QualityPage's "Recent Judge Evaluations" table.
// Shows the original question + answer, plus a pill row per judge (Custom Judge / RAGAS),
// matching the flat-badge style used in the chat view (Judge %, Faithfulness %, Relevance %,
// Completeness %, Citations %).
import { useEffect, useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { qualityApi } from '../../lib/api'
import { cn } from '../../lib/utils'

// ── Types ────────────────────────────────────────────────────────────────────
// NOTE: this matches the EvalLog shape QualityPage already uses, plus the
// fields that are NOT currently returned by GET /evaluate/logs (question,
// answer, citations_count). The backend needs a new endpoint —
// GET /evaluate/logs/{query_id} — that returns this shape. See the note
// at the bottom of this file for the exact contract.

interface JudgeEvaluation {
  model_used: string // 'custom_judge' | 'ragas'
  overall_score: number | null
  faithfulness_score: number | null
  relevance_score: number | null
  completeness_score: number | null
  evaluated_at: string
}

interface QueryDetail {
  query_id: number
  domain_id?: string
  user_id?: string
  query: string
  answer: string
  llm_route?: string
  model?: string
  citations_count?: number
  evaluation_status?: string
  cache_hit?: boolean
  created_at?: string
  evaluations: JudgeEvaluation[]
}

// ── Helpers (mirrors QualityPage.tsx's color scale) ─────────────────────────

function scorePct(score: number | null): number | null {
  if (score === null || score === undefined) return null
  return Math.round(score * 100)
}

function pillColor(pct: number | null): string {
  if (pct === null) return 'bg-muted/30 text-muted-foreground border-border'
  if (pct >= 80) return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
  if (pct >= 60) return 'bg-amber-400/10 text-amber-400 border-amber-400/20'
  return 'bg-red-400/10 text-red-400 border-red-400/20'
}

function judgeBadgeColor(model: string): string {
  return model === 'ragas'
    ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
    : 'bg-purple-500/10 text-purple-400 border-purple-500/20'
}

function Pill({ label, pct }: { label: string; pct: number | null }) {
  return (
    <span className={cn('inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-medium', pillColor(pct))}>
      {label} <span className="font-bold">{pct === null ? '—' : `${pct}%`}</span>
    </span>
  )
}

// One judge's full pill row: badge + Overall + Faith/Relev/Complete
function JudgeBlock({ ev }: { ev: JudgeEvaluation }) {
  const overall = scorePct(ev.overall_score)
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={cn('px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase', judgeBadgeColor(ev.model_used))}>
        {ev.model_used}
      </span>
      <Pill label="Judge" pct={overall} />
      <Pill label="Faithfulness" pct={scorePct(ev.faithfulness_score)} />
      <Pill label="Relevance" pct={scorePct(ev.relevance_score)} />
      <Pill label="Completeness" pct={scorePct(ev.completeness_score)} />
    </div>
  )
}

// ── Main Drawer ──────────────────────────────────────────────────────────────

export default function QueryDetailDrawer({
  queryId,
  onClose,
}: {
  queryId: number | null
  onClose: () => void
}) {
  const [detail, setDetail] = useState<QueryDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (queryId === null) {
      setDetail(null)
      setError('')
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    qualityApi
      .queryDetail(queryId)
      .then((res: any) => {
        if (!cancelled) setDetail(res)
      })
      .catch((e: any) => {
        if (!cancelled) setError(e.message ?? 'Failed to load query detail')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [queryId])

  if (queryId === null) return null

  return (
    <div className="fixed inset-0 z-30 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-lg h-full bg-card border-l border-border shadow-xl p-5 overflow-y-auto animate-in slide-in-from-right">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-sm">Query #{queryId}</h3>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-accent">
            <X size={16} />
          </button>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
            <Loader2 size={16} className="animate-spin" /> Loading...
          </div>
        )}

        {!loading && error && (
          <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg p-3">
            {error}
          </div>
        )}

        {!loading && !error && detail && (
          <div className="space-y-5 text-sm">
            {/* Question */}
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground font-semibold">Question</div>
              <div className="rounded-md border border-border bg-muted/30 p-3 leading-relaxed">
                {detail.query}
              </div>
            </div>

            {/* Answer */}
            <div className="space-y-1">
              <div className="text-xs text-muted-foreground font-semibold">Answer</div>
              <div className="rounded-md border border-border bg-muted/30 p-3 leading-relaxed whitespace-pre-wrap">
                {detail.answer}
              </div>
            </div>

            {/* Route / model / citations meta line */}
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground uppercase tracking-wide">
              {detail.llm_route && <span>{detail.llm_route} ROUTE</span>}
              {detail.model && <span>· {detail.model}</span>}
              {detail.citations_count !== undefined && (
                <span>· {detail.citations_count} citation{detail.citations_count === 1 ? '' : 's'}</span>
              )}
              {detail.cache_hit && <span>· cache hit</span>}
              {detail.created_at && <span className="normal-case">· {new Date(detail.created_at).toLocaleString()}</span>}
            </div>

            {/* Judge score pill rows */}
            <div className="space-y-3 pt-2 border-t border-border/50">
              <div className="text-xs text-muted-foreground font-semibold">Quality Scores</div>
              {detail.evaluations.length === 0 ? (
                <p className="text-xs text-muted-foreground">No evaluations recorded yet for this query.</p>
              ) : (
                detail.evaluations.map((ev) => <JudgeBlock key={ev.model_used} ev={ev} />)
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// BACKEND CONTRACT (verified against the real rag_query_logs schema and
// evaluation_logs table — implemented in evaluation-service):
//
//   GET /evaluate/logs/{query_id}
//
//   Response body:
//   {
//     "query_id": 21,
//     "domain_id": "cs-book",
//     "user_id": "u1",
//     "query": "what is the breedify project ?",
//     "answer": "Breedify is a high-performance mobile...",
//     "llm_route": "local",
//     "model": "gemini-2.5-flash",
//     "citations_count": 1,
//     "evaluation_status": "done",
//     "cache_hit": false,
//     "created_at": "2026-06-26T02:03:04Z",
//     "evaluations": [
//       { "model_used": "ragas", "overall_score": 0.94, "faithfulness_score": null,
//         "relevance_score": 0.94, "completeness_score": 0.94, "evaluated_at": "..." },
//       { "model_used": "custom_judge", "overall_score": 1.0, "faithfulness_score": 1.0,
//         "relevance_score": 1.0, "completeness_score": 1.0, "evaluated_at": "..." }
//     ]
//   }
//
//   404 if no rag_query_logs row exists for that query_id. An empty
//   `evaluations` array (not an error) means the query just hasn't been
//   scored yet.
// ─────────────────────────────────────────────────────────────────────────────