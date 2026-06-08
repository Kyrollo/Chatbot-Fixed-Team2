import fitz                      # PyMuPDF
import pytesseract
from PIL import Image
import io


def extract_text_from_pdf(file_path: str) -> list[dict]:
    """
    Extracts text from every page of a PDF.
    - Normal pages  → PyMuPDF (fast, no OCR needed)
    - Scanned pages → pytesseract OCR fallback

    Returns a list of dicts:
    [
        {"page": 1, "text": "..."},
        {"page": 2, "text": "..."},
        ...
    ]
    """
    pages = []

    doc = fitz.open(file_path)

    for page_num in range(len(doc)):
        page = doc[page_num]

        # ── Try normal text extraction first ──────────────────────
        text = page.get_text().strip()

        # ── OCR fallback if page has no selectable text ───────────
        # This happens with scanned PDFs (image-only pages)
        if not text:
            print(f"  Page {page_num + 1}: no text found → running OCR")
            text = _ocr_page(page)

        if text:
            pages.append({
                "page":  page_num + 1,
                "text":  text,
            })
        else:
            print(f"  Page {page_num + 1}: no text extracted (blank or image-only)")

    doc.close()
    return pages


def _ocr_page(page) -> str:
    """
    Renders a PDF page as an image and runs Tesseract OCR on it.
    Called only when PyMuPDF finds no text on a page.
    """
    # Render page at 2x zoom for better OCR accuracy
    mat  = fitz.Matrix(2.0, 2.0)
    pix  = page.get_pixmap(matrix=mat)
    img  = Image.open(io.BytesIO(pix.tobytes("png")))

    # lang="ara+eng" supports both Arabic and English
    text = pytesseract.image_to_string(img, lang="ara+eng")
    return text.strip()
