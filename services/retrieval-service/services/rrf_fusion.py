import logging
from collections import defaultdict

from schemas.retrieval import ChunkResult

logger = logging.getLogger(__name__)


def fuse_results(*ranked_lists: list[ChunkResult], k: int = 60) -> list[ChunkResult]:
    scores: dict[str, float] = defaultdict(float)
    best_by_chunk: dict[str, ChunkResult] = {}

    # Log what came in from each engine
    for i, ranked in enumerate(ranked_lists):
        engine = ranked[0].source if ranked else f"engine_{i}"
        logger.info(
            "RRF input [%s]: %d chunks — top 3: %s",
            engine,
            len(ranked),
            [f"{r.chunk_id[:8]}(score={r.score:.4f})" for r in ranked[:3]],
        )

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            rrf_score = 1.0 / (k + rank)
            scores[item.chunk_id] += rrf_score
            current = best_by_chunk.get(item.chunk_id)
            if current is None or item.score > current.score:
                best_by_chunk[item.chunk_id] = item

    fused: list[ChunkResult] = []
    for chunk_id, score in scores.items():
        item = best_by_chunk[chunk_id]
        fused.append(
            ChunkResult(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                filename=item.filename,
                source_type=item.source_type,
                chunk_index=item.chunk_index,
                page=item.page,
                text=item.text,
                score=score,
                source="rrf",
            )
        )

    fused.sort(key=lambda item: item.score, reverse=True)

    # Log fusion output
    unique = len(fused)
    appeared_in_both = sum(1 for s in scores.values() if s > 1.0 / (k + 1))
    logger.info(
        "RRF output: %d unique chunks — %d appeared in multiple engines — top 3: %s",
        unique,
        appeared_in_both,
        [f"{r.chunk_id[:8]}(rrf={r.score:.5f})" for r in fused[:3]],
    )

    return fused