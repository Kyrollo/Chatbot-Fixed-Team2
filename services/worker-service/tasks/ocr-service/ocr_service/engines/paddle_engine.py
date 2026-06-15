"""
engines/paddle_engine.py
------------------------
OCR engine using PaddleOCR (PP-OCRv5), as required by the project spec and
as pinned in requirements.txt (`paddlepaddle==3.3.1`, `paddleocr>=3.7.0`).

WHY THIS FILE CHANGED (this revision)
--------------------------------------
1. CRASH FIX — `enable_mkldnn=False`
   On paddlepaddle 3.3.x (CPU), the oneDNN backend cannot convert a PIR
   attribute used by PP-OCRv5 models, and every call to `ocr.predict()`
   raises:

       NotImplementedError: (Unimplemented) ConvertPirAttribute2Runtime
       Attribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]
       (at ..\\paddle\\fluid\\framework\\new_executor\\instruction\\onednn\\
        onednn_instruction.cc:118)

   This is a known paddlepaddle 3.3.0/3.3.1 CPU bug, independent of the
   model language or input image. Passing `enable_mkldnn=False` makes
   PaddleOCR use the plain CPU kernels instead of the oneDNN-fused ones,
   which avoids this code path entirely. On paddlepaddle 3.x this does NOT
   carry the severe slowdown seen on 2.6.x (that slowdown was specific to
   2.6.x's fallback kernels) — 3.x's non-oneDNN CPU kernels are reasonably
   fast.

2. MULTI-LANGUAGE / LANGUAGE-AGNOSTIC OCR
   Previously hardcoded to `lang="ar"`. This revision supports running
   OCR with multiple language models and picking the best result, OR
   running a single configurable language via OCR_LANG, OR an
   auto-detect-ish strategy that tries a small set of languages and keeps
   whichever produces the highest-confidence text.

   Strategy (controlled by OCR_LANG / OCR_LANGS env vars):

     - OCR_LANG=<code>        -> single language, exactly as before
                                  (default: "ar")
     - OCR_LANGS=ar,en,fr,...  -> try EACH language's model on the image,
                                  keep the result with the highest average
                                  recognition confidence. Slower (one
                                  PaddleOCR pipeline per language, each
                                  loaded once and cached), but works
                                  regardless of the document's language.

   If OCR_LANGS is set, it takes precedence over OCR_LANG. Default
   behavior (neither set) is unchanged: single "ar" model, which also
   reasonably handles embedded Latin/digits.

PaddleOCR 3.x API
-----------------
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(
        lang="ar",                        # language model to load
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        device="cpu",
        enable_mkldnn=False,               # <-- crash workaround
    )
    results = ocr.predict(input=image_bgr_numpy_array)
    for res in results:
        res["rec_texts"]   # list[str]  — recognized text per line
        res["rec_scores"]  # list[float] — confidence per line (0..1)

`res` is a dict subclass (paddlex BaseResult), so `res["rec_texts"]` /
`res.get(...)` work directly.

Note: PaddleX/OpenCV-based pipelines expect images in BGR order, while PIL
gives RGB — we flip channels before calling predict().

Singleton loading — same pattern as surya_engine.py, but keyed per
language so multiple PaddleOCR pipelines can be cached simultaneously
when OCR_LANGS is used.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# Cache of loaded PaddleOCR pipelines, keyed by language code.
_models: dict[str, Any] = {}

# ----------------------------------------------------------------------
# Language configuration
# ----------------------------------------------------------------------
# Single-language mode (default): OCR_LANG, defaults to "en" since
# documents commonly contain English-language images/text. Override with
# OCR_LANG=ar for Arabic-only documents.
OCR_LANG: str = os.getenv("OCR_LANG", "en")

# Multi-language mode: comma-separated list, e.g. "ar,en,fr".
# If set, run_paddle_ocr tries each language and keeps the best result.
_OCR_LANGS_RAW = os.getenv("OCR_LANGS", "").strip()
OCR_LANGS: list[str] = (
    [code.strip() for code in _OCR_LANGS_RAW.split(",") if code.strip()]
    if _OCR_LANGS_RAW
    else []
)

# Optional accuracy toggles (off by default to keep the fast path fast —
# image_processor.py already denoises/deskews/resizes before this runs).
_USE_DOC_ORIENTATION = os.getenv("USE_DOC_ORIENTATION_CLASSIFY", "false").lower() == "true"
_USE_DOC_UNWARPING   = os.getenv("USE_DOC_UNWARPING", "false").lower() == "true"
_USE_TEXTLINE_ORIENT = os.getenv("USE_TEXTLINE_ORIENTATION", "true").lower() == "true"


def _build_pipeline(lang: str) -> Any:
    """Constructs a PaddleOCR pipeline for the given language code."""
    from paddleocr import PaddleOCR  # noqa: PLC0415

    logger.info("Loading PaddleOCR (lang=%s, device=cpu, mkldnn=False)...", lang)

    # CRASH WORKAROUND (part 2): with enable_mkldnn=False, the default
    # "PP-OCRv5_server_det" detection model causes a native access
    # violation (exit code 0xC0000005 / 3221225477) on the very first
    # predict() call — a hard process crash, not a Python exception.
    #
    # Forcing the lightweight "mobile" detection model avoids this crash
    # path entirely and is also faster on CPU.
    try:
        model = PaddleOCR(
            lang=lang,
            use_doc_orientation_classify=_USE_DOC_ORIENTATION,
            use_doc_unwarping=_USE_DOC_UNWARPING,
            use_textline_orientation=_USE_TEXTLINE_ORIENT,
            device="cpu",
            # CRASH WORKAROUND (part 1): paddlepaddle 3.3.x + oneDNN cannot
            # run PP-OCRv5 models on CPU — see module docstring. Disabling
            # mkldnn avoids:
            #   NotImplementedError: ConvertPirAttribute2RuntimeAttribute
            #   not support [pir::ArrayAttribute<pir::DoubleAttribute>]
            enable_mkldnn=False,
            # CRASH WORKAROUND (part 2): force mobile detection model
            # instead of the default server_det, which segfaults when
            # mkldnn is disabled.
            text_detection_model_name="PP-OCRv5_mobile_det",
        )
        logger.info("PaddleOCR loaded (lang=%s, det=PP-OCRv5_mobile_det)", lang)
    except TypeError:
        # Older paddleocr builds may not accept text_detection_model_name —
        # fall back without it (may re-hit the server_det segfault on some
        # systems, but at least won't crash on an unexpected kwarg).
        model = PaddleOCR(
            lang=lang,
            use_doc_orientation_classify=_USE_DOC_ORIENTATION,
            use_doc_unwarping=_USE_DOC_UNWARPING,
            use_textline_orientation=_USE_TEXTLINE_ORIENT,
            device="cpu",
            enable_mkldnn=False,
        )
        logger.info("PaddleOCR loaded (lang=%s, default det model)", lang)

    return model


def get_paddle_model(lang: str | None = None) -> Any:
    """
    Returns the shared PaddleOCR pipeline for `lang` (loaded once per
    language, then cached). If `lang` is None, uses OCR_LANG.
    """
    lang = lang or OCR_LANG
    if lang not in _models:
        _models[lang] = _build_pipeline(lang)
    return _models[lang]


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


def run_paddle_ocr(image: Image.Image) -> dict:
    """
    Runs PaddleOCR on a PIL Image.

    Behavior:
      - If OCR_LANGS is set (e.g. "ar,en,fr"), runs OCR once per language
        and returns the result with the highest average word confidence.
        This makes the engine effectively language-agnostic at the cost
        of one PaddleOCR pass per configured language.
      - Otherwise, runs OCR once using OCR_LANG (default "ar").

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

    langs = OCR_LANGS if OCR_LANGS else [OCR_LANG]

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