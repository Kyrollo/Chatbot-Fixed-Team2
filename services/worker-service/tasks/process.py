from worker import celery_app

# Unified extraction backend: PaddleOCR + Surya ensemble (Tesseract removed).
# Routing: .docx → python-docx | .csv → pandas
#          .pdf  → PyMuPDF (native text) or OCR pipeline (scanned pages)
#          .png/.jpg/.jpeg → OCR pipeline
# See tasks/extract.py for full routing logic.
from tasks.extract import extract_text

from tasks.chunk   import chunk_pages
from tasks.embed   import embed_chunks, get_model
from tasks.index   import index_chunks, index_chunks_postgres, update_document_status

from sqlalchemy import create_engine, text
import os

# Sync URL — prefer SYNC_DATABASE_URL, fall back to DATABASE_URL with asyncpg stripped
_raw_url     = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/domain_db")
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


def process_document_sync(document_id: str) -> dict:
    """
    Main processing pipeline — called by Celery or local subprocess mode.
    Pipeline: extract → chunk → embed → index (Qdrant + PostgreSQL)

    Routing by file extension (handled inside tasks/extract.py):
      .pdf              → PyMuPDF (native text) or PaddleOCR+Surya (scanned pages)
      .docx             → python-docx, segmented by headings / char count
      .csv              → pandas, batched in groups of 10 rows
      .png/.jpg/.jpeg   → PaddleOCR + Surya OCR pipeline
    """
    print(f"\n{'='*50}")
    print(f"Processing document: {document_id}")
    print(f"{'='*50}")

    update_document_status(document_id, "processing")

    doc = _get_document(document_id)
    file_path = doc["file_path"]
    domain_id = doc["domain_id"]
    filename  = doc["filename"]

    # Derive source_type from file extension
    ext = os.path.splitext(file_path)[1].lower()
    source_type = ext.lstrip(".")          # "pdf", "docx", "csv", "png" …

    print(f"  File:        {file_path}")
    print(f"  Domain:      {domain_id}")
    print(f"  Source type: {source_type}")

    print(f"\n[1/4] Extracting text from {source_type.upper()} file...")
    pages = extract_text(file_path)
    print(f"  Extracted {len(pages)} pages/segments")

    if not pages:
        raise ValueError(f"No text could be extracted from this {source_type.upper()} file.")

    print("\n[2/4] Chunking text (semantic)...")
    chunks = chunk_pages(
        pages=pages,
        document_id=document_id,
        domain_id=domain_id,
        model=get_model(),
        source_type=source_type,
        filename=filename,
    )

    if not chunks:
        raise ValueError("No chunks were produced from this document.")

    print("\n[3/4] Embedding chunks...")
    chunks_with_vectors = embed_chunks(chunks)

    print("\n[4/4] Indexing into Qdrant + PostgreSQL...")
    qdrant_count = index_chunks(chunks_with_vectors)
    pg_count     = index_chunks_postgres(chunks_with_vectors)

    update_document_status(document_id, "done")

    print(f"\nDocument {document_id} processed successfully")
    print(f"  Source type: {source_type}")
    print(f"  Qdrant:      {qdrant_count} chunks indexed")
    print(f"  PostgreSQL:  {pg_count} chunks indexed (BM25)")
    print(f"{'='*50}\n")

    return {
        "document_id": document_id,
        "pages":       len(pages),
        "chunks":      qdrant_count,
        "source_type": source_type,
        "status":      "done",
    }


@celery_app.task(
    name="worker.tasks.process_document",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def process_document(self, document_id: str):
    try:
        return process_document_sync(document_id)
    except Exception as exc:
        print(f"\n[ERROR] Error processing document {document_id}: {exc}")
        update_document_status(document_id, status="failed", error_msg=str(exc))
        raise self.retry(exc=exc) from exc