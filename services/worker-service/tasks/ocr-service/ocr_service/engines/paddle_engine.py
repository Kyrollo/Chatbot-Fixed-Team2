"""
engines/paddle_engine.py
------------------------
OCR engine using EasyOCR as a drop-in replacement for PaddleOCR.

PaddleOCR on Windows requires Intel MKL DLLs that conflict with the
installed paddlepaddle build, causing:
    ImportError: DLL load failed while importing libpaddle

EasyOCR uses PyTorch (already installed) and works reliably on Windows CPU.
The public interface is identical — no other files need changing.
"""
from __future__ import annotations

import logging
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_model: Any = None   # lazy-loaded EasyOCR Reader


def get_paddle_model() -> Any:
    """
    Returns the shared EasyOCR Reader (loaded once on first call).
    Named get_paddle_model() to keep the interface identical.
    """
    global _model
    if _model is None:
        logger.info("Loading EasyOCR model (ar + en, CPU)...")
        import easyocr  # noqa: PLC0415
        _model = easyocr.Reader(["ar", "en"], gpu=False)
        logger.info("EasyOCR loaded")
    return _model


def run_paddle_ocr(image: Image.Image) -> dict:
    """
    Runs EasyOCR on a PIL Image.

    Returns the same dict shape the rest of the pipeline expects:
        {
            "text":       "full extracted text as single string",
            "words":      [{"text": str, "confidence": float}, ...],
            "raw_result": <native EasyOCR output>,
        }
    """
    import numpy as np

    reader    = get_paddle_model()
    img_array = np.array(image)

    # detail=1 → [([bbox_points], text, confidence), ...]
    raw = reader.readtext(img_array, detail=1)

    words = []
    lines = []

    for (bbox, text, confidence) in raw:
        text = str(text).strip()
        if text:
            words.append({"text": text, "confidence": float(confidence)})
            lines.append(text)

    return {
        "text":       "\n".join(lines),
        "words":      words,
        "raw_result": raw,
    }