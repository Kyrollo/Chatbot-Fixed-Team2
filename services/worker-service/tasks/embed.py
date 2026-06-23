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
    Returns the shared SentenceTransformer instance.
    Loaded once on first call (when the first document arrives), then
    cached for the worker lifetime. PyTorch is NOT loaded at Celery
    startup — only when this function is first called.

    WHY lazy import?
    ──────────────────────────────────────────────────────────────────
    sentence_transformers imports torch at module level, which loads
    ~3 GB of DLLs (torch_python.dll etc.). On Windows, loading these
    while multiple other services are also starting exhausts the paging
    file, causing [WinError 1455] in the worker and cascading
    MemoryErrors in sibling processes (e.g. retrieval-service).
    Deferring the import until the first document is processed means the
    worker is idle and other services are already settled by then.

    WHY HF_HUB_OFFLINE + TRANSFORMERS_OFFLINE?
    ──────────────────────────────────────────────────────────────────
    On Windows, Transformer.__init__ calls find_adapter_config_file which
    enters cached_file → cached_files in transformers/utils/hub.py.  That
    network-probing call stack is deep enough (C→Python callbacks) to exceed
    Python's recursion limit (set to 10 000 by hf_env.py), raising:
        RecursionError: maximum recursion depth exceeded while calling a Python object
    Setting those env vars (done in hf_env.py before any import) makes hub
    utilities return immediately without network access.

    WHY explicit modules instead of SentenceTransformer(model_name)?
    ──────────────────────────────────────────────────────────────────
    intfloat/multilingual-e5-small does not ship a sentence_bert_config.json,
    so sentence-transformers would fall back to another internal
    SentenceTransformer() call, adding even more recursion depth.
    Passing modules=[Transformer, Pooling] directly skips that path.

    NOTE: do NOT pass config_args={"local_files_only": True} — that makes
    AutoConfig look for a literal directory named "intfloat/multilingual-e5-small"
    on disk instead of the HuggingFace cache, causing an OSError.
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                resolved_model_name = _resolve_model_name()
                print(f"Loading embedding model: {resolved_model_name}...")
                # Lazy imports — torch loads here, not at Celery startup.
                from sentence_transformers import SentenceTransformer          # noqa: PLC0415
                from sentence_transformers import models as st_models          # noqa: PLC0415

                transformer = st_models.Transformer(resolved_model_name)
                pooling = st_models.Pooling(
                    transformer.get_word_embedding_dimension(),
                    pooling_mode_mean_tokens=True,
                )
                _model = SentenceTransformer(modules=[transformer, pooling])
                print("Model loaded")
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

    # Dynamic Model Offloading: unload model and trigger GC to save RAM
    global _model
    if _model is not None:
        _model = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    return chunks
