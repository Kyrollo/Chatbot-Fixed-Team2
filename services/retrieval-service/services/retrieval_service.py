import logging

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.embedding import get_embedding_service
from services.qdrant_search import get_qdrant_search_service

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self) -> None:
        self._embedder = get_embedding_service()
        self._qdrant = get_qdrant_search_service()

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:

        try:
            # embedding
            query_vector = self._embedder.embed_query(request.query)

            # search
            results = await self._qdrant.search(
                domain_id=request.domain_id,
                query_vector=query_vector,
                top_k=request.top_k,
            )

            return RetrievalResponse(results=results)

        except Exception as exc:
            logger.exception("Retrieval pipeline failed: %s", exc)
            return RetrievalResponse(results=[])