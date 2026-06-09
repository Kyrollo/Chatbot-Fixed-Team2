import uuid
from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from celery import Celery
from storage import save_file, insert_document, get_document_status
from config import settings
from enums import FileTypeEnum, DocumentStatusEnum, QueueEnum, TaskEnum
from dependencies import CurrentUser, check_domain_access

router = APIRouter()

celery_app = Celery("ingestion", broker=settings.redis_url)


@router.post("/ingest", status_code=202)
async def ingest_document(
    file: UploadFile = File(...),
    domain_id: str = Form(...),
    user: CurrentUser = None,
):
    """
    Accepts a PDF upload for a specific domain.

    Auth flow:
    1. JWT is validated by the get_current_user dependency
    2. Domain-level RBAC is checked via domain-service internal endpoint
       (user must be at least a contributor on this domain)
    3. File is saved and a Celery job is enqueued
    4. Returns 202 with document_id for status polling
    """

    # 1. Validate file type
    if not file.filename.lower().endswith(FileTypeEnum.PDF):
        raise HTTPException(400, "Only PDF files are accepted.")

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

    # 5. Enqueue processing job — worker picks it up asynchronously
    celery_app.send_task(
        TaskEnum.PROCESS_DOCUMENT,
        args=[document_id],
        queue=QueueEnum.INGESTION,
    )

    return {
        "document_id": document_id,
        "status": DocumentStatusEnum.PENDING,
        "message": "Document accepted. Processing has been queued.",
    }


@router.get("/ingest/{document_id}")
async def get_status(document_id: str, user: CurrentUser = None):
    """
    Poll the processing status of an uploaded document.
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