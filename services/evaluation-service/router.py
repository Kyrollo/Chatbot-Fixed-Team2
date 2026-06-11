from fastapi import APIRouter, HTTPException, status

from config import settings
from judge import JudgeService
from schemas import EvaluationRequest, EvaluationResponse

router = APIRouter(prefix="/evaluate", tags=["evaluation"])
_judge = JudgeService()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@router.post("", response_model=EvaluationResponse)
async def evaluate(payload: EvaluationRequest) -> EvaluationResponse:
    try:
        return await _judge.evaluate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Evaluation failed: {exc}",
        ) from exc


async def close_router_resources() -> None:
    await _judge.close()
