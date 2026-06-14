"""
api/app.py
-----------
FastAPI service exposing the OCR pipeline over HTTP.

Endpoints:
  POST /ocr/file     — Upload any supported file (PDF / image)
  POST /ocr/image    — Upload a raw image (PNG/JPG)
  POST /ocr/batch    — Upload multiple images
  GET  /health       — Liveness check
"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image

from ocr_service.pipeline import run_ocr_pipeline, run_ocr_on_image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Intelligent OCR Service",
    description=(
        "Ensemble OCR pipeline using PaddleOCR (fast path) + Surya OCR (fallback). "
        "Supports Arabic, English, and mixed documents. "
        "Returns the best result with confidence scoring."
    ),
    version="1.0.0",
)

_SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}


# ──────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "ocr"}


@app.post("/ocr/file")
async def ocr_file(
    file:   UploadFile = File(...),
    deskew: bool       = Form(True),
    page:   Optional[int] = Form(None),
):
    """
    Upload any supported file (PDF or image) and get OCR results.

    - **file**:   The document to process.
    - **deskew**: Apply deskew correction (default: true).
    - **page**:   For PDFs only — process a single page (0-based index).
                  Omit to process all pages.

    Returns a list of page results.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}'. "
                   f"Supported: {sorted(_SUPPORTED_EXTENSIONS)}",
        )

    # Save upload to a temp file — pipeline needs a file path for PDF
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        t = time.perf_counter()
        results = run_ocr_pipeline(tmp_path, page=page, deskew=deskew)
        elapsed = round((time.perf_counter() - t) * 1000, 1)

        logger.info(
            "Processed '%s' — %d page(s) in %.0f ms",
            file.filename, len(results), elapsed,
        )

        return JSONResponse({
            "filename":         file.filename,
            "pages_processed":  len(results),
            "total_time_ms":    elapsed,
            "results":          results,
        })
    finally:
        os.unlink(tmp_path)


@app.post("/ocr/image")
async def ocr_image(
    file:   UploadFile = File(...),
    deskew: bool       = Form(True),
):
    """
    Upload a single image and get OCR result.

    Returns a single-page result dict:
    {
        "text": "...",
        "model_used": "paddle | surya",
        "confidence_score": 0.91,
        ...
    }
    """
    data = await file.read()
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot open image: {exc}")

    result = run_ocr_on_image(image, deskew=deskew)
    return JSONResponse(result)


@app.post("/ocr/batch")
async def ocr_batch(
    files:  list[UploadFile] = File(...),
    deskew: bool             = Form(True),
):
    """
    Upload multiple images and get OCR results for all.

    Returns a list of result dicts, one per uploaded image.
    Processed sequentially (parallel processing can be added via asyncio.gather
    if GPU is available).
    """
    if len(files) > 20:
        raise HTTPException(status_code=422, detail="Max 20 files per batch request.")

    results = []
    for i, f in enumerate(files):
        data = await f.read()
        try:
            image  = Image.open(io.BytesIO(data)).convert("RGB")
            result = run_ocr_on_image(image, page_num=i + 1, deskew=deskew)
            result["filename"] = f.filename
            results.append(result)
        except Exception as exc:
            logger.error("Failed to process '%s': %s", f.filename, exc)
            results.append({
                "filename":         f.filename,
                "error":            str(exc),
                "text":             "",
                "model_used":       None,
                "confidence_score": 0.0,
            })

    return JSONResponse({"batch_size": len(results), "results": results})