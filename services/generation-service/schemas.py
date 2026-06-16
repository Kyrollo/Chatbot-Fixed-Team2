from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    domain_id: str = Field(..., min_length=1)
    top_k_retrieve: int | None = Field(default=None, ge=1, le=50)
    top_k_rerank: int | None = Field(default=None, ge=1, le=20)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=32, le=4096)
    stream: bool = False


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    filename: str = ""
    source_type: str = "pdf"
    chunk_type: str = "text"
    chunk_index: int = 0
    page: int | None = None
    score: float
    text: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    cache_hit: bool = False
    llm_route: str
    model: str
