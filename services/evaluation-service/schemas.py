from pydantic import BaseModel, Field


class EvaluationRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    answer: str = Field(..., min_length=1, max_length=12000)
    context_chunks: list[str] = Field(default_factory=list)


class EvaluationResponse(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    explanation: str
    route_used: str
    model: str
