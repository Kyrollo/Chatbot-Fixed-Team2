from worker import celery_app
from tasks.extract import extract_text_from_pdf
from tasks.chunk   import chunk_pages
from tasks.embed   import embed_chunks, get_model
from tasks.index   import index_chunks, index_chunks_postgres, update_document_status

from sqlalchemy import create_engine, text
import os

# Sync URL — prefer SYNC_DATABASE_URL, fall back to DATABASE_URL with asyncpg stripped
_raw_url    = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/domain_db")
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

_engine = create_engine(DATABASE_URL)


def _get_document(document_id: str) -> dict:
    """Reads document metadata from Postgres."""
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM documents WHERE id = :id"),
            {"id": document_id}
        ).fetchone()
    if not row:
        raise ValueError(f"Document {document_id} not found in Postgres")
    return dict(row._mapping)


@celery_app.task(
    name="worker.tasks.process_document",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_document(self, document_id: str):
    """
    Main Celery task — called by the ingestion service.
    Pipeline: extract → chunk → embed → index (Qdrant + PostgreSQL)
    """
    print(f"\n{'='*50}")
    print(f"Processing document: {document_id}")
    print(f"{'='*50}")

    try:
        # ── Mark as processing ────────────────────────────────────────────────
        update_document_status(document_id, "processing")

        # ── Read metadata from Postgres ───────────────────────────────────────
        doc       = _get_document(document_id)
        file_path = doc["file_path"]
        domain_id = doc["domain_id"]

        print(f"  File:   {file_path}")
        print(f"  Domain: {domain_id}")

        # ── Step 1: Extract text ──────────────────────────────────────────────
        print("\n[1/4] Extracting text from PDF...")
        pages = extract_text_from_pdf(file_path)
        print(f"  Extracted {len(pages)} pages")

        if not pages:
            raise ValueError("No text could be extracted from this PDF.")

        # ── Step 2: Semantic chunking ─────────────────────────────────────────
        print("\n[2/4] Chunking text (semantic)...")
        chunks = chunk_pages(
            pages=pages,
            document_id=document_id,
            domain_id=domain_id,
            model=get_model(),
        )

        if not chunks:
            raise ValueError("No chunks were produced from this document.")

        # ── Step 3: Embed ─────────────────────────────────────────────────────
        print("\n[3/4] Embedding chunks...")
        chunks_with_vectors = embed_chunks(chunks)

        # ── Step 4a: Index into Qdrant (vector search) ────────────────────────
        print("\n[4/4] Indexing into Qdrant + PostgreSQL...")
        qdrant_count = index_chunks(chunks_with_vectors)

        # ── Step 4b: Index into PostgreSQL (BM25 / full-text search) ─────────
        pg_count = index_chunks_postgres(chunks_with_vectors)

        # ── Mark as done ──────────────────────────────────────────────────────
        update_document_status(document_id, "done")

        print(f"\n✓ Document {document_id} processed successfully")
        print(f"  Qdrant:     {qdrant_count} chunks indexed")
        print(f"  PostgreSQL: {pg_count} chunks indexed (BM25)")
        print(f"{'='*50}\n")

        return {
            "document_id": document_id,
            "pages":       len(pages),
            "chunks":      qdrant_count,
            "status":      "done",
        }

    except Exception as exc:
        print(f"\n✗ Error processing document {document_id}: {exc}")
        update_document_status(document_id, status="failed", error_msg=str(exc))
        raise self.retry(exc=exc)