import os
import uuid
import aiofiles
from datetime import datetime
from sqlalchemy import (
    MetaData, Table, Column, String, Text, DateTime, create_engine
)
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------
# Database connection
# ------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://rag:rag@localhost:5432/ragdb"
)

# databases library uses asyncpg under the hood for async queries
database = Database(DATABASE_URL)

metadata = MetaData()

# ------------------------------------------------------------------
# Table definition (mirrors what you created in DBeaver)
# ------------------------------------------------------------------
documents_table = Table(
    "documents",
    metadata,
    Column("id",         String, primary_key=True),
    Column("domain_id",  String, nullable=False),
    Column("user_id",    String, nullable=False),
    Column("filename",   String, nullable=False),
    Column("file_path",  String, nullable=False),
    Column("status",     String, default="pending"),
    Column("error_msg",  Text,   nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow),
)


async def create_tables():
    """
    Creates the documents table if it does not exist yet.
    Safe to call on every startup.
    """
    sync_url = DATABASE_URL.replace("+asyncpg", "")
    engine = create_engine(sync_url.replace("postgresql", "postgresql"))
    metadata.create_all(engine)
    engine.dispose()


# ------------------------------------------------------------------
# File storage
# ------------------------------------------------------------------
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")


async def save_file(file_bytes: bytes, filename: str, document_id: str) -> str:
    """
    Saves the PDF to disk under /data/uploads/<document_id>/<filename>
    Returns the full file path.
    """
    folder = os.path.join(UPLOAD_DIR, document_id)
    os.makedirs(folder, exist_ok=True)

    file_path = os.path.join(folder, filename)
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(file_bytes)

    return file_path


# ------------------------------------------------------------------
# Database operations
# ------------------------------------------------------------------
async def insert_document(
    domain_id: str,
    user_id: str,
    filename: str,
    file_path: str,
) -> str:
    """
    Inserts a new document row with status='pending'.
    Returns the generated document_id (UUID).
    """
    document_id = str(uuid.uuid4())

    query = documents_table.insert().values(
        id=document_id,
        domain_id=domain_id,
        user_id=user_id,
        filename=filename,
        file_path=file_path,
        status="pending",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    await database.execute(query)
    return document_id


async def update_status(document_id: str, status: str, error_msg: str = None):
    """
    Updates the document status.
    Called by the worker when processing starts, finishes, or fails.
    Statuses: pending → processing → done | failed
    """
    query = (
        documents_table.update()
        .where(documents_table.c.id == document_id)
        .values(
            status=status,
            error_msg=error_msg,
            updated_at=datetime.utcnow(),
        )
    )
    await database.execute(query)


async def get_document_status(document_id: str) -> dict | None:
    """
    Returns the current status of a document.
    Used by the GET /ingest/{document_id} polling endpoint.
    """
    query = documents_table.select().where(
        documents_table.c.id == document_id
    )
    row = await database.fetch_one(query)
    return dict(row) if row else None
