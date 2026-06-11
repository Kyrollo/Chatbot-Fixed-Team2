"""Shared Qdrant client helpers for local file storage (embedded mode)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from qdrant_client import AsyncQdrantClient, QdrantClient


def qdrant_path() -> str:
    return os.getenv("QDRANT_PATH", "data/qdrant").strip()


def sync_qdrant_client(retries: int = 15, delay: float = 0.2) -> QdrantClient:
    path = qdrant_path()
    Path(path).mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            return QdrantClient(path=path)
        except RuntimeError as e:
            if "already accessed" in str(e) or "locked" in str(e).lower():
                if attempt < retries - 1:
                    time.sleep(delay)
                    continue
            raise


def async_qdrant_client(retries: int = 15, delay: float = 0.2) -> AsyncQdrantClient:
    path = qdrant_path()
    Path(path).mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            return AsyncQdrantClient(path=path)
        except RuntimeError as e:
            if "already accessed" in str(e) or "locked" in str(e).lower():
                if attempt < retries - 1:
                    time.sleep(delay)
                    continue
            raise
