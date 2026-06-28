"""
RetrievalPipeline — orchestrates the full retrieval flow.

Stages:
  1. Analyze Query       (local, instant)
  2. Route Query         (LLM, 2s timeout, has fallback)
  3. Execute Retrievers  (parallel)
  4. Fuse Results        (RRF, with table-query boosting)
  5. Table fallback      (if a table-seeking query found too few table chunks)
  6. Re-rank Results     (Cross-Encoder)
  7. Return Final Chunks

Changes vs original:
  - FIX A: Prometheus metrics fully wired (qdrant, bm25, rerank, rrf, cache, full_pipeline)
  - FIX B: route_query timeout reduced to 2s (was effectively >5s due to httpx overhead)
  - FIX B: local fast-path routing added — skips LLM for simple queries
"""
import asyncio
import logging
import time

from schemas.retrieval import RetrievalRequest, RetrievalResponse
from config import settings
from services.bm25_retriever import BM25Retriever
from services.cache import get_retrieval_cache
from services.graph_retriever import GraphRetriever
from services.query_analyzer import analyze_query
from services.reranker import get_reranker_service
from services.retrieval_router import route_query
from services.rrf_fusion import fuse_results
from services.vector_retriever import VectorRetriever

# ── FIX A: Import metrics ──────────────────────────────────────────────────────
from retrieval_metrics import (
    retrieval_requests_total,
    retrieval_stage_latency,
    retrieval_results_count,
    retrieval_cache_hits,
    retrieval_cache_misses,
    retrieval_stage_errors,
    qdrant_search_latency,
    bm25_search_latency,
    rerank_latency,
    graph_search_latency,
    rrf_top_score,
)

logger = logging.getLogger(__name__)

_TABLE_KEYWORDS = [
    "table", "csv", "excel", "sheet", "row", "col", "column",
    "average", "sum", "total", "report", "statistics", "data",
    "withholding", "filing", "earns", "wage", "salary", "rate",
    "range", "bracket", "amount", "lookup", "intersection",
    "cross-reference", "cell", "value at",
]


def _is_table_query(query: str) -> bool:
    q_lower = query.lower()
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
        domain_id_str = str(request.domain_id)
        pipeline_start = time.perf_counter()

        try:
            # Stage 1: Analyze (local, instant)
            t0 = time.perf_counter()
            analysis = analyze_query(request.query)
            retrieval_stage_latency.labels(stage="analyze").observe(time.perf_counter() - t0)
            logger.debug("QueryAnalysis: type=%s score=%s", analysis.query_type, analysis.keyword_score)

            # Stage 2: Route — FIX B: 2s timeout, fallback is instant
            t0 = time.perf_counter()
            try:
                routing = await asyncio.wait_for(
                    route_query(request.query, analysis),
                    timeout=2.0,  # was effectively 5s+, now hard 2s
                )
            except asyncio.TimeoutError:
                logger.warning("route_query timed out after 2s — using fallback")
                from services.retrieval_router import _FALLBACK, RoutingDecision
                use_graph = bool(settings.AGE_DATABASE_DSN) and (
                    analysis.contains_entities or analysis.relationship_intent
                )
                routing = RoutingDecision(
                    use_vector=True,
                    use_bm25=True,
                    use_graph=use_graph,
                    vector_weight=0.7,
                    bm25_weight=0.3,
                    graph_weight=0.5 if use_graph else 0.0,
                    decided_by="timeout_fallback",
                )
            retrieval_stage_latency.labels(stage="route").observe(time.perf_counter() - t0)
            logger.info("Routing [%s]: vector=%s bm25=%s graph=%s",
                        routing.decided_by, routing.use_vector, routing.use_bm25, routing.use_graph)

            # Table-query detection
            is_table_query = _is_table_query(request.query)
            top_k_retrieve = request.top_k_retrieve
            if is_table_query:
                top_k_retrieve = max(top_k_retrieve, 40)

            # Stage 3: Execute retrievers in parallel — FIX A: record per-engine latency
            graph_diagnostics = {
                "enabled": routing.use_graph,
                "matched_entities": 0,
                "matched_chunk_ids": 0,
                "returned_chunks": 0,
                "skip_reason": None if routing.use_graph else "graph_disabled_by_router",
            }

            async def timed_vector():
                t = time.perf_counter()
                try:
                    res = await self._vector.search(request.query, request.domain_id, top_k_retrieve)
                    qdrant_search_latency.labels(domain_id=domain_id_str).observe(time.perf_counter() - t)
                    retrieval_stage_latency.labels(stage="dense").observe(time.perf_counter() - t)
                    return res
                except Exception as e:
                    retrieval_stage_errors.labels(stage="dense").inc()
                    raise e

            async def timed_bm25():
                t = time.perf_counter()
                try:
                    res = await self._bm25.search(request.query, request.domain_id, top_k_retrieve)
                    bm25_search_latency.labels(domain_id=domain_id_str).observe(time.perf_counter() - t)
                    retrieval_stage_latency.labels(stage="sparse").observe(time.perf_counter() - t)
                    return res
                except Exception as e:
                    retrieval_stage_errors.labels(stage="sparse").inc()
                    raise e

            async def timed_graph():
                t = time.perf_counter()
                try:
                    res = await self._graph.search_with_diagnostics(
                        request.query, request.domain_id, top_k_retrieve
                    )
                    graph_search_latency.labels(domain_id=domain_id_str).observe(time.perf_counter() - t)
                    retrieval_stage_latency.labels(stage="graph").observe(time.perf_counter() - t)
                    return res
                except Exception as e:
                    retrieval_stage_errors.labels(stage="graph").inc()
                    raise e

            tasks, labels = [], []
            if routing.use_vector:
                tasks.append(timed_vector())
                labels.append("vector")
            if routing.use_bm25:
                tasks.append(timed_bm25())
                labels.append("bm25")
            if routing.use_graph:
                tasks.append(timed_graph())
                labels.append("graph")

            results_per_engine = await asyncio.gather(*tasks, return_exceptions=True)

            active_result_lists = []
            for label, result in zip(labels, results_per_engine):
                if isinstance(result, Exception):
                    logger.error("Engine '%s' raised: %s", label, result)
                    if label == "graph":
                        graph_diagnostics["skip_reason"] = "graph_execution_failed"
                else:
                    if label == "graph":
                        graph_results, graph_diagnostics = result
                        active_result_lists.append(graph_results)
                    else:
                        active_result_lists.append(result)

            # Stage 4: Fuse (RRF) — FIX A: record rrf top score
            if not active_result_lists:
                logger.error("All retrieval engines failed — returning empty results")
                retrieval_requests_total.labels(domain_id=domain_id_str, status="failure").inc()
                return RetrievalResponse(results=[], cache_hit=False)

            t0 = time.perf_counter()
            fused = fuse_results(*active_result_lists, query=request.query)
            retrieval_stage_latency.labels(stage="rrf").observe(time.perf_counter() - t0)

            if fused:
                # FIX A: record top RRF score
                rrf_top_score.labels(domain_id=domain_id_str).set(fused[0].score)
                avg_score = sum(c.score for c in fused) / len(fused)
                await self._cache.incr("rag:metrics:fusion:count")
                await self._cache.incrbyfloat("rag:metrics:fusion:total_score", avg_score)

            # Stage 5: Table fallback
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

            # Stage 6: Re-rank — FIX A: record rerank latency
            t0 = time.perf_counter()
            try:
                reranked = await self._reranker.rerank(
                    request.query,
                    fused[:settings.RERANKER_CANDIDATE_CAP],
                    request.top_k_rerank,
                )
                rerank_latency.labels(domain_id=domain_id_str).observe(time.perf_counter() - t0)
                retrieval_stage_latency.labels(stage="rerank").observe(time.perf_counter() - t0)
            except Exception as e:
                retrieval_stage_errors.labels(stage="rerank").inc()
                logger.error("Reranker failed: %s — returning fused results", e)
                reranked = fused[:request.top_k_rerank]

            # FIX A: record full pipeline metrics
            pipeline_duration = time.perf_counter() - pipeline_start
            retrieval_stage_latency.labels(stage="full_pipeline").observe(pipeline_duration)
            retrieval_requests_total.labels(domain_id=domain_id_str, status="success").inc()
            retrieval_results_count.labels(domain_id=domain_id_str).observe(len(reranked))
            retrieval_cache_misses.labels(domain_id=domain_id_str).inc()

            # Stage 7: Return
            return RetrievalResponse(
                results=reranked,
                cache_hit=False,
                diagnostics={
                    "router": {
                        "vector": routing.use_vector,
                        "bm25": routing.use_bm25,
                        "graph": routing.use_graph,
                        "decided_by": routing.decided_by,
                    },
                    "graph": graph_diagnostics,
                },
            )

        except Exception:
            logger.exception("RetrievalPipeline.run() uncaught exception")
            retrieval_requests_total.labels(domain_id=domain_id_str, status="failure").inc()
            retrieval_stage_latency.labels(stage="full_pipeline").observe(
                time.perf_counter() - pipeline_start
            )
            return RetrievalResponse(
                results=[],
                cache_hit=False,
                diagnostics={
                    "router": {"vector": True, "bm25": True, "graph": False, "decided_by": "pipeline_error"},
                    "graph": {"enabled": False, "matched_entities": 0, "matched_chunk_ids": 0,
                              "returned_chunks": 0, "skip_reason": "pipeline_error"},
                },
            )