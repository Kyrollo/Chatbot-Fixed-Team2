"""Shared Qdrant client helpers for local file storage or remote server."""

from __future__ import annotations

import os
from pathlib import Path

from qdrant_client import AsyncQdrantClient, QdrantClient


def qdrant_path() -> str | None:
    path = os.getenv("QDRANT_PATH", "").strip()
    return path or None


def sync_qdrant_client() -> QdrantClient:
    path = qdrant_path()
    if path:
        Path(path).mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=path)
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    return QdrantClient(url=url)


def async_qdrant_client() -> AsyncQdrantClient:
    path = qdrant_path()
    if path:
        Path(path).mkdir(parents=True, exist_ok=True)
        return AsyncQdrantClient(path=path)
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    return AsyncQdrantClient(url=url)
