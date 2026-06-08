import os
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, UpdateStatus
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL    = os.getenv("QDRANT_URL",   "http://localhost:6333")
EMBEDDING_DIM = 768

# Sync SQLAlchemy engine — strip asyncpg driver if present in DATABASE_URL
_raw_url     = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/domain_db")
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://")

_qdrant = QdrantClient(url=QDRANT_URL)
_engine = create_engine(DATABASE_URL)


def _ensure_collection(domain_id: str):
    """
    Creates a Qdrant collection for the domain if it doesn't exist yet.
    One collection per domain — enforces data isolation at the DB level.
    """
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

    Each point stored in Qdrant:
    - id      → stable integer hash of chunk_id
    - vector  → 768-dim embedding
    - payload → metadata for filtering at retrieval time

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

    print(f"  ✓ Indexed {total}/{len(chunks)} chunks into Qdrant collection '{domain_id}'")
    return total


def _chunk_id_to_int(chunk_id: str) -> int:
    """Qdrant point IDs must be unsigned integers — hash the string chunk_id."""
    return abs(hash(chunk_id)) % (2 ** 63)


def update_document_status(document_id: str, status: str, error_msg: str = None):
    """
    Updates document status in Postgres.
    Status flow: pending → processing → done / failed
    """
    with _engine.connect() as conn:
        conn.execute(
            text("""
                UPDATE documents
                SET    status    = :status,
                       error_msg = :error_msg,
                       updated_at = now()
                WHERE  id = :document_id
            """),
            {"status": status, "error_msg": error_msg, "document_id": document_id},
        )
        conn.commit()
    print(f"  ✓ Document {document_id} status → {status}")