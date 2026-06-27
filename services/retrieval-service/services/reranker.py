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


def _validate_model_path(model_name: str) -> Path:
    model_path = Path(model_name)
    if not model_path.exists():
        raise FileNotFoundError(
            f"Reranker model path does not exist: {model_name}. "
            "Set RERANKER_MODEL to a downloaded local model directory."
        )
    return model_path


class RerankerService:
    """Cross-encoder reranker — lazy-loads the model on first use."""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._lock = asyncio.Lock()
        self._offload_task = None
        self._last_used = 0.0
        self._idle_timeout = float(settings.RERANKER_IDLE_TIMEOUT_SECONDS)

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def _load_model(self) -> None:
        model_path = _validate_model_path(settings.RERANKER_MODEL)
        logger.info("Loading reranker model: %s (exists=%s)", model_path, model_path.exists())
        t0 = time.perf_counter()
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(str(model_path), local_files_only=True)
        elapsed = time.perf_counter() - t0
        logger.info("Reranker model ready in %.1fs", elapsed)

    async def _ensure_model(self) -> bool:
        if not settings.ENABLE_RERANKER:
            return False
        if self._model is not None:
            self._last_used = time.time()
            return True
        if self._load_failed:
            return False

        try:
            await asyncio.to_thread(self._load_model)
            self._last_used = time.time()
            self._start_offloader()
            return True
        except Exception:
            self._load_failed = True
            logger.exception(
                "Failed to load reranker model '%s'. "
                "Reranking will be skipped — results returned by fusion score only.",
                settings.RERANKER_MODEL,
            )
            return False

    def _start_offloader(self) -> None:
        if settings.RERANKER_KEEP_LOADED:
            return
        if self._offload_task is None or self._offload_task.done():
            self._offload_task = asyncio.create_task(self._offload_loop())

    async def warmup(self) -> bool:
        async with self._lock:
            return await self._ensure_model()

    async def _offload_loop(self) -> None:
        logger.info("Reranker idle offloader started.")
        while True:
            await asyncio.sleep(30.0)  # Check every 30 seconds
            async with self._lock:
                if self._model is None:
                    break
                idle_time = time.time() - self._last_used
                if idle_time >= self._idle_timeout:
                    logger.info("Reranker model has been idle for %.1fs. Offloading to free RAM.", idle_time)
                    self._model = None
                    import gc
                    gc.collect()
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except ImportError:
                        pass
                    break
        logger.info("Reranker idle offloader stopped.")

    async def rerank(self, query: str, candidates: list[ChunkResult], top_k: int) -> list[ChunkResult]:
        if not candidates:
            return []
        if not settings.ENABLE_RERANKER:
            logger.info("Reranker disabled by ENABLE_RERANKER=false; returning fusion-ranked candidates.")
            return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]

        async with self._lock:
            if not await self._ensure_model():
                logger.warning("Reranker unavailable — returning candidates by fusion score")
                return sorted(candidates, key=lambda c: c.score, reverse=True)[:top_k]

            self._last_used = time.time()
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
            self._last_used = time.time()

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
