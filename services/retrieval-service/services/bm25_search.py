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
                c.id,
                c.document_id,
                c.page_num,
                c.chunk_index,
                c.text,
                d.filename,
                COALESCE(c.source_type, 'pdf') AS source_type,
                COALESCE(c.chunk_type, 'text') AS chunk_type,
                ts_rank_cd(c.search_vec, websearch_to_tsquery('simple', $2)) AS score
            FROM document_chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.domain_id = $1
              AND c.search_vec @@ websearch_to_tsquery('simple', $2)
            ORDER BY score DESC
            LIMIT $3
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, domain_id, query, top_k)

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
