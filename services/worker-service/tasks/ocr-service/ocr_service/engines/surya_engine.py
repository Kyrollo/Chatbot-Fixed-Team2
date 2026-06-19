"""
engines/surya_engine.py
-----------------------
Surya OCR engine wrapper.

Surya is a document-layout-aware OCR built on top of a transformer model.
It excels at structured documents, mixed Arabic/English, and low-quality scans
where PaddleOCR/EasyOCR's confidence drops.

WHAT CHANGED IN THIS REVISION
------------------------------
Added `warm_up_surya_model()`, the Surya counterpart to
`paddle_engine.warm_up_paddle_models()`. It eagerly triggers
`_load_surya_models()` once at worker/process startup so Surya's
detection + recognition (+ foundation, on newer surya-ocr) models are
already loaded into memory before the first real image arrives — instead
of paying that load cost mid-request the first time PaddleOCR's
confidence drops below threshold and ocr_router falls back to Surya.

Nothing about the actual recognition/detection logic changed — Surya is
single-model (multilingual out of the box), so there is no per-language
cache here the way paddle_engine.py has one; there is just one Surya
pipeline, loaded once, reused for every page/image for the life of the
process.

BUG FIX (ModuleNotFoundError: No module named 'surya.ocr')
------------------------------------------------------------
The previous version of this file used the old surya-ocr API:

    from surya.model.detection.model import load_model as load_det_model
    from surya.model.detection.processor import load_processor as load_det_proc
    from surya.model.recognition.model import load_model as load_rec_model
    from surya.model.recognition.processor import load_processor as load_rec_proc
    from surya.ocr import run_ocr

These modules were removed from surya-ocr a while ago. Current surya-ocr
ships a "Predictor" API instead:

    from surya.recognition import RecognitionPredictor
    from surya.detection   import DetectionPredictor
    # surya-ocr >= ~0.15 also requires a FoundationPredictor:
    from surya.foundation  import FoundationPredictor

    foundation_predictor  = FoundationPredictor()                       # >= 0.15 only
    recognition_predictor = RecognitionPredictor(foundation_predictor)  # or RecognitionPredictor() on <= 0.14
    detection_predictor   = DetectionPredictor()

    # >= ~0.14: `task_names` is optional (defaults to OCR-with-boxes)
    predictions = recognition_predictor([image], det_predictor=detection_predictor)
    # <= ~0.13 (e.g. 0.13.1, pinned in requirements.txt): `langs` is a
    # REQUIRED positional argument — one language list per image
    predictions = recognition_predictor([image], [["ar", "en"]], detection_predictor)

    # predictions[0].text_lines -> list of TextLine(text=..., confidence=...)

_load_surya_models() tries the FoundationPredictor-based constructor first
and falls back to the no-argument constructor, and run_surya_ocr() tries the
`task_names`-style call first and falls back to the `langs`-style call —
so this works across the 0.13.x, 0.14.x, and 0.15-0.17.x lines, including
the surya-ocr==0.13.1 pinned in requirements.txt.

IMPORTANT — requirements.txt
------------------------------
surya-ocr >= 0.18 ("Surya 2") reworked the API again around
`SuryaInferenceManager` and *requires* a separately running vLLM (NVIDIA GPU)
or llama.cpp server (CPU/Apple Silicon) — that's a much heavier deployment
than a local Windows/CPU setup wants. Keep the existing pin

    surya-ocr==0.13.1

(or any 0.13.x-0.17.x version) rather than upgrading to 0.18+.

Singleton loading — same pattern as paddle_engine.py.
"""
from __future__ import annotations

import logging
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

_foundation_predictor:  Any = None
_recognition_predictor: Any = None
_detection_predictor:   Any = None

# Once Surya fails to load (missing/incompatible package, model download
# failure, etc.) we remember that and stop retrying on every page — this
# keeps the pipeline fast and lets ocr_router fall back to PaddleOCR/EasyOCR
# immediately instead of re-attempting a slow model load each time.
_surya_unavailable: bool = False


def _load_surya_models() -> tuple[Any, Any]:
    """
    Loads Surya's foundation + recognition + detection predictors on first call.

    Returns:
        (recognition_predictor, detection_predictor)

    Raises:
        Exception: if surya-ocr is missing/incompatible or model weights
                    fail to download. Callers (ocr_router) should catch this
                    and fall back to PaddleOCR/EasyOCR.
    """
    global _foundation_predictor, _recognition_predictor, _detection_predictor
    global _surya_unavailable

    if _surya_unavailable:
        raise RuntimeError(
            "Surya OCR is unavailable in this process (failed on a previous "
            "call) — skipping to avoid repeated slow load attempts."
        )

    if _recognition_predictor is None:
        try:
            logger.info("Loading Surya OCR models...")

            from surya.detection   import DetectionPredictor    # noqa: PLC0415
            from surya.recognition import RecognitionPredictor  # noqa: PLC0415

            _detection_predictor = DetectionPredictor()

            try:
                # surya-ocr >= ~0.15: RecognitionPredictor needs a FoundationPredictor
                from surya.foundation import FoundationPredictor  # noqa: PLC0415
                _foundation_predictor  = FoundationPredictor()
                _recognition_predictor = RecognitionPredictor(_foundation_predictor)
            except ImportError:
                # surya-ocr <= ~0.14: RecognitionPredictor loads its own model
                _recognition_predictor = RecognitionPredictor()

            logger.info("Surya OCR models loaded")
        except Exception:
            _surya_unavailable = True
            logger.exception("Failed to load Surya OCR models — disabling Surya for this process")
            raise

    return _recognition_predictor, _detection_predictor


def warm_up_surya_model() -> None:
    """
    Eagerly loads Surya's models (foundation + recognition + detection) so
    they're already resident in memory before the first real image needs
    them — call this ONCE at worker/process startup, alongside
    paddle_engine.warm_up_paddle_models().

    If Surya fails to load (e.g. incompatible package, no internet for the
    first-time model download), this logs and swallows the error rather
    than crashing worker startup: ocr_router.py already falls back to
    PaddleOCR/EasyOCR whenever Surya is unavailable, so a failed Surya
    warm-up should not stop the worker from starting and serving requests.
    Safe to call more than once — _load_surya_models() is itself a no-op
    once already loaded (or already marked unavailable).
    """
    logger.info("Warming up Surya OCR...")
    try:
        _load_surya_models()
        logger.info("Surya OCR warm-up complete")
    except Exception:
        logger.warning(
            "Surya OCR warm-up failed — will retry lazily on first use, "
            "and ocr_router will fall back to PaddleOCR if it keeps failing."
        )


def run_surya_ocr(image: Image.Image) -> dict:
    """
    Runs Surya OCR on a PIL Image.

    Surya's RecognitionPredictor performs detection + recognition in a
    single call when given a DetectionPredictor.

    Returns:
        {
            "text":       "full extracted text as single string",
            "words":      [{"text": str, "confidence": float}, ...],
            "raw_result": <native Surya output (list[OCRResult])>,
        }
    """
    rec_predictor, det_predictor = _load_surya_models()

    try:
        # surya-ocr >= ~0.14: `task_names` is optional and defaults to
        # OCR-with-boxes, so it doesn't need to be passed at all.
        predictions = rec_predictor([image], det_predictor=det_predictor)
    except TypeError:
        # surya-ocr <= ~0.13 (e.g. 0.13.1, pinned in requirements.txt):
        # __call__(images, langs, det_predictor=None, ...) — `langs` is a
        # required positional arg: one language list per image. "ar"+"en"
        # matches this project's Arabic/English documents (same languages
        # passed to PaddleOCR's warm-up set in paddle_engine.py).
        langs = [["ar", "en"]]
        predictions = rec_predictor([image], langs, det_predictor)

    words = []
    lines = []

    if predictions:
        page_result = predictions[0]
        for text_line in page_result.text_lines:
            text       = (text_line.text or "").strip()
            confidence = float(text_line.confidence or 0.0)
            if text:
                words.append({
                    "text":       text,
                    "confidence": confidence,
                })
                lines.append(text)

    full_text = "\n".join(lines)

    return {
        "text":       full_text,
        "words":      words,
        "raw_result": predictions,
    }