"""
routes/moderation.py
-----------------------
Small FastAPI router for the human review side of the moderation queue.
"""
import uuid

from fastapi import APIRouter, HTTPException

from schemas import (
    ModerationDecision,
    ModerationDecisionOut,
    ModerationQueueResponse,
)
from db.queries import list_pending_moderation_items, decide_moderation_item, SessionLocal
from metrics import moderation_queue_size  # FIX: added import

router = APIRouter()


@router.get("/queue", response_model=ModerationQueueResponse)
async def get_moderation_queue():
    """
    Returns all pending moderation items, each with its score and the
    original query/answer, so a reviewer's UI has everything it needs in
    one call.
    """
    items = list_pending_moderation_items()
    moderation_queue_size.set(len(items))  # FIX: update gauge on every call
    return {"count": len(items), "items": items}


@router.post("/{item_id}/decide", response_model=ModerationDecisionOut)
async def submit_decision(item_id: uuid.UUID, body: ModerationDecision):
    """
    Records a human reviewer's approve/reject decision.
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


@router.get("/audit")
async def get_audit_logs(event_type: str = None, limit: int = 100) -> dict:
    """
    Returns audit log entries. Falls back to evaluation_logs as an audit trail
    if a dedicated audit_logs table doesn't exist.
    """
    try:
        from db.models import EvaluationLog as EvalLogModel
        session = SessionLocal()
        try:
            rows = (
                session.query(EvalLogModel)
                .order_by(EvalLogModel.evaluated_at.desc())
                .limit(limit)
                .all()
            )
            logs = [
                {
                    "id": str(r.id),
                    "event_type": "evaluation",
                    "actor": r.model_used,
                    "query_id": r.query_id,
                    "details": {
                        "overall_score": r.overall_score,
                        "model_used": r.model_used,
                    },
                    "created_at": r.evaluated_at.isoformat() if r.evaluated_at else None,
                }
                for r in rows
            ]
        finally:
            session.close()
        return {"logs": logs}
    except Exception as exc:
        return {"logs": []}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "moderation"}