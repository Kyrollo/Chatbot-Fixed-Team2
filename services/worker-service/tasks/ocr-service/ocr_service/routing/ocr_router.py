"""
routing/ocr_router.py
----------------------
Smart routing engine — decides which OCR model(s) to run and picks
the best result.

Decision flow:
1. Run PaddleOCR/EasyOCR (fast path)
2. Score the result
3. If score >= CONFIDENCE_THRESHOLD → return immediately (early exit)
4. Otherwise run Surya, score its output
5. Compare both scores → return the winner

This design means Surya is only invoked when the fast path genuinely
struggles, keeping average latency low.

RESILIENCE FIX
--------------
Previously, if Surya raised ANY exception (e.g. the
"ModuleNotFoundError: No module named 'surya.ocr'" caused by an
incompatible surya-ocr version, or a model-download failure), that
exception propagated all the way up through run_ocr_on_image() ->
extract_text() -> process_document_sync(), causing the whole document
to be marked "failed" and retried by Celery — even though PaddleOCR/
EasyOCR had already produced a usable (if lower-confidence) result.

Now, any failure from run_surya_ocr() is caught here and we fall back
to the PaddleOCR/EasyOCR result instead, so image OCR keeps working
even when Surya can't run.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from PIL import Image

from ocr_service.engines.paddle_engine import run_paddle_ocr
from ocr_service.engines.surya_engine  import run_surya_ocr
from ocr_service.scoring.ocr_scorer   import score_paddle_result, score_surya_result

logger = logging.getLogger(__name__)

# If PaddleOCR/EasyOCR scores >= this, skip Surya entirely.
# Override at runtime: set OCR_CONFIDENCE_THRESHOLD=0.75 in your .env to force more Surya usage,
# or 0.95 for maximum Surya usage on every page.
import os as _os
CONFIDENCE_THRESHOLD: float = float(_os.getenv("OCR_CONFIDENCE_THRESHOLD", "0.85"))


@dataclass
class OCRResult:
    text:             str
    model_used:       str        # "paddle" | "surya" | "paddle+surya"
    confidence_score: float
    paddle_score:     float | None = None
    surya_score:      float | None = None
    processing_time_ms: float = 0.0
    decision_reason:  str = ""


def route_ocr(image: Image.Image) -> OCRResult:
    """
    Main routing function.

    Args:
        image: Preprocessed PIL Image.

    Returns:
        OCRResult with the best extracted text and metadata.
    """
    t_start = time.perf_counter()

    # ── Step 1: Run PaddleOCR/EasyOCR ───────────────────────────────
    logger.info("Running PaddleOCR...")
    paddle_raw   = run_paddle_ocr(image)
    paddle_score = score_paddle_result(paddle_raw)

    logger.info("PaddleOCR score: %.3f (threshold=%.2f)", paddle_score, CONFIDENCE_THRESHOLD)

    # ── Step 2: Early exit if confident enough ─────────────────────
    if paddle_score >= CONFIDENCE_THRESHOLD:
        elapsed = (time.perf_counter() - t_start) * 1000
        reason  = f"PaddleOCR early exit (score={paddle_score:.3f} >= {CONFIDENCE_THRESHOLD})"
        logger.info(reason)
        return OCRResult(
            text              = paddle_raw["text"],
            model_used        = "paddle",
            confidence_score  = paddle_score,
            paddle_score      = paddle_score,
            surya_score       = None,
            processing_time_ms= round(elapsed, 1),
            decision_reason   = reason,
        )

    # ── Step 3: Run Surya (fallback / ensemble) ────────────────────
    logger.info("PaddleOCR below threshold — running Surya OCR...")
    try:
        surya_raw   = run_surya_ocr(image)
        surya_score = score_surya_result(surya_raw)
        logger.info("Surya score: %.3f", surya_score)
    except Exception as exc:
        # Surya failed to load or run (incompatible package version, model
        # download failure, OOM, etc). Don't fail the whole document —
        # use the PaddleOCR/EasyOCR result we already have.
        elapsed = (time.perf_counter() - t_start) * 1000
        reason  = (
            f"Surya unavailable ({exc.__class__.__name__}: {exc}) — "
            f"using PaddleOCR result (paddle={paddle_score:.3f})"
        )
        logger.warning(reason)
        return OCRResult(
            text              = paddle_raw["text"],
            model_used        = "paddle",
            confidence_score  = paddle_score,
            paddle_score      = paddle_score,
            surya_score       = None,
            processing_time_ms= round(elapsed, 1),
            decision_reason   = reason,
        )

    # ── Step 4: Pick winner ────────────────────────────────────────
    elapsed = (time.perf_counter() - t_start) * 1000

    if surya_score >= paddle_score:
        winner_text  = surya_raw["text"]
        winner_model = "surya"
        winner_score = surya_score
        reason = (
            f"Surya selected (surya={surya_score:.3f} >= paddle={paddle_score:.3f})"
        )
    else:
        winner_text  = paddle_raw["text"]
        winner_model = "paddle"
        winner_score = paddle_score
        reason = (
            f"Paddle selected despite low confidence "
            f"(paddle={paddle_score:.3f} > surya={surya_score:.3f})"
        )

    logger.info("Decision: %s | %s", winner_model, reason)

    return OCRResult(
        text              = winner_text,
        model_used        = winner_model,
        confidence_score  = winner_score,
        paddle_score      = paddle_score,
        surya_score       = surya_score,
        processing_time_ms= round(elapsed, 1),
        decision_reason   = reason,
    )