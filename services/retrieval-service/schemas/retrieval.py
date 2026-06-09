from pydantic import BaseModel, Field
from typing import Optional


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    domain_id: str = Field(..., description="Target domain — maps to a Qdrant collection")
    top_k: int = Field(default=5, ge=1, le=20)


class ChunkResult(BaseModel):
    chunk_id: str
    document_id: str
    page: Optional[int] = None
    text: str
    score: float


class RetrievalResponse(BaseModel):
    results: list[ChunkResult]
