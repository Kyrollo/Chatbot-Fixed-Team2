from functools import lru_cache
import os
import re

import asyncpg

from config import settings
from schemas.retrieval import ChunkResult

_TSVEC_LANG = os.getenv("TSVEC_LANGUAGE", "simple")

# Universal stop words to strip before building the OR tsquery.
# Covers English and Arabic function words. Extend as needed.
_BM25_STOPWORDS = {
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "up",
    "and", "or", "but", "not", "so", "yet", "both", "either", "nor",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    "may", "might", "must", "have", "has", "had", "according", "using",
    # Arabic
    "من", "إلى", "على", "في", "عن", "مع", "هو", "هي", "هم", "هن",
    "أن", "إن", "لا", "لم", "لن", "قد", "كان", "كانت",
}

def _extract_bm25_terms(query: str, max_terms: int = 8) -> list[str]:
    """
    Extract the most informative tokens from a query for OR-based BM25 matching.
    Works for any language — simply removes stop words and short tokens,
    then keeps up to max_terms of the remainder.
    """
    # Tokenise: split on whitespace and strip punctuation from each token
    tokens = re.split(r"\s+", query.strip())
    terms = []
    for tok in tokens:
        clean = re.sub(r"[^\w\u0600-\u06FF]", "", tok).lower()
        if len(clean) >= 3 and clean not in _BM25_STOPWORDS:
            terms.append(clean)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:max_terms]


class BM25SearchService:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            self._pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
        return self._pool

    async def search(self, *, domain_id: str, query: str, top_k: int) -> list[ChunkResult]:
        terms = _extract_bm25_terms(query)
        if not terms:
            return []  # Nothing meaningful to search for
        # Join with | for OR semantics — a chunk matches if it contains ANY key term
        or_query = " | ".join(terms)

        pool = await self._get_pool()
        sql = """
            SELECT
                c.id,
                c.document_id,
                c.page_num,
                c.chunk_index,
                c.text,
                d.filename,
                COALESCE(c.source_type, 'pdf') AS source_type,
                COALESCE(c.chunk_type, 'text') AS chunk_type,
                ts_rank_cd(c.search_vec, to_tsquery($4, $2)) AS score
            FROM document_chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.domain_id = $1
              AND c.search_vec @@ to_tsquery($4, $2)
            ORDER BY score DESC
            LIMIT $3
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, domain_id, or_query, top_k, _TSVEC_LANG)

        return [
            ChunkResult(
                chunk_id=row["id"],
                document_id=row["document_id"],
                filename=row["filename"],
                source_type=row["source_type"],
                chunk_type=row["chunk_type"],
                chunk_index=row["chunk_index"] or 0,
                page=row["page_num"],
                text=row["text"],
                score=float(row["score"] or 0.0),
                source="bm25",
            )
            for row in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


@lru_cache(maxsize=1)
def get_bm25_search_service() -> BM25SearchService:
    return BM25SearchService()
