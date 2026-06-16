import logging

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.cache import get_retrieval_cache
from services.retrieval_pipeline import RetrievalPipeline

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self) -> None:
        self._cache = get_retrieval_cache()
        self._pipeline = RetrievalPipeline()

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        try:
            # Check cache first
            cached = await self._cache.get(
                domain_id=request.domain_id,
                query=request.query,
                top_k_retrieve=request.top_k_retrieve,
                top_k_rerank=request.top_k_rerank,
            )
            if cached is not None:
                return cached

            # Run the full orchestrated pipeline (analyze -> route -> search
            # -> table-query boosting/fallback -> fuse -> rerank). All of
            # that logic lives in RetrievalPipeline, which owns the actual
            # retriever instances (_vector, _bm25, _reranker).
            response = await self._pipeline.run(request)

            # Cache the result
            await self._cache.set(
                domain_id=request.domain_id,
                query=request.query,
                top_k_retrieve=request.top_k_retrieve,
                top_k_rerank=request.top_k_rerank,
                response=response,
            )
            return response

        except Exception as exc:
            logger.exception("Retrieval pipeline failed: %s", exc)
            return RetrievalResponse(results=[], cache_hit=False)