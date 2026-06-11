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
    """Cross-encoder reranker — lazy-loads the model on first use.

    The CrossEncoder (and therefore PyTorch) is NOT loaded at import time
    or __init__ time.  It loads on the first rerank() call, so:
      - Service startup is instant (no 15-second model load blocking)
      - PyTorch DLLs (~3 GB) are loaded only when actually needed
      - If no reranking is ever needed, no memory is consumed
    """

    def __init__(self) -> None:
        self._model = None  # loaded lazily
        self._load_failed = False

    def _ensure_model(self) -> bool:
        """Load the CrossEncoder model if not already loaded. Returns True on success."""
        if self._model is not None:
            return True
        if self._load_failed:
            return False

        model_name = settings.RERANKER_MODEL
        logger.info("Loading reranker model: %s", model_name)
        t0 = time.perf_counter()

        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415

            self._model = CrossEncoder(model_name)
            elapsed = time.perf_counter() - t0
            logger.info("Reranker model ready in %.1fs", elapsed)
            return True
        except Exception:
            self._load_failed = True
            logger.exception(
                "Failed to load reranker model '%s'. "
                "Reranking will be skipped — results returned by fusion score only. "
                "Ensure 'sentencepiece' is installed and the model cache is complete. "
                "Run: pip install sentencepiece && python -c "
                "\"from sentence_transformers import CrossEncoder; "
                "CrossEncoder('%s')\"",
                model_name,
                model_name,
            )
            return False

    async def rerank(self, query: str, candidates: list[ChunkResult], top_k: int) -> list[ChunkResult]:
        if not candidates:
            return []

        # If model isn't available, return candidates sorted by existing score
        if not self._ensure_model():
            logger.warning("Reranker unavailable — returning candidates by fusion score")
            sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
            return sorted_candidates[:top_k]

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
