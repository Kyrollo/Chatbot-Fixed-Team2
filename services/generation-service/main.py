import logging

import uvicorn
from fastapi import FastAPI

from config import settings
from router import close_router_resources, ensure_query_log_table, router

from service_metrics import metrics_router, instrument_app




logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Generation Service",
    description="RAG answer generation and semantic cache layer.",
    version="1.0.0",
)
app.include_router(router)
instrument_app(app, service_name="generation-service")
app.include_router(metrics_router)

@app.on_event("startup")
async def startup() -> None:
    await ensure_query_log_table()


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_router_resources()


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.SERVICE_PORT, reload=False)
