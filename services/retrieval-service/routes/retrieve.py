import logging

from fastapi import APIRouter, HTTPException, status

from dependencies import CurrentUser, check_domain_access
from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)
router = APIRouter()

_service: RetrievalService | None = None


def _get_service() -> RetrievalService:
    """Lazy singleton — retries construction on subsequent requests if it failed before.

    The old code would leave _service as None forever if __init__ threw,
    permanently poisoning the endpoint.  Now we retry each time so transient
    issues (model still downloading, Qdrant locked, etc.) can self-heal.
    """
    global _service
    if _service is None:
        try:
            _service = RetrievalService()
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
        svc = _get_service()
        return await svc.retrieve(request)
    except Exception as exc:
        logger.exception("Retrieval failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed. Please try again.",
        )
