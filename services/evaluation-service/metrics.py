"""
metrics.py
-----------
Prometheus metrics for evaluation-service.

Exposes a /metrics endpoint via prometheus_fastapi_instrumentator (the same
library used in other services in this project) plus custom gauges for
evaluation pipeline health.

Mount in main.py AFTER app is created:
    from metrics import setup_metrics
    setup_metrics(app)

MULTIPROCESS MODE (why this is needed)
----------------------------------------
This service runs as THREE separate OS processes: the FastAPI app
(main.py, serving /metrics), the Celery worker (runs evaluate_batch.py),
and Celery beat. prometheus_client keeps every Counter/Gauge/Histogram in
that process's own memory — there is no sharing between processes just
because they import the same metrics.py. Concretely: when
evaluate_batch.py (running inside the Celery worker process) calls
eval_runs_total.inc(), that increment happens in the WORKER's memory.
The /metrics endpoint is served by main.py, a completely different
process — it has no way to see what happened in the worker, so those
counters always read back as 0 from Prometheus's point of view, even
though .inc() really was called.

The fix is prometheus_client's official "multiprocess mode": every
process writes its metric deltas to small files on disk in a shared
directory (PROMETHEUS_MULTIPROC_DIR), and whichever process serves
/metrics reads + merges ALL those files into one combined snapshot. This
file MUST set that env var, and EVERY process that imports this module
(main.py, the Celery worker, Celery beat) must see the same value —
set it in .env, not just in one process's shell, or this silently keeps
not working for the processes that don't have it.

See https://prometheus.github.io/client_python/multiprocess/ for the
full reference this follows.
"""
import os

# MUST run before importing prometheus_client itself — the library reads
# PROMETHEUS_MULTIPROC_DIR at import time to decide whether to switch
# every Counter/Gauge/Histogram constructor into multiprocess-safe mode
# internally. Importing prometheus_client first and setting the env var
# after does NOT work retroactively.
_MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR")
if _MULTIPROC_DIR:
    os.makedirs(_MULTIPROC_DIR, exist_ok=True)

from prometheus_client import Gauge, Counter, Histogram, CollectorRegistry, multiprocess
from prometheus_fastapi_instrumentator import Instrumentator

# ── Original metrics (unchanged) ────────────────────────────────────────────

eval_runs_total = Counter(
    "evaluation_runs_total",
    "Total number of evaluation batch runs completed",
)

eval_rows_evaluated = Counter(
    "evaluation_rows_evaluated_total",
    "Total number of query rows evaluated across all runs",
)

eval_rows_flagged = Counter(
    "evaluation_rows_flagged_total",
    "Total number of query rows flagged for moderation",
)

eval_score_gauge = Gauge(
    "evaluation_latest_overall_score",
    "Overall score of the most recently evaluated row",
    ["judge"],   # label: "custom_judge" or "ragas"
    multiprocess_mode="mostrecent" if _MULTIPROC_DIR else "all",
    # "mostrecent": across processes, show whichever process set this
    # gauge's value last (by timestamp) — the right choice for "latest
    # score", since summing or maxing scores across processes wouldn't
    # mean anything. Only meaningful in multiprocess mode; the parameter
    # is harmless (unused) when PROMETHEUS_MULTIPROC_DIR isn't set.
)

eval_latency = Histogram(
    "evaluation_judge_latency_seconds",
    "Time taken for a single judge call",
    ["judge"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    # Histograms don't take a multiprocess_mode argument — in
    # multiprocess mode their bucket counts are always SUMMED across
    # processes, which is exactly what you want for latency observed
    # from multiple processes (combined distribution).
)

moderation_queue_size = Gauge(
    "moderation_queue_pending_items",
    "Current number of items with status=pending in moderation_queue",
    multiprocess_mode="mostrecent" if _MULTIPROC_DIR else "all",
)

# ── NEW metrics (added for alerting + Grafana dashboard) ────────────────────
# Same multiprocess rules apply — Gauges need multiprocess_mode,
# Counters and Histograms are handled automatically by prometheus_client.

eval_requests_total = Counter(
    "eval_requests_total",
    "Total evaluation requests received, by judge and outcome",
    ["judge", "status"],   # status: "success" | "failure"
    # Counter in multiprocess mode: values are SUMMED across all
    # processes — correct behaviour for a cumulative counter.
)

eval_judge_reachable = Gauge(
    "eval_judge_reachable",
    "1 if the judge LLM responded successfully on the last call, 0 if it failed",
    ["judge"],
    multiprocess_mode="mostrecent" if _MULTIPROC_DIR else "all",
    # "mostrecent" — we want the latest reachability status, not a sum
    # or max across processes.
)

eval_queue_depth = Gauge(
    "eval_queue_depth",
    "Number of evaluations currently waiting in the Celery queue",
    multiprocess_mode="mostrecent" if _MULTIPROC_DIR else "all",
)


# ── HOW TO USE THE NEW METRICS in evaluate_batch.py / router.py ─────────────
#
# import time
# from metrics import (
#     eval_requests_total,
#     eval_judge_reachable,
#     eval_score_gauge,
#     eval_latency,
# )
#
# judge = "custom_judge"   # or "ragas"
# start = time.perf_counter()
# try:
#     score = call_judge(...)
#     eval_score_gauge.labels(judge=judge).set(score)
#     eval_requests_total.labels(judge=judge, status="success").inc()
#     eval_judge_reachable.labels(judge=judge).set(1)
# except Exception:
#     eval_requests_total.labels(judge=judge, status="failure").inc()
#     eval_judge_reachable.labels(judge=judge).set(0)
#     raise
# finally:
#     eval_latency.labels(judge=judge).observe(time.perf_counter() - start)
#
# For queue depth — call this wherever you poll the Celery queue length:
#     from metrics import eval_queue_depth
#     eval_queue_depth.set(celery_inspect().reserved().__len__())
# ────────────────────────────────────────────────────────────────────────────


def setup_metrics(app):
    """
    Call this in main.py after app is created to:
      1. Add /metrics endpoint (scraped by Prometheus)
      2. Auto-instrument all FastAPI routes with request duration histograms

    prometheus_fastapi_instrumentator itself checks for
    PROMETHEUS_MULTIPROC_DIR (it also accepts the legacy lowercase
    prometheus_multiproc_dir spelling) and, when present, automatically
    builds a fresh CollectorRegistry() + MultiProcessCollector(registry)
    per request instead of using the global default registry — see the
    library's own source. No extra wiring needed here beyond having the
    env var set before this module (and prometheus_client) is imported.
    """
    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)