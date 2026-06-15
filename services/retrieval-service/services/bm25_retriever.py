"""
BM25Retriever — wraps the existing BM25SearchService.
"""
import logging

from .base_retriever import BaseRetriever
from .bm25_search import get_bm25_search_service

logger = logging.getLogger(__name__)


class BM25Retriever(BaseRetriever):
    def __init__(self) -> None:
        self._bm25 = get_bm25_search_service()

    async def search(self, query: str, domain_id: str, top_k: int) -> list:
        try:
            return await self._bm25.search(
                domain_id=domain_id,
                query=query,
                top_k=top_k,
            )
        except Exception:
            logger.exception("BM25Retriever failed")
            return []