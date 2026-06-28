"""
router.py
----------
The live POST /evaluate endpoint plus supporting endpoints.

/evaluate/health          — service liveness check
/evaluate/judge-health    — probe the LLM judge for reachability (Phase 7)
/evaluate                 — score a (query, answer, context) triple
/evaluate/logs            — recent evaluation logs
/evaluate/logs/{query_id} — full detail for one query (question, answer,
                             every judge evaluation) — Quality Dashboard
                             detail drawer
"""
import logging
import time

from fastapi import APIRouter, HTTPException, status

from config import settings
from judge import JudgeService, check_judge_health
from metrics import eval_score_gauge, eval_latency
from schemas import EvaluationRequest, EvaluationResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluate", tags=["evaluation"])
_judge = JudgeService()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@router.get("/judge-health")
async def judge_health() -> dict:
    """
    Probes the configured LLM judge (Groq or Ollama) for connectivity.
    Returns:
      { "reachable": bool, "route": "api"|"local", "model": str, "error": str|None }

    Use this to verify the judge is available before triggering evaluations,
    or to diagnose why /evaluate is returning errors (ALLOW_MOCK_JUDGE=False)
    or mock scores (ALLOW_MOCK_JUDGE=True).
    """
    result = await check_judge_health()
    return {
        **result,
        "allow_mock_judge": settings.ALLOW_MOCK_JUDGE,
    }


@router.post("", response_model=EvaluationResponse)
async def evaluate(payload: EvaluationRequest) -> EvaluationResponse:
    start = time.perf_counter()
    try:
        result = await _judge.evaluate(payload)
    except RuntimeError as exc:
        # Judge unavailable and ALLOW_MOCK_JUDGE=False — surface it clearly
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Judge LLM unavailable: {exc}. Check /evaluate/judge-health.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Evaluation failed: {exc}",
        ) from exc
    finally:
        eval_latency.labels(judge="custom_judge").observe(
            time.perf_counter() - start
        )

    # Score is only meaningful on success — outside the finally block.
    eval_score_gauge.labels(judge="custom_judge").set(result.score)

    # Persist context_chunks so evaluate_batch.py can recover them for
    # this (query, answer) pair. Best-effort — never breaks the response.
    try:
        from db.queries import (
            save_live_evaluation_cache,
            ensure_tables_exist,
            log_audit_event,
            save_evaluation_result,
            flag_for_moderation,
            MODERATION_THRESHOLD,
        )

        ensure_tables_exist()
        save_live_evaluation_cache(
            query=payload.query,
            answer=payload.answer,
            context_chunks=payload.context_chunks,
            reference=getattr(payload, "reference", None),
        )

        log_audit_event(
            event_type="live_evaluation",
            actor=None,
            query_id=payload.query_id,
            details={
                "score": result.score,
                "model": result.model,
                "route": result.route_used,
            },
        )

        if payload.query_id:
            # Compute overall score as the average of the available scores,
            # skipping None. Mirrors evaluate_batch.py's _overall_score()
            # (same skip-None-and-average logic, just inlined here instead
            # of a shared helper). Now that JudgeService.evaluate() returns
            # faithfulness=None when context_chunks is empty (instead of a
            # fabricated score against a placeholder string), this average
            # is automatically computed over only the metrics that are
            # actually meaningful for this row — exactly like the batch
            # path already did.
            valid_scores = [v for v in [result.faithfulness, result.score, result.completeness] if v is not None]
            overall = sum(valid_scores) / len(valid_scores) if valid_scores else result.score

            # Save the live evaluation result directly to evaluation_logs
            log_id = save_evaluation_result(
                query_id=payload.query_id,
                model_used="custom_judge",
                faithfulness_score=result.faithfulness,
                relevance_score=result.score,
                completeness_score=result.completeness,
                overall_score=overall,
                raw_judge_response=result.explanation,
            )

            # Flag for moderation if the score is below the threshold
            if log_id and result.score < MODERATION_THRESHOLD:
                flag_for_moderation(
                    query_id=payload.query_id,
                    evaluation_log_id=log_id,
                )
    except Exception as exc:
        logger.warning(
            "Could not cache context, persist evaluation or log audit for evaluation "
            "(live /evaluate response is unaffected): %s",
            exc,
        )

    return result


@router.get("/logs")
async def get_eval_logs():
    """Returns recent evaluation logs."""
    try:
        from db.queries import list_evaluation_logs
        logs = list_evaluation_logs()
        return {"logs": logs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/logs/{query_id}")
async def get_query_detail(query_id: int):
    """
    Full detail for one query — question, answer, and every judge
    evaluation recorded for it. Powers the Quality Dashboard's detail
    drawer (clicking a Query ID row in "Recent Judge Evaluations").
    """
    try:
        from db.queries import get_query_detail as _get_query_detail
        detail = _get_query_detail(query_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"No query log found for query_id={query_id}",
        )
    return detail


@router.post("/reset")
async def reset_eval_data():
    """Clears all logs and moderation data in the evaluation DB."""
    try:
        from db.queries import reset_evaluation_data
        reset_evaluation_data()
        return {"status": "ok", "message": "Evaluation and logs reset successfully"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


async def close_router_resources() -> None:
    await _judge.close()