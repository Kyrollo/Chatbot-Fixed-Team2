import logging
from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from config import settings
from schemas.retrieval import ChunkResult

logger = logging.getLogger(__name__)


class QdrantSearchService:
    def __init__(self) -> None:
        self._client = AsyncQdrantClient(url=settings.QDRANT_URL)

    async def search(
        self,
        domain_id: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[ChunkResult]:

        collection_name = domain_id

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
        await self._client.close()


@lru_cache(maxsize=1)
def get_qdrant_search_service() -> QdrantSearchService:
    return QdrantSearchService()
