"""
VectorRetriever — wraps the existing QdrantSearchService.
"""
import logging

from .base_retriever import BaseRetriever
from .embedding import get_embedding_service
from .qdrant_search import get_qdrant_search_service

logger = logging.getLogger(__name__)


class VectorRetriever(BaseRetriever):
    def __init__(self) -> None:
        self._embedder = get_embedding_service()
        self._qdrant = get_qdrant_search_service()

    async def search(self, query: str, domain_id: str, top_k: int) -> list:
        try:
            query_vector = self._embedder.embed_query(query)
            return await self._qdrant.search(
                domain_id=domain_id,
                query_vector=query_vector,
                top_k=top_k,
            )
        except Exception:
            logger.exception("VectorRetriever failed")
            return []
