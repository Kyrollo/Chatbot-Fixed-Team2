"""In-process TTL cache used when Redis is unavailable (local API mode)."""

from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class MemoryTTLCache(Generic[T]):
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T, ttl_seconds: int) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)

    def close(self) -> None:
        self._store.clear()
