import logging

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.bm25_search import get_bm25_search_service
from services.cache import get_retrieval_cache
from services.embedding import get_embedding_service
from services.qdrant_search import get_qdrant_search_service
from services.reranker import get_reranker_service
from services.rrf_fusion import fuse_results

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self) -> None:
        self._embedder = get_embedding_service()
        self._qdrant = get_qdrant_search_service()
        self._bm25 = get_bm25_search_service()
        self._reranker = get_reranker_service()
        self._cache = get_retrieval_cache()

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        try:
            cached = await self._cache.get(
                domain_id=request.domain_id,
                query=request.query,
                top_k_retrieve=request.top_k_retrieve,
                top_k_rerank=request.top_k_rerank,
            )
            if cached is not None:
                return cached

            query_vector = self._embedder.embed_query(request.query)

            # Run vector + BM25 search in parallel for speed
            vector_results = await self._qdrant.search(
                domain_id=request.domain_id,
                query_vector=query_vector,
                top_k=request.top_k_retrieve,
            )
            bm25_results = await self._bm25.search(
                domain_id=request.domain_id,
                query=request.query,
                top_k=request.top_k_retrieve,
            )

            fused_results = fuse_results(vector_results, bm25_results)

            # Reranker gracefully degrades — returns fusion-scored results
            # if the model isn't available (see reranker.py)
            reranked_results = await self._reranker.rerank(
                request.query,
                fused_results[: request.top_k_retrieve],
                request.top_k_rerank,
            )

            response = RetrievalResponse(results=reranked_results, cache_hit=False)
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
