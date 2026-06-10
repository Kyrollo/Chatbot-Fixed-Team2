import asyncio
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import network_bootstrap  # noqa: F401, E402

from config import settings
from schemas.retrieval import ChunkResult


class RerankerService:
    def __init__(self) -> None:
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self._model = CrossEncoder(settings.RERANKER_MODEL)

    async def rerank(self, query: str, candidates: list[ChunkResult], top_k: int) -> list[ChunkResult]:
        if not candidates:
            return []

        pairs = [(query, item.text) for item in candidates]
        scores = await asyncio.to_thread(self._model.predict, pairs)

        reranked = [
            ChunkResult(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                page=item.page,
                text=item.text,
                score=float(score),
                source="reranked",
            )
            for item, score in zip(candidates, scores, strict=False)
        ]
        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked[:top_k]


@lru_cache(maxsize=1)
def get_reranker_service() -> RerankerService:
    return RerankerService()
