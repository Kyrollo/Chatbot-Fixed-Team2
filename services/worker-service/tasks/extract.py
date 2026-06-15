"""
tasks/extract.py
─────────────────
Unified text extraction backend.

Routing logic
─────────────
  .pdf              → PyMuPDF for native-text pages  (fast, zero model overhead)
                      OCR pipeline for scanned pages  (PaddleOCR → Surya if needed)
  .docx             → python-docx, segmented by headings / char count
  .csv              → pandas, batched in groups of 10 rows
  .png/.jpg/.jpeg   → OCR pipeline (PaddleOCR → Surya, deskew enabled)

Public API:
    extract_text(file_path, mime_type=None) → list[{"page": int, "text": str}]
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Ensure ocr_service is importable from the ocr-service sibling directory.
#
# Project layout assumed:
#   services/
#     ocr-service/          ← ocr_service package lives here
#       ocr_service/
#         pipeline.py
#         preprocessing/
#         engines/
#         routing/
#         scoring/
#     worker-service/
#       tasks/
#         extract.py        ← this file
#
# We walk up from this file to services/ and then point at ocr-service/.
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_ocr_service_on_path() -> None:
    """Add the directory containing the ocr_service package to sys.path.

    Supports two layouts:
      1. services/worker-service/tasks/ocr-service/ocr_service   (nested copy)
      2. services/ocr-service/ocr_service                        (sibling service, default)

    We try the nested layout first (kept for backward compatibility), then
    fall back to the sibling layout described in pipeline.py / main.py.
    """
    this_file = Path(__file__).resolve()

    # Candidate 1: tasks/ocr-service/ (nested copy next to this file)
    nested_dir = this_file.parent / "ocr-service"

    # Candidate 2: services/ocr-service/ (sibling of worker-service)
    # this_file = services/worker-service/tasks/extract.py
    # -> parents[1] = services/worker-service -> parents[2] = services
    sibling_dir = this_file.parents[2] / "ocr-service"

    for candidate in (nested_dir, sibling_dir):
        if candidate.is_dir() and (candidate / "ocr_service").is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
                logger.debug("Added %s to sys.path for ocr_service imports", candidate_str)
            return

    raise ImportError(
        f"ocr-service package not found. Tried:\n"
        f"  - {nested_dir} (expects {nested_dir / 'ocr_service'})\n"
        f"  - {sibling_dir} (expects {sibling_dir / 'ocr_service'})\n"
        "Make sure the ocr_service package exists in one of these locations."
    )


_ensure_ocr_service_on_path()


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str, mime_type: str | None = None) -> list[dict]:
    """
    Extracts text from a file, routing to the correct backend by extension.

    Args:
        file_path:  Absolute path to the file on disk.
        mime_type:  Optional MIME hint (unused; kept for API compatibility).

    Returns:
        list[dict] — one dict per page/segment:
            {"page": int, "text": str}

    Raises:
        ValueError: for unsupported file extensions.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".doc":
        return _extract_doc(file_path)
    elif ext in (".xlsx", ".xls"):
        return _extract_excel(file_path)
    elif ext == ".csv":
        return _extract_csv(file_path)
    elif ext in (".png", ".jpg", ".jpeg"):
        return _extract_image(file_path)
    else:
        raise ValueError(
            f"Unsupported file extension: '{ext}'. "
            "Supported: .pdf, .docx, .doc, .xlsx, .xls, .csv, .png, .jpg, .jpeg"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helper for converting DataFrames to Markdown tables
# ──────────────────────────────────────────────────────────────────────────────

def _df_to_markdown(df) -> str:
    """Converts a pandas DataFrame into a clean Markdown table representation."""
    headers = [str(c) for c in df.columns]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for _, row in df.iterrows():
        vals = [str(v).replace("\n", " ").replace("|", "\\|") for v in row.values]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# PDF — hybrid: PyMuPDF for digital pages (table-aware), OCR for scanned pages
# ──────────────────────────────────────────────────────────────────────────────

def _extract_pdf(file_path: str) -> list[dict]:
    """
    Page-by-page PDF extraction.
    Extracts native text and tables, merging them by vertical layout order (y-coordinate).
    """
    import fitz  # PyMuPDF
    from ocr_service.pipeline import run_ocr_on_image
    from ocr_service.preprocessing.image_processor import pdf_page_to_image

    doc   = fitz.open(file_path)
    pages = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Detect tables on page
            tables = page.find_tables()
            table_blocks = []
            for t in tables:
                bbox = t.bbox  # (x0, y0, x1, y1)
                data = t.extract()
                if not data:
                    continue
                # Format table as Markdown
                lines = []
                for r_idx, row in enumerate(data):
                    vals = [str(cell or "").strip().replace("\n", " ").replace("|", "\\|") for cell in row]
                    lines.append("| " + " | ".join(vals) + " |")
                    if r_idx == 0:
                        lines.append("| " + " | ".join(["---"] * len(vals)) + " |")
                md_table = "[TABLE]\n" + "\n".join(lines) + "\n[/TABLE]"
                table_blocks.append({"bbox": bbox, "text": md_table, "y0": bbox[1]})

            # Extract native text blocks and filter out text inside tables
            raw_blocks = page.get_text("blocks")
            text_blocks = []
            for b in raw_blocks:
                if b[6] != 0:  # Skip image blocks
                    continue
                bbox = (b[0], b[1], b[2], b[3])
                text = b[4].strip()
                if not text:
                    continue
                
                # Check for significant intersection with tables to avoid duplication
                rect_b = fitz.Rect(*bbox)
                is_inside_table = False
                for t in tables:
                    rect_t = fitz.Rect(*t.bbox)
                    overlap = rect_b & rect_t
                    if overlap.is_valid and (overlap.get_area() > 0.5 * rect_b.get_area()):
                        is_inside_table = True
                        break
                
                if not is_inside_table:
                    text_blocks.append({"bbox": bbox, "text": text, "y0": bbox[1]})

            # Combine and sort vertically to preserve original reading flow
            all_elements = table_blocks + text_blocks
            if all_elements:
                all_elements.sort(key=lambda x: x["y0"])
                page_text = "\n\n".join(el["text"] for el in all_elements).strip()
            else:
                page_text = ""

            if page_text:
                pages.append({"page": page_num + 1, "text": page_text})
                logger.debug(
                    "PDF page %d: native layout merged (%d chars)", page_num + 1, len(page_text)
                )
            else:
                # Scanned page fallback
                logger.info(
                    "PDF page %d: no text — running OCR pipeline", page_num + 1
                )
                img    = pdf_page_to_image(file_path, page_num=page_num, dpi=200)
                result = run_ocr_on_image(img, page_num=page_num + 1, deskew=True)

                if result["text"]:
                    pages.append({"page": page_num + 1, "text": result["text"]})
                    logger.info(
                        "  Page %d OCR → model=%s  confidence=%.3f",
                        page_num + 1,
                        result["model_used"],
                        result["confidence_score"],
                    )
                else:
                    logger.warning(
                        "  Page %d: no text after OCR — skipping", page_num + 1
                    )
    finally:
        doc.close()

    logger.info(
        "PDF extraction complete: %d pages with text (%s)",
        len(pages), os.path.basename(file_path),
    )
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# Standalone image — full OCR pipeline (PaddleOCR → Surya)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_image(file_path: str) -> list[dict]:
    """
    Routes a standalone image file (.png / .jpg / .jpeg) through the OCR pipeline.
    """
    from ocr_service.pipeline import run_ocr_on_image

    img    = Image.open(file_path).convert("RGB")
    result = run_ocr_on_image(img, page_num=1, deskew=True)

    if result["text"]:
        logger.info(
            "Image OCR (%s) → model=%s  confidence=%.3f",
            os.path.basename(file_path),
            result["model_used"],
            result["confidence_score"],
        )
        return [{"page": 1, "text": result["text"]}]

    logger.warning(
        "Image OCR: no text extracted from '%s'", os.path.basename(file_path)
    )
    return []


# ──────────────────────────────────────────────────────────────────────────────
# DOCX — python-docx, table-aware element stream
# ──────────────────────────────────────────────────────────────────────────────

def _extract_docx(file_path: str) -> list[dict]:
    """
    Extracts text and tables from a .docx file using python-docx.
    Iterates through body elements in document order to capture layout correctly.
    """
    import docx

    doc = docx.Document(file_path)
    pages, current_segment, segment_index, char_count = [], [], 1, 0

    def format_docx_table(tbl) -> str:
        markdown = []
        for r_idx, row in enumerate(tbl.rows):
            row_cells = [cell.text.strip().replace("\n", " ").replace("|", "\\|") for cell in row.cells]
            if not any(row_cells):
                continue
            markdown.append("| " + " | ".join(row_cells) + " |")
            if r_idx == 0:
                markdown.append("| " + " | ".join(["---"] * len(row_cells)) + " |")
        return "\n[TABLE]\n" + "\n".join(markdown) + "\n[/TABLE]\n"

    for child in doc.element.body:
        if child.tag.endswith('p'):
            para = docx.text.paragraph.Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue

            current_segment.append(text)
            char_count += len(text)

            if para.style.name.startswith("Heading") or char_count > 1500:
                pages.append({"page": segment_index, "text": "\n".join(current_segment)})
                current_segment, char_count = [], 0
                segment_index += 1

        elif child.tag.endswith('tbl'):
            table = docx.table.Table(child, doc)
            table_text = format_docx_table(table)
            if table_text.strip():
                current_segment.append(table_text)
                char_count += len(table_text)

                if char_count > 1500:
                    pages.append({"page": segment_index, "text": "\n".join(current_segment)})
                    current_segment, char_count = [], 0
                    segment_index += 1

    if current_segment:
        pages.append({"page": segment_index, "text": "\n".join(current_segment)})

    logger.info(
        "DOCX: %d segments from '%s'", len(pages), os.path.basename(file_path)
    )
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# Legacy DOC — win32com conversion or raw extraction fallback
# ──────────────────────────────────────────────────────────────────────────────

def _convert_doc_to_docx_win32(doc_path: str) -> str:
    """Uses win32com on Windows to convert a legacy binary .doc to a modern .docx."""
    import win32com.client
    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(doc_path)
        new_path = doc_path + "x"  # Convert .doc to .docx
        doc.SaveAs2(new_path, FileFormat=16)  # 16 is wdFormatXMLDocument
        doc.Close()
        return new_path
    except Exception as e:
        logger.warning("win32com .doc conversion failed: %s", e)
        raise e
    finally:
        if word:
            word.Quit()


def _extract_doc_fallback(doc_path: str) -> list[dict]:
    """Lightweight plain text fallback for .doc extraction when MS Word is unavailable."""
    import re
    # 1. Try using pypandoc
    try:
        import pypandoc
        text = pypandoc.convert_file(doc_path, 'plain')
        if text.strip():
            return [{"page": 1, "text": text.strip()}]
    except Exception as e:
        logger.debug("pypandoc .doc extraction fallback failed: %s", e)

    # 2. Crude binary string extraction fallback
    try:
        with open(doc_path, 'rb') as f:
            content = f.read()
        import string
        printable = set(string.printable.encode('ascii'))
        text = "".join(chr(b) for b in content if b in printable)
        # Clean up binary garbage
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 50:
            return [{"page": 1, "text": text}]
    except Exception as e:
        logger.warning("Binary text extraction fallback failed: %s", e)

    raise ValueError(f"Could not extract legacy DOC file: {os.path.basename(doc_path)}")


def _extract_doc(file_path: str) -> list[dict]:
    """Extracts text from legacy .doc files using win32com conversion or plain-text fallback."""
    if os.name == 'nt':
        try:
            docx_path = _convert_doc_to_docx_win32(file_path)
            res = _extract_docx(docx_path)
            if os.path.exists(docx_path):
                os.remove(docx_path)
            return res
        except Exception as e:
            logger.warning("Failed to convert legacy DOC using win32com: %s. Trying fallback.", e)
    
    return _extract_doc_fallback(file_path)


# ──────────────────────────────────────────────────────────────────────────────
# Excel — pandas sheet-by-sheet logical segmentation
# ──────────────────────────────────────────────────────────────────────────────

def _extract_excel(file_path: str) -> list[dict]:
    """
    Extracts data from Excel files sheet-by-sheet.
    Segments large sheets into logical pages of 25 rows, replicating headers.
    """
    import pandas as pd

    try:
        excel_file = pd.ExcelFile(file_path)
    except Exception as e:
        logger.warning("Failed to parse Excel file %s: %s", file_path, e)
        return []

    pages = []
    page_index = 1

    for sheet_name in excel_file.sheet_names:
        try:
            df = excel_file.parse(sheet_name)
        except Exception as e:
            logger.warning("Failed to parse sheet %s in Excel %s: %s", sheet_name, file_path, e)
            continue
        df = df.dropna(how='all')
        if df.empty:
            continue

        chunk_size = 25
        for i in range(0, len(df), chunk_size):
            sub_df = df.iloc[i : i + chunk_size]
            md_table = _df_to_markdown(sub_df)
            pages.append({
                "page": page_index,
                "text": f"Sheet: {sheet_name}\n[TABLE]\n{md_table}\n[/TABLE]"
            })
            page_index += 1

    logger.info(
        "Excel: %d logical pages from '%s'", len(pages), os.path.basename(file_path)
    )
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# CSV — pandas segmented layout
# ──────────────────────────────────────────────────────────────────────────────

def _extract_csv(file_path: str) -> list[dict]:
    """
    Extracts data from CSV files.
    Segments rows into groups of 25, replicating headers in every chunk.
    """
    import pandas as pd

    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        logger.warning("Failed to parse CSV file %s: %s", file_path, e)
        return []

    df = df.dropna(how='all')
    if df.empty:
        return []

    pages = []
    chunk_size = 25
    for i in range(0, len(df), chunk_size):
        sub_df = df.iloc[i : i + chunk_size]
        md_table = _df_to_markdown(sub_df)
        pages.append({
            "page": i // chunk_size + 1,
            "text": f"[TABLE]\n{md_table}\n[/TABLE]"
        })

    logger.info(
        "CSV: %d logical pages (%d rows) from '%s'",
        len(pages), len(df), os.path.basename(file_path),
    )
    return pages