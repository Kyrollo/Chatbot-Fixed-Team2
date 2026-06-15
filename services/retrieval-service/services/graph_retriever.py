"""
GraphRetriever — skeleton for future Graph Retrieval integration.
"""
import logging

from .base_retriever import BaseRetriever

logger = logging.getLogger(__name__)


class GraphRetriever(BaseRetriever):
    async def search(self, query: str, domain_id: str, top_k: int) -> list:
        logger.debug("GraphRetriever.search called (not yet implemented) — returning []")
        return []
