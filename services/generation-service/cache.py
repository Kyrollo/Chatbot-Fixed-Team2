import hashlib
from functools import lru_cache

from redis.asyncio import Redis

from config import settings
from schemas import QueryResponse


class GenerationCache:
    def __init__(self) -> None:
        self._client = Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @staticmethod
    def _key(domain_id: str, query: str) -> str:
        digest = hashlib.sha256(f"{domain_id}:{query.strip().lower()}".encode("utf-8")).hexdigest()
        return f"generation:{digest}"

    async def get(self, *, domain_id: str, query: str) -> QueryResponse | None:
        payload = await self._client.get(self._key(domain_id, query))
        if not payload:
            return None
        response = QueryResponse.model_validate_json(payload)
        response.cache_hit = True
        return response

    async def set(self, *, domain_id: str, query: str, response: QueryResponse) -> None:
        await self._client.setex(
            self._key(domain_id, query),
            settings.CACHE_TTL_SECONDS,
            response.model_dump_json(),
        )

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache(maxsize=1)
def get_generation_cache() -> GenerationCache:
    return GenerationCache()
