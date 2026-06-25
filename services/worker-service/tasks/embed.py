from __future__ import annotations

# HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, and sys.setrecursionlimit are all
# set by hf_env.py, which worker.py imports first — before Celery loads
# tasks.process → embed.py. No duplicate setup needed here.
#
# IMPORTANT: sentence_transformers (and therefore PyTorch) is imported
# lazily inside get_model() — NOT at module level. Loading ~3 GB of PyTorch
# DLLs at Celery startup exhausts the Windows paging file while other
# services are also booting, causing [WinError 1455] and cascading
# MemoryErrors. The import is deferred until the first document arrives.

import os
from pathlib import Path
from typing import Any

import numpy as np

# ------------------------------------------------------------------
# Model is loaded ONCE when the worker process starts.
# get_model() returns the same instance every call — no re-loading.
# Both embed.py and chunk.py use the same model instance.
# ------------------------------------------------------------------

_model: Any = None  # SentenceTransformer, lazily loaded
import threading
_model_lock = threading.Lock()

EMBEDDING_DIM = 384
BATCH_SIZE    = 32


REMOTE_MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_NAME = os.getenv("WORKER_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or REMOTE_MODEL_NAME


def _resolve_model_name() -> str:
    model_name = MODEL_NAME
    model_path = Path(model_name)
    if model_path.exists():
        return str(model_path)
    offline_enabled = os.getenv("HF_HUB_OFFLINE") == "1" or os.getenv("TRANSFORMERS_OFFLINE") == "1"
    if model_name != REMOTE_MODEL_NAME or offline_enabled:
        raise FileNotFoundError(
            f"Worker embedding model path does not exist: {model_name}. "
            "Set WORKER_EMBEDDING_MODEL or EMBEDDING_MODEL to a valid local directory."
        )
    return model_name


def get_model() -> Any:
    """
    Returns the shared ONNXEmbeddingClient instance.
    Loaded once on first call (when the first document arrives), then
    cached for the worker lifetime.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                resolved_model_name = _resolve_model_name()
                print(f"Loading embedding model in ONNX Runtime: {resolved_model_name}...")
                from tasks.onnx_client import ONNXEmbeddingClient
                _model = ONNXEmbeddingClient(resolved_model_name)
                print("Model loaded (ONNX)")
    return _model


def embed_chunks(chunks: list[dict]) -> list[dict]:
    """
    Takes chunks from chunk.py and adds an 'embedding' key to each.

    multilingual-e5-base requires a prefix:
    - Documents at ingest time → "passage: " + text
    - Queries   at query time  → "query: "   + text
    Both must use the same model for vectors to be in the same space.

    Returns the same list of chunks with 'embedding' added.
    """
    if chunks:
        model = get_model()
        texts = [f"passage: {chunk['text']}" for chunk in chunks]

        print(f"  Embedding {len(texts)} chunks in batches of {BATCH_SIZE}...")

        embeddings = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

        for chunk, embedding in zip(chunks, embeddings):
            chunk["embedding"] = embedding.tolist()
    else:
        print("  No chunks to embed")

    print(f"  Embedded {len(chunks)} chunks — dim={EMBEDDING_DIM}")

    return chunks
