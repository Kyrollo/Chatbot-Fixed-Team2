from functools import lru_cache

import asyncpg

from config import settings
from schemas.retrieval import ChunkResult


class BM25SearchService:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            self._pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
        return self._pool

    async def search(self, *, domain_id: str, query: str, top_k: int) -> list[ChunkResult]:
        pool = await self._get_pool()
        sql = """
            SELECT
                id,
                document_id,
                page_num,
                text,
                ts_rank_cd(search_vec, websearch_to_tsquery('simple', $2)) AS score
            FROM document_chunks
            WHERE domain_id = $1
              AND search_vec @@ websearch_to_tsquery('simple', $2)
            ORDER BY score DESC
            LIMIT $3
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, domain_id, query, top_k)

        return [
            ChunkResult(
                chunk_id=row["id"],
                document_id=row["document_id"],
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
