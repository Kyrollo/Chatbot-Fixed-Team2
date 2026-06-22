"""
db/models.py
-------------
SQLAlchemy models for the evaluation pipeline's own tables.

These are SEPARATE from rag_query_logs (which generation-service already
writes to). Evaluation results reference a query log row by ID instead of
modifying it — evaluation happens after the fact, often much later, and
keeping it in its own table means a slow/broken evaluation run can never
corrupt or lock the original query log.

CORRECTED AGAINST THE REAL SCHEMA
-----------------------------------
An earlier draft of this file used UUID for query_id, which is WRONG.
Per the real rag_query_logs ERD in README.md, that table's `id` column is
`bigint` (an auto-incrementing integer), not a UUID. query_id below is
now BigInteger to match — using UUID against a bigint column would have
caused a type error on the very first real insert. evaluation_logs.id and
moderation_queue.id stay as UUID since those are this pipeline's OWN
tables, designed from scratch here — only query_id (which references
rag_query_logs.id) needed correcting.

NEW IN THIS FIX — three problems addressed
---------------------------------------------
1. "rag_query_logs does not store retrieved context / reference answers"
   -> LiveEvaluationCache. The live POST /evaluate endpoint (router.py /
      judge.py) already receives context_chunks (and, going forward,
      optionally a reference answer) from the CALLER on every request —
      that data simply used to be thrown away after scoring. Now it is
      saved here, keyed by a hash of (query, answer), the moment a live
      evaluation happens. The scheduled batch job
      (tasks/evaluate_batch.py) then looks up this table by the same
      hash when it later samples a row from rag_query_logs, recovering
      the context/reference that rag_query_logs itself never stored.
      This is a pragmatic fix that requires NO changes to
      generation-service or rag_query_logs — it only works for rows that
      were actually scored once live (i.e. went through /evaluate at
      answer time). Rows that were never scored live still have no
      context recoverable after the fact; that part of the limitation is
      structural and can only be fully solved by generation-service
      persisting context into rag_query_logs directly.

2. "prevent duplicate evaluation" -> query_id now has a UNIQUE constraint
   combined with model_used (see EvaluationLog.__table_args__) PLUS
   db/queries.py's save path uses an upsert (INSERT ... ON CONFLICT DO
   NOTHING) so the same (query_id, model_used) pair can never be written
   twice, even under concurrent/retried task execution. This is stronger
   than the old "NOT EXISTS" pre-check alone, which has a race window
   between the check and the insert.

3. "track last evaluated record" -> EvalCursor. A single-row table that
   stores the highest rag_query_logs.id the batch job has successfully
   processed up to. Each run reads the cursor, evaluates rows with
   id > cursor, and advances the cursor at the end — replacing the old
   "look back N minutes and hope nothing was missed or double-counted"
   sliding time window with a deterministic, monotonic watermark.
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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def context_cache_key(query: str, answer: str) -> str:
    """
    Deterministic key used to match a live /evaluate call back to the
    rag_query_logs row the batch job later samples for the SAME
    query+answer pair. SHA-256 of the exact query+answer text — if either
    differs by even one character the lookup simply misses (falls back to
    no context, same as today), it never matches the wrong row.
    """
    raw = f"{query.strip()}\u241f{answer.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class EvaluationLog(Base):
    """
    One row per (query, judge run). A single query_id can have more than
    one row here if it's evaluated more than once — e.g. once by the
    custom judge (model_used="custom_judge") and once by RAGAS
    (model_used="ragas") — both are stored, never overwritten, so you can
    compare what each judge said about the same answer.
    """
    __tablename__ = "evaluation_logs"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id    = Column(BigInteger, nullable=False, index=True)
    # ^ references rag_query_logs.id (bigint, NOT uuid — see module
    #   docstring). Not a hard FK constraint on purpose, so this table
    #   works even if rag_query_logs lives in a different logical area
    #   or gets partitioned/archived later.

    model_used        = Column(String, nullable=False)   # "custom_judge" | "ragas"

    # Shared fields — populated by BOTH the custom judge and RAGAS, using
    # whichever of their own metrics maps closest to each one.
    faithfulness_score = Column(Float, nullable=True)
    relevance_score    = Column(Float, nullable=True)
    completeness_score = Column(Float, nullable=True)
    overall_score       = Column(Float, nullable=True)

    # RAGAS-specific metrics — NULL for model_used="custom_judge" rows,
    # since the custom judge doesn't compute these.
    # Group B metrics (context_precision, context_recall,
    # context_entity_recall, answer_correctness, answer_similarity) are
    # also NULL on live production rows with no reference answer — that's
    # expected, not an error; see tasks/ragas_judge.py's module docstring.
    ragas_context_precision    = Column(Float, nullable=True)
    ragas_context_recall        = Column(Float, nullable=True)
    ragas_context_entity_recall = Column(Float, nullable=True)
    ragas_answer_correctness    = Column(Float, nullable=True)
    ragas_answer_similarity      = Column(Float, nullable=True)

    raw_judge_response  = Column(Text, nullable=True)     # full judge output, for debugging

    evaluated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # FIX (duplicate evaluation): the same query can never be scored
        # twice by the same judge. Enforced at the DATABASE level, not
        # just in Python, so it holds even if two Celery workers ever run
        # at once or a task is retried after a partial failure.
        UniqueConstraint("query_id", "model_used", name="uq_evaluation_logs_query_judge"),
    )


class ModerationQueueItem(Base):
    """
    One row per answer flagged for human review. Created by
    tasks/moderation.py whenever an EvaluationLog's overall_score falls
    below the moderation threshold.
    """
    __tablename__ = "moderation_queue"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id          = Column(BigInteger, nullable=False, index=True)  # references rag_query_logs.id (bigint)
    evaluation_log_id = Column(UUID(as_uuid=True), ForeignKey("evaluation_logs.id"), nullable=False)

    status   = Column(String, nullable=False, default="pending")  # pending | approved | rejected
    reviewer = Column(String, nullable=True)    # who made the decision
    decision_notes = Column(Text, nullable=True)

    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at  = Column(DateTime(timezone=True), nullable=True)


class LiveEvaluationCache(Base):
    """
    FIX (retrieved context / reference answers not stored): every time
    the live POST /evaluate endpoint runs (router.py -> judge.py), the
    caller (generation-service) sends context_chunks — and, going
    forward, may optionally send a reference answer — that are normally
    thrown away after scoring. This table saves them, keyed by a hash of
    the exact (query, answer) text (see context_cache_key() above).

    Later, when the scheduled batch job (tasks/evaluate_batch.py) samples
    a row from rag_query_logs — which has NO context column of its own —
    it computes the same hash from that row's query+answer and looks it
    up here. If a live evaluation already happened for that exact
    query+answer, the real retrieved context (and reference, if any) is
    recovered and used for RAGAS faithfulness / Group B metrics instead
    of silently scoring with no context at all.

    This is keyed by content hash, not query_id, on purpose: the live
    /evaluate endpoint is called by generation-service at answer time and
    has no rag_query_logs.id to attach to (EvaluationRequest has no
    query_id field — see schemas.py) — by the time /evaluate runs, the
    log row may not even be committed yet. Matching on the exact
    query+answer text is the only correlation available without changing
    generation-service itself.

    Rows are kept for `ttl_hours` worth of inspection value, then can be
    pruned by prune_old_cache_entries() in db/queries.py — this table is
    a short-lived bridge, not a permanent archive (evaluation_logs is the
    permanent record once the batch job has run).
    """
    __tablename__ = "live_evaluation_cache"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_key       = Column(String(64), nullable=False, unique=True, index=True)  # context_cache_key()

    query           = Column(Text, nullable=False)
    answer          = Column(Text, nullable=False)
    context_chunks  = Column(Text, nullable=True)   # JSON-encoded list[str], as received in context_chunks
    reference       = Column(Text, nullable=True)   # optional ground-truth answer, if the caller supplied one

    consumed        = Column(Boolean, nullable=False, default=False)
    # ^ set True once the batch job successfully matches and uses this
    #   row for a rag_query_logs row, so re-runs don't keep re-reading it
    #   (not that re-reading would corrupt anything — it's just noise).

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EvalCursor(Base):
    """
    FIX (tracking the last evaluated record): a single-row table holding
    the highest rag_query_logs.id the batch job has successfully finished
    processing. Replaces the old approach of re-scanning a sliding
    "last N minutes" time window on every run, which could either miss
    rows (if a run was late by more than the lookback window) or
    needlessly re-scan rows already evaluated (relying solely on the
    evaluation_logs NOT EXISTS check to skip them).

    There is normally exactly one row here, with id="default" — see
    db/queries.py's get_cursor()/advance_cursor(). Using a named singleton
    row (rather than a one-column/one-row table with no key) keeps the
    door open for a second named cursor later (e.g. a separate cursor per
    domain_id) without a schema change.
    """
    __tablename__ = "eval_cursor"

    name              = Column(String(64), primary_key=True, default="default")
    last_query_id      = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                         onupdate=lambda: datetime.now(timezone.utc))
