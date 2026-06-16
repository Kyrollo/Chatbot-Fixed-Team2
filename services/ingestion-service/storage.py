import uuid
import aiofiles
import os
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import MetaData, Table, Column, String, Text, DateTime, select, insert, update
from config import settings

# ------------------------------------------------------------------
# Async engine — uses asyncpg under the hood
# ------------------------------------------------------------------
engine = create_async_engine(settings.database_url, echo=False)

async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

metadata = MetaData()

documents_table = Table(
    "documents", metadata,
    Column("id",         String,   primary_key=True),
    Column("domain_id",  String,   nullable=False),
    Column("user_id",    String,   nullable=False),
    Column("filename",   String,   nullable=False),
    Column("file_path",  String,   nullable=False),
    Column("status",     String,   default="pending"),
    Column("error_msg",  Text,     nullable=True),
    Column("task_id",    String,   nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow),
)


async def create_tables():
    """Creates documents table if not exists. Safe to call every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


# ------------------------------------------------------------------
# File storage
# ------------------------------------------------------------------
async def save_file(file_bytes: bytes, filename: str, document_id: str) -> str:
    folder = os.path.join(settings.upload_dir, document_id)
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
    document_id: str,
) -> str:
    async with engine.begin() as conn:
        await conn.execute(
            insert(documents_table).values(
                id=document_id,
                domain_id=domain_id,
                user_id=user_id,
                filename=filename,
                file_path=file_path,
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
    return document_id


async def update_status(document_id: str, status: str, error_msg: str = None):
    async with engine.begin() as conn:
        await conn.execute(
            update(documents_table)
            .where(documents_table.c.id == document_id)
            .values(status=status, error_msg=error_msg, updated_at=datetime.utcnow())
        )


async def get_document_status(document_id: str) -> dict | None:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(documents_table).where(documents_table.c.id == document_id)
        )
        row = result.fetchone()
    return dict(row._mapping) if row else None


async def update_task_id(document_id: str, task_id: str):
    """Stores the Celery task_id for cancel support."""
    async with engine.begin() as conn:
        await conn.execute(
            update(documents_table)
            .where(documents_table.c.id == document_id)
            .values(task_id=task_id, updated_at=datetime.utcnow())
        )
