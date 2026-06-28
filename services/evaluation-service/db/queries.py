"""
db/queries.py
--------------
Database connection + query helpers for the evaluation pipeline.

Follows the exact same DB connection pattern already used in
worker-service/tasks/process.py — prefers SYNC_DATABASE_URL, falls back to
DATABASE_URL with asyncpg stripped, then builds one from individual
POSTGRES_* env vars.

SCHEMA NOTE
-----------
rag_query_logs.id is `bigint` (auto-increment integer), NOT a UUID.
rag_query_logs has NO context/reference column — id, domain_id, user_id,
query, answer, llm_route, model, created_at is the full set per the ERD.

THREE PRODUCTION FIXES IN THIS FILE
--------------------------------------
Fix 1 — save_live_evaluation_cache() / get_cached_context()
    Store and recover context_chunks/reference for a (query, answer) pair so
    the batch job isn't always scoring with context=None. Upsert on
    cache_key so a repeated (query, answer) overwrites stale context instead
    of leaving both rows.

Fix 2 — save_evaluation_result() upsert
    Uses INSERT ... ON CONFLICT (query_id, model_used) DO NOTHING backed by
    the UniqueConstraint in db/models.py. A retried or concurrent Celery
    task call is a safe no-op — never creates a duplicate row.

Fix 3 — get_cursor() / advance_cursor() + cursor-based fetch_sample_query_ids()
    Deterministic watermark: rows with id > cursor are processed, then the
    cursor is advanced.  First-run fallback (cursor==0) uses
    EVAL_LOOKBACK_MINUTES to avoid evaluating the entire history at once.
    flag_for_moderation() also uses INSERT ... ON CONFLICT DO NOTHING to
    prevent duplicate moderation entries.
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
    AuditLog,
    context_cache_key,
)

load_dotenv()

# Same resolution order as worker-service/tasks/process.py
_raw_url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
if not _raw_url:
    from urllib.parse import quote
    _user     = os.getenv("POSTGRES_USER", "postgres")
    _password = quote(os.getenv("POSTGRES_PASSWORD", "postgres"), safe="")
    _db       = os.getenv("POSTGRES_DB", "domain_db")
    _host     = os.getenv("POSTGRES_HOST", "localhost")
    _port     = os.getenv("POSTGRES_PORT", "5434")
    _raw_url  = f"postgresql://{_user}:{_password}@{_host}:{_port}/{_db}"
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

_engine      = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# ── Tuning constants (all read from .env, with sane defaults) ────────────────

# Used ONLY as a safety fallback on the very first run (cursor==0), so a
# fresh install doesn't try to evaluate the entire rag_query_logs history
# in one shot. Ignored on every subsequent run once the cursor exists.
EVAL_LOOKBACK_MINUTES = int(os.getenv("EVAL_LOOKBACK_MINUTES", "525960"))  # default = 1 year

# Fraction of eligible rows to actually evaluate each run. 0.05 = 5%.
EVAL_SAMPLE_RATE = float(os.getenv("EVAL_SAMPLE_RATE", "1.0"))

# Score below this triggers a moderation_queue entry.
MODERATION_THRESHOLD = float(os.getenv("MODERATION_THRESHOLD", "0.9"))

# How long a live-evaluation cache row is kept before pruning. 7 days is
# generous; the batch job runs every 30 minutes and consumes entries long
# before this TTL is reached in practice.
LIVE_CACHE_TTL_HOURS = int(os.getenv("LIVE_CACHE_TTL_HOURS", "168"))

_CURSOR_NAME = "default"


# ─────────────────────────────────────────────────────────────────────────────
# Schema bootstrap
# ─────────────────────────────────────────────────────────────────────────────

def ensure_tables_exist() -> None:
    """
    Creates evaluation_logs, moderation_queue, live_evaluation_cache, and
    eval_cursor if they don't exist yet.

    Safe to call on every startup — Base.metadata.create_all() is a no-op
    for tables that already exist. For production environments with Alembic
    migrations, call ensure_tables_exist() as a fallback only; let
    migrations handle schema evolution.
    """
    Base.metadata.create_all(bind=_engine)


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — retrieved context / reference persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_live_evaluation_cache(
    query: str,
    answer: str,
    context_chunks: list[str],
    reference: Optional[str] = None,
) -> None:
    """
    Called from router.py after every successful POST /evaluate. Saves
    context_chunks (and optionally a reference answer) so evaluate_batch.py
    can recover them for the same (query, answer) pair when it later samples
    that row from rag_query_logs.

    Upsert semantics: if this exact (query, answer) hash was already cached
    (e.g. the same question was asked twice), the newer context overwrites
    the older one — only the most recent retrieval for that exact text is
    relevant.

    Never raises — this is a best-effort side channel. Failures are caught
    here; router.py wraps calls in its own try/except so the live response
    is never affected by a cache write failure.
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
            created_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "context_chunks": stmt.excluded.context_chunks,
                "reference":      stmt.excluded.reference,
                "consumed":       False,
                "created_at":     datetime.now(timezone.utc),
            },
        )
        session.execute(stmt)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_cached_context(
    query: str,
    answer: str,
) -> tuple[Optional[list[str]], Optional[str]]:
    """
    Looks up previously-saved context_chunks/reference for this exact
    (query, answer) pair.

    Returns (context_chunks, reference) — both None if nothing was cached
    (i.e. this row was written directly to rag_query_logs without ever
    going through POST /evaluate).

    Marks the row consumed=True on a successful hit, purely for
    observability (SQL queries can then distinguish "was this context entry
    ever used?" from "is this stale and can be pruned?"). Does NOT delete
    the row — the same query+answer pair could legitimately be sampled
    again in a later batch run.
    """
    key = context_cache_key(query, answer)
    session = SessionLocal()
    try:
        row = (
            session.query(LiveEvaluationCache)
            .filter(LiveEvaluationCache.cache_key == key)
            .with_for_update(skip_locked=True)   # safe concurrent access
            .first()
        )
        if row is None:
            return None, None
        if not row.consumed:
            row.consumed = True
            session.commit()
        chunks = json.loads(row.context_chunks) if row.context_chunks else []
        return chunks or None, row.reference
    except Exception:
        session.rollback()
        return None, None
    finally:
        session.close()


def prune_old_cache_entries(ttl_hours: int = LIVE_CACHE_TTL_HOURS) -> int:
    """
    Deletes live_evaluation_cache rows older than ttl_hours. Returns the
    number of rows deleted.

    Intended to be called at the end of evaluate_recent_answers() so the
    bridge table doesn't grow forever. Default TTL is 7 days — generous
    given the batch job runs every 30 minutes and typically consumes rows
    long before they age out.
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
    except Exception:
        session.rollback()
        return 0
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3 — cursor / watermark tracking
# ─────────────────────────────────────────────────────────────────────────────

def get_cursor() -> int:
    """
    Returns the highest rag_query_logs.id the batch job has already finished
    processing (0 if this is the very first ever run). Creates the singleton
    cursor row on first call if it doesn't exist yet.
    """
    session = SessionLocal()
    try:
        row = (
            session.query(EvalCursor)
            .filter(EvalCursor.name == _CURSOR_NAME)
            .first()
        )
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
    Moves the cursor to new_last_id.

    Forward movement (new_last_id > current): normal operation after each
    batch run — advances the watermark so the next run picks up where this
    one left off.

    Reset to 0 (new_last_id == 0): explicit self-healing — called by
    evaluate_batch.py when the cursor has overshot all existing rows (e.g.
    after clear_database.py). Passing 0 is always honoured regardless of
    the current cursor value so the reset actually takes effect.

    Any other backward move (0 < new_last_id < current) is still ignored
    to prevent accidental re-evaluation of already-processed rows.
    """
    session = SessionLocal()
    try:
        row = (
            session.query(EvalCursor)
            .filter(EvalCursor.name == _CURSOR_NAME)
            .with_for_update()
            .first()
        )
        if row is None:
            row = EvalCursor(
                name=_CURSOR_NAME,
                last_query_id=new_last_id,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
        elif new_last_id == 0 or new_last_id > row.last_query_id:
            # Allow explicit reset to 0 (self-healing) OR normal forward move.
            row.last_query_id = new_last_id
            row.updated_at    = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_max_query_id() -> int:
    """
    Returns the highest id currently in rag_query_logs, or 0 if the
    table is empty.

    Used by evaluate_batch.py's cursor self-healing block to detect when
    the cursor has overshot all existing rows — e.g. after
    clear_database.py wipes and re-seeds the DB, leaving the old cursor
    value (stored in the eval_cursor table) pointing past every new
    row's id.

    Never raises: if the table doesn't exist yet, the SELECT returns
    NULL which COALESCE turns into 0, and the caller treats 0 as
    "nothing to compare against, skip the reset".

    FIX: previously called a nonexistent `_get_conn()` helper (leftover
    from a raw psycopg2-style pattern that was never defined/imported in
    this file), which raised NameError every time this function was
    reached — i.e. on every run where there were no new rows to sample
    AND the cursor was already past 0 (a perfectly normal, frequent
    steady-state, not just a post-reset edge case). Rewritten to use the
    same SQLAlchemy `_engine` + `text()` pattern already used everywhere
    else in this file (see get_query_detail() below for the same shape).
    """
    sql = text("SELECT COALESCE(MAX(id), 0) FROM rag_query_logs;")
    with _engine.connect() as conn:
        return conn.execute(sql).scalar()


# ─────────────────────────────────────────────────────────────────────────────
# Sampling — cursor-based, not a sliding time window
# ─────────────────────────────────────────────────────────────────────────────

def fetch_sample_query_ids(sample_rate: float = EVAL_SAMPLE_RATE) -> list[dict]:
    """
    Pulls a random sample of rag_query_logs rows with id > cursor that
    have NOT yet been evaluated by ANY judge (NOT EXISTS check on
    evaluation_logs). The NOT EXISTS is a belt-and-suspenders guard on
    top of the cursor and the UniqueConstraint — it is NOT the primary
    deduplication mechanism anymore.

    First-run fallback: when cursor == 0 (never run before), an additional
    time bound of EVAL_LOOKBACK_MINUTES is applied so a fresh install
    doesn't attempt to evaluate years of history in one go. Every
    subsequent run uses id > cursor only.

    Returns a list of dicts: [{"id": int, "query": str, "answer": str}, …]
    Does NOT advance the cursor — call advance_cursor() after successfully
    processing the batch (see tasks/evaluate_batch.py).
    """
    cursor = get_cursor()

    if cursor == 0:
        # First run — use time-based bound as well.
        sql = text("""
            SELECT q.id, q.query, q.answer
            FROM   rag_query_logs q
            WHERE  q.created_at >= NOW() - CAST(:lookback AS INTERVAL)
              AND  NOT EXISTS (
                       SELECT 1
                       FROM   evaluation_logs e
                       WHERE  e.query_id = q.id
                         AND  e.model_used = 'ragas'
                   )
              AND  random() < :sample_rate
            ORDER  BY q.id ASC
        """)
        params: dict = {
            "lookback":    f"{EVAL_LOOKBACK_MINUTES} minutes",
            "sample_rate": sample_rate,
        }
    else:
        sql = text("""
            SELECT q.id, q.query, q.answer
            FROM   rag_query_logs q
            WHERE  q.id > :cursor
              AND  NOT EXISTS (
                       SELECT 1
                       FROM   evaluation_logs e
                       WHERE  e.query_id = q.id
                         AND  e.model_used = 'ragas'
                   )
              AND  random() < :sample_rate
            ORDER  BY q.id ASC
        """)
        params = {"cursor": cursor, "sample_rate": sample_rate}

    with _engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [dict(row._mapping) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — duplicate-safe evaluation result persistence
# ─────────────────────────────────────────────────────────────────────────────

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
    Fix 2 — Duplicate Evaluations

    Upserts via INSERT ... ON CONFLICT (query_id, model_used) DO NOTHING,
    backed by the UNIQUE constraint in EvaluationLog.__table_args__.

    If a row for this exact (query_id, model_used) pair already exists —
    because of a retried Celery task, a concurrent worker, or a manual
    re-run — this call is a safe no-op instead of creating a duplicate.

    Returns the new row's UUID on insert, or the existing row's UUID on
    conflict so callers always have a usable id (e.g. for flag_for_moderation)
    regardless of whether this call actually wrote anything.
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
            evaluated_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["query_id", "model_used"],
        )
        result = session.execute(stmt)
        session.commit()

        if result.rowcount == 0:
            # Row already existed — return its id so the caller can still
            # use it for flag_for_moderation etc.
            existing = (
                session.query(EvaluationLog)
                .filter(
                    EvaluationLog.query_id   == query_id,
                    EvaluationLog.model_used == model_used,
                )
                .first()
            )
            return existing.id if existing else None

        return new_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def flag_for_moderation(query_id: int, evaluation_log_id: uuid.UUID) -> bool:
    """
    Inserts a pending moderation_queue row for a low-scoring answer.

    Fix 2 extension — uses INSERT ... ON CONFLICT DO NOTHING (backed by the
    UniqueConstraint on query_id in ModerationQueueItem) so if both judges
    independently trigger a flag for the same query_id, only the first row
    survives and the second is a silent no-op.

    Returns True if a new row was inserted, False if the query was already
    flagged.
    """
    session = SessionLocal()
    try:
        stmt = pg_insert(ModerationQueueItem).values(
            id=uuid.uuid4(),
            query_id=query_id,
            evaluation_log_id=evaluation_log_id,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["query_id"],
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Moderation queue — read + decision
# ─────────────────────────────────────────────────────────────────────────────

def list_pending_moderation_items() -> list[dict]:
    """Returns all pending moderation_queue rows joined with their scores and
    the original query/answer text from rag_query_logs."""
    sql = text("""
        SELECT m.id,
               m.query_id,
               m.status,
               m.created_at,
               e.overall_score,
               e.faithfulness_score,
               e.relevance_score,
               q.query,
               q.answer
        FROM   moderation_queue m
        JOIN   evaluation_logs  e ON e.id = m.evaluation_log_id
        JOIN   rag_query_logs   q ON q.id = m.query_id
        WHERE  m.status = 'pending'
        ORDER  BY m.created_at ASC
    """)
    with _engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row._mapping) for row in rows]


def decide_moderation_item(
    item_id: uuid.UUID,
    decision: str,
    reviewer: str,
    notes: Optional[str] = None,
) -> bool:
    """
    Records a human reviewer's approve/reject decision.
    Returns False if item_id doesn't exist, True on success.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")

    session = SessionLocal()
    try:
        item = (
            session.query(ModerationQueueItem)
            .filter(ModerationQueueItem.id == item_id)
            .first()
        )
        if item is None:
            return False
        item.status         = decision
        item.reviewer       = reviewer
        item.decision_notes = notes
        item.decided_at     = datetime.now(timezone.utc)

        # Log decision to audit_logs
        log = AuditLog(
            id=uuid.uuid4(),
            event_type="moderation_decision",
            actor=reviewer,
            query_id=item.query_id,
            details={"decision": decision, "notes": notes},
            created_at=datetime.now(timezone.utc),
        )
        session.add(log)

        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# Audit logging and Evaluation logs
# ─────────────────────────────────────────────────────────────────────────────

def log_audit_event(
    event_type: str,
    actor: Optional[str] = None,
    query_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    """
    Inserts a new event row in the audit_logs table. Best effort, never blocks execution.
    """
    session = SessionLocal()
    try:
        log = AuditLog(
            id=uuid.uuid4(),
            event_type=event_type,
            actor=actor,
            query_id=query_id,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
        session.add(log)
        session.commit()
    except Exception as exc:
        session.rollback()
        # Non-fatal log
        import logging
        logging.getLogger(__name__).warning("Failed to write to audit_logs: %s", exc)
    finally:
        session.close()


def list_audit_logs(event_type: Optional[str] = None, limit: int = 50) -> list[dict]:
    """
    Returns audit logs filtered optionally by event_type.
    """
    session = SessionLocal()
    try:
        q = session.query(AuditLog)
        if event_type and event_type != "all":
            q = q.filter(AuditLog.event_type == event_type)
        rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(r.id),
                "event_type": r.event_type,
                "actor": r.actor,
                "query_id": r.query_id,
                "details": r.details,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    finally:
        session.close()


def list_evaluation_logs(limit: int = 50) -> list[dict]:
    """
    Returns the recent evaluation logs.
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(EvaluationLog)
            .order_by(EvaluationLog.evaluated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": str(r.id),
                "query_id": r.query_id,
                "model_used": r.model_used,
                "overall_score": r.overall_score,
                "faithfulness_score": r.faithfulness_score,
                "relevance_score": r.relevance_score,
                "completeness_score": r.completeness_score,
                "evaluated_at": r.evaluated_at.isoformat(),
            }
            for r in rows
        ]
    finally:
        session.close()


def get_query_detail(query_id: int) -> Optional[dict]:
    """
    Full detail for one query: the original question/answer (read straight
    from rag_query_logs) merged with every judge evaluation recorded
    against it in evaluation_logs.

    rag_query_logs columns (per generation-service's CREATE TABLE, which is
    the authoritative source — not this module's earlier docstring, which
    predates a few columns being added):
        id, domain_id, user_id, query, answer, llm_route, model,
        citation_chunk_ids (TEXT[]), retrieval_diagnostics (JSONB),
        evaluation_status, cache_hit, correlation_id, created_at

    Powers GET /evaluate/logs/{query_id} — the Quality Dashboard's
    detail drawer (opened by clicking a Query ID row).

    Returns None if no rag_query_logs row exists for this query_id (the
    router turns that into a 404). A query_id with zero evaluations yet
    is NOT an error — it returns normally with evaluations=[].
    """
    sql = text("""
        SELECT id, domain_id, user_id, query, answer, llm_route, model,
               citation_chunk_ids, evaluation_status, cache_hit, created_at
        FROM   rag_query_logs
        WHERE  id = :query_id
    """)
    with _engine.connect() as conn:
        row = conn.execute(sql, {"query_id": query_id}).fetchone()

    if row is None:
        return None

    log_row = dict(row._mapping)

    session = SessionLocal()
    try:
        evals = (
            session.query(EvaluationLog)
            .filter(EvaluationLog.query_id == query_id)
            .order_by(EvaluationLog.evaluated_at.asc())
            .all()
        )
        evaluations = [
            {
                "id": str(e.id),
                "query_id": e.query_id,
                "model_used": e.model_used,
                "overall_score": e.overall_score,
                "faithfulness_score": e.faithfulness_score,
                "relevance_score": e.relevance_score,
                "completeness_score": e.completeness_score,
                "ragas_context_precision": e.ragas_context_precision,
                "ragas_context_recall": e.ragas_context_recall,
                "ragas_context_entity_recall": e.ragas_context_entity_recall,
                "ragas_answer_correctness": e.ragas_answer_correctness,
                "ragas_answer_similarity": e.ragas_answer_similarity,
                "evaluated_at": e.evaluated_at.isoformat(),
            }
            for e in evals
        ]
    finally:
        session.close()

    citation_ids = log_row.get("citation_chunk_ids") or []

    return {
        "query_id": log_row["id"],
        "domain_id": log_row.get("domain_id"),
        "user_id": log_row.get("user_id"),
        "query": log_row["query"],
        "answer": log_row["answer"],
        "llm_route": log_row.get("llm_route"),
        "model": log_row.get("model"),
        "citations_count": len(citation_ids),
        "evaluation_status": log_row.get("evaluation_status"),
        "cache_hit": log_row.get("cache_hit"),
        "created_at": log_row["created_at"].isoformat() if log_row.get("created_at") else None,
        "evaluations": evaluations,
    }


def reset_evaluation_data() -> None:
    """
    Clears all evaluation logs, moderation queue items, audit events, and
    live evaluation caches, and resets the batch processing cursor to 0.
    """
    session = SessionLocal()
    try:
        session.query(ModerationQueueItem).delete()
        session.query(EvaluationLog).delete()
        session.query(LiveEvaluationCache).delete()
        session.query(AuditLog).delete()
        
        # Reset the default cursor
        cursor = session.query(EvalCursor).filter(EvalCursor.name == _CURSOR_NAME).first()
        if cursor:
            cursor.last_query_id = 0
            cursor.updated_at = datetime.now(timezone.utc)
        else:
            cursor = EvalCursor(name=_CURSOR_NAME, last_query_id=0, updated_at=datetime.now(timezone.utc))
            session.add(cursor)
            
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()