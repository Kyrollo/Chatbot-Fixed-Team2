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

# Sprint 3 — Task 2.2: Entity Extraction Worker.
# Lives at the worker-service root (not tasks/) alongside ontology.py,
# matching where config.py and hf_env.py already sit — see ner.py's
# module docstring for why GLiNER is loaded as an in-process module
# here instead of a separate microservice.
from ner import extract_entities_for_chunks

# Sprint 3 — Task 2.3: Relation Extraction. Runs after NER — see
# relation_extraction.py's module docstring for why this uses one
# Groq call per CHUNKS_PER_GROUP chunks instead of per-chunk or
# whole-document calls, and why an LLM was chosen over spaCy.
from relation_extraction import extract_relations_for_chunks
from graph_writer import write_to_graph

from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

# Sync URL — prefer SYNC_DATABASE_URL, fall back to DATABASE_URL with asyncpg stripped
_raw_url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
if not _raw_url:
    from urllib.parse import quote
    user = os.getenv("POSTGRES_USER", "postgres")
    password = quote(os.getenv("POSTGRES_PASSWORD", "postgres"), safe="")
    db = os.getenv("POSTGRES_DB", "domain_db")
    pg_port = os.getenv("POSTGRES_PORT", "5432")
    _raw_url = f"postgresql://{user}:{password}@localhost:{pg_port}/{db}"
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

    # Pre-load all models before starting the pipeline
    import time
    
    print("  Pre-loading models...")
    t0 = time.perf_counter()

    print("  [Model 1/2] Embedding model...")
    _ = get_model()  # blocks until loaded
    print(f"  [Model 1/2] Ready ({time.perf_counter()-t0:.1f}s)")

    # NER model (GLiNer) is NOT pre-loaded here — it crashes Windows memory
    # when loaded upfront via PyTorch. It is loaded lazily inside the
    # try/except at step 5, so a crash there is caught and the document
    # still completes indexing via Vector + BM25.
    print("  [Model 2/2] NER model — will load at step 5 (lazy)")
    print(f"  Embedding model ready in {time.perf_counter()-t0:.1f}s")

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

    print(f"\n[1/6] Extracting text from {source_type.upper()} file...")
    pages = extract_text(file_path)
    print(f"  Extracted {len(pages)} pages/segments")

    if not pages:
        raise ValueError(f"No text could be extracted from this {source_type.upper()} file.")

    print("\n[2/6] Chunking text (semantic)...")
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

    print("\n[3/6] Embedding chunks...")
    chunks_with_vectors = embed_chunks(chunks)

    print("\n[4/6] Indexing into Qdrant + PostgreSQL...")
    qdrant_count = index_chunks(chunks_with_vectors)
    pg_count     = index_chunks_postgres(chunks_with_vectors)

    # Step 5 runs AFTER indexing, deliberately. The Vector/BM25 RAG
    # pipeline (steps 1-4) is the system's core function and must
    # already be fully searchable before we touch anything graph-related.
    # If NER throws (model download hiccup, unexpected input, etc.), we
    # log it and continue — a document that's indexed for retrieval but
    # missing graph entities is a degraded experience; a document stuck
    # in "failed" because of a Sprint 3 add-on is a regression.
    print("\n[5/6] Extracting entities (NER) for graph construction...")
    entity_count = 0
    chunks_with_entities = chunks_with_vectors
    try:
        chunks_with_entities = extract_entities_for_chunks(chunks_with_vectors)
        entity_count = sum(len(c.get("entities", [])) for c in chunks_with_entities)
    except Exception as exc:
        print(f"  [WARN] NER extraction failed, continuing without graph data: {exc}")

    # Step 6 depends entirely on step 5's entities — same failure
    # isolation logic applies (see comment above). Skipped automatically
    # if entity_count is 0, since there's nothing to find relations between.
    print("\n[6/7] Extracting relations (LLM) for graph construction...")
    relation_count = 0
    triples = []
    if entity_count > 0:
        try:
            triples = extract_relations_for_chunks(chunks_with_entities)
            relation_count = len(triples)
        except Exception as exc:
            print(f"  [WARN] Relation extraction failed, continuing without graph relations: {exc}")
    else:
        print("  Skipped — no entities were found in step 5")

    # Step 7 writes the extracted entities and relations to Apache AGE.
    # Same failure isolation: failures here are logged and skipped so they
    # do not prevent document ingestion.
    print("\n[7/7] Writing entities and relations to Apache AGE graph...")
    graph_status = "skipped"
    if entity_count > 0:
        try:
            res = write_to_graph(chunks_with_entities, triples, domain_id, document_id)
            graph_status = res.get("status", "error")
            print(f"  Graph write status: {graph_status}")
        except Exception as exc:
            print(f"  [WARN] Writing to Apache AGE failed, continuing: {exc}")
    else:
        print("  Skipped — no entities to write to graph")

    update_document_status(document_id, "done")

    print(f"\nDocument {document_id} processed successfully")
    print(f"  Source type: {source_type}")
    print(f"  Qdrant:      {qdrant_count} chunks indexed")
    print(f"  PostgreSQL:  {pg_count} chunks indexed (BM25)")
    print(f"  Entities:    {entity_count} extracted (NER)")
    print(f"  Relations:   {relation_count} extracted (LLM)")
    print(f"  Graph Write: {graph_status}")
    print(f"{'='*50}\n")

    return {
        "document_id": document_id,
        "pages":       len(pages),
        "chunks":      qdrant_count,
        "entities":    entity_count,
        "relations":   relation_count,
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