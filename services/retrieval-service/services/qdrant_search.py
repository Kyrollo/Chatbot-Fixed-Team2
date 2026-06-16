import logging
import sys
from functools import lru_cache
from pathlib import Path

from config import settings
from schemas.retrieval import ChunkResult

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from qdrant_client_factory import async_qdrant_client  # noqa: E402

logger = logging.getLogger(__name__)


class QdrantSearchService:
    """Vector search via embedded Qdrant — opens and closes a client per request.

    To support concurrent writes from worker-service in embedded local mode,
    we do not hold a persistent lock on the RocksDB storage folder for the
    service lifetime. The client is created per query and closed immediately
    after.
    """

    def __init__(self) -> None:
        pass

    async def search(
        self,
        domain_id: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[ChunkResult]:

        collection_name = domain_id
        client = async_qdrant_client()
        try:
            collections = await client.get_collections()
            exists = any(c.name == collection_name for c in collections.collections)

            if not exists:
                logger.error("Collection %s does not exist", collection_name)
                return []

            hits = await client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
            )
        except Exception:
            logger.exception("Qdrant search failed for collection %s", collection_name)
            return []
        finally:
            await client.close()

        results: list[ChunkResult] = []

        for hit in hits:
            payload = hit.payload or {}

            results.append(
                ChunkResult(
                    chunk_id=str(payload.get("chunk_id", hit.id)),
                    document_id=payload.get("document_id", ""),
                    filename=payload.get("filename", ""),
                    source_type=payload.get("source_type", "pdf"),
                    chunk_type=payload.get("chunk_type", "text"),
                    chunk_index=payload.get("chunk_index", 0),
                    page=payload.get("page"),
                    text=payload.get("text", ""),
                    score=hit.score,
                    source="vector",
                )
            )

        return results

    async def close(self) -> None:
        pass


@lru_cache(maxsize=1)
def get_qdrant_search_service() -> QdrantSearchService:
    return QdrantSearchService()
