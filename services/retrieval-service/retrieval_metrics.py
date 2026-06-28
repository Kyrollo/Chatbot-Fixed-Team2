"""
retrieval-service/retrieval_metrics.py
---------------------------------------
Custom Prometheus metrics for the hybrid retrieval pipeline.
Tracks each stage of the 6-step pipeline separately so you can
pinpoint exactly where latency or failures come from.

USAGE in retrieval-service/router.py (or wherever retrieval runs):

    from retrieval_metrics import (
        retrieval_requests_total,
        retrieval_stage_latency,
        retrieval_results_count,
        retrieval_cache_hits,
        retrieval_stage_errors,
        qdrant_search_latency,
        bm25_search_latency,
        rerank_latency,
        graph_search_latency,
    )

    # Example wrapping the full pipeline:
    import time

    start = time.perf_counter()
    try:
        results = await run_retrieval_pipeline(query, domain_id)
        retrieval_requests_total.labels(domain_id=str(domain_id), status="success").inc()
        retrieval_results_count.labels(domain_id=str(domain_id)).observe(len(results))
    except Exception:
        retrieval_requests_total.labels(domain_id=str(domain_id), status="failure").inc()
        raise
    finally:
        retrieval_stage_latency.labels(stage="full_pipeline").observe(time.perf_counter() - start)
"""

from prometheus_client import Counter, Histogram, Gauge

# ── Full pipeline ──────────────────────────────────────────────────────────────

retrieval_requests_total = Counter(
    "retrieval_requests_total",
    "Total retrieval requests",
    ["domain_id", "status"],        # status: success | failure
)

retrieval_stage_latency = Histogram(
    "retrieval_stage_latency_seconds",
    "Latency of each retrieval pipeline stage",
    ["stage"],                      # stage: embed | dense | sparse | rrf | rerank | full_pipeline
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

retrieval_results_count = Histogram(
    "retrieval_results_count",
    "Number of chunks returned per retrieval request",
    ["domain_id"],
    buckets=[1, 3, 5, 10, 20, 50],
)

retrieval_stage_errors = Counter(
    "retrieval_stage_errors_total",
    "Errors per retrieval stage",
    ["stage"],                      # stage: embed | dense | sparse | rerank | graph
)

# ── Cache ──────────────────────────────────────────────────────────────────────

retrieval_cache_hits = Counter(
    "retrieval_cache_hits_total",
    "Retrieval results served from Redis cache",
    ["domain_id"],
)

retrieval_cache_misses = Counter(
    "retrieval_cache_misses_total",
    "Retrieval requests that bypassed cache",
    ["domain_id"],
)

# ── Individual stage latencies ─────────────────────────────────────────────────

qdrant_search_latency = Histogram(
    "qdrant_search_latency_seconds",
    "Qdrant dense vector search latency",
    ["domain_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2],
)

bm25_search_latency = Histogram(
    "bm25_search_latency_seconds",
    "PostgreSQL BM25 sparse search latency",
    ["domain_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1],
)

rerank_latency = Histogram(
    "rerank_latency_seconds",
    "Cross-encoder reranking latency",
    ["domain_id"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

graph_search_latency = Histogram(
    "graph_search_latency_seconds",
    "Apache AGE graph traversal latency (Sprint 3)",
    ["domain_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2],
)

# ── RRF fusion score tracking ──────────────────────────────────────────────────

rrf_top_score = Gauge(
    "rrf_top_score",
    "RRF fusion score of the top-ranked chunk in the last retrieval",
    ["domain_id"],
)