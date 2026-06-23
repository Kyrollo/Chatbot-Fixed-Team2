import logging

from fastapi import APIRouter, HTTPException, status

from config import settings
from dependencies import CurrentUser, check_domain_access
from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.embedding import get_embedding_service
from services.reranker import get_reranker_service
from services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)
router = APIRouter()

import asyncio

_service_lock = asyncio.Lock()
_service: RetrievalService | None = None


async def _get_service() -> RetrievalService:
    """Lazy singleton — retries construction on subsequent requests if it failed before.

    Uses an asyncio.Lock to ensure only one request initializes the service at a time,
    preventing concurrent model loading.
    """
    global _service
    if _service is None:
        async with _service_lock:
            if _service is None:
                try:
                    logger.info("Initializing RetrievalService (loading model)...")
                    _service = RetrievalService()
                    logger.info("RetrievalService initialized successfully.")
                except Exception:
                    logger.exception("Failed to initialise RetrievalService — will retry on next request")
                    raise
    return _service


@router.post(
    "/retrieve",
    response_model=RetrievalResponse,
    summary="Retrieve relevant chunks for a query",
)
async def retrieve(request: RetrievalRequest, user: CurrentUser) -> RetrievalResponse:
    """
    Hybrid retrieval endpoint — requires authentication and domain access.

    Sprint 2: Added RBAC filtering. The user must have at least 'reader'
    role on the target domain, or be a system_admin.
    """
    # Domain-level RBAC check — user must have at least reader access
    allowed = await check_domain_access(
        user_id=user["user_id"],
        domain_id=request.domain_id,
        required_role="reader",
        is_system_admin=user.get("is_system_admin", False),
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have access to this domain",
        )

    try:
        svc = await _get_service()
        return await svc.retrieve(request)
    except Exception as exc:
        logger.exception("Retrieval failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed. Please try again.",
        )


@router.post("/warmup", summary="Warm local retrieval models")
async def warmup() -> dict:
    embedding_loaded = False
    reranker_loaded = False
    if settings.WARMUP_EMBEDDING:
        await get_embedding_service().warmup()
        embedding_loaded = True
    if settings.WARMUP_RERANKER:
        reranker_loaded = await get_reranker_service().warmup()
    return {
        "status": "ok",
        "embedding_loaded": embedding_loaded,
        "reranker_loaded": reranker_loaded,
    }
