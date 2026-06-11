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
    """Vector search via embedded Qdrant — reuses a single client instance.

    The old code created a new AsyncQdrantClient on every search() call and
    closed it afterwards.  For embedded Qdrant this means opening/closing the
    RocksDB storage per request — very expensive.  Now the client is created
    once and reused for the service lifetime.
    """

    def __init__(self) -> None:
        self._client = async_qdrant_client()

    async def search(
        self,
        domain_id: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[ChunkResult]:

        collection_name = domain_id
        try:
            collections = await self._client.get_collections()
            exists = any(c.name == collection_name for c in collections.collections)

            if not exists:
                logger.error("Collection %s does not exist", collection_name)
                return []

            hits = await self._client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
            )
        except Exception:
            logger.exception("Qdrant search failed for collection %s", collection_name)
            return []

        results: list[ChunkResult] = []

        for hit in hits:
            payload = hit.payload or {}

            results.append(
                ChunkResult(
                    chunk_id=str(payload.get("chunk_id", hit.id)),
                    document_id=payload.get("document_id", ""),
                    page=payload.get("page"),
                    text=payload.get("text", ""),
                    score=hit.score,
                    source="vector",
                )
            )

        return results

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


@lru_cache(maxsize=1)
def get_qdrant_search_service() -> QdrantSearchService:
    return QdrantSearchService()
