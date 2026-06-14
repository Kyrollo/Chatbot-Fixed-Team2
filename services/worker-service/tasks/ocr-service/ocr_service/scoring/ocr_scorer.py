"""
scoring/ocr_scorer.py
----------------------
Scoring system for OCR output quality.

Two separate scoring functions — one per engine — because each engine
exposes different signals:

PaddleOCR: confidence scores per detected word → use them directly.
Surya:     no per-word confidence in all versions → infer quality from
           text properties (density, language consistency, noise level).

Both functions return a float in [0, 1]. Higher = better output.
"""
from __future__ import annotations

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# ─── Arabic/English character ranges ──────────────────────────────
_ARABIC_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')
_LATIN_RE  = re.compile(r'[A-Za-z]')
_DIGIT_RE  = re.compile(r'[0-9٠-٩]')      # ASCII + Arabic-Indic digits
_NOISE_RE  = re.compile(r'[^\w\s\u0600-\u06FF.,،؟!؟:()\-/]', re.UNICODE)


# ──────────────────────────────────────────────────────────────────
# PaddleOCR scorer
# ──────────────────────────────────────────────────────────────────

def score_paddle_result(result: dict) -> float:
    """
    Scores PaddleOCR output using two signals:

    1. Average confidence  (weight 0.7)
       — PaddleOCR provides a confidence score per detected word.
       — High confidence → the model is certain about its detections.

    2. Valid word ratio    (weight 0.3)
       — Ratio of words containing Arabic/Latin/digit characters.
       — Penalizes outputs full of symbols, punctuation noise, or gibberish.

    Returns:
        float in [0, 1]
    """
    words = result.get("words", [])
    text  = result.get("text",  "")

    if not words and not text.strip():
        return 0.0

    # Signal 1: average confidence
    if words:
        avg_confidence = sum(w["confidence"] for w in words) / len(words)
    else:
        avg_confidence = 0.0

    # Signal 2: valid word ratio
    valid_word_ratio = _compute_valid_word_ratio(text)

    score = 0.7 * avg_confidence + 0.3 * valid_word_ratio

    logger.debug(
        "PaddleOCR score: %.3f  (conf=%.3f, valid_ratio=%.3f)",
        score, avg_confidence, valid_word_ratio,
    )
    return float(score)


# ──────────────────────────────────────────────────────────────────
# Surya scorer
# ──────────────────────────────────────────────────────────────────

def score_surya_result(result: dict) -> float:
    """
    Scores Surya OCR output using text-property signals:

    1. Text density       (weight 0.35)
       — Amount of meaningful text relative to detected tokens.
       — Short/empty outputs score low.

    2. Language consistency (weight 0.40)
       — Ratio of Arabic + Latin + digit characters in total text.
       — Higher ratio = fewer random symbols or noise characters.

    3. Noise penalty      (weight 0.25)
       — Penalizes strings with high noise character density.

    If Surya provides per-line confidence, incorporates it as a
    4th signal (blended in, replacing density).

    Returns:
        float in [0, 1]
    """
    words = result.get("words", [])
    text  = result.get("text",  "")

    if not text.strip():
        return 0.0

    # Signal 1: text density
    density_score = _compute_text_density(text)

    # Signal 2: language consistency
    lang_score = _compute_language_consistency(text)

    # Signal 3: noise penalty (inverted — high noise → low score)
    noise_score = 1.0 - _compute_noise_ratio(text)

    # Signal 4: confidence (if available from Surya)
    conf_scores = [w["confidence"] for w in words if w.get("confidence", 0) > 0]
    if conf_scores:
        avg_conf = sum(conf_scores) / len(conf_scores)
        score = (0.25 * avg_conf + 0.25 * density_score
                 + 0.30 * lang_score + 0.20 * noise_score)
    else:
        score = 0.35 * density_score + 0.40 * lang_score + 0.25 * noise_score

    logger.debug(
        "Surya score: %.3f  (density=%.3f, lang=%.3f, noise_inv=%.3f)",
        score, density_score, lang_score, noise_score,
    )
    return float(score)


# ──────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────

def _compute_valid_word_ratio(text: str) -> float:
    """Fraction of space-separated tokens that contain at least one
    Arabic, Latin, or digit character."""
    tokens = text.split()
    if not tokens:
        return 0.0
    valid = sum(
        1 for t in tokens
        if _ARABIC_RE.search(t) or _LATIN_RE.search(t) or _DIGIT_RE.search(t)
    )
    return valid / len(tokens)


def _compute_text_density(text: str) -> float:
    """
    Normalised measure of how much meaningful text was extracted.
    Score is based on character count, capped to avoid rewarding
    runaway/garbage-filled outputs.
    """
    stripped = text.strip()
    char_count = len(stripped.replace(" ", "").replace("\n", ""))
    # Sigmoid-like mapping: 500+ chars → ~1.0
    return min(1.0, char_count / 500)


def _compute_language_consistency(text: str) -> float:
    """Ratio of Arabic + Latin + digit characters in total non-space chars."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    meaningful = sum(
        1 for c in chars
        if _ARABIC_RE.match(c) or _LATIN_RE.match(c) or _DIGIT_RE.match(c)
        or c in '.,،؟!؟:()\-/'
    )
    return meaningful / len(chars)


def _compute_noise_ratio(text: str) -> float:
    """Fraction of characters that look like noise symbols."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    noise_chars = len(_NOISE_RE.findall(text))
    return min(1.0, noise_chars / len(chars))