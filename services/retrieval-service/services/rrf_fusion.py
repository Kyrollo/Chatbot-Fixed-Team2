from collections import defaultdict
import re
from schemas.retrieval import ChunkResult


def fuse_results(*ranked_lists: list[ChunkResult], k: int = 60, query: str | None = None) -> list[ChunkResult]:
    scores: dict[str, float] = defaultdict(float)
    best_by_chunk: dict[str, ChunkResult] = {}

    # Check if this is a table-seeking query
    is_table_q = False
    if query:
        table_keywords = ["table", "csv", "excel", "sheet", "row", "col", "column", "average", "sum", "total", "report", "statistics", "data"]
        q = query.lower()
        is_table_q = any(kw in q for kw in table_keywords)

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item.chunk_id] += 1.0 / (k + rank)
            
            # Apply table boost if query is table-seeking and chunk is a table
            if is_table_q:
                is_table_chunk = "[TABLE]" in item.text or item.source_type in ("csv", "xls", "xlsx")
                if is_table_chunk:
                    scores[item.chunk_id] += 0.05  # Boost RRF score directly

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
    return fused
