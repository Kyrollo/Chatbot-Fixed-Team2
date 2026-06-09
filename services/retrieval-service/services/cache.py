import hashlib
import json
from functools import lru_cache

from redis.asyncio import Redis

from config import settings
from schemas.retrieval import RetrievalResponse


class RetrievalCache:
    def __init__(self) -> None:
        self._client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @staticmethod
    def _key(domain_id: str, query: str, top_k_retrieve: int, top_k_rerank: int) -> str:
        digest = hashlib.sha256(
            f"{domain_id}:{top_k_retrieve}:{top_k_rerank}:{query.strip().lower()}".encode("utf-8")
        ).hexdigest()
        return f"retrieval:{digest}"

    async def get(
        self,
        *,
        domain_id: str,
        query: str,
        top_k_retrieve: int,
        top_k_rerank: int,
    ) -> RetrievalResponse | None:
        payload = await self._client.get(
            self._key(domain_id, query, top_k_retrieve, top_k_rerank)
        )
        if not payload:
            return None
        data = json.loads(payload)
        data["cache_hit"] = True
        return RetrievalResponse.model_validate(data)

    async def set(
        self,
        *,
        domain_id: str,
        query: str,
        top_k_retrieve: int,
        top_k_rerank: int,
        response: RetrievalResponse,
    ) -> None:
        await self._client.setex(
            self._key(domain_id, query, top_k_retrieve, top_k_rerank),
            settings.CACHE_TTL_SECONDS,
            response.model_dump_json(),
        )

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache(maxsize=1)
def get_retrieval_cache() -> RetrievalCache:
    return RetrievalCache()
