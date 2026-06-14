"""
preprocessing/image_processor.py
---------------------------------
Preprocessing pipeline for OCR input images.
Applies denoising, deskewing, and resizing as needed.
All operations are non-destructive — returns a new PIL Image.
"""
from __future__ import annotations

import io
import logging
import math

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def preprocess_image(image: Image.Image, *, deskew: bool = True) -> Image.Image:
    """
    Full preprocessing pipeline for an OCR-bound image.

    Steps applied (in order):
    1. Convert to RGB (handles RGBA, grayscale, palette modes)
    2. Denoise (fastNlMeansDenoisingColored)
    3. Deskew — detect and correct text rotation
    4. Adaptive resize — upscale small images, cap huge ones

    Args:
        image:   Input PIL Image (any mode).
        deskew:  Whether to apply deskew correction (default True).
                 Can be disabled for already-clean digital documents.

    Returns:
        Preprocessed PIL Image in RGB mode.
    """
    # 1. Normalize to RGB
    img = image.convert("RGB")
    arr = np.array(img)

    # 2. Denoise
    arr = _denoise(arr)

    # 3. Deskew
    if deskew:
        arr = _deskew(arr)

    # 4. Resize
    arr = _adaptive_resize(arr)

    result = Image.fromarray(arr)
    logger.debug("Preprocessing complete — size: %s", result.size)
    return result


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

def _denoise(arr: np.ndarray) -> np.ndarray:
    """
    Applies OpenCV fastNlMeansDenoisingColored.
    h=10 is a mild setting — enough to reduce scanner noise
    without blurring text characters.
    """
    try:
        denoised = cv2.fastNlMeansDenoisingColored(arr, None, h=10, hColor=10,
                                                    templateWindowSize=7,
                                                    searchWindowSize=21)
        return denoised
    except Exception as exc:
        logger.warning("Denoising skipped: %s", exc)
        return arr


def _deskew(arr: np.ndarray) -> np.ndarray:
    """
    Detects text skew angle via Hough line transform and rotates to correct it.
    Only corrects angles within ±15° to avoid over-rotating portrait/landscape
    images that are simply different orientations.
    """
    try:
        gray  = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, math.pi / 180,
                                threshold=100, minLineLength=100, maxLineGap=10)

        if lines is None or len(lines) == 0:
            return arr

        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 - x1 != 0:
                angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
                if abs(angle) < 15:   # ignore steep lines (not text baseline)
                    angles.append(angle)

        if not angles:
            return arr

        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.5:   # < 0.5° — don't bother rotating
            return arr

        logger.debug("Deskewing by %.2f°", -median_angle)
        h, w = arr.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, -median_angle, 1.0)
        rotated = cv2.warpAffine(arr, M, (w, h),
                                  flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_REPLICATE)
        return rotated

    except Exception as exc:
        logger.warning("Deskew skipped: %s", exc)
        return arr


def _adaptive_resize(arr: np.ndarray,
                     min_dim: int = 1000,
                     max_dim: int = 4096) -> np.ndarray:
    """
    Ensures the image is large enough for OCR but not excessively huge.

    - Upscale:  if the shorter side < min_dim (OCR engines need resolution)
    - Downscale: if the longer side > max_dim (avoid memory blowout)
    """
    h, w = arr.shape[:2]
    short_side = min(h, w)
    long_side  = max(h, w)

    if short_side < min_dim:
        scale  = min_dim / short_side
        new_w  = int(w * scale)
        new_h  = int(h * scale)
        arr    = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        logger.debug("Upscaled to %dx%d (scale=%.2f)", new_w, new_h, scale)

    elif long_side > max_dim:
        scale  = max_dim / long_side
        new_w  = int(w * scale)
        new_h  = int(h * scale)
        arr    = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        logger.debug("Downscaled to %dx%d (scale=%.2f)", new_w, new_h, scale)

    return arr


def pdf_page_to_image(pdf_path: str, page_num: int = 0, dpi: int = 200) -> Image.Image:
    """
    Renders a single PDF page to a PIL Image using PyMuPDF.

    Args:
        pdf_path: Path to the PDF file.
        page_num: Zero-based page index.
        dpi:      Render resolution (200 dpi is a good OCR baseline).

    Returns:
        PIL Image of the rendered page.
    """
    import fitz  # PyMuPDF — already a dependency of the parent project
    doc  = fitz.open(pdf_path)
    page = doc[page_num]
    zoom = dpi / 72          # 72 dpi is PDF's default unit
    mat  = fitz.Matrix(zoom, zoom)
    pix  = page.get_pixmap(matrix=mat)
    img  = Image.open(io.BytesIO(pix.tobytes("png")))
    doc.close()
    return img