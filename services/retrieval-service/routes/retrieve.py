import logging

from fastapi import APIRouter, HTTPException, status

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)
router = APIRouter()

_service: RetrievalService | None = None


def _get_service() -> RetrievalService:
    global _service
    if _service is None:
        _service = RetrievalService()
    return _service


@router.post(
    "/retrieve",
    response_model=RetrievalResponse,
    summary="Retrieve relevant chunks for a query",
)
async def retrieve(request: RetrievalRequest) -> RetrievalResponse:
    try:
        return await _get_service().retrieve(request)
    except Exception as exc:
        logger.exception("Retrieval failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed. Please try again.",
        )
