"""
RetrievalPipeline — orchestrates the full retrieval flow.

Stages:
  1. Analyze Query       (local, instant)
  2. Route Query         (LLM, 5s timeout, has fallback)
  3. Execute Retrievers  (parallel)
  4. Fuse Results        (RRF)
  5. Re-rank Results     (Cross-Encoder)
  6. Return Final Chunks
"""
import asyncio
import logging

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.bm25_retriever import BM25Retriever
from services.graph_retriever import GraphRetriever
from services.query_analyzer import analyze_query
from services.reranker import get_reranker_service
from services.retrieval_router import route_query
from services.rrf_fusion import fuse_results
from services.vector_retriever import VectorRetriever

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    def __init__(self) -> None:
        self._vector = VectorRetriever()
        self._bm25 = BM25Retriever()
        self._graph = GraphRetriever()
        self._reranker = get_reranker_service()

    async def run(self, request: RetrievalRequest) -> RetrievalResponse:
        try:
            # Stage 1: Analyze
            analysis = analyze_query(request.query)
            logger.debug("QueryAnalysis: type=%s score=%s", analysis.query_type, analysis.keyword_score)

            # Stage 2: Route
            routing = await route_query(request.query, analysis)
            logger.info("Routing [%s]: vector=%s bm25=%s graph=%s",
                        routing.decided_by, routing.use_vector, routing.use_bm25, routing.use_graph)

            # Stage 3: Execute retrievers in parallel
            tasks, labels = [], []
            if routing.use_vector:
                tasks.append(self._vector.search(request.query, request.domain_id, request.top_k_retrieve))
                labels.append("vector")
            if routing.use_bm25:
                tasks.append(self._bm25.search(request.query, request.domain_id, request.top_k_retrieve))
                labels.append("bm25")
            if routing.use_graph:
                tasks.append(self._graph.search(request.query, request.domain_id, request.top_k_retrieve))
                labels.append("graph")

            results_per_engine = await asyncio.gather(*tasks, return_exceptions=True)

            active_result_lists = []
            for label, result in zip(labels, results_per_engine):
                if isinstance(result, Exception):
                    logger.error("Engine '%s' raised: %s", label, result)
                else:
                    active_result_lists.append(result)

            # Stage 4: Fuse
            if not active_result_lists:
                logger.error("All retrieval engines failed — returning empty results")
                return RetrievalResponse(results=[], cache_hit=False)

            fused = fuse_results(*active_result_lists)

            # Stage 5: Re-rank
            reranked = await self._reranker.rerank(
                request.query,
                fused[: request.top_k_retrieve],
                request.top_k_rerank,
            )

            # Stage 6: Return
            return RetrievalResponse(results=reranked, cache_hit=False)

        except Exception:
            logger.exception("RetrievalPipeline.run() uncaught exception")
            return RetrievalResponse(results=[], cache_hit=False)
