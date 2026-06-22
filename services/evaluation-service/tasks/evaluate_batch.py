"""
tasks/evaluate_batch.py
-------------------------
The task Celery Beat fires on a schedule (see celery_app.py).

Each run:
  1. Reads the cursor (db/queries.get_cursor) — the highest
     rag_query_logs.id already processed — and pulls a random sample of
     rows with id > cursor that don't already have an evaluation_logs
     entry (db/queries.fetch_sample_query_ids).
  2. For each row, tries to recover its retrieved context (and reference
     answer, if any) from live_evaluation_cache via
     db/queries.get_cached_context() — populated whenever that exact
     query+answer previously went through the live POST /evaluate
     endpoint (router.py). If nothing was cached (the row was never
     scored live), context stays empty, same behavior as before this fix.
  3. Scores each row using BOTH judges:
       - the existing custom Judge LLM (judge.py)
       - RAGAS (tasks/ragas_judge.py) — answer_relevancy on every row;
         faithfulness when context was recovered; context_precision,
         context_recall, context_entity_recall, answer_correctness, and
         answer_similarity when a reference answer was recovered.
     Both results are saved as SEPARATE rows in evaluation_logs
     (model_used="custom_judge" and model_used="ragas") via an UPSERT
     (db/queries.save_evaluation_result) that can never create a
     duplicate row for the same (query_id, model_used) pair, even if this
     task is retried.
  4. The overall score used for the moderation threshold check is taken
     from whichever judge(s) actually ran — see _overall_score_for_flagging.
  5. If that score is low, flags the query into moderation_queue
     (also de-duplicated — see db/queries.flag_for_moderation).
  6. Advances the cursor to the highest query_id processed this run, so
     the NEXT run picks up exactly where this one left off — no sliding
     time window, no re-scanning.
  7. Prunes live_evaluation_cache rows older than LIVE_CACHE_TTL_HOURS so
     that bridge table doesn't grow forever.

ABOUT CONTEXT AVAILABILITY
------------------------------
rag_query_logs (the real table, per README.md's ERD) has NO context
column of its own — id, domain_id, user_id, query, answer, llm_route,
model, created_at is the full set. Context recovery here ONLY works for
rows that were actually scored once live, i.e. went through POST
/evaluate at answer time and got cached (see db/models.py's
LiveEvaluationCache docstring). Rows that were written to rag_query_logs
without ever calling /evaluate still have no context recoverable after
the fact — context stays None for those, exactly as before this fix.
The only way to close that remaining gap completely is for
generation-service to persist context into rag_query_logs directly,
which is outside the scope of this service.

Failure handling: each judge call is wrapped separately. If RAGAS fails
(e.g. package not installed, LLM provider error) but the custom judge
succeeds, the custom judge's row is still saved — one judge failing never
blocks the other, and never crashes the whole batch.
"""
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
except Exception:  # pragma: no cover - metrics module should always be present, but never let it block scoring
    _METRICS_AVAILABLE = False
    logger.warning("metrics module not available — Prometheus counters will not be updated")


def _score_with_custom_judge(query: str, answer: str, context: str | None) -> dict:
    """
    Calls the existing custom Judge LLM (evaluation-service/judge.py).
    Import is local to this function so a missing/broken judge.py module
    doesn't break celery_app's import chain at startup.

    `context` is the recovered context for this row (joined into a single
    string), or None if nothing was ever cached for this exact
    query+answer (see this file's module docstring). judge.py's
    evaluate_answer() treats None as "no context provided" and scores
    accordingly rather than erroring.

    Expected return shape (adjust to match your real judge.py output):
        {
            "faithfulness":  0.0-1.0,
            "relevance":     0.0-1.0,
            "completeness":  0.0-1.0,
            "raw_response":  "<full LLM judge text, for debugging>",
        }
    """
    from judge import evaluate_answer  # your existing function in judge.py

    result = evaluate_answer(query=query, answer=answer, context=context)
    return result


def _overall_score(scores: dict) -> float:
    """
    Combines a judge's sub-scores into one overall score. Simple average
    of whichever fields are present (None values skipped) — adjust
    weights here if one dimension matters more than the others.
    """
    parts = [
        scores.get("faithfulness"),
        scores.get("relevance"),
        scores.get("completeness"),
    ]
    valid = [p for p in parts if p is not None]
    return sum(valid) / len(valid) if valid else 0.0


class _noop_ctx:
    """Used as a no-op context manager when metrics aren't available, so
    the `with eval_latency...` lines below don't need an if/else branch
    duplicated for every judge call."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@celery_app.task(
    name="tasks.evaluate_batch.evaluate_recent_answers",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def evaluate_recent_answers(self):
    """
    The scheduled task. Celery Beat calls this — you never call it
    directly in normal operation, though you CAN trigger it manually for
    testing (see SETUP_GUIDE.md for the exact command).
    """
    ensure_tables_exist()

    cursor_before = get_cursor()
    rows = fetch_sample_query_ids()
    logger.info(
        "evaluate_recent_answers: %d rows sampled for evaluation (cursor was id > %d)",
        len(rows), cursor_before,
    )

    evaluated = 0
    flagged   = 0
    max_id_seen = cursor_before

    for row in rows:
        query_id = row["id"]
        max_id_seen = max(max_id_seen, query_id)

        # FIX: recover context/reference for this exact query+answer from
        # the live-evaluation cache, if it was ever scored live. Falls
        # back to (None, None) if it never was — same as before this fix.
        cached_chunks, reference = get_cached_context(row["query"], row["answer"])
        context = "\n\n".join(cached_chunks) if cached_chunks else None

        row_overall_scores = []
        saved_log_ids = []

        # ── Judge 1: custom judge (judge.py) ──────────────────────────
        try:
            with (eval_latency.labels(judge="custom_judge").time() if _METRICS_AVAILABLE else _noop_ctx()):
                custom_scores = _score_with_custom_judge(
                    query=row["query"],
                    answer=row["answer"],
                    context=context,
                )
            custom_overall = _overall_score(custom_scores)
            custom_log_id = save_evaluation_result(
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
            logger.warning("Custom judge failed for query_id=%s: %s", query_id, exc)

        # ── Judge 2: RAGAS (full metric suite) ────────────────────────
        try:
            from tasks.ragas_judge import score_with_ragas_for_pipeline

            with (eval_latency.labels(judge="ragas").time() if _METRICS_AVAILABLE else _noop_ctx()):
                ragas_result = score_with_ragas_for_pipeline(
                    query=row["query"],
                    answer=row["answer"],
                    context=context,
                    reference=reference,
                )
            ragas_full = ragas_result["ragas_full"]
            ragas_overall = _overall_score({
                "faithfulness": ragas_full["faithfulness"],
                "relevance":    ragas_full["answer_relevancy"],
                "completeness": ragas_full["answer_correctness"],
            })
            ragas_log_id = save_evaluation_result(
                query_id=query_id,
                model_used="ragas",
                faithfulness_score=ragas_full["faithfulness"],
                relevance_score=ragas_full["answer_relevancy"],
                completeness_score=ragas_full["answer_correctness"],
                overall_score=ragas_overall,
                raw_judge_response=ragas_full["raw_response"],
                ragas_context_precision=ragas_full["context_precision"],
                ragas_context_recall=ragas_full["context_recall"],
                ragas_context_entity_recall=ragas_full["context_entity_recall"],
                ragas_answer_correctness=ragas_full["answer_correctness"],
                ragas_answer_similarity=ragas_full["answer_similarity"],
            )
            row_overall_scores.append(ragas_overall)
            if ragas_log_id is not None:
                saved_log_ids.append(ragas_log_id)
            if _METRICS_AVAILABLE:
                eval_score_gauge.labels(judge="ragas").set(ragas_overall)
        except Exception as exc:
            logger.warning("RAGAS judge failed for query_id=%s: %s", query_id, exc)

        if not row_overall_scores:
            # Both judges failed for this row — nothing to flag. Cursor
            # still advances past this id (see below) since retrying the
            # exact same row forever isn't useful; the NOT EXISTS check
            # in fetch_sample_query_ids would have skipped it next run
            # anyway once max_id_seen moves past it. If you want failed
            # rows retried instead of skipped, don't advance the cursor
            # past max_id_seen here — that's a deliberate trade-off this
            # version makes in favor of forward progress.
            continue

        evaluated += 1

        # Flag for moderation if EITHER judge's score is low — better to
        # over-flag (a human dismisses a false alarm) than under-flag (a
        # bad answer slips through because only one judge caught it).
        worst_score = min(row_overall_scores)
        if should_flag_for_moderation(worst_score, threshold=MODERATION_THRESHOLD) and saved_log_ids:
            # Use whichever evaluation_logs row was saved first as the
            # foreign key target — moderation_queue references one
            # specific evaluation_log row, even though both judges'
            # scores informed the decision to flag. flag_for_moderation()
            # itself de-duplicates by query_id.
            flag_for_moderation(query_id=query_id, evaluation_log_id=saved_log_ids[0])
            flagged += 1

    # FIX (tracking last evaluated record): advance the cursor to the
    # highest query_id we saw this run, so the next run starts exactly
    # where this one left off instead of re-scanning a time window.
    if max_id_seen > cursor_before:
        advance_cursor(max_id_seen)

    pruned = 0
    try:
        pruned = prune_old_cache_entries()
    except Exception as exc:
        logger.warning("Could not prune live_evaluation_cache: %s", exc)

    if _METRICS_AVAILABLE:
        eval_runs_total.inc()
        eval_rows_evaluated.inc(evaluated)
        eval_rows_flagged.inc(flagged)

    logger.info(
        "evaluate_recent_answers complete — evaluated=%d flagged_for_review=%d "
        "cursor_advanced_to=%d cache_rows_pruned=%d",
        evaluated, flagged, max_id_seen, pruned,
    )
    return {
        "evaluated": evaluated,
        "flagged_for_review": flagged,
        "cursor": max_id_seen,
        "cache_rows_pruned": pruned,
    }
