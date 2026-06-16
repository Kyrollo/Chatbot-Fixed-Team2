"""
RetrievalPipeline — orchestrates the full retrieval flow.

Stages:
  1. Analyze Query       (local, instant)
  2. Route Query         (LLM, 5s timeout, has fallback)
  3. Execute Retrievers  (parallel)
  4. Fuse Results        (RRF, with table-query boosting)
  5. Table fallback      (if a table-seeking query found too few table chunks)
  6. Re-rank Results     (Cross-Encoder)
  7. Return Final Chunks
"""
import asyncio
import logging

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from services.bm25_retriever import BM25Retriever
from services.cache import get_retrieval_cache
from services.graph_retriever import GraphRetriever
from services.query_analyzer import analyze_query
from services.reranker import get_reranker_service
from services.retrieval_router import route_query
from services.rrf_fusion import fuse_results
from services.vector_retriever import VectorRetriever

logger = logging.getLogger(__name__)

# Keywords that signal the user is asking about tabular data (CSV/Excel/etc).
# Used to (a) widen candidate retrieval depth and (b) verify table chunks
# actually made it into the fused results, with a fallback search if not.
_TABLE_KEYWORDS = [
    "table", "csv", "excel", "sheet", "row", "col", "column",
    "average", "sum", "total", "report", "statistics", "data",
    "withholding", "filing", "earns", "wage", "salary", "rate",
    "range", "bracket", "amount", "lookup", "intersection",
    "cross-reference", "cell", "value at",
]


def _is_table_query(query: str) -> bool:
    q_lower = query.lower()
    # Also detect currency patterns like $200,000
    if "$" in q_lower:
        return True
    return any(kw in q_lower for kw in _TABLE_KEYWORDS)


def _is_table_chunk(chunk) -> bool:
    return ("[TABLE_NL]" in chunk.text or "[TABLE_MD]" in chunk.text or
            "[TABLE]" in chunk.text or
            chunk.source_type in ("csv", "xls", "xlsx") or
            getattr(chunk, 'chunk_type', 'text') in ("table_nl", "table_md"))


class RetrievalPipeline:
    def __init__(self) -> None:
        self._vector = VectorRetriever()
        self._bm25 = BM25Retriever()
        self._graph = GraphRetriever()
        self._reranker = get_reranker_service()
        self._cache = get_retrieval_cache()

    async def run(self, request: RetrievalRequest) -> RetrievalResponse:
        try:
            # Stage 1: Analyze
            analysis = analyze_query(request.query)
            logger.debug("QueryAnalysis: type=%s score=%s", analysis.query_type, analysis.keyword_score)

            # Stage 2: Route
            routing = await route_query(request.query, analysis)
            logger.info("Routing [%s]: vector=%s bm25=%s graph=%s",
                        routing.decided_by, routing.use_vector, routing.use_bm25, routing.use_graph)

            # Table-query detection: dynamically widen candidate depth so
            # table chunks (often sparse / clustered) have a better chance
            # of surviving into the fused top-k.
            is_table_query = _is_table_query(request.query)
            top_k_retrieve = request.top_k_retrieve
            if is_table_query:
                top_k_retrieve = max(top_k_retrieve, 40)

            # Stage 3: Execute retrievers in parallel
            tasks, labels = [], []

            async def timed_search(label, search_coro):
                import time
                start_t = time.perf_counter()
                res = await search_coro
                dur = (time.perf_counter() - start_t) * 1000.0
                await self._cache.incr(f"rag:metrics:{label}:count")
                await self._cache.incrbyfloat(f"rag:metrics:{label}:total_ms", dur)
                return res

            if routing.use_vector:
                tasks.append(timed_search("vector", self._vector.search(request.query, request.domain_id, top_k_retrieve)))
                labels.append("vector")
            if routing.use_bm25:
                tasks.append(timed_search("bm25", self._bm25.search(request.query, request.domain_id, top_k_retrieve)))
                labels.append("bm25")
            if routing.use_graph:
                tasks.append(timed_search("graph", self._graph.search(request.query, request.domain_id, top_k_retrieve)))
                labels.append("graph")

            results_per_engine = await asyncio.gather(*tasks, return_exceptions=True)

            active_result_lists = []
            for label, result in zip(labels, results_per_engine):
                if isinstance(result, Exception):
                    logger.error("Engine '%s' raised: %s", label, result)
                else:
                    active_result_lists.append(result)

            # Stage 4: Fuse (RRF, with table-query boosting applied inside fuse_results)
            if not active_result_lists:
                logger.error("All retrieval engines failed — returning empty results")
                return RetrievalResponse(results=[], cache_hit=False)

            fused = fuse_results(*active_result_lists, query=request.query)
            if fused:
                avg_score = sum(c.score for c in fused) / len(fused)
                await self._cache.incr("rag:metrics:fusion:count")
                await self._cache.incrbyfloat("rag:metrics:fusion:total_score", avg_score)

            # Stage 5: Table fallback — verify table-seeking queries actually
            # retrieved table-shaped chunks; if not, run a targeted BM25
            # search for the [TABLE] marker and merge in anything new.
            if is_table_query:
                table_chunks = [c for c in fused if _is_table_chunk(c)]
                if len(table_chunks) < 2 and routing.use_bm25:
                    logger.info(
                        "Fewer than 2 table chunks retrieved for table query — "
                        "executing structured fallback search"
                    )
                    fallback_bm25 = await self._bm25.search(
                        request.query + " [TABLE_NL]",
                        request.domain_id,
                        5,
                    )
                    existing_ids = {c.chunk_id for c in fused}
                    new_chunks = [c for c in fallback_bm25 if c.chunk_id not in existing_ids]

                    if new_chunks:
                        bm25_list = next(
                            (r for label, r in zip(labels, active_result_lists) if label == "bm25"),
                            [],
                        )
                        vector_list = next(
                            (r for label, r in zip(labels, active_result_lists) if label == "vector"),
                            [],
                        )
                        fused = fuse_results(
                            vector_list,
                            bm25_list + new_chunks,
                            query=request.query,
                        )

            # Stage 6: Re-rank
            reranked = await self._reranker.rerank(
                request.query,
                fused[:top_k_retrieve],
                request.top_k_rerank,
            )

            # Stage 7: Return
            return RetrievalResponse(results=reranked, cache_hit=False)

        except Exception:
            logger.exception("RetrievalPipeline.run() uncaught exception")
            return RetrievalResponse(results=[], cache_hit=False)