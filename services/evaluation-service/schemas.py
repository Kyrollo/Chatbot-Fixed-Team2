"""
schemas.py
-----------
Pydantic models for evaluation-service.

THIS FILE WAS BROKEN BEFORE THIS FIX
--------------------------------------
The version of this file you had only contained the NEW moderation-
pipeline models (EvaluationLogOut, ModerationItemOut, etc.) — the
ORIGINAL EvaluationRequest/EvaluationResponse models that judge.py and
router.py both import were missing entirely. That meant the service
could not even start: main.py -> router.py -> `from schemas import
EvaluationRequest, EvaluationResponse` would raise ImportError on
startup.

EvaluationRequest/EvaluationResponse below are reconstructed exactly to
match how judge.py and router.py actually use them (request.query,
request.answer, request.context_chunks, and EvaluationResponse(score=,
explanation=, route_used=, model=)) — so this is not a guess at unrelated
fields, it's built directly from real usage in your other files.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# ORIGINAL — the /evaluate endpoint (judge.py, router.py)
# ----------------------------------------------------------------------

class EvaluationRequest(BaseModel):
    """Request body for POST /evaluate."""
    query: str
    answer: str
    context_chunks: list[str] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    """Response body for POST /evaluate."""
    score: float
    explanation: str
    route_used: str   # "api" (Groq) or "local" (Ollama)
    model: str


# ----------------------------------------------------------------------
# NEW — evaluation pipeline results (tasks/evaluate_batch.py,
# tasks/ragas_judge.py) — what one judge run produced for one query
# ----------------------------------------------------------------------

class EvaluationLogOut(BaseModel):
    """Response shape for a single evaluation_logs row."""
    id: uuid.UUID
    query_id: int   # rag_query_logs.id is bigint, not UUID — see db/models.py
    model_used: str   # "custom_judge" | "ragas"

    faithfulness_score: Optional[float] = None
    relevance_score: Optional[float] = None
    completeness_score: Optional[float] = None
    overall_score: Optional[float] = None

    # RAGAS-only fields — always None for model_used="custom_judge" rows.
    # Also None on rows with no context (faithfulness) or no reference
    # answer (the other 5) — see tasks/ragas_judge.py's module docstring.
    ragas_context_precision: Optional[float] = None
    ragas_context_recall: Optional[float] = None
    ragas_context_entity_recall: Optional[float] = None
    ragas_answer_correctness: Optional[float] = None
    ragas_answer_similarity: Optional[float] = None

    evaluated_at: datetime

    class Config:
        from_attributes = True  # lets this build directly from a SQLAlchemy EvaluationLog row


# ----------------------------------------------------------------------
# NEW — moderation queue (routes/moderation.py) — human review
# ----------------------------------------------------------------------

class ModerationItemOut(BaseModel):
    """Response shape for one pending/decided moderation_queue row,
    joined with its score and the original query/answer — this is what
    GET /moderation/queue returns, one of these per item."""
    id: uuid.UUID
    query_id: int
    status: str   # "pending" | "approved" | "rejected"
    created_at: datetime

    overall_score: Optional[float] = None
    faithfulness_score: Optional[float] = None
    relevance_score: Optional[float] = None

    query: str
    answer: str

    class Config:
        from_attributes = True


class ModerationDecision(BaseModel):
    """Request body for POST /moderation/{item_id}/decide."""
    decision: str            # "approved" or "rejected"
    reviewer: str             # who is making this decision
    notes: Optional[str] = None


class ModerationDecisionOut(BaseModel):
    """Response body after a decision is recorded."""
    item_id: uuid.UUID
    status: str


class ModerationQueueResponse(BaseModel):
    """Response shape for GET /moderation/queue."""
    count: int
    items: list[ModerationItemOut]