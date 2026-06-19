"""
ocr_service/pipeline.py
------------------------
Top-level OCR pipeline.

This is the single entry point consumed by:
- The FastAPI endpoint (api/app.py)
- The Celery task replacement for extract.py (tasks/extract.py)
- Direct CLI usage

Accepts a file path OR a PIL Image and handles:
- PDF (all pages or a specific page)
- Image files (PNG, JPG, JPEG)

Returns a list of page results, one per page/image.

WHAT CHANGED IN THIS REVISION
------------------------------
Added `warm_up_ocr_pipeline()` — a single entry point that eagerly loads
both engines (PaddleOCR for OCR_WARMUP_LANGS, default "ar,en", and Surya)
once at process/worker startup, so the FIRST real image/PDF page processed
doesn't pay any model-load latency. Call this once, e.g. from the Celery
worker's bootstrap (worker.py `worker_process_init` signal) or from
main.py's FastAPI startup event:

    from ocr_service.pipeline import warm_up_ocr_pipeline
    warm_up_ocr_pipeline()

Without this call, behavior is unchanged from before: each engine still
lazily loads itself on first use and is cached from then on — warm-up is
a pure startup-time optimization, not a correctness requirement.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image

from ocr_service.preprocessing.image_processor import (
    preprocess_image,
    pdf_page_to_image,
)
from ocr_service.routing.ocr_router import route_ocr, OCRResult

logger = logging.getLogger(__name__)

# ── Simple in-process cache: hash(file_bytes) → result ────────────
# Avoids re-OCR-ing the same file if submitted twice in the same worker
# lifetime. Bounded by LRU max size.
_CACHE: dict[str, list[dict]] = {}
_CACHE_MAX_ENTRIES = 64

_warmed_up: bool = False


def warm_up_ocr_pipeline(paddle_langs: list[str] | None = None) -> None:
    """
    Eagerly loads every OCR engine used by route_ocr() — PaddleOCR (for
    each language in `paddle_langs`, default OCR_WARMUP_LANGS / "ar,en")
    and Surya — so they're already resident in memory before the first
    real OCR request arrives.

    Call this ONCE at process startup. Safe to call more than once: each
    engine's own warm-up function only loads languages/models that aren't
    already cached, so a repeat call is a fast no-op.
    """
    global _warmed_up

    from ocr_service.engines.paddle_engine import warm_up_paddle_models
    from ocr_service.engines.surya_engine  import warm_up_surya_model

    t_start = time.perf_counter()
    logger.info("Warming up OCR pipeline (PaddleOCR + Surya)...")

    warm_up_paddle_models(paddle_langs)
    warm_up_surya_model()

    elapsed = round((time.perf_counter() - t_start) * 1000, 1)
    _warmed_up = True
    logger.info("OCR pipeline warm-up complete in %.0f ms", elapsed)


def run_ocr_pipeline(
    file_path: str,
    *,
    page: Optional[int] = None,       # None = all pages; int = specific 0-based page
    deskew: bool = True,
    use_cache: bool = True,
) -> list[dict]:
    """
    Full OCR pipeline for a file.

    Args:
        file_path:  Path to PDF or image file.
        page:       For PDFs — process a single page (0-based). None = all.
        deskew:     Apply deskew correction in preprocessing.
        use_cache:  Cache results by file content hash.

    Returns:
        List of page dicts:
        [
            {
                "page":             1,
                "text":             "...",
                "model_used":       "paddle | surya",
                "confidence_score": 0.91,
                "processing_time_ms": 342.1,
                "decision_reason":  "...",
            },
            ...
        ]
    """
    t_total = time.perf_counter()
    path    = Path(file_path)
    ext     = path.suffix.lower()

    # ── Cache check ────────────────────────────────────────────────
    if use_cache:
        cache_key = _file_hash(file_path)
        if cache_key in _CACHE:
            logger.info("Cache hit for %s", path.name)
            return _CACHE[cache_key]

    # ── Route by file type ─────────────────────────────────────────
    if ext == ".pdf":
        results = _process_pdf(file_path, page=page, deskew=deskew)
    elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
        results = _process_image_file(file_path, deskew=deskew)
    else:
        raise ValueError(f"Unsupported file type for OCR pipeline: {ext}")

    total_ms = round((time.perf_counter() - t_total) * 1000, 1)
    logger.info(
        "Pipeline complete — %d pages in %.0f ms (%s)",
        len(results), total_ms, path.name,
    )

    # ── Cache write ────────────────────────────────────────────────
    if use_cache:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            # Evict oldest entry (dict preserves insertion order in Python 3.7+)
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[cache_key] = results

    return results


def run_ocr_on_image(
    image: Image.Image,
    *,
    page_num: int = 1,
    deskew:   bool = True,
) -> dict:
    """
    Runs the full OCR pipeline on a single PIL Image already in memory.
    Used by the FastAPI /ocr/image endpoint that accepts image uploads.

    Returns a single page dict (same format as run_ocr_pipeline's list items).
    """
    preprocessed = preprocess_image(image, deskew=deskew)
    result       = route_ocr(preprocessed)
    return _format_result(result, page_num)


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

def _process_pdf(file_path: str, page: Optional[int], deskew: bool) -> list[dict]:
    """Processes all pages (or a single page) of a PDF."""
    import fitz
    doc      = fitz.open(file_path)
    n_pages  = len(doc)
    doc.close()

    page_range = [page] if page is not None else range(n_pages)
    results    = []

    for p in page_range:
        logger.info("Processing PDF page %d/%d", p + 1, n_pages)
        img    = pdf_page_to_image(file_path, page_num=p, dpi=200)
        result = _run_single_page(img, page_num=p + 1, deskew=deskew)
        results.append(result)

    return results


def _process_image_file(file_path: str, deskew: bool) -> list[dict]:
    """Processes a standalone image file."""
    img    = Image.open(file_path).convert("RGB")
    result = _run_single_page(img, page_num=1, deskew=deskew)
    return [result]


def _run_single_page(image: Image.Image, page_num: int, deskew: bool) -> dict:
    """Preprocesses + routes OCR for one page image."""
    preprocessed = preprocess_image(image, deskew=deskew)
    ocr_result   = route_ocr(preprocessed)
    return _format_result(ocr_result, page_num)


def _format_result(result: OCRResult, page_num: int) -> dict:
    return {
        "page":               page_num,
        "text":               result.text,
        "model_used":         result.model_used,
        "confidence_score":   round(result.confidence_score, 4),
        "paddle_score":       round(result.paddle_score, 4) if result.paddle_score is not None else None,
        "surya_score":        round(result.surya_score,  4) if result.surya_score  is not None else None,
        "processing_time_ms": result.processing_time_ms,
        "decision_reason":    result.decision_reason,
        "detected_langs":     result.detected_langs,   # None = detection skipped/failed → env defaults used
    }


def _file_hash(file_path: str) -> str:
    """SHA-256 of first 1 MB of file — fast cache key."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        h.update(f.read(1_048_576))
    return h.hexdigest()