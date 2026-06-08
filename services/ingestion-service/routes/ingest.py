import uuid
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException
from celery import Celery
from storage import save_file, insert_document, get_document_status
from config import settings
from enums import FileTypeEnum, DocumentStatusEnum, UserRoleEnum, QueueEnum, TaskEnum

router = APIRouter()

celery_app = Celery("ingestion", broker=settings.redis_url)

# Roles allowed to upload documents
UPLOAD_ROLES = {UserRoleEnum.SYSTEM_ADMIN, UserRoleEnum.DOMAIN_ADMIN, UserRoleEnum.CONTRIBUTOR}


@router.post("/ingest", status_code=202)
async def ingest_document(
    file:         UploadFile = File(...),
    domain_id:    str        = Form(...),
    x_user_id:    str        = Header(..., alias="x-user-id"),
    x_user_roles: str        = Header(..., alias="x-user-roles"),
):
    """
    Accepts a PDF upload for a specific domain.

    Steps:
    1. Validate file extension
    2. Read file bytes and check size
    3. Check user has permission to upload
    4. Save file to disk and insert record in Postgres (status=pending)
    5. Push processing job to Redis queue
    6. Return 202 with document_id for status polling
    """

    # 1. Validate file extension
    if not file.filename.lower().endswith(FileTypeEnum.PDF):
        raise HTTPException(400, "Only PDF files accepted.")

    # 2. Read file bytes and check size
    file_bytes = await file.read()
    if len(file_bytes) > settings.max_size_mb * 1024 * 1024:
        raise HTTPException(400, f"File too large. Max {settings.max_size_mb}MB.")

    # 3. Check user has permission to upload
    roles = {r.strip() for r in x_user_roles.split(",")}
    if not roles.intersection(UPLOAD_ROLES):
        raise HTTPException(403, "You do not have permission to upload documents.")

    # 4. Generate document ID, save file to disk, insert record in Postgres
    document_id = str(uuid.uuid4())

    file_path = await save_file(
        file_bytes=file_bytes,
        filename=file.filename,
        document_id=document_id,
    )

    await insert_document(
        domain_id=domain_id,
        user_id=x_user_id,
        filename=file.filename,
        file_path=file_path,
        document_id=document_id,
    )

    # 5. Push processing job to Redis queue — worker picks it up asynchronously
    celery_app.send_task(
        TaskEnum.PROCESS_DOCUMENT,
        args=[document_id],
        queue=QueueEnum.INGESTION,
    )

    # 6. Return 202 immediately — do not wait for processing
    return {
        "document_id": document_id,
        "status":      DocumentStatusEnum.PENDING,
        "message":     "Document accepted. Processing will begin shortly.",
    }


@router.get("/ingest/{document_id}")
async def get_status(
    document_id: str,
    x_user_id:   str = Header(..., alias="x-user-id"),
):
    """
    Poll the processing status of an uploaded document.
    Returns: pending | processing | done | failed
    """

    # Fetch current processing status from Postgres
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