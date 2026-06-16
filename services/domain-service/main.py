from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from database import dispose_engine, init_db
from router import internal_router, router
from monitoring_router import router as monitoring_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables (use Alembic in production)
    await init_db()
    yield
    # Shutdown
    await dispose_engine()


app = FastAPI(
    title="Domain Service",
    description="Multi-User Multi-Domain RAG System - Domain Service (Sprint 1)",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(internal_router)
app.include_router(monitoring_router)


@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": settings.SERVICE_NAME}
