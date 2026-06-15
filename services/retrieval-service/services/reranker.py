import asyncio
import logging
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import network_bootstrap  # noqa: F401, E402

from config import settings
from schemas.retrieval import ChunkResult

logger = logging.getLogger(__name__)


class RerankerService:
    """Cross-encoder reranker — lazy-loads the model on first use."""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False

        model_name = settings.RERANKER_MODEL
        logger.info("Loading reranker model: %s", model_name)
        t0 = time.perf_counter()

        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(model_name)
            elapsed = time.perf_counter() - t0
            logger.info("Reranker model ready in %.1fs", elapsed)
            return True
        except Exception:
            self._load_failed = True
            logger.exception(
                "Failed to load reranker model '%s'. "
                "Reranking will be skipped — results returned by fusion score only.",
                model_name,
            )
            return False

    async def rerank(self, query: str, candidates: list[ChunkResult], top_k: int) -> list[ChunkResult]:
        if not candidates:
            return []

        if not self._ensure_model():
            logger.warning("Reranker unavailable — returning candidates by fusion score")
            return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]

        logger.info(
            "Reranker input: %d candidates for query=%r",
            len(candidates),
            query[:60],
        )

        # Log top candidates before reranking
        for i, c in enumerate(candidates[:5]):
            logger.info(
                "  [before] rank=%d chunk=%s page=%s rrf_score=%.5f text_preview=%r",
                i + 1,
                c.chunk_id[:8],
                c.page,
                c.score,
                c.text[:80],
            )

        t0 = time.perf_counter()
        pairs = [(query, item.text) for item in candidates]
        scores = await asyncio.to_thread(self._model.predict, pairs)
        elapsed = time.perf_counter() - t0

        reranked = [
            ChunkResult(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                filename=item.filename,
                source_type=item.source_type,
                chunk_index=item.chunk_index,
                page=item.page,
                text=item.text,
                score=float(score),
                source="reranked",
            )
            for item, score in zip(candidates, scores, strict=False)
        ]
        reranked.sort(key=lambda item: item.score, reverse=True)
        final = reranked[:top_k]

        logger.info(
            "Reranker done in %.2fs — %d candidates → top %d results:",
            elapsed,
            len(candidates),
            len(final),
        )
        # Log top results after reranking
        for i, c in enumerate(final):
            logger.info(
                "  [after]  rank=%d chunk=%s page=%s rerank_score=%.4f text_preview=%r",
                i + 1,
                c.chunk_id[:8],
                c.page,
                c.score,
                c.text[:80],
            )

        return final


@lru_cache(maxsize=1)
def get_reranker_service() -> RerankerService:
    return RerankerService()