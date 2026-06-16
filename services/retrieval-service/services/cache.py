import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

from redis.asyncio import Redis

from config import settings
from schemas.retrieval import RetrievalResponse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from memory_cache import MemoryTTLCache  # noqa: E402


class RetrievalCache:
    def __init__(self) -> None:
        self._memory = MemoryTTLCache[str]()
        self._client: Redis | None = None
        if settings.REDIS_URL and settings.REDIS_URL != "memory://":
            self._client = Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)

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
        key = self._key(domain_id, query, top_k_retrieve, top_k_rerank)
        if self._client is None:
            payload = self._memory.get(key)
        else:
            try:
                payload = await self._client.get(key)
            except Exception:
                payload = self._memory.get(key)

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
        key = self._key(domain_id, query, top_k_retrieve, top_k_rerank)
        payload = response.model_dump_json()
        if self._client is None:
            self._memory.set(key, payload, settings.CACHE_TTL_SECONDS)
            return
        try:
            await self._client.setex(key, settings.CACHE_TTL_SECONDS, payload)
        except Exception:
            self._memory.set(key, payload, settings.CACHE_TTL_SECONDS)

    async def incr(self, key: str, amount: int = 1) -> None:
        if self._client is not None:
            try:
                await self._client.incrby(key, amount)
            except Exception:
                pass

    async def incrbyfloat(self, key: str, amount: float) -> None:
        if self._client is not None:
            try:
                await self._client.incrbyfloat(key, amount)
            except Exception:
                pass

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._memory.close()


@lru_cache(maxsize=1)
def get_retrieval_cache() -> RetrievalCache:
    return RetrievalCache()
