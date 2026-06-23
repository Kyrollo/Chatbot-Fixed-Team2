"""
db/models.py
-------------
SQLAlchemy models for the evaluation pipeline's own tables.

These are SEPARATE from rag_query_logs (which generation-service already
writes to). Evaluation results reference a query log row by ID instead of
modifying it — evaluation happens after the fact, often much later, and
keeping it in its own table means a slow/broken evaluation run can never
corrupt or lock the original query log.

SCHEMA — corrected against the real rag_query_logs ERD
--------------------------------------------------------
rag_query_logs.id is `bigint` (auto-increment integer), NOT a UUID.
query_id in EvaluationLog and ModerationQueueItem is BigInteger to match.

THREE PRODUCTION ISSUES FIXED IN THIS FILE
--------------------------------------------
Issue 1 — Missing Retrieved Context
    rag_query_logs has NO context column. Context is only available at
    request time (POST /evaluate). LiveEvaluationCache stores it there
    keyed by SHA-256(query+answer), and evaluate_batch.py looks it up
    by that same hash when processing a sampled row.

Issue 2 — Duplicate Evaluations
    EvaluationLog now has a UniqueConstraint("query_id", "model_used"),
    enforced at the database level. db/queries.save_evaluation_result()
    uses INSERT ... ON CONFLICT DO NOTHING for a race-free upsert.
    flag_for_moderation() also has a unique constraint on query_id to
    prevent double-flagging.

Issue 3 — Evaluation Progress Tracking
    EvalCursor replaces the old "last N minutes" sliding window with a
    deterministic, monotonically-advancing watermark. Each run reads the
    cursor, processes rows with id > cursor, then advances the cursor.
    This means no row is ever re-evaluated (even if the job restarts),
    and no row is ever silently skipped because the lookback window moved
    past it.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    BigInteger,
    Boolean,
    String,
    Float,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def context_cache_key(query: str, answer: str) -> str:
    """
    Deterministic 64-char hex key: SHA-256 of (query + separator + answer).
    Used to correlate a live /evaluate call (which has context) with the
    rag_query_logs row (which has no context column) the batch job later
    samples for the same query+answer pair.

    The separator U+241F (UNIT SEPARATOR) is chosen because it cannot
    appear in normal prose, making accidental collisions between
    (query="A", answer="BC") and (query="AB", answer="C") impossible.
    """
    raw = f"{query.strip()}\u241f{answer.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class EvaluationLog(Base):
    """
    One row per (query_id, judge) evaluation run.

    A single query can have TWO rows here — one for model_used="custom_judge"
    and one for model_used="ragas" — but never two rows for the same
    (query_id, model_used) pair. The UniqueConstraint enforces this at the
    database level, and queries.save_evaluation_result() uses an upsert so
    concurrent/retried task calls are safe no-ops, not duplicate inserts.
    """
    __tablename__ = "evaluation_logs"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id    = Column(BigInteger, nullable=False, index=True)
    # ^ references rag_query_logs.id (bigint). Not a hard FK on purpose:
    #   evaluation_logs may live longer than rag_query_logs rows (if those
    #   are ever archived/partitioned), and this service shouldn't need DDL
    #   access to the generation-service's table to create its own schema.

    model_used  = Column(String, nullable=False)  # "custom_judge" | "ragas"

    # Shared score fields — populated by both judges (mapping their own
    # metric names onto these shared slots).
    faithfulness_score  = Column(Float, nullable=True)
    relevance_score     = Column(Float, nullable=True)
    completeness_score  = Column(Float, nullable=True)
    overall_score       = Column(Float, nullable=True)

    # RAGAS-specific metrics — NULL for model_used="custom_judge" rows.
    # Group B metrics (context_precision … answer_similarity) are also
    # NULL on rows evaluated without a reference answer — that is expected
    # and normal for live production traffic, not an error.
    ragas_context_precision     = Column(Float, nullable=True)
    ragas_context_recall        = Column(Float, nullable=True)
    ragas_context_entity_recall = Column(Float, nullable=True)
    ragas_answer_correctness    = Column(Float, nullable=True)
    ragas_answer_similarity     = Column(Float, nullable=True)

    raw_judge_response = Column(Text, nullable=True)  # full judge output for debugging

    evaluated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        # FIX Issue 2 — Duplicate Evaluations
        # The same query can never be scored twice by the same judge, even
        # under concurrent Celery workers or task retries.  This is
        # enforced at the database level (not just in Python), so it holds
        # regardless of race conditions.
        UniqueConstraint(
            "query_id", "model_used",
            name="uq_evaluation_logs_query_judge",
        ),
        # Composite index for the list_pending_moderation_items JOIN and
        # any analytical queries filtering by model_used.
        Index("ix_evaluation_logs_query_model", "query_id", "model_used"),
    )


class ModerationQueueItem(Base):
    """
    One row per answer flagged for human review.

    A single query_id can appear at most ONCE (unique constraint) — if both
    the custom judge and RAGAS flag the same answer, only the first flag is
    kept; the second is a silent no-op (see queries.flag_for_moderation).
    """
    __tablename__ = "moderation_queue"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id          = Column(BigInteger, nullable=False, index=True)
    evaluation_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_logs.id"),
        nullable=False,
    )

    status         = Column(String, nullable=False, default="pending")
    reviewer       = Column(String, nullable=True)
    decision_notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # FIX Issue 2 — Duplicate moderation entries
        # Prevents the same query_id appearing twice in the queue, even if
        # both judges score it low and both try to flag it.
        UniqueConstraint("query_id", name="uq_moderation_queue_query_id"),
    )


class LiveEvaluationCache(Base):
    """
    FIX Issue 1 — Missing Retrieved Context

    rag_query_logs has no context column. Every time the live POST /evaluate
    endpoint runs, the caller (generation-service) sends context_chunks that
    are scored by the judge and then — before this fix — thrown away.

    This table saves those context_chunks (plus an optional reference answer)
    keyed by SHA-256(query+answer) the moment a live evaluation occurs.
    The scheduled batch job (tasks/evaluate_batch.py) computes the same
    hash when it samples a row from rag_query_logs and looks up the stored
    context here, giving RAGAS real retrieved chunks instead of always
    running with context=None.

    Limitation: this only works for rows that were scored live through
    POST /evaluate. Rows written directly to rag_query_logs without
    calling /evaluate still have no recoverable context — solving that
    completely requires generation-service to persist context into
    rag_query_logs directly, which is outside this service's scope.

    Design choice — keyed by content hash, not query_id:
    The live /evaluate endpoint is called at answer-generation time, when
    the rag_query_logs row may not yet be committed (no query_id available
    to the caller). Matching on the exact query+answer text is the only
    correlation available without changes to generation-service.

    Rows are pruned after LIVE_CACHE_TTL_HOURS (default 7 days) by
    prune_old_cache_entries() — this table is a short-lived bridge, not
    a permanent archive (evaluation_logs is the permanent record).
    """
    __tablename__ = "live_evaluation_cache"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_key   = Column(String(64), nullable=False, unique=True, index=True)

    query           = Column(Text, nullable=False)
    answer          = Column(Text, nullable=False)
    context_chunks  = Column(Text, nullable=True)   # JSON-encoded list[str]
    reference       = Column(Text, nullable=True)   # optional ground-truth answer

    consumed    = Column(Boolean, nullable=False, default=False)
    # ^ True once the batch job uses this row.  Does NOT trigger deletion
    #   (the same query+answer could be sampled again); it is purely an
    #   observability flag so you can see via SQL which entries were used.

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class EvalCursor(Base):
    """
    FIX Issue 3 — Evaluation Progress Tracking

    A single-row table that stores the highest rag_query_logs.id the batch
    job has successfully finished processing.

    Before this fix the batch job re-scanned a "last N minutes" sliding time
    window on every run. That approach had two failure modes:
      - Miss: if a run was delayed by more than the lookback window, rows
        written during the gap would never be evaluated.
      - Re-scan: rows already evaluated would be checked again on every run
        (relying solely on the NOT EXISTS guard to skip them), wasting DB
        resources that grow with table size.

    The cursor replaces both with a single, monotonically-advancing integer
    watermark:
      - get_cursor() → the id already processed up to (0 on first run)
      - advance_cursor(n) → move the watermark forward to n (never backward)
      - fetch_sample_query_ids() → pulls rows with id > cursor

    On the very first run (cursor == 0), a EVAL_LOOKBACK_MINUTES time bound
    is also applied so a fresh install doesn't attempt to evaluate years of
    history in one shot.

    The singleton row uses name="default" as its primary key. Using a named
    key instead of a single-column table keeps the option open for multiple
    named cursors (e.g. one per domain_id) without a schema change.
    """
    __tablename__ = "eval_cursor"

    name           = Column(String(64), primary_key=True, default="default")
    last_query_id  = Column(BigInteger, nullable=False, default=0)
    updated_at     = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
