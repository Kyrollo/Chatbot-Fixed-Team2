import os
import fitz                      # PyMuPDF
import pytesseract
from PIL import Image
import io


# ──────────────────────────────────────────────────────────────────────────────
# Generic dispatcher — routes extraction by file extension
# ──────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str, mime_type: str | None = None) -> list[dict]:
    """
    Extracts text from a file based on its extension.
    Returns a list of dicts: [{"page": int, "text": str}, ...]

    Supported formats:
      .pdf   → PyMuPDF with OCR fallback for scanned pages
      .docx  → python-docx, segmented by headings or character count
      .csv   → pandas, grouped in batches of 10 rows
      .png/.jpg/.jpeg → Tesseract OCR on standalone images
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".csv":
        return extract_text_from_csv(file_path)
    elif ext in (".png", ".jpg", ".jpeg"):
        return extract_text_via_ocr(file_path)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")


# ──────────────────────────────────────────────────────────────────────────────
# PDF extractor (existing — unchanged)
# ──────────────────────────────────────────────────────────────────────────────

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
            print(f"  Page {page_num + 1}: no text found -> running OCR")
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


# ──────────────────────────────────────────────────────────────────────────────
# DOCX extractor (Sprint 2 — new)
# ──────────────────────────────────────────────────────────────────────────────

def extract_text_from_docx(file_path: str) -> list[dict]:
    """
    Extracts text from a .docx file using python-docx.

    Since DOCX has no fixed pages, segments are created by:
      - Heading boundaries (Heading 1, Heading 2, etc.)
      - Character count threshold (~1500 chars) as a fallback

    Returns the same format as PDF extraction:
    [{"page": segment_index, "text": "..."}, ...]
    """
    import docx

    doc = docx.Document(file_path)
    pages, current_segment, segment_index, char_count = [], [], 1, 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        current_segment.append(text)
        char_count += len(text)

        # Segment roughly by heading or character threshold (~1500 chars)
        if para.style.name.startswith("Heading") or char_count > 1500:
            pages.append({"page": segment_index, "text": "\n".join(current_segment)})
            current_segment, char_count = [], 0
            segment_index += 1

    if current_segment:
        pages.append({"page": segment_index, "text": "\n".join(current_segment)})

    print(f"  DOCX: extracted {len(pages)} segments from {os.path.basename(file_path)}")
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# CSV extractor (Sprint 2 — new)
# ──────────────────────────────────────────────────────────────────────────────

def extract_text_from_csv(file_path: str) -> list[dict]:
    """
    Extracts text from a .csv file using pandas.

    Groups rows in batches of 10. Each chunk's text is prefixed with
    column headers for context, so the embedding model can understand
    the data structure:

        Headers: ID, Name, Salary
        Row 0: 123, John Doe, $1000
        Row 1: 124, Jane Smith, $1100

    The "page" field represents the starting row number (for citation).
    """
    import pandas as pd

    df = pd.read_csv(file_path)
    headers = ", ".join(df.columns)
    pages, chunk_size = [], 10

    for i in range(0, len(df), chunk_size):
        subset = df.iloc[i:i + chunk_size]
        rows = [f"Row {idx}: " + ", ".join(str(v) for v in row.values)
                for idx, row in subset.iterrows()]
        pages.append({"page": i + 1, "text": f"Headers: {headers}\n" + "\n".join(rows)})

    print(f"  CSV: extracted {len(pages)} chunks ({len(df)} rows) from {os.path.basename(file_path)}")
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# Generic image OCR extractor (Sprint 2 — new)
# ──────────────────────────────────────────────────────────────────────────────

def extract_text_via_ocr(file_path: str) -> list[dict]:
    """
    Runs Tesseract OCR on a standalone image file (.png, .jpg, .jpeg).
    Returns a single-page result.
    """
    text = pytesseract.image_to_string(Image.open(file_path))
    text = text.strip()

    if not text:
        print(f"  OCR: no text extracted from {os.path.basename(file_path)}")

    return [{"page": 1, "text": text}] if text else []
