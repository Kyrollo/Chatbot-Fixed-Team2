"""
routes/moderation.py
-----------------------
Small FastAPI router for the human review side of the moderation queue.

Mount this in evaluation-service's main.py alongside the existing
/evaluate router:

    from routes.moderation import router as moderation_router
    app.include_router(moderation_router, prefix="/moderation")

This is intentionally separate from the existing router.py so the
original /evaluate endpoint is never touched by this change.

Request/response models live in schemas.py (see schemas_additions.py for
the exact models to merge in) — not defined inline here, so all of
evaluation-service's Pydantic models stay in one place.
"""
import uuid

from fastapi import APIRouter, HTTPException

from schemas import (
    ModerationDecision,
    ModerationDecisionOut,
    ModerationQueueResponse,
)
from db.queries import list_pending_moderation_items, decide_moderation_item

router = APIRouter()


@router.get("/queue", response_model=ModerationQueueResponse)
async def get_moderation_queue():
    """
    Returns all pending moderation items, each with its score and the
    original query/answer, so a reviewer's UI has everything it needs in
    one call.
    """
    items = list_pending_moderation_items()
    return {"count": len(items), "items": items}


@router.post("/{item_id}/decide", response_model=ModerationDecisionOut)
async def submit_decision(item_id: uuid.UUID, body: ModerationDecision):
    """
    Records a human reviewer's approve/reject decision. This is the
    write side of the audit trail — every decision made here is
    permanently recorded with who made it and when.
    """
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")

    success = decide_moderation_item(
        item_id=item_id,
        decision=body.decision,
        reviewer=body.reviewer,
        notes=body.notes,
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"Moderation item {item_id} not found")

    return {"item_id": item_id, "status": body.decision}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "moderation"}
