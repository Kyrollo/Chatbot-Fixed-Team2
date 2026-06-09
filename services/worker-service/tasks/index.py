"""
Indexing step — writes embedded chunks to:
  1. Qdrant  — vector search (dense retrieval)
  2. PostgreSQL document_chunks table — BM25 / full-text search

Both use the same domain_id as the namespace key.
"""
import os
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, UpdateStatus
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL    = os.getenv("QDRANT_URL",   "http://localhost:6333")
EMBEDDING_DIM = 768

# Sync SQLAlchemy engine — Celery workers are synchronous
_raw_url     = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/domain_db")
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

_qdrant = QdrantClient(url=QDRANT_URL)
_engine = create_engine(DATABASE_URL)

# Flag so we only run CREATE TABLE once per worker process lifetime
_chunk_table_ready = False


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL document_chunks table bootstrap
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_chunk_table():
    """
    Creates document_chunks table if it doesn't exist.
    Called once per worker process on first indexing operation.
    The retrieval-service also runs this on startup — both are idempotent.
    """
    global _chunk_table_ready
    if _chunk_table_ready:
        return

    with _engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id           TEXT PRIMARY KEY,
                document_id  TEXT NOT NULL,
                domain_id    TEXT NOT NULL,
                page_num     INTEGER,
                chunk_index  INTEGER,
                text         TEXT NOT NULL,
                search_vec   TSVECTOR,
                created_at   TIMESTAMPTZ DEFAULT now()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_chunks_domain "
            "ON document_chunks(domain_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_chunks_doc "
            "ON document_chunks(document_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_chunks_fts "
            "ON document_chunks USING GIN(search_vec)"
        ))
        conn.commit()

    _chunk_table_ready = True
    print("  ✓ document_chunks table ready")


# ──────────────────────────────────────────────────────────────────────────────
# Qdrant vector indexing
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_collection(domain_id: str):
    """Creates a Qdrant collection for the domain if it doesn't exist yet."""
    collections = [c.name for c in _qdrant.get_collections().collections]
    if domain_id not in collections:
        _qdrant.create_collection(
            collection_name=domain_id,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"  ✓ Created Qdrant collection: {domain_id}")
    else:
        print(f"  ✓ Qdrant collection exists: {domain_id}")


def index_chunks(chunks: list[dict]) -> int:
    """
    Upserts all embedded chunks into the Qdrant collection for their domain.
    Returns the number of chunks successfully indexed.
    """
    if not chunks:
        return 0

    domain_id = chunks[0]["domain_id"]
    _ensure_collection(domain_id)

    points = [
        PointStruct(
            id=_chunk_id_to_int(chunk["chunk_id"]),
            vector=chunk["embedding"],
            payload={
                "chunk_id":    chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "domain_id":   chunk["domain_id"],
                "page":        chunk["page"],
                "chunk_index": chunk["chunk_index"],
                "text":        chunk["text"],
            },
        )
        for chunk in chunks
    ]

    batch_size = 100
    total = 0
    for i in range(0, len(points), batch_size):
        batch  = points[i : i + batch_size]
        result = _qdrant.upsert(collection_name=domain_id, points=batch)
        if result.status == UpdateStatus.COMPLETED:
            total += len(batch)

    print(f"  ✓ Qdrant: indexed {total}/{len(chunks)} chunks into '{domain_id}'")
    return total


def _chunk_id_to_int(chunk_id: str) -> int:
    """Qdrant point IDs must be unsigned integers — hash the string chunk_id."""
    return abs(hash(chunk_id)) % (2 ** 63)


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL full-text indexing (for BM25 retrieval)
# ──────────────────────────────────────────────────────────────────────────────

def index_chunks_postgres(chunks: list[dict]) -> int:
    """
    Inserts chunk text into document_chunks table with a precomputed tsvector.
    Uses ON CONFLICT DO UPDATE so re-processing is safe.
    Returns the number of chunks written.
    """
    if not chunks:
        return 0

    _ensure_chunk_table()

    with _engine.connect() as conn:
        for chunk in chunks:
            conn.execute(
                text("""
                    INSERT INTO document_chunks
                        (id, document_id, domain_id, page_num, chunk_index, text, search_vec)
                    VALUES
                        (:id, :document_id, :domain_id, :page_num, :chunk_index, :text,
                         to_tsvector('simple', :text))
                    ON CONFLICT (id) DO UPDATE SET
                        text       = EXCLUDED.text,
                        search_vec = to_tsvector('simple', EXCLUDED.text),
                        page_num   = EXCLUDED.page_num,
                        chunk_index = EXCLUDED.chunk_index
                """),
                {
                    "id":          chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "domain_id":   chunk["domain_id"],
                    "page_num":    chunk.get("page", 0),
                    "chunk_index": chunk.get("chunk_index", 0),
                    "text":        chunk["text"],
                },
            )
        conn.commit()

    print(f"  ✓ PostgreSQL: indexed {len(chunks)} chunks (BM25 ready)")
    return len(chunks)


# ──────────────────────────────────────────────────────────────────────────────
# Document status update
# ──────────────────────────────────────────────────────────────────────────────

def update_document_status(document_id: str, status: str, error_msg: str = None):
    """Updates document status in Postgres. Flow: pending → processing → done / failed"""
    with _engine.connect() as conn:
        conn.execute(
            text("""
                UPDATE documents
                SET    status     = :status,
                       error_msg  = :error_msg,
                       updated_at = now()
                WHERE  id = :document_id
            """),
            {"status": status, "error_msg": error_msg, "document_id": document_id},
        )
        conn.commit()
    print(f"  ✓ Document {document_id} status → {status}")