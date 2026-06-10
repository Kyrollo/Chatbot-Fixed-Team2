import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import network_bootstrap  # noqa: F401, E402

import logging

import uvicorn
from fastapi import FastAPI

from config import settings
from routes.retrieve import router as retrieve_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Retrieval Service",
    description="Semantic vector search over domain-isolated Qdrant collections.",
    version="1.0.0",
)

app.include_router(retrieve_router, prefix="/api/v1", tags=["Retrieval"])


@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@app.on_event("shutdown")
async def _shutdown() -> None:
    from services.bm25_search import get_bm25_search_service
    from services.cache import get_retrieval_cache
    from services.qdrant_search import get_qdrant_search_service

    await get_retrieval_cache().close()
    await get_bm25_search_service().close()
    await get_qdrant_search_service().close()
    logger.info("%s shut down.", settings.SERVICE_NAME)


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.SERVICE_PORT, reload=False)
