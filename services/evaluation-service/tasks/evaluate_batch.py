"""
tasks/evaluate_batch.py
-------------------------
The Celery Beat scheduled task — runs on a configurable timer (default:
every 30 minutes) via celery_app.py.

WHAT EACH RUN DOES
-------------------
1. Read the cursor (db/queries.get_cursor) — the highest rag_query_logs.id
   already processed — and pull a random sample of rows with id > cursor
   that have no evaluation_logs entry yet.

2. For each row, try to recover its retrieved context (and reference answer,
   if any) from live_evaluation_cache via db/queries.get_cached_context().
   This cache is populated whenever that exact query+answer previously went
   through the live POST /evaluate endpoint (router.py). If nothing was
   cached, context stays None — same as before this fix.

3. Score each row with BOTH judges independently:
     - Custom LLM judge (judge.py) — always runs
     - RAGAS (tasks/ragas_judge.py) — answer_relevancy always; faithfulness
       when context was recovered; Group B metrics when a reference was
       recovered too.
   Each judge's result is saved as a SEPARATE row in evaluation_logs
   (model_used="custom_judge" and model_used="ragas") via an upsert that
   can never produce a duplicate row for the same (query_id, model_used)
   pair, even on retries.

4. Determine the overall score used for moderation flagging from whichever
   judge(s) actually completed. If EITHER judge flags the answer as low,
   it goes into the moderation queue (better to over-flag than under-flag).

5. Advance the cursor to the highest query_id processed this run, so the
   NEXT run picks up exactly where this one left off.

6. Prune live_evaluation_cache rows older than LIVE_CACHE_TTL_HOURS.

FAILURE HANDLING
-----------------
Each judge call is wrapped separately. If RAGAS fails (missing dependency,
LLM provider error, etc.) but the custom judge succeeds, the custom judge's
row is still saved. One judge failing never blocks the other, and never
crashes the whole batch.

If BOTH judges fail for a row, the cursor still advances past that row —
retrying the same row indefinitely isn't useful, and the NOT EXISTS check
in fetch_sample_query_ids would skip it next run anyway once cursor moves
past it. If you want failed rows retried, do NOT advance the cursor past
max_id_seen — that is a deliberate trade-off this version makes in favour
of forward progress.

CONTEXT AVAILABILITY
---------------------
rag_query_logs has no context column. Context recovery only works for rows
that went through POST /evaluate at answer time and got their context_chunks
cached in live_evaluation_cache (see db/models.py LiveEvaluationCache
docstring). Rows written directly to rag_query_logs without calling
/evaluate still score with context=None. The only complete solution for
that remaining gap is for generation-service to persist context into
rag_query_logs directly — outside this service's scope.
"""
from __future__ import annotations

import logging

from celery_app import celery_app
from db.queries import (
    ensure_tables_exist,
    fetch_sample_query_ids,
    save_evaluation_result,
    flag_for_moderation,
    get_cached_context,
    get_cursor,
    advance_cursor,
    prune_old_cache_entries,
    MODERATION_THRESHOLD,
    log_audit_event,
    get_max_query_id,
)
from tasks.moderation import should_flag_for_moderation

logger = logging.getLogger(__name__)

try:
    from metrics import (
        eval_runs_total,
        eval_rows_evaluated,
        eval_rows_flagged,
        eval_score_gauge,
        eval_latency,
    )
    _METRICS_AVAILABLE = True
except Exception:
    _METRICS_AVAILABLE = False
    logger.warning(
        "metrics module not available — Prometheus counters will not be updated"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _noop_ctx:
    """No-op context manager used when the metrics module is unavailable,
    so the `with eval_latency...` blocks below don't need if/else branches."""
    def __enter__(self):  return self
    def __exit__(self, *_): return False


def _score_with_custom_judge(
    query: str,
    answer: str,
    context: str | None,
) -> dict:
    """
    Calls the existing custom LLM judge (judge.py).

    `context` is the recovered context string (chunks joined with \\n\\n),
    or None if nothing was ever cached for this query+answer pair.
    judge.py's evaluate_answer() treats None as "No context provided" and
    scores accordingly — it does not error.

    Returns:
        {
            "faithfulness":  float 0–1,
            "relevance":     float 0–1,
            "completeness":  float 0–1,
            "raw_response":  str,
        }
    """
    from judge import evaluate_answer
    return evaluate_answer(query=query, answer=answer, context=context)


def _overall_score(scores: dict) -> float:
    """
    Simple average of the three shared score dimensions (faithfulness,
    relevance, completeness), skipping any that are None. Returns 0.0 if all
    three are None (shouldn't happen in practice but is defensive).
    """
    parts = [
        scores.get("faithfulness"),
        scores.get("relevance"),
        scores.get("completeness"),
    ]
    valid = [p for p in parts if p is not None]
    return sum(valid) / len(valid) if valid else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Celery task
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks.evaluate_batch.evaluate_recent_answers",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,          # task is re-queued if the worker crashes mid-run
    reject_on_worker_lost=True,
)
def evaluate_recent_answers(self):
    """
    Scheduled task. Celery Beat fires this — you don't call it directly in
    normal operation, though you can trigger it manually for testing:

        celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers

    See SETUP_GUIDE.md for the full manual-trigger command.
    """
    ensure_tables_exist()

    cursor_before = get_cursor()
    rows          = fetch_sample_query_ids()

    # ── Cursor self-healing ──────────────────────────────────────────────────
    # If the cursor has overshot all existing rows (e.g. after
    # clear_database.py, or after the cursor was advanced past the latest
    # id by a previous bug), reset it to 0 so the next sample picks up
    # from the beginning again.
    # Safe to do: fetch_sample_query_ids uses NOT EXISTS on evaluation_logs,
    # so rows already evaluated are skipped even after a cursor reset —
    # no row is ever double-scored.
    if len(rows) == 0 and cursor_before > 0:
        max_existing = get_max_query_id()   # 0 if table is empty
        if max_existing < cursor_before:
            logger.warning(
                "Cursor (%d) is ahead of the highest query_log id (%d) — "
                "resetting cursor to 0 so unevaluated rows are picked up.",
                cursor_before, max_existing,
            )
            advance_cursor(0)
            cursor_before = 0
            rows = fetch_sample_query_ids()

    logger.info(
        "evaluate_recent_answers start — %d rows sampled (cursor was id > %d)",
        len(rows), cursor_before,
    )

    evaluated   = 0
    flagged     = 0
    max_id_seen = cursor_before

    for row in rows:
        query_id    = row["id"]
        max_id_seen = max(max_id_seen, query_id)

        # ── Fix 1: recover context/reference from live cache ────────────────
        # Falls back to (None, None) if this row was never scored live —
        # same scoring behaviour as before this fix was applied.
        cached_chunks, reference = get_cached_context(row["query"], row["answer"])
        context = "\n\n".join(cached_chunks) if cached_chunks else None
        if context is None:
            logger.warning(
                "query_id=%s has no cached context — faithfulness score will be "
                "meaningless (context=None). Ensure EVALUATE_SYNC=true and "
                "EVALUATE_ON_GENERATION=true in .env so all queries populate the cache.",
                query_id,
            )

        row_overall_scores: list[float] = []
        saved_log_ids: list = []

        # ── Judge 1: custom judge (judge.py) ────────────────────────────────
        try:
            with (
                eval_latency.labels(judge="custom_judge").time()
                if _METRICS_AVAILABLE else _noop_ctx()
            ):
                custom_scores = _score_with_custom_judge(
                    query=row["query"],
                    answer=row["answer"],
                    context=context,
                )
            custom_overall = _overall_score(custom_scores)
            custom_log_id  = save_evaluation_result(
                query_id=query_id,
                model_used="custom_judge",
                faithfulness_score=custom_scores.get("faithfulness"),
                relevance_score=custom_scores.get("relevance"),
                completeness_score=custom_scores.get("completeness"),
                overall_score=custom_overall,
                raw_judge_response=custom_scores.get("raw_response"),
            )
            row_overall_scores.append(custom_overall)
            if custom_log_id is not None:
                saved_log_ids.append(custom_log_id)
            if _METRICS_AVAILABLE:
                eval_score_gauge.labels(judge="custom_judge").set(custom_overall)
        except Exception as exc:
            logger.warning(
                "Custom judge failed for query_id=%s: %s", query_id, exc,
                exc_info=True,
            )

        # ── Judge 2: RAGAS (full metric suite) ──────────────────────────────
        try:
            from tasks.ragas_judge import score_with_ragas_for_pipeline

            with (
                eval_latency.labels(judge="ragas").time()
                if _METRICS_AVAILABLE else _noop_ctx()
            ):
                ragas_result = score_with_ragas_for_pipeline(
                    query=row["query"],
                    answer=row["answer"],
                    context=context,
                    reference=reference,
                )
            ragas_full    = ragas_result["ragas_full"]
            # Use answer_relevancy as completeness proxy when answer_correctness
            # is None (no reference — normal for live traffic). Prevents overall
            # from being computed on a single metric.
            completeness_score = (
                ragas_full.get("answer_correctness")
                if ragas_full.get("answer_correctness") is not None
                else ragas_full.get("answer_relevancy")
            )
            ragas_overall = _overall_score({
                "faithfulness": ragas_full.get("faithfulness"),
                "relevance":    ragas_full.get("answer_relevancy"),
                "completeness": completeness_score,
            })
            ragas_log_id = save_evaluation_result(
                query_id=query_id,
                model_used="ragas",
                faithfulness_score=ragas_full.get("faithfulness"),
                relevance_score=ragas_full.get("answer_relevancy"),
                completeness_score=completeness_score,
                overall_score=ragas_overall,
                raw_judge_response=ragas_full.get("raw_response"),
                ragas_context_precision=ragas_full.get("context_precision"),
                ragas_context_recall=ragas_full.get("context_recall"),
                ragas_context_entity_recall=ragas_full.get("context_entity_recall"),
                ragas_answer_correctness=ragas_full.get("answer_correctness"),
                ragas_answer_similarity=ragas_full.get("answer_similarity"),
            )
            row_overall_scores.append(ragas_overall)
            if ragas_log_id is not None:
                saved_log_ids.append(ragas_log_id)
            if _METRICS_AVAILABLE:
                eval_score_gauge.labels(judge="ragas").set(ragas_overall)
        except Exception as exc:
            logger.warning(
                "RAGAS judge failed for query_id=%s: %s", query_id, exc,
                exc_info=True,
            )

        if not row_overall_scores:
            # Both judges failed — skip moderation for this row but still
            # advance the cursor past it (forward progress > perfect coverage).
            logger.error(
                "Both judges failed for query_id=%s — row skipped", query_id
            )
            continue

        evaluated += 1

        # Flag for moderation if EITHER judge's score is below threshold —
        # better to over-flag (human dismisses false alarm) than under-flag
        # (bad answer slips through because only one judge caught it).
        worst_score = min(row_overall_scores)
        if (
            should_flag_for_moderation(worst_score, threshold=MODERATION_THRESHOLD)
            and saved_log_ids
        ):
            # flag_for_moderation() is itself idempotent (ON CONFLICT DO
            # NOTHING on query_id), so calling it twice for the same row
            # is safe even if this task is retried.
            was_new = flag_for_moderation(
                query_id=query_id,
                evaluation_log_id=saved_log_ids[0],
            )
            if was_new:
                flagged += 1

    # ── Fix 3: advance cursor so the next run starts where this one ended ───
    if max_id_seen > cursor_before:
        advance_cursor(max_id_seen)

    # Prune stale live-cache entries — best-effort, never crashes the task.
    pruned = 0
    try:
        pruned = prune_old_cache_entries()
    except Exception as exc:
        logger.warning("Could not prune live_evaluation_cache: %s", exc)

    if _METRICS_AVAILABLE:
        eval_runs_total.inc()
        eval_rows_evaluated.inc(evaluated)
        eval_rows_flagged.inc(flagged)

    try:
        log_audit_event(
            event_type="batch_run",
            actor="celery_beat",
            query_id=None,
            details={
                "evaluated": evaluated,
                "flagged": flagged,
                "cursor_before": cursor_before,
                "cursor_after": max_id_seen,
                "pruned": pruned,
            },
        )
    except Exception as exc:
        logger.warning("Could not log batch run audit: %s", exc)

    logger.info(
        "evaluate_recent_answers complete — evaluated=%d flagged=%d "
        "cursor_advanced_to=%d cache_rows_pruned=%d",
        evaluated, flagged, max_id_seen, pruned,
    )
    return {
        "evaluated":       evaluated,
        "flagged_for_review": flagged,
        "cursor":          max_id_seen,
        "cache_rows_pruned": pruned,
    }