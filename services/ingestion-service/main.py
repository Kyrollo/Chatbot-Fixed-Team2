from fastapi import FastAPI
from contextlib import asynccontextmanager
from storage import create_tables
from routes.ingest import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    print("Database tables ready")
    yield
    print("Shutting down")


app = FastAPI(
    title="Ingestion Service",
    description="Accepts PDF uploads, saves them, and enqueues processing jobs.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ingestion-service"}
