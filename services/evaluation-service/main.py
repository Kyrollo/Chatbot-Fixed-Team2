"""
main.py
--------
FastAPI application entry point for evaluation-service.

FIX: The original main.py only mounted the /evaluate router. The moderation
router (routes/moderation.py) was wired internally but never registered with
the app, meaning GET /moderation/queue and POST /moderation/{id}/decide would
return 404 on every call. This version mounts both routers.
"""
import uvicorn
from fastapi import FastAPI

from config import settings
from router import close_router_resources, router
from routes.moderation import router as moderation_router   # FIX: was missing
from metrics import setup_metrics

app = FastAPI(
    title="Evaluation Service",
    description="LLM-as-judge scoring + RAGAS evaluation pipeline + moderation queue.",
    version="2.0.0",
)

# /evaluate  — live per-request scoring (judge.py + router.py)
app.include_router(router)

# /moderation — human review queue (routes/moderation.py)
app.include_router(moderation_router, prefix="/moderation", tags=["moderation"])  # FIX

# /metrics — Prometheus scrape endpoint
setup_metrics(app)


@app.on_event("startup")
async def startup() -> None:
    try:
        from db.queries import ensure_tables_exist
        ensure_tables_exist()
    except Exception as exc:
        import logging
        logging.getLogger("main").error("Failed to bootstrap tables: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_router_resources()


@app.get("/health", tags=["health"])
async def root_health():
    return {"status": "ok", "service": settings.SERVICE_NAME}


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.SERVICE_PORT, reload=False)
