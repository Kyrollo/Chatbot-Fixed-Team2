import hashlib
import sys
from functools import lru_cache
from pathlib import Path

from redis.asyncio import Redis

from config import settings
from schemas import QueryResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from memory_cache import MemoryTTLCache  # noqa: E402


class GenerationCache:
    def __init__(self) -> None:
        self._memory = MemoryTTLCache[str]()
        self._client: Redis | None = None
        if settings.REDIS_URL and settings.REDIS_URL != "memory://":
            self._client = Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)

    @staticmethod
    def _key(domain_id: str, query: str) -> str:
        digest = hashlib.sha256(f"{domain_id}:{query.strip().lower()}".encode("utf-8")).hexdigest()
        return f"generation:{digest}"

    async def get(self, *, domain_id: str, query: str) -> QueryResponse | None:
        key = self._key(domain_id, query)
        if self._client is None:
            payload = self._memory.get(key)
        else:
            try:
                payload = await self._client.get(key)
            except Exception:
                payload = self._memory.get(key)

        if not payload:
            return None
        response = QueryResponse.model_validate_json(payload)
        response.cache_hit = True
        return response

    async def set(self, *, domain_id: str, query: str, response: QueryResponse) -> None:
        key = self._key(domain_id, query)
        payload = response.model_dump_json()
        if self._client is None:
            self._memory.set(key, payload, settings.CACHE_TTL_SECONDS)
            return
        try:
            await self._client.setex(key, settings.CACHE_TTL_SECONDS, payload)
        except Exception:
            self._memory.set(key, payload, settings.CACHE_TTL_SECONDS)

    async def incr(self, key: str) -> None:
        if self._client is not None:
            try:
                await self._client.incr(key)
            except Exception:
                pass

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._memory.close()


@lru_cache(maxsize=1)
def get_generation_cache() -> GenerationCache:
    return GenerationCache()
