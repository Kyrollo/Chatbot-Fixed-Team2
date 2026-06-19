"""
engines/paddle_engine.py
------------------------
OCR engine using PaddleOCR (PP-OCRv5), running on the ONNX Runtime backend.

WHAT CHANGED IN THIS REVISION
------------------------------
1. ONNX RUNTIME BACKEND (speed + accuracy consistency)
   PaddleOCR/PaddleX supports swapping its inference backend via the
   `engine` kwarg. Previously this project ran on the default native
   Paddle backend (`engine=None` -> "paddle"), which on paddlepaddle 3.3.x
   CPU has a oneDNN/PIR bug (see surya_engine.py-era notes / git history):

       NotImplementedError: ConvertPirAttribute2RuntimeAttribute not
       support [pir::ArrayAttribute<pir::DoubleAttribute>]

   which we previously worked around with `enable_mkldnn=False` (slower,
   plain CPU kernels, no oneDNN fusion).

   Switching to `engine="onnxruntime"` avoids that whole code path (ONNX
   Runtime never goes through Paddle's oneDNN/PIR executor) and is
   generally FASTER on CPU than un-fused Paddle kernels, while producing
   numerically equivalent results to the original Paddle model (same
   weights, same graph, just a different inference runtime). This is the
   officially supported PaddleOCR 3.x ONNX deployment path — PaddleX
   converts the Paddle model to ONNX automatically (via Paddle2ONNX) the
   first time each model is loaded with `engine="onnxruntime"`, caches the
   .onnx file under `~/.paddlex/`, and reuses it on every subsequent load
   (including subsequent runs/process restarts) — exactly like the
   original .pdiparams/.pdmodel files were already cached.

   Requires the `onnxruntime` package (CPU build) — see requirements.txt.

2. EAGER WARM-UP / EXPLICIT CACHING (warm_up_paddle_models)
   Previously, each PaddleOCR pipeline (one per language) was lazily
   built on its FIRST use inside route_ocr() / run_paddle_ocr(), i.e. the
   first image of that language paid the full model-load cost mid-request.

   This revision adds `warm_up_paddle_models()`, called once at worker
   startup (see ocr_router.py / pipeline.py wiring), which eagerly builds
   and caches the PaddleOCR pipeline for every language in OCR_WARMUP_LANGS
   (default: "ar,en"). Once warmed, `get_paddle_model(lang)` / 
   `run_paddle_ocr(...)` for any of those languages is served instantly
   from the in-memory `_models` cache — no reload happens unless a NEW
   language (not already in `_models` and not already warmed) is
   requested, in which case it's loaded once, on demand, and cached the
   same way from then on.

KNOWN BUG — disable oneDNN (enable_mkldnn=False) — STILL APPLIED
-------------------------------------------------------------------
We still pass `enable_mkldnn=False` defensively even on the ONNX Runtime
engine: PaddleX's preprocessing/postprocessing steps around the ONNX
session (resize, NMS-like box ops) can still touch the Paddle CPU runtime
for some pipeline components depending on version, so we keep this
workaround in place. It is a no-op extra safety net, not a requirement for
the ONNX path itself.

PaddleOCR 3.x API
-----------------
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        lang="ar",
        engine="onnxruntime",              # <-- ONNX Runtime backend
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        device="cpu",
        enable_mkldnn=False,
    )
    results = ocr.predict(input=image_bgr_numpy_array)
    for res in results:
        res["rec_texts"]   # list[str]  — recognized text per line
        res["rec_scores"]  # list[float] — confidence per line (0..1)

`res` is a dict subclass (paddlex BaseResult), so `res["rec_texts"]` /
`res.get(...)` work directly.

Note: PaddleX/OpenCV-based pipelines expect images in BGR order, while PIL
gives RGB — we flip channels before calling predict().

Language
--------
  - OCR_WARMUP_LANGS=ar,en   (default) — languages eagerly loaded at
    startup via warm_up_paddle_models(). Both stay resident/cached for
    the life of the process.
  - OCR_LANG=<code>           (default "en") — language used by
    run_paddle_ocr() when OCR_LANGS (plural) is NOT set.
  - OCR_LANGS=ar,en,fr,...    — if set, run_paddle_ocr() tries EACH
    language's model on the image and keeps the highest-confidence
    result (language-agnostic mode, slower per image).

Singleton loading — pipelines are cached per language in `_models`, so a
language already warmed (or already used once) is never reloaded.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# Cache of loaded PaddleOCR pipelines, keyed by language code.
# A language only ever gets built once per process; everything after that
# is served from this dict.
_models: dict[str, Any] = {}

# ----------------------------------------------------------------------
# Language configuration
# ----------------------------------------------------------------------
# Single source of truth: OCR_DETECT_LANGS drives detection (language_detector.py),
# warm-up, AND the multi-language fallback sweep — so adding a language
# to OCR_DETECT_LANGS in .env automatically propagates everywhere with
# no code changes needed in any file.
#
# Individual overrides are still supported for fine-grained control:
#   OCR_WARMUP_LANGS  — if set, overrides which langs are eagerly loaded at
#                       worker startup (default: same as OCR_DETECT_LANGS)
#   OCR_LANGS         — if set, overrides the fallback sweep langs when
#                       language detection is disabled or fails
#                       (default: same as OCR_DETECT_LANGS)
#   OCR_LANG          — single-language mode when OCR_LANGS is empty
#                       (default: "en")
#
# Typical .env for Arabic + English + French:
#   OCR_DETECT_LANGS=ar,en,fr     ← one line controls everything
# ----------------------------------------------------------------------

# The base language list — everything else defaults to this.
_OCR_DETECT_LANGS_RAW: str = os.getenv("OCR_DETECT_LANGS", "ar,en")
_OCR_DETECT_LANGS: list[str] = [
    c.strip() for c in _OCR_DETECT_LANGS_RAW.split(",") if c.strip()
]

# Languages eagerly loaded by warm_up_paddle_models() at process startup.
# Default: same as OCR_DETECT_LANGS so warm-up always covers detected langs.
OCR_WARMUP_LANGS: list[str] = [
    code.strip()
    for code in os.getenv("OCR_WARMUP_LANGS", _OCR_DETECT_LANGS_RAW).split(",")
    if code.strip()
]

# Single-language mode (default): OCR_LANG, defaults to "en".
OCR_LANG: str = os.getenv("OCR_LANG", "en")

# Multi-language fallback sweep: used when detection is disabled or fails.
# Default: same as OCR_DETECT_LANGS so the fallback covers the same set.
_OCR_LANGS_RAW = os.getenv("OCR_LANGS", _OCR_DETECT_LANGS_RAW).strip()
OCR_LANGS: list[str] = (
    [code.strip() for code in _OCR_LANGS_RAW.split(",") if code.strip()]
    if _OCR_LANGS_RAW
    else []
)

# Inference engine: "onnxruntime" (default, fast + avoids the oneDNN/PIR
# crash) or "paddle" (original native backend) — override via .env if
# onnxruntime ever needs to be disabled for debugging.
OCR_ENGINE: str = os.getenv("OCR_ENGINE", "onnxruntime")

# Optional accuracy toggles (off by default to keep the fast path fast —
# image_processor.py already denoises/deskews/resizes before this runs).
_USE_DOC_ORIENTATION = os.getenv("USE_DOC_ORIENTATION_CLASSIFY", "false").lower() == "true"
_USE_DOC_UNWARPING   = os.getenv("USE_DOC_UNWARPING", "false").lower() == "true"
_USE_TEXTLINE_ORIENT = os.getenv("USE_TEXTLINE_ORIENTATION", "true").lower() == "true"


def _build_pipeline(lang: str) -> Any:
    """Constructs a PaddleOCR pipeline for the given language code."""
    from paddleocr import PaddleOCR  # noqa: PLC0415

    logger.info(
        "Loading PaddleOCR (lang=%s, device=cpu, engine=%s)...", lang, OCR_ENGINE
    )

    common_kwargs = dict(
        lang=lang,
        use_doc_orientation_classify=_USE_DOC_ORIENTATION,
        use_doc_unwarping=_USE_DOC_UNWARPING,
        use_textline_orientation=_USE_TEXTLINE_ORIENT,
        device="cpu",
        # Defensive — see module docstring. Cheap no-op on the ONNX path.
        enable_mkldnn=False,
    )
    if OCR_ENGINE:
        common_kwargs["engine"] = OCR_ENGINE

    # CRASH WORKAROUND: with enable_mkldnn=False on the native "paddle"
    # engine, the default "PP-OCRv5_server_det" detection model can cause
    # a native access violation on the very first predict() call. Forcing
    # the lightweight "mobile" detection model avoids that crash path and
    # is also faster/lighter — used regardless of which engine we're on.
    try:
        model = PaddleOCR(
            **common_kwargs,
            text_detection_model_name="PP-OCRv5_mobile_det",
        )
        logger.info(
            "PaddleOCR loaded (lang=%s, engine=%s, det=PP-OCRv5_mobile_det)",
            lang, OCR_ENGINE,
        )
    except TypeError:
        # Older paddleocr builds may not accept text_detection_model_name.
        model = PaddleOCR(**common_kwargs)
        logger.info(
            "PaddleOCR loaded (lang=%s, engine=%s, default det model)",
            lang, OCR_ENGINE,
        )
    except Exception:
        if OCR_ENGINE == "onnxruntime":
            # ONNX Runtime path failed (e.g. onnxruntime package missing,
            # or this paddleocr/paddlex build doesn't support the engine
            # kwarg). Fall back to the native Paddle engine rather than
            # crashing the whole worker — slower, but still functional.
            logger.exception(
                "Failed to load PaddleOCR with engine='onnxruntime' for "
                "lang=%s — falling back to the native 'paddle' engine. "
                "Check that the 'onnxruntime' package is installed.",
                lang,
            )
            common_kwargs.pop("engine", None)
            try:
                model = PaddleOCR(
                    **common_kwargs,
                    text_detection_model_name="PP-OCRv5_mobile_det",
                )
            except TypeError:
                model = PaddleOCR(**common_kwargs)
            logger.info("PaddleOCR loaded (lang=%s, engine=paddle [fallback])", lang)
        else:
            raise

    return model


def get_paddle_model(lang: str | None = None) -> Any:
    """
    Returns the shared PaddleOCR pipeline for `lang` (built once per
    language, then cached for the lifetime of the process). If `lang` is
    None, uses OCR_LANG.

    A language already warmed via warm_up_paddle_models() (or already
    requested once before) is returned instantly from cache — it is never
    rebuilt. Only a genuinely new, not-yet-cached language triggers a load.
    """
    lang = lang or OCR_LANG
    if lang not in _models:
        _models[lang] = _build_pipeline(lang)
    return _models[lang]


def warm_up_paddle_models(langs: list[str] | None = None) -> None:
    """
    Eagerly loads and caches PaddleOCR pipelines for `langs` (default:
    OCR_WARMUP_LANGS, i.e. "ar,en" unless overridden via .env).

    Call this ONCE at worker/process startup (see worker bootstrap /
    pipeline.py) so the very first real image processed doesn't pay the
    model-load latency. Languages already cached (e.g. warmed twice by
    mistake, or already used) are skipped — this is always safe to call
    more than once.
    """
    targets = langs if langs is not None else OCR_WARMUP_LANGS
    if not targets:
        return

    logger.info("Warming up PaddleOCR for languages: %s", targets)
    for lang in targets:
        if lang in _models:
            logger.debug("PaddleOCR lang=%s already cached — skipping warm-up", lang)
            continue
        try:
            get_paddle_model(lang)
        except Exception:
            logger.exception("Warm-up failed for PaddleOCR lang=%s", lang)
    logger.info("PaddleOCR warm-up complete. Cached languages: %s", list(_models.keys()))


def _run_single_language(img_bgr, lang: str) -> dict:
    """Runs PaddleOCR for one language and returns the standard result dict."""
    ocr = get_paddle_model(lang)
    results = list(ocr.predict(input=img_bgr))

    words = []
    lines = []

    if results:
        res        = results[0]
        rec_texts  = res.get("rec_texts", []) or []
        rec_scores = res.get("rec_scores", []) or []

        for text, score in zip(rec_texts, rec_scores):
            text = (text or "").strip()
            if text:
                words.append({"text": text, "confidence": float(score)})
                lines.append(text)

    return {
        "text":       "\n".join(lines),
        "words":      words,
        "raw_result": results[0] if results else {},
        "lang":       lang,
    }


def run_paddle_ocr(image: Image.Image, langs: list[str] | None = None) -> dict:
    """
    Runs PaddleOCR on a PIL Image.

    Behavior:
      - If `langs` is passed in (e.g. from pre-OCR language detection in
        ocr_router.py — see language_detector.py), those languages are
        used instead of the env-based defaults below. This lets the
        router skip languages it already knows aren't present on the
        page, instead of always brute-forcing every configured language.
      - Otherwise (langs=None, i.e. detection wasn't used or failed):
          - If OCR_LANGS is set (e.g. "ar,en,fr"), runs OCR once per
            language and returns the result with the highest average
            word confidence. This makes the engine effectively
            language-agnostic at the cost of one PaddleOCR pass per
            configured language.
          - Otherwise, runs OCR once using OCR_LANG (default "en").

    All languages used here are served from the warm cache built by
    warm_up_paddle_models() at startup, UNLESS a language outside
    OCR_WARMUP_LANGS is requested — in that case it is loaded once, on
    demand, and cached from then on (see get_paddle_model).

    Returns the dict shape the rest of the pipeline expects (consumed by
    ocr_scorer.score_paddle_result and ocr_router.route_ocr):
        {
            "text":       "full extracted text as single string",
            "words":      [{"text": str, "confidence": float}, ...],
            "raw_result": <native PaddleOCR result dict>,
            "lang":       "<language code that produced this result>",
        }
    """
    import numpy as np

    # PIL gives RGB; PaddleX/OpenCV-based pipelines expect BGR.
    img_rgb = np.array(image.convert("RGB"))
    img_bgr = img_rgb[:, :, ::-1]

    if langs:
        # Caller (ocr_router.py) already decided which language(s) this
        # page needs — e.g. via language_detector.py's CLIP-based
        # detection — so use exactly that instead of the env defaults.
        resolved_langs = langs
    else:
        resolved_langs = OCR_LANGS if OCR_LANGS else [OCR_LANG]

    langs = resolved_langs

    if len(langs) == 1:
        return _run_single_language(img_bgr, langs[0])

    # Multi-language mode: try each, keep the best by average confidence.
    best: dict | None = None
    best_avg_conf = -1.0

    for lang in langs:
        try:
            result = _run_single_language(img_bgr, lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PaddleOCR failed for lang=%s: %s", lang, exc)
            continue

        words = result["words"]
        avg_conf = (
            sum(w["confidence"] for w in words) / len(words) if words else 0.0
        )

        logger.debug(
            "PaddleOCR lang=%s -> %d words, avg_confidence=%.3f",
            lang, len(words), avg_conf,
        )

        # Prefer higher average confidence; on ties, prefer more text.
        if (
            best is None
            or avg_conf > best_avg_conf
            or (avg_conf == best_avg_conf and len(result["text"]) > len(best["text"]))
        ):
            best = result
            best_avg_conf = avg_conf

    if best is None:
        # All languages failed — return an empty result rather than raising,
        # so ocr_router can still fall back to Surya.
        return {"text": "", "words": [], "raw_result": {}, "lang": None}

    logger.info(
        "PaddleOCR multi-lang: selected lang=%s (avg_confidence=%.3f) from %s",
        best["lang"], best_avg_conf, langs,
    )
    return best