"""
worker-service/celery_metrics.py
---------------------------------
Adds Prometheus metrics to Celery tasks.

HOW TO WIRE IT UP
-----------------
In your celery app init (e.g., worker_service/celery_app.py):

    from celery_metrics import setup_celery_metrics
    celery = Celery(...)
    setup_celery_metrics(celery)

Then expose them via a tiny HTTP server (runs in the same process):

    from celery_metrics import start_metrics_server
    start_metrics_server(port=9090)   # call this ONCE at startup

Prometheus scrapes http://localhost:9090/metrics for worker metrics.

ENVIRONMENT VARIABLES
---------------------
METRICS_PORT  (default 9090) — port for the metrics HTTP server
"""

import time
import os
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    start_http_server,
)
from celery.signals import (
    task_prerun,
    task_postrun,
    task_failure,
    task_retry,
    worker_ready,
)

# ── Metrics ────────────────────────────────────────────────────────────────────

celery_tasks_total = Counter(
    "celery_tasks_total",
    "Total Celery tasks dispatched",
    ["task_name", "status"],   # status: success | failure | retry
)

celery_task_duration_seconds = Histogram(
    "celery_task_duration_seconds",
    "Celery task execution time",
    ["task_name"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

celery_tasks_in_flight = Gauge(
    "celery_tasks_in_flight",
    "Celery tasks currently executing",
    ["task_name"],
)

celery_worker_up = Gauge(
    "celery_worker_up",
    "1 if the Celery worker process is alive",
)

# Internal dict to track per-task start times  {task_id: (task_name, start_time)}
_task_starts: dict = {}


# ── Signal handlers ────────────────────────────────────────────────────────────

@task_prerun.connect
def on_task_start(task_id, task, *args, **kwargs):
    _task_starts[task_id] = (task.name, time.perf_counter())
    celery_tasks_in_flight.labels(task_name=task.name).inc()


@task_postrun.connect
def on_task_done(task_id, task, retval, state, *args, **kwargs):
    info = _task_starts.pop(task_id, None)
    if info:
        task_name, start = info
        duration = time.perf_counter() - start
        celery_task_duration_seconds.labels(task_name=task_name).observe(duration)
        celery_tasks_in_flight.labels(task_name=task_name).dec()
        status = "success" if state == "SUCCESS" else "failure"
        celery_tasks_total.labels(task_name=task_name, status=status).inc()


@task_failure.connect
def on_task_failure(task_id, exception, task, *args, **kwargs):
    celery_tasks_total.labels(task_name=task.name, status="failure").inc()
    info = _task_starts.pop(task_id, None)
    if info:
        _, start = info
        celery_task_duration_seconds.labels(task_name=task.name).observe(
            time.perf_counter() - start
        )
        celery_tasks_in_flight.labels(task_name=task.name).dec()


@task_retry.connect
def on_task_retry(request, *args, **kwargs):
    celery_tasks_total.labels(task_name=request.task, status="retry").inc()


@worker_ready.connect
def on_worker_ready(*args, **kwargs):
    celery_worker_up.set(1)


# ── Public API ─────────────────────────────────────────────────────────────────

def setup_celery_metrics(celery_app):
    """Connect all Prometheus signal handlers. Call once after Celery() init."""
    # Signals are already connected via decorators above; this function
    # exists as an explicit hook so the import + wiring is obvious in main.
    celery_worker_up.set(0)   # will flip to 1 on worker_ready signal
    return celery_app


def start_metrics_server(port: int | None = None):
    """Start a background HTTP server that serves /metrics for Prometheus."""
    port = port or int(os.getenv("METRICS_PORT", "9090"))
    start_http_server(port)
    print(f"[celery-metrics] Prometheus metrics server started on :{port}/metrics")