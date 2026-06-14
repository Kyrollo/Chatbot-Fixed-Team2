"""
tasks/extract_with_ocr.py
──────────────────────────
Intelligent extraction backend that routes every file type through the most
appropriate pipeline.  This module is a DROP-IN replacement for tasks/extract.py.

To activate, change ONE line in tasks/process.py:

    # Before (basic Tesseract):
    from tasks.extract import extract_text

    # After (PaddleOCR + Surya ensemble):
    from tasks.extract_with_ocr import extract_text

Routing logic
─────────────
  .pdf              → PyMuPDF for native-text pages  (fast, zero model overhead)
                      OCR pipeline for scanned pages  (PaddleOCR → Surya if needed)
  .docx             → python-docx, unchanged
  .csv              → pandas, unchanged
  .png/.jpg/.jpeg   → OCR pipeline (full image, deskew enabled)

The OCR pipeline is defined in ocr_service/ and uses a PaddleOCR fast path
with an automatic Surya fallback when confidence is below the threshold (0.85).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Ensure ocr_service is importable (same logic as tasks/extract.py)
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_ocr_service_on_path() -> None:
    """Add the directory containing the ocr_service package to sys.path.

    Supports two layouts:
      1. services/worker-service/tasks/ocr-service/ocr_service   (nested copy)
      2. services/ocr-service/ocr_service                        (sibling service, default)
    """
    this_file = Path(__file__).resolve()

    nested_dir  = this_file.parent / "ocr-service"
    sibling_dir = this_file.parents[2] / "ocr-service"

    for candidate in (nested_dir, sibling_dir):
        if candidate.is_dir() and (candidate / "ocr_service").is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
                logger.debug("Added %s to sys.path for ocr_service imports", candidate_str)
            return

    raise ImportError(
        f"ocr-service package not found. Tried:\n"
        f"  - {nested_dir} (expects {nested_dir / 'ocr_service'})\n"
        f"  - {sibling_dir} (expects {sibling_dir / 'ocr_service'})\n"
        "Make sure the ocr_service package exists in one of these locations."
    )


_ensure_ocr_service_on_path()


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point — same signature as tasks/extract.py
# ──────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str, mime_type: str | None = None) -> list[dict]:
    """
    Extracts text from a file, routing to the best extraction method.

    Args:
        file_path:  Absolute path to the file.
        mime_type:  Optional MIME type hint (not used currently; kept for
                    API compatibility with tasks/extract.py).

    Returns:
        list[dict]: [{"page": int, "text": str}, ...]
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf_smart(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".csv":
        return _extract_csv(file_path)
    elif ext in (".png", ".jpg", ".jpeg"):
        return _extract_image_ocr(file_path)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")


# ──────────────────────────────────────────────────────────────────────────────
# PDF — smart extraction: native text first, OCR pipeline as fallback
# ──────────────────────────────────────────────────────────────────────────────

def _extract_pdf_smart(file_path: str) -> list[dict]:
    """
    Page-by-page PDF extraction.

    Native-text pages:  PyMuPDF (no model, near-instant).
    Scanned/image pages: OCR pipeline (PaddleOCR → Surya fallback).

    This means a mixed PDF — some digital, some scanned — is handled
    correctly without running OCR on pages that already have text.
    """
    import fitz
    from ocr_service.pipeline import run_ocr_on_image
    from ocr_service.preprocessing.image_processor import pdf_page_to_image

    doc   = fitz.open(file_path)
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()

        if text:
            # Native selectable text — no OCR needed
            pages.append({"page": page_num + 1, "text": text})
            logger.debug("Page %d: native text (%d chars)", page_num + 1, len(text))
        else:
            # Scanned / image-only page — run OCR pipeline
            logger.info("Page %d: no native text — running OCR pipeline", page_num + 1)
            img    = pdf_page_to_image(file_path, page_num=page_num, dpi=200)
            result = run_ocr_on_image(img, page_num=page_num + 1, deskew=True)

            if result["text"]:
                pages.append({"page": page_num + 1, "text": result["text"]})
                logger.info(
                    "  Page %d OCR: model=%s confidence=%.3f",
                    page_num + 1,
                    result["model_used"],
                    result["confidence_score"],
                )
            else:
                logger.warning("  Page %d: no text after OCR — skipping", page_num + 1)

    doc.close()
    logger.info("PDF extraction complete: %d pages with text", len(pages))
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# Standalone image — full OCR pipeline
# ──────────────────────────────────────────────────────────────────────────────

def _extract_image_ocr(file_path: str) -> list[dict]:
    """
    Routes a standalone image file through the OCR pipeline.
    Deskew is enabled by default for better accuracy on tilted scans.
    """
    from ocr_service.pipeline import run_ocr_on_image

    img    = Image.open(file_path).convert("RGB")
    result = run_ocr_on_image(img, page_num=1, deskew=True)

    if result["text"]:
        logger.info(
            "Image OCR: model=%s confidence=%.3f",
            result["model_used"],
            result["confidence_score"],
        )
        return [{"page": 1, "text": result["text"]}]

    logger.warning("Image OCR: no text extracted from %s", os.path.basename(file_path))
    return []


# ──────────────────────────────────────────────────────────────────────────────
# DOCX — unchanged from tasks/extract.py
# ──────────────────────────────────────────────────────────────────────────────

def _extract_docx(file_path: str) -> list[dict]:
    """
    Extracts text from a .docx file using python-docx.
    Segments by heading boundaries or ~1 500-character threshold.
    """
    import docx
    doc = docx.Document(file_path)
    pages, current_segment, segment_index, char_count = [], [], 1, 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        current_segment.append(text)
        char_count += len(text)
        if para.style.name.startswith("Heading") or char_count > 1500:
            pages.append({"page": segment_index, "text": "\n".join(current_segment)})
            current_segment, char_count = [], 0
            segment_index += 1

    if current_segment:
        pages.append({"page": segment_index, "text": "\n".join(current_segment)})

    logger.info("DOCX: %d segments from %s", len(pages), os.path.basename(file_path))
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# CSV — unchanged from tasks/extract.py
# ──────────────────────────────────────────────────────────────────────────────

def _extract_csv(file_path: str) -> list[dict]:
    """
    Extracts text from a .csv file using pandas.
    Groups rows in batches of 10, prefixed with column headers for context.
    """
    import pandas as pd
    df         = pd.read_csv(file_path)
    headers    = ", ".join(df.columns)
    pages      = []
    chunk_size = 10

    for i in range(0, len(df), chunk_size):
        subset = df.iloc[i:i + chunk_size]
        rows = [
            f"Row {idx}: " + ", ".join(str(v) for v in row.values)
            for idx, row in subset.iterrows()
        ]
        pages.append({"page": i + 1, "text": f"Headers: {headers}\n" + "\n".join(rows)})

    logger.info("CSV: %d chunks (%d rows) from %s", len(pages), len(df), os.path.basename(file_path))
    return pages