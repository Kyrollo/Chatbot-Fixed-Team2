from typing import Optional

from pydantic import BaseModel, Field, model_validator

from config import settings


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    domain_id: str = Field(..., description="Target domain — maps to a Qdrant collection")
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    top_k_retrieve: Optional[int] = Field(default=None, ge=1, le=50)
    top_k_rerank: Optional[int] = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def apply_defaults(self) -> "RetrievalRequest":
        if self.top_k_retrieve is None:
            self.top_k_retrieve = self.top_k or settings.TOP_K_RETRIEVE
        if self.top_k_rerank is None:
            self.top_k_rerank = self.top_k or settings.TOP_K_RERANK
        self.top_k_rerank = min(self.top_k_rerank, self.top_k_retrieve)
        return self


class ChunkResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str = ""
    source_type: str = "pdf"
    chunk_type: str = "text"
    chunk_index: int = 0
    page: Optional[int] = None
    text: str
    score: float
    source: Optional[str] = None


class RetrievalResponse(BaseModel):
    results: list[ChunkResult]
    cache_hit: bool = False
