from collections import defaultdict

from schemas.retrieval import ChunkResult


def fuse_results(*ranked_lists: list[ChunkResult], k: int = 60) -> list[ChunkResult]:
    scores: dict[str, float] = defaultdict(float)
    best_by_chunk: dict[str, ChunkResult] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item.chunk_id] += 1.0 / (k + rank)
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
                page=item.page,
                text=item.text,
                score=score,
                source="rrf",
            )
        )

    fused.sort(key=lambda item: item.score, reverse=True)
    return fused
