"""
router.py
----------
The live POST /evaluate endpoint.

FIX (retrieved context not stored): after a successful evaluation, the
request's context_chunks (and reference, if provided) are now saved via
db.queries.save_live_evaluation_cache() — keyed by a hash of
(query, answer) — so the scheduled batch job (tasks/evaluate_batch.py)
can recover them later for the SAME query+answer pair when it samples
that row from rag_query_logs. See db/models.py's LiveEvaluationCache
docstring for the full reasoning.

This save is wrapped in its own try/except and never affects the live
response returned to the caller (generation-service) — if the DB write
fails for any reason, /evaluate still returns the score normally, it
just means this particular call's context won't be recoverable later by
the batch job (same as before this fix).

FIX (metrics never recorded): metrics.py defines eval_score_gauge
("evaluation_latest_overall_score") and eval_latency
("evaluation_judge_latency_seconds"), but nothing in the codebase ever
called .set()/.observe() on them — they showed up in /metrics with HELP/
TYPE lines but no series, because a Gauge/Histogram with zero
observations emits no samples at all. This endpoint is the natural place
to record both: it's the single call site that times the judge and gets
its score back. Recorded with judge="custom_judge" to match the label
RAGAS's own scoring path (tasks/ragas_judge.py / evaluate_batch.py) uses
for its judge="ragas" series, so both judges land on the same two
metrics with a different label value rather than needing separate
metric names.
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
        # Recorded in finally so latency is captured even if the call
        # above raised — a slow failing call is exactly the case you
        # want visible in evaluation_judge_latency_seconds, not hidden
        # by an early return.
        eval_latency.labels(judge="custom_judge").observe(time.perf_counter() - start)

    # Score is only meaningful on success, so this stays outside the
    # finally block — recorded right after a successful result, same
    # pattern the batch job follows for evaluation_logs.overall_score.
    eval_score_gauge.labels(judge="custom_judge").set(result.score)

    # FIX: persist context_chunks so the batch job can recover it later
    # for this same (query, answer) pair. Best-effort — never breaks the
    # response already computed above.
    try:
        from db.queries import save_live_evaluation_cache, ensure_tables_exist

        ensure_tables_exist()
        save_live_evaluation_cache(
            query=payload.query,
            answer=payload.answer,
            context_chunks=payload.context_chunks,
        )
    except Exception as exc:
        logger.warning(
            "Could not cache context for batch re-evaluation (live /evaluate "
            "response is unaffected): %s", exc,
        )

    return result


async def close_router_resources() -> None:
    await _judge.close()