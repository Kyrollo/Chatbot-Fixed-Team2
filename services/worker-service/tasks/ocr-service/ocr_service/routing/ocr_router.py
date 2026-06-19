"""
routing/ocr_router.py
----------------------
Smart routing engine — decides which OCR model(s) to run and picks
the best result.

Decision flow:
0. (NEW) Detect language(s) on the page (language_detector.py, CLIP-based)
1. Run PaddleOCR/EasyOCR (fast path) — using the detected language(s) if
   detection succeeded, otherwise the existing OCR_LANGS/OCR_LANG env
   defaults exactly as before
2. Score the result
3. If score >= CONFIDENCE_THRESHOLD → return immediately (early exit)
4. Otherwise run Surya, score its output
5. Compare both scores → return the winner

This design means Surya is only invoked when the fast path genuinely
struggles, keeping average latency low.

WHAT CHANGED IN THIS REVISION — PRE-OCR LANGUAGE DETECTION
------------------------------------------------------------
Added a Step 0 that runs BEFORE PaddleOCR: language_detector.py uses CLIP
to classify the page image as more likely Arabic, English, or — if neither
prompt clearly wins — both. The result is passed into run_paddle_ocr(image,
langs=...) so PaddleOCR only loads/runs the language(s) actually present on
the page, instead of always brute-forcing every language in OCR_LANGS.

This is a pure latency optimization, not a correctness requirement:
  - If detection succeeds and is confident: PaddleOCR runs ONCE, with just
    the detected language(s) — faster than the old default multi-language
    sweep when OCR_LANGS="ar,en" was always trying both regardless of what
    was actually on the page.
  - If detection is ambiguous (neither language clearly wins): both "ar"
    and "en" are passed, matching the OLD default behavior exactly.
  - If CLIP isn't installed, or detection raises ANY exception for any
    reason (missing model weights, corrupt image, etc.): we catch it here,
    log a warning, and fall through to passing langs=None — which makes
    run_paddle_ocr() use its own original OCR_LANGS/OCR_LANG env-based
    defaults, completely unchanged from before this revision. Detection
    failing NEVER fails the document.

Toggle with OCR_LANGUAGE_DETECTION=true|false in .env (default: true).
Set to false to skip detection entirely and always use the env defaults,
e.g. while debugging or if CLIP isn't installed in this environment.

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
import os
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

# Enable/disable the new pre-OCR language detection step. Default ON.
# Set OCR_LANGUAGE_DETECTION=false in .env to skip detection entirely and
# always fall back to PaddleOCR's own OCR_LANGS/OCR_LANG env defaults —
# i.e. the exact behavior this project had before this revision.
LANGUAGE_DETECTION_ENABLED: bool = (
    _os.getenv("OCR_LANGUAGE_DETECTION", "true").strip().lower() == "true"
)


def _detect_langs_safe(image: Image.Image) -> list[str] | None:
    """
    Runs pre-OCR language detection and returns a list like ["ar"],
    ["en"], or ["ar", "en"] — or None if detection is disabled, CLIP
    isn't installed, or detection fails for any reason.

    Returning None here is the signal to run_paddle_ocr() to fall back
    to its own OCR_LANGS/OCR_LANG env-based defaults, so a detection
    failure can never break OCR — it just disables the optimization for
    that one page.
    """
    if not LANGUAGE_DETECTION_ENABLED:
        return None

    try:
        from ocr_service.preprocessing.language_detector import (
            detect_languages_for_ocr_pil_image,
        )
        langs = detect_languages_for_ocr_pil_image(image)
        logger.info("Language detection: %s", langs)
        return langs
    except Exception as exc:
        # Covers: CLIP not installed, model download failure, corrupt
        # image, or any other detection-time error. Never block OCR.
        logger.warning(
            "Pre-OCR language detection failed (%s: %s) — falling back to "
            "OCR_LANGS/OCR_LANG env defaults for this page.",
            exc.__class__.__name__, exc,
        )
        return None


@dataclass
class OCRResult:
    text:             str
    model_used:       str        # "paddle" | "surya" | "paddle+surya"
    confidence_score: float
    paddle_score:     float | None = None
    surya_score:      float | None = None
    processing_time_ms: float = 0.0
    decision_reason:  str = ""
    detected_langs:   list[str] | None = None  # set by pre-OCR language detection; None = detection skipped/failed


def route_ocr(image: Image.Image) -> OCRResult:
    """
    Main routing function.

    Args:
        image: Preprocessed PIL Image.

    Returns:
        OCRResult with the best extracted text and metadata.
        OCRResult.detected_langs carries the language(s) CLIP detected
        before OCR ran (e.g. ["ar"], ["en"], ["ar","en"]), or None if
        detection was disabled or failed (in which case PaddleOCR used
        its own OCR_LANGS/OCR_LANG env defaults, unchanged from before).
    """
    t_start = time.perf_counter()

    # ── Step 0: Pre-OCR language detection (CLIP-based) ───────────────
    # detect_langs is either a list like ["ar"] / ["en"] / ["ar","en"],
    # or None if detection was skipped/failed.
    # run_paddle_ocr(image, langs=detect_langs) uses it when not None,
    # and falls back to OCR_LANGS/OCR_LANG env defaults when None —
    # so either way PaddleOCR always runs, detection failure is silent.
    detected_langs = _detect_langs_safe(image)

    # ── Step 1: Run PaddleOCR ─────────────────────────────────────────
    logger.info("Running PaddleOCR...")
    paddle_raw   = run_paddle_ocr(image, langs=detected_langs)
    paddle_score = score_paddle_result(paddle_raw)

    logger.info("PaddleOCR score: %.3f (threshold=%.2f)", paddle_score, CONFIDENCE_THRESHOLD)

    # ── Step 2: Early exit if confident enough ─────────────────────
    if paddle_score >= CONFIDENCE_THRESHOLD:
        elapsed = (time.perf_counter() - t_start) * 1000
        reason  = f"PaddleOCR early exit (score={paddle_score:.3f} >= {CONFIDENCE_THRESHOLD})"
        logger.info(reason)
        return OCRResult(
            text               = paddle_raw["text"],
            model_used         = "paddle",
            confidence_score   = paddle_score,
            paddle_score       = paddle_score,
            surya_score        = None,
            processing_time_ms = round(elapsed, 1),
            decision_reason    = reason,
            detected_langs     = detected_langs,
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
            text               = paddle_raw["text"],
            model_used         = "paddle",
            confidence_score   = paddle_score,
            paddle_score       = paddle_score,
            surya_score        = None,
            processing_time_ms = round(elapsed, 1),
            decision_reason    = reason,
            detected_langs     = detected_langs,
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
        text               = winner_text,
        model_used         = winner_model,
        confidence_score   = winner_score,
        paddle_score       = paddle_score,
        surya_score        = surya_score,
        processing_time_ms = round(elapsed, 1),
        decision_reason    = reason,
        detected_langs     = detected_langs,
    )