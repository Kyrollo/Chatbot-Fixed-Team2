import uvicorn
from fastapi import FastAPI

from config import settings
from router import close_router_resources, router

app = FastAPI(
    title="Evaluation Service",
    description="LLM-as-judge scoring stub for generated RAG answers.",
    version="1.0.0",
)
app.include_router(router)


@app.on_event("shutdown")
async def shutdown() -> None:
    await close_router_resources()


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.SERVICE_PORT, reload=False)
