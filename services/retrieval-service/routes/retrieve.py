import logging

from fastapi import APIRouter, HTTPException, status

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)
router = APIRouter()

_retrieval_service = RetrievalService()


@router.post(
    "/retrieve",
    response_model=RetrievalResponse,
    summary="Retrieve relevant chunks for a query",
)
async def retrieve(request: RetrievalRequest) -> RetrievalResponse:
    try:
        return await _retrieval_service.retrieve(request)
    except Exception as exc:
        logger.exception("Retrieval failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed. Please try again.",
        )
