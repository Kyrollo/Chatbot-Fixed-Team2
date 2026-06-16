import os
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from celery import Celery
from storage import save_file, insert_document, get_document_status, update_status, update_task_id
from config import settings
from enums import FileTypeEnum, DocumentStatusEnum, QueueEnum, TaskEnum
from dependencies import CurrentUser, check_domain_access

router = APIRouter()


@router.get("/ingest/health", tags=["health"])
async def router_health_check():
    return {"status": "ok", "service": "ingestion-service"}

celery_app = Celery("ingestion", broker=settings.redis_url)
ROOT = Path(__file__).resolve().parents[3]
WORKER_DIR = ROOT / "services" / "worker-service"
SYNC_INGESTION = os.getenv("SYNC_INGESTION", "").lower() in {"1", "true", "yes"}


def _enqueue_processing(document_id: str) -> str | None:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONPATH", str(ROOT / "scripts"))

    if SYNC_INGESTION:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tasks.run_document", document_id],
            cwd=WORKER_DIR,
            env=env,
        )
        return str(proc.pid)

    res = celery_app.send_task(
        TaskEnum.PROCESS_DOCUMENT,
        args=[document_id],
        queue=QueueEnum.INGESTION,
    )
    return res.id


@router.post("/ingest", status_code=202)
async def ingest_document(
    user: CurrentUser,
    file: UploadFile = File(...),
    domain_id: str = Form(...),
):
    """
    Accepts a document upload for a specific domain.

    Supported file types: PDF, DOCX, CSV, PNG, JPG, JPEG.

    Auth flow:
    1. JWT is validated by the get_current_user dependency (mandatory)
    2. Domain-level RBAC is checked via domain-service internal endpoint
       (user must be at least a contributor on this domain)
    3. File is saved and a Celery job is enqueued
    4. Returns 202 with document_id for status polling
    """

    # 1. Validate file type
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {e.value for e in FileTypeEnum}:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # 2. Read and validate file size
    file_bytes = await file.read()
    if len(file_bytes) > settings.max_size_mb * 1024 * 1024:
        raise HTTPException(400, f"File exceeds {settings.max_size_mb} MB limit.")

    # 3. Domain RBAC check — must be at least a contributor
    allowed = await check_domain_access(
        user_id=user["user_id"],
        domain_id=domain_id,
        required_role="contributor",
        is_system_admin=user["is_system_admin"],
    )
    if not allowed:
        raise HTTPException(
            403,
            "You do not have contributor or higher access to this domain.",
        )

    # 4. Save file and create document record
    document_id = str(uuid.uuid4())

    file_path = await save_file(
        file_bytes=file_bytes,
        filename=file.filename,
        document_id=document_id,
    )

    await insert_document(
        domain_id=domain_id,
        user_id=user["user_id"],
        filename=file.filename,
        file_path=file_path,
        document_id=document_id,
    )

    # 5. Enqueue processing job (Celery worker or local subprocess)
    task_id = _enqueue_processing(document_id)
    if task_id:
        await update_task_id(document_id, task_id)

    return {
        "document_id": document_id,
        "status": DocumentStatusEnum.PENDING,
        "message": "Document accepted. Processing has been queued.",
    }


@router.get("/ingest/{document_id}")
async def get_status(document_id: str, user: CurrentUser):
    """
    Poll the processing status of an uploaded document.
    Requires a valid JWT — any authenticated user may check status.
    Returns: pending | processing | done | failed
    """
    doc = await get_document_status(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")

    return {
        "document_id": doc["id"],
        "filename":    doc["filename"],
        "status":      doc["status"],
        "error_msg":   doc.get("error_msg"),
        "created_at":  str(doc["created_at"]),
        "updated_at":  str(doc["updated_at"]),
    }


@router.post("/ingest/{document_id}/cancel")
async def cancel_processing(document_id: str, user: CurrentUser):
    """Cancels an in-progress document processing job."""
    doc = await get_document_status(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")

    allowed = await check_domain_access(
        user_id=user["user_id"],
        domain_id=doc["domain_id"],
        required_role="contributor",
        is_system_admin=user["is_system_admin"],
    )
    if not allowed:
        raise HTTPException(
            403,
            "You do not have contributor or higher access to this domain.",
        )

    if doc["status"] not in ("pending", "processing"):
        raise HTTPException(400, f"Cannot cancel — document is already '{doc['status']}'")

    task_id = doc.get("task_id")
    if task_id:
        if SYNC_INGESTION:
            import signal
            try:
                pid = int(task_id)
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        else:
            try:
                celery_app.control.revoke(task_id, terminate=True, signal='SIGTERM')
            except Exception:
                pass

    await update_status(document_id, "cancelled")
    return {"document_id": document_id, "status": "cancelled"}