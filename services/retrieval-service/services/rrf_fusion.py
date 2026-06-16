import logging
from collections import defaultdict

from schemas.retrieval import ChunkResult

logger = logging.getLogger(__name__)

_TABLE_KEYWORDS = [
    "table", "csv", "excel", "sheet", "row", "col", "column",
    "average", "sum", "total", "report", "statistics", "data",
    "withholding", "filing", "earns", "wage", "salary", "rate",
    "range", "bracket", "amount", "lookup", "intersection",
    "cross-reference", "cell", "value at",
]


def fuse_results(*ranked_lists: list[ChunkResult], k: int = 60, query: str | None = None) -> list[ChunkResult]:
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

    # Check if this is a table-seeking query
    is_table_q = False
    if query:
        q = query.lower()
        is_table_q = any(kw in q for kw in _TABLE_KEYWORDS)

    # Single pass: accumulate RRF score per chunk, apply table boost once,
    # and track the highest-scoring original ChunkResult per chunk_id.
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item.chunk_id] += 1.0 / (k + rank)

            if is_table_q:
                is_table_nl = "[TABLE_NL]" in item.text or getattr(item, 'chunk_type', '') == 'table_nl'
                is_table_md = ("[TABLE_MD]" in item.text or "[TABLE]" in item.text or
                               getattr(item, 'chunk_type', '') == 'table_md')
                is_table_source = item.source_type in ("csv", "xls", "xlsx")
                if is_table_nl:
                    scores[item.chunk_id] += 0.08  # Higher boost for NL descriptions
                elif is_table_md or is_table_source:
                    scores[item.chunk_id] += 0.03  # Lower boost for raw markdown

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