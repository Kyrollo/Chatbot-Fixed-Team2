"""
Pre-OCR language detection helpers.

Detects which language(s) are on a page using CLIP (ViT-B/32) before OCR
runs, so PaddleOCR only loads/runs the model(s) actually needed.

ADDING A NEW LANGUAGE
---------------------
Only one thing needs to change: add the language code to OCR_DETECT_LANGS
in your .env file.  No code changes required anywhere.

    OCR_DETECT_LANGS=ar,en,fr,de

The mapping from language code → CLIP prompt label is defined in
_LANG_CLIP_LABELS below.  If you add a code that isn't in that dict yet,
add it there too — that's the only code touch needed, and it's a one-liner.

CLIP prompt labels must be natural English descriptions of what that script
looks like on a page, because CLIP was trained on English captions.
"""

import io
import os
from typing import BinaryIO, Dict, List


# ------------------------------------------------------------------
# Language code → CLIP prompt label
# ------------------------------------------------------------------
# This is the ONLY place a new language needs a code-level entry.
# Add one line here when you add a new language code to OCR_DETECT_LANGS.
# The label is what CLIP sees — keep it a plain English description of the
# script/language as it appears visually on a page.
# ------------------------------------------------------------------
_LANG_CLIP_LABELS: dict[str, str] = {
    "ar": "Arabic text",
    "en": "English text",
    "fr": "French text",
    "de": "German text",
    "es": "Spanish text",
    "zh": "Chinese text",
    "ja": "Japanese text",
    "ko": "Korean text",
    "ru": "Russian text",
    "tr": "Turkish text",
    "fa": "Persian text",
    "ur": "Urdu text",
    "hi": "Hindi text",
    "it": "Italian text",
    "pt": "Portuguese text",
}

# ------------------------------------------------------------------
# Build LANGUAGE_PROMPTS from OCR_DETECT_LANGS env var at startup.
# Default: "ar,en" — matches the original hardcoded behavior.
# To add a language: OCR_DETECT_LANGS=ar,en,fr   (no code changes needed)
# ------------------------------------------------------------------
_DETECT_LANGS_RAW: str = os.getenv("OCR_DETECT_LANGS", "ar,en")

LANGUAGE_PROMPTS: list[dict] = []
for _code in [c.strip() for c in _DETECT_LANGS_RAW.split(",") if c.strip()]:
    if _code not in _LANG_CLIP_LABELS:
        import warnings
        warnings.warn(
            f"OCR_DETECT_LANGS contains '{_code}' but no CLIP label is defined "
            f"for it in language_detector._LANG_CLIP_LABELS. "
            f"This language will be skipped in detection. "
            f"Add a label entry to _LANG_CLIP_LABELS to enable it.",
            stacklevel=1,
        )
        continue
    LANGUAGE_PROMPTS.append({
        "name":      _code,
        "label":     _LANG_CLIP_LABELS[_code],
        "languages": [_code],
    })

_CLIP_MODEL = None
_CLIP_PREPROCESS = None
_CLIP_TOKENIZER = None

# OpenAI's pretrained ViT-B/32 weights, served through open_clip's loader.
# Same weights as the original `clip.load("ViT-B/32")` — only the loading
# code changed, not the model itself, so detection accuracy is unaffected.
_CLIP_MODEL_NAME = "ViT-B-32"
_CLIP_PRETRAINED = "openai"


def _load_clip():
    """
    Loads CLIP via open_clip_torch instead of the original (unmaintained)
    openai/CLIP package.

    WHY THE SWITCH:
    openai/CLIP's clip.load() calls torch.jit.load() and then walks
    private TorchScript internals (torch.jit._recursive.create_script_module
    -> RecursiveScriptModule._construct) that are not part of PyTorch's
    public API. Those internals get renamed/removed across torch releases
    with no deprecation warning, and openai/CLIP hasn't been updated to
    track them. On torch==2.6.0 (pinned in requirements.txt) this fails
    with:
        AttributeError: type object 'RecursiveScriptModule' has no
        attribute '_construct'
    open_clip_torch is the actively maintained successor and loads the
    same OpenAI-published ViT-B/32 weights through a plain nn.Module
    state_dict path (no torch.jit.load), so it isn't exposed to this
    class of breakage.
    """
    global _CLIP_MODEL, _CLIP_PREPROCESS, _CLIP_TOKENIZER

    if _CLIP_MODEL is not None and _CLIP_PREPROCESS is not None:
        return _CLIP_MODEL, _CLIP_PREPROCESS

    try:
        import open_clip
        import torch
    except ImportError as exc:
        raise ImportError(
            "open_clip_torch is required for pre-OCR language detection.\n"
            "Install it with:  pip install open_clip_torch"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    cache_dir = os.getenv("CLIP_MODEL_DIR")  # same env var as before, repurposed

    model, _, preprocess = open_clip.create_model_and_transforms(
        _CLIP_MODEL_NAME,
        pretrained=_CLIP_PRETRAINED,
        cache_dir=cache_dir,
    )
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(_CLIP_MODEL_NAME)

    _CLIP_MODEL, _CLIP_PREPROCESS, _CLIP_TOKENIZER = model, preprocess, tokenizer
    return _CLIP_MODEL, _CLIP_PREPROCESS


def detect_language_from_pil_image(image) -> Dict:
    """
    Detect whether an image is more likely Arabic or English text.
    """
    import torch

    model, preprocess = _load_clip()
    device = next(model.parameters()).device

    image_tensor = preprocess(image.convert("RGB")).unsqueeze(0).to(device)
    text_tensor = _CLIP_TOKENIZER([item["label"] for item in LANGUAGE_PROMPTS]).to(device)

    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
        text_features = model.encode_text(text_tensor)

        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        logits = 100.0 * image_features @ text_features.T
        probs = logits.softmax(dim=-1)[0]

    ranked = sorted(
        (
            {
                "language": item["name"],
                "prompt": item["label"],
                "score": float(prob),
                "suggested_languages": item["languages"],
            }
            for item, prob in zip(LANGUAGE_PROMPTS, probs.tolist())
        ),
        key=lambda item: item["score"],
        reverse=True,
    )
    return {"top": ranked[0], "ranked": ranked}


def detect_languages_for_ocr_pil_image(image) -> List[str]:
    return detect_language_from_pil_image(image)["top"]["suggested_languages"]


def _read_image_from_file(file: BinaryIO):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for image language detection.\n"
            "Install it with:  pip install Pillow"
        ) from exc

    current_pos = file.tell()
    data = file.read()
    file.seek(current_pos)
    return Image.open(io.BytesIO(data)).convert("RGB")


def _read_pdf_preview_from_file(file: BinaryIO):
    try:
        from pdf2image import convert_from_bytes
    except ImportError as exc:
        raise ImportError(
            "pdf2image is required for PDF language detection.\n"
            "Install it with:  pip install pdf2image"
        ) from exc

    current_pos = file.tell()
    data = file.read()
    file.seek(current_pos)
    images = convert_from_bytes(data, dpi=150, first_page=1, last_page=1, fmt="RGB")
    if not images:
        raise ValueError("Could not rasterize PDF preview for language detection.")
    return images[0]


def _read_docx_preview_from_file(file: BinaryIO):
    try:
        from docx import Document
        from PIL import Image
    except ImportError:
        return None

    current_pos = file.tell()
    data = file.read()
    file.seek(current_pos)
    doc = Document(io.BytesIO(data))

    for rel in doc.part._rels.values():
        target = getattr(rel, "target_part", None)
        if target is None:
            continue
        content_type = getattr(target, "content_type", "") or ""
        if not content_type.startswith("image/"):
            continue
        blob = getattr(target, "blob", None)
        if not blob:
            continue
        try:
            return Image.open(io.BytesIO(blob)).convert("RGB")
        except Exception:
            continue
    return None


def detect_ocr_languages(file: BinaryIO, file_type: str) -> List[str]:
    """
    Detect OCR languages from a file-like object.

    Returns a list such as ["ar"] or ["en"].
    """
    if file_type == "image":
        preview = _read_image_from_file(file)
    elif file_type == "pdf":
        preview = _read_pdf_preview_from_file(file)
    elif file_type == "docx":
        preview = _read_docx_preview_from_file(file)
        if preview is None:
            return ["ar", "en"]
    else:
        return ["ar", "en"]

    return detect_languages_for_ocr_pil_image(preview)