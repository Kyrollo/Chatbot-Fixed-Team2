"""
router.py
----------
The live POST /evaluate endpoint.

FIX 1 — retrieved context persistence
    After a successful evaluation, the request's context_chunks (and
    optionally a reference answer) are saved via
    db.queries.save_live_evaluation_cache() — keyed by SHA-256(query+answer)
    — so evaluate_batch.py can recover them later when it samples that row
    from rag_query_logs.

    This save is wrapped in its own try/except and NEVER affects the live
    response returned to the caller. If the DB write fails for any reason,
    /evaluate still returns its score normally — it just means this call's
    context won't be recoverable by the batch job (same as before this fix).

FIX — metrics now recorded
    eval_score_gauge and eval_latency were defined in metrics.py but never
    observed anywhere. This is the natural place to record both: it is the
    single site that times the judge call and receives its score.
    Recorded with judge="custom_judge" so both judges' series land on the
    same two Prometheus metrics with different label values.
"""
import logging
import time

from fastapi import APIRouter, HTTPException, status

from config import settings
from judge import JudgeService
from metrics import eval_score_gauge, eval_latency
from schemas import EvaluationRequest, EvaluationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluate", tags=["evaluation"])
_judge = JudgeService()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@router.post("", response_model=EvaluationResponse)
async def evaluate(payload: EvaluationRequest) -> EvaluationResponse:
    start = time.perf_counter()
    try:
        result = await _judge.evaluate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Evaluation failed: {exc}",
        ) from exc
    finally:
        # Recorded in finally so latency is captured even on failure —
        # a slow failing call is exactly what you want visible in the
        # histogram, not hidden by an early return.
        eval_latency.labels(judge="custom_judge").observe(
            time.perf_counter() - start
        )

    # Score is only meaningful on success — outside the finally block.
    eval_score_gauge.labels(judge="custom_judge").set(result.score)

    # FIX 1: persist context_chunks so evaluate_batch.py can recover
    # them for this (query, answer) pair. Best-effort — never breaks
    # the response already computed above.
    try:
        from db.queries import save_live_evaluation_cache, ensure_tables_exist

        ensure_tables_exist()
        save_live_evaluation_cache(
            query=payload.query,
            answer=payload.answer,
            context_chunks=payload.context_chunks,
            reference=getattr(payload, "reference", None),
        )
    except Exception as exc:
        logger.warning(
            "Could not cache context for batch re-evaluation "
            "(live /evaluate response is unaffected): %s",
            exc,
        )

    return result


async def close_router_resources() -> None:
    await _judge.close()
