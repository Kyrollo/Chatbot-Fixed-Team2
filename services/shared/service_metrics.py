"""
shared/services_metrics.py
-----------------
Drop this file into: domain-service, ingestion-service,
retrieval-service, generation-service.

DO NOT use in evaluation-service — it already uses
prometheus_fastapi_instrumentator which handles /metrics itself.

Usage in main.py:
    from metrics import metrics_router, instrument_app
    instrument_app(app, service_name="generation-service")
    app.include_router(metrics_router)
"""

import time
from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    REGISTRY,
    generate_latest,
)

# ── /metrics router ────────────────────────────────────────────────────────────

metrics_router = APIRouter(tags=["observability"])


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


# ── Guard: register once, reuse if already registered ─────────────────────────
# prometheus_client raises ValueError if you register two metrics with the
# same name. This happens on hot-reload or if two services share a process.

def _get_or_create(metric_class, name, documentation, labelnames=(), **kwargs):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_class(name, documentation, labelnames, **kwargs)


# ── Standard metrics (shared names, differentiated by `service` label) ────────

def _make_service_metrics():
    req_counter = _get_or_create(
        Counter,
        "http_requests_total",
        "Total HTTP requests",
        ["service", "method", "endpoint", "status_code"],
    )
    req_latency = _get_or_create(
        Histogram,
        "http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["service", "method", "endpoint"],
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    )
    in_flight = _get_or_create(
        Gauge,
        "http_requests_in_flight",
        "HTTP requests currently being processed",
        ["service"],
    )
    return req_counter, req_latency, in_flight


# ── Middleware ─────────────────────────────────────────────────────────────────

def instrument_app(app, service_name: str):
    """
    Add Prometheus middleware to a FastAPI app.
    Call AFTER app = FastAPI(...) and BEFORE app.include_router(...).
    """
    req_counter, req_latency, in_flight = _make_service_metrics()

    @app.middleware("http")
    async def prometheus_middleware(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        in_flight.labels(service=service_name).inc()
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start
            status = response.status_code if response else 500
            req_counter.labels(
                service=service_name,
                method=request.method,
                endpoint=request.url.path,
                status_code=str(status),
            ).inc()
            req_latency.labels(
                service=service_name,
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration)
            in_flight.labels(service=service_name).dec()