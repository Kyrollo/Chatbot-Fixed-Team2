"""
db/queries.py
--------------
Database connection + query helpers for the evaluation pipeline.

Follows the exact same DB connection pattern already used in
worker-service/tasks/process.py — prefer SYNC_DATABASE_URL, fall back to
DATABASE_URL with asyncpg stripped, fall back to building one from
individual POSTGRES_* env vars. Reusing this pattern means no new .env
keys are required if you're running this inside the same environment as
worker-service.

SCHEMA NOTE (README.md ERD, rag_query_logs table)
----------------------------------------------------
rag_query_logs.id is `bigint`, NOT a UUID, so evaluation_logs.query_id
and every function signature below use `int`. rag_query_logs itself has
NO context/reference column — id, domain_id, user_id, query, answer,
llm_route, model, created_at is the full set per the ERD. See
db/models.py's module docstring for how this fix recovers context anyway
(LiveEvaluationCache) without needing to touch rag_query_logs or
generation-service.

THREE FIXES IN THIS FILE
----------------------------
1. save_live_evaluation_cache() / get_cached_context() — store and
   recover context_chunks/reference for a (query, answer) pair so the
   batch job isn't always working with context=None.
2. save_evaluation_result() now does an upsert (INSERT ... ON CONFLICT
   (query_id, model_used) DO NOTHING) instead of a plain INSERT, so a
   retried/duplicated task call can never create a second row for the
   same (query_id, model_used) pair — backed by the UNIQUE constraint
   added in db/models.py.
3. get_cursor() / advance_cursor() + fetch_sample_query_ids() now sample
   rows with id > cursor instead of "rows from the last N minutes",
   giving a deterministic watermark instead of a sliding time window.
   EVAL_LOOKBACK_MINUTES is kept ONLY as a safety fallback for the very
   first run (when no cursor exists yet) so a brand-new install doesn't
   try to evaluate the entire historical table in one go.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from db.models import (
    Base,
    EvaluationLog,
    ModerationQueueItem,
    LiveEvaluationCache,
    EvalCursor,
    context_cache_key,
)

load_dotenv()

# Same resolution order as worker-service/tasks/process.py
_raw_url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
if not _raw_url:
    from urllib.parse import quote
    user     = os.getenv("POSTGRES_USER", "postgres")
    password = quote(os.getenv("POSTGRES_PASSWORD", "postgres"), safe="")
    db       = os.getenv("POSTGRES_DB", "domain_db")
    _raw_url = f"postgresql://{user}:{password}@localhost:5432/{db}"
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

_engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=_engine)

# Used ONLY as a fallback on the very first run, before any cursor row
# exists — bounds how far back the first-ever batch run looks, so a
# fresh install doesn't try to evaluate the entire history of
# rag_query_logs in one shot. After the first run, the cursor
# (eval_cursor table) takes over and this value stops mattering.
EVAL_LOOKBACK_MINUTES = int(os.getenv("EVAL_LOOKBACK_MINUTES", "35"))

# What fraction of eligible rows to actually evaluate. 0.05 = 5%.
EVAL_SAMPLE_RATE = float(os.getenv("EVAL_SAMPLE_RATE", "0.05"))

# Score below this triggers a moderation_queue entry.
MODERATION_THRESHOLD = float(os.getenv("MODERATION_THRESHOLD", "0.6"))

# How long a live-evaluation cache row is kept before prune_old_cache_entries()
# considers it stale. Default 7 days — generous, since the batch job runs
# every 30 minutes by default and should consume entries long before this.
LIVE_CACHE_TTL_HOURS = int(os.getenv("LIVE_CACHE_TTL_HOURS", "168"))

_CURSOR_NAME = "default"


def ensure_tables_exist() -> None:
    """
    Creates evaluation_logs, moderation_queue, live_evaluation_cache, and
    eval_cursor if they don't exist yet. Safe to call on every startup —
    Base.metadata.create_all() is a no-op for tables that already exist.
    Switch to a real Alembic migration later if the schema needs to
    evolve in a controlled way.
    """
    Base.metadata.create_all(bind=_engine)


# ----------------------------------------------------------------------
# FIX 1 — retrieved context / reference persistence
# ----------------------------------------------------------------------

def save_live_evaluation_cache(query: str, answer: str,
                                context_chunks: list[str],
                                reference: Optional[str] = None) -> None:
    """
    Called from router.py / judge.py every time the live POST /evaluate
    endpoint scores a request. Saves context_chunks (and an optional
    reference answer, if a caller ever starts sending one) so the
    scheduled batch job can recover them later for the SAME query+answer
    pair, instead of always scoring with context=None.

    Upsert on cache_key: if this exact (query, answer) was already cached
    (e.g. the same question was asked twice), the newer context simply
    overwrites the older one — there's no value in keeping both, only the
    most recent retrieval for that exact text matters.

    Never raises — this is a best-effort side channel for the batch job.
    If it fails (DB hiccup, etc.) the live /evaluate response to the
    caller must NOT be affected, so failures are swallowed here and the
    caller (router.py) should call this in a way that can't break the
    live response (e.g. wrapped in try/except, or fire-and-forget).
    """
    key = context_cache_key(query, answer)
    session = SessionLocal()
    try:
        stmt = pg_insert(LiveEvaluationCache).values(
            id=uuid.uuid4(),
            cache_key=key,
            query=query,
            answer=answer,
            context_chunks=json.dumps(context_chunks or []),
            reference=reference,
            consumed=False,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[LiveEvaluationCache.cache_key],
            set_={
                "context_chunks": stmt.excluded.context_chunks,
                "reference": stmt.excluded.reference,
                "consumed": False,
                "created_at": datetime.now(timezone.utc),
            },
        )
        session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_cached_context(query: str, answer: str) -> tuple[Optional[list[str]], Optional[str]]:
    """
    Looks up previously-saved context_chunks/reference for this exact
    (query, answer) pair, computed via the same query+answer the batch
    job pulled from rag_query_logs. Returns (context_chunks, reference) —
    both None if nothing was ever cached for this pair (e.g. the row was
    written directly to rag_query_logs without ever going through the
    live /evaluate endpoint).

    Marks the row consumed=True on a hit, purely for observability (so
    you can tell, via SQL, which cache entries actually got used vs. went
    stale and were pruned unused) — does NOT delete it, since the same
    query+answer could legitimately be sampled again if it reappears.
    """
    key = context_cache_key(query, answer)
    session = SessionLocal()
    try:
        row = session.query(LiveEvaluationCache).filter_by(cache_key=key).first()
        if row is None:
            return None, None
        if not row.consumed:
            row.consumed = True
            session.commit()
        chunks = json.loads(row.context_chunks) if row.context_chunks else []
        return chunks, row.reference
    finally:
        session.close()


def prune_old_cache_entries(ttl_hours: int = LIVE_CACHE_TTL_HOURS) -> int:
    """
    Deletes live_evaluation_cache rows older than ttl_hours. Call this
    occasionally (e.g. once a day, or at the top of evaluate_recent_answers)
    to keep the table from growing forever — it's a short-lived bridge
    table, not a permanent archive. Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    session = SessionLocal()
    try:
        deleted = (
            session.query(LiveEvaluationCache)
            .filter(LiveEvaluationCache.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        session.commit()
        return deleted
    finally:
        session.close()


# ----------------------------------------------------------------------
# FIX 3 — cursor / watermark tracking
# ----------------------------------------------------------------------

def get_cursor() -> int:
    """
    Returns the last rag_query_logs.id the batch job has finished
    processing (0 if this is the very first run ever). Creates the
    cursor row on first call if it doesn't exist yet.
    """
    session = SessionLocal()
    try:
        row = session.query(EvalCursor).filter_by(name=_CURSOR_NAME).first()
        if row is None:
            row = EvalCursor(name=_CURSOR_NAME, last_query_id=0)
            session.add(row)
            session.commit()
            return 0
        return row.last_query_id
    finally:
        session.close()


def advance_cursor(new_last_id: int) -> None:
    """
    Moves the cursor forward to new_last_id. Only ever moves forward —
    if new_last_id is smaller than the current cursor (shouldn't happen
    in normal operation, but defensively guarded), the cursor is left
    unchanged rather than going backwards and risking re-evaluating rows.
    """
    session = SessionLocal()
    try:
        row = session.query(EvalCursor).filter_by(name=_CURSOR_NAME).first()
        if row is None:
            row = EvalCursor(name=_CURSOR_NAME, last_query_id=new_last_id)
            session.add(row)
        elif new_last_id > row.last_query_id:
            row.last_query_id = new_last_id
        session.commit()
    finally:
        session.close()


# ----------------------------------------------------------------------
# Sampling — now cursor-based instead of a sliding time window
# ----------------------------------------------------------------------

def fetch_sample_query_ids(sample_rate: float = EVAL_SAMPLE_RATE) -> list[dict]:
    """
    Pulls a random sample of rag_query_logs rows with id > the current
    cursor (see get_cursor() above) that don't already have an
    evaluation_logs entry — the second check is a defensive belt-and-
    suspenders alongside the cursor and the UNIQUE constraint on
    evaluation_logs, not the only thing standing between this and
    duplicate evaluation anymore.

    On the very first run ever (cursor == 0), falls back to
    EVAL_LOOKBACK_MINUTES so a fresh install doesn't try to evaluate the
    entire history of rag_query_logs in one go — every run after that is
    purely id > cursor, no time window involved.

    Columns match the REAL rag_query_logs schema (README.md ERD):
    id (bigint), domain_id, user_id, query, answer, llm_route, model,
    created_at. There is no context/reference column on this table — see
    db/models.py's module docstring for how LiveEvaluationCache recovers
    this for rows that were scored live.

    Returns a list of dicts: [{"id": <int>, "query": ..., "answer": ...}, ...]
    Does NOT advance the cursor — call advance_cursor() yourself after
    successfully processing the batch (see tasks/evaluate_batch.py).
    """
    cursor = get_cursor()

    if cursor == 0:
        # First run ever — bound by lookback window AND id, whichever is
        # more restrictive, so a fresh install doesn't try to evaluate
        # years of history in one batch.
        sql = text("""
            SELECT q.id, q.query, q.answer
            FROM rag_query_logs q
            WHERE q.created_at >= NOW() - INTERVAL :lookback
              AND NOT EXISTS (
                  SELECT 1 FROM evaluation_logs e WHERE e.query_id = q.id
              )
              AND random() < :sample_rate
            ORDER BY q.id ASC
        """)
        params = {"lookback": f"{EVAL_LOOKBACK_MINUTES} minutes", "sample_rate": sample_rate}
    else:
        sql = text("""
            SELECT q.id, q.query, q.answer
            FROM rag_query_logs q
            WHERE q.id > :cursor
              AND NOT EXISTS (
                  SELECT 1 FROM evaluation_logs e WHERE e.query_id = q.id
              )
              AND random() < :sample_rate
            ORDER BY q.id ASC
        """)
        params = {"cursor": cursor, "sample_rate": sample_rate}

    with _engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(row._mapping) for row in rows]


def save_evaluation_result(
    query_id: int,
    model_used: str,
    faithfulness_score: Optional[float],
    relevance_score: Optional[float],
    completeness_score: Optional[float],
    overall_score: Optional[float],
    raw_judge_response: Optional[str],
    ragas_context_precision: Optional[float] = None,
    ragas_context_recall: Optional[float] = None,
    ragas_context_entity_recall: Optional[float] = None,
    ragas_answer_correctness: Optional[float] = None,
    ragas_answer_similarity: Optional[float] = None,
) -> Optional[uuid.UUID]:
    """
    FIX (duplicate evaluation): upserts via INSERT ... ON CONFLICT
    (query_id, model_used) DO NOTHING, backed by the UNIQUE constraint on
    EvaluationLog (db/models.py). If a row for this exact (query_id,
    model_used) pair already exists — e.g. a retried Celery task, or a
    race between two runs — this is a safe no-op instead of a second row.

    Returns the new row's id, or the EXISTING row's id if the insert was
    skipped because that (query_id, model_used) pair was already
    evaluated — callers (tasks/evaluate_batch.py) always get a usable id
    back, e.g. for flag_for_moderation, whether or not this call actually
    inserted anything.
    """
    session = SessionLocal()
    try:
        new_id = uuid.uuid4()
        stmt = pg_insert(EvaluationLog).values(
            id=new_id,
            query_id=query_id,
            model_used=model_used,
            faithfulness_score=faithfulness_score,
            relevance_score=relevance_score,
            completeness_score=completeness_score,
            overall_score=overall_score,
            raw_judge_response=raw_judge_response,
            ragas_context_precision=ragas_context_precision,
            ragas_context_recall=ragas_context_recall,
            ragas_context_entity_recall=ragas_context_entity_recall,
            ragas_answer_correctness=ragas_answer_correctness,
            ragas_answer_similarity=ragas_answer_similarity,
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["query_id", "model_used"],
        )
        result = session.execute(stmt)
        session.commit()

        if result.rowcount == 0:
            # Already evaluated by this judge — look up the existing row
            # id so callers that need it (e.g. for flag_for_moderation)
            # still get something usable.
            existing = (
                session.query(EvaluationLog)
                .filter_by(query_id=query_id, model_used=model_used)
                .first()
            )
            return existing.id if existing else None
        return new_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def flag_for_moderation(query_id: int, evaluation_log_id: uuid.UUID) -> None:
    """
    Inserts a pending moderation_queue row for a low-scoring answer.
    Guards against double-flagging the same query_id (e.g. if both judges
    independently trigger a flag across retried runs) by checking for an
    existing row first.
    """
    session = SessionLocal()
    try:
        already_flagged = (
            session.query(ModerationQueueItem)
            .filter_by(query_id=query_id)
            .first()
        )
        if already_flagged is not None:
            return
        item = ModerationQueueItem(
            query_id=query_id,
            evaluation_log_id=evaluation_log_id,
            status="pending",
        )
        session.add(item)
        session.commit()
    finally:
        session.close()


def list_pending_moderation_items() -> list[dict]:
    """Returns all pending moderation_queue rows, joined with their score."""
    sql = text("""
        SELECT m.id, m.query_id, m.status, m.created_at,
               e.overall_score, e.faithfulness_score, e.relevance_score,
               q.query, q.answer
        FROM moderation_queue m
        JOIN evaluation_logs e ON e.id = m.evaluation_log_id
        JOIN rag_query_logs   q ON q.id = m.query_id
        WHERE m.status = 'pending'
        ORDER BY m.created_at ASC
    """)
    with _engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row._mapping) for row in rows]


def decide_moderation_item(item_id: uuid.UUID, decision: str,
                            reviewer: str, notes: Optional[str] = None) -> bool:
    """
    Records a human reviewer's decision. decision must be "approved" or
    "rejected". Returns False if the item_id doesn't exist.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")

    session = SessionLocal()
    try:
        item = session.query(ModerationQueueItem).filter_by(id=item_id).first()
        if item is None:
            return False
        item.status = decision
        item.reviewer = reviewer
        item.decision_notes = notes
        item.decided_at = datetime.now(timezone.utc)
        session.commit()
        return True
    finally:
        session.close()
