"""
tasks/extract.py
─────────────────
Unified text extraction backend.

Routing logic
─────────────
  .pdf              → PyMuPDF for native-text pages  (fast, zero model overhead)
                      Camelot for table extraction    (lattice+stream, max accuracy)
                      OCR pipeline for scanned pages  (PaddleOCR → Surya if needed)
  .docx             → python-docx, segmented by headings / char count
  .csv              → pandas, streamed in chunks of 500 rows, NL descriptions
  .png/.jpg/.jpeg   → OCR pipeline (PaddleOCR → Surya, deskew enabled)

Public API:
    extract_text(file_path, mime_type=None) → list[{"page": int, "text": str}]
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
import fitz  # PyMuPDF
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
# BUG FIX: these two imports were missing entirely in the previous version of
# this file (lost during the Camelot/table-extraction rewrite), even though
# both _extract_pdf() and _extract_image() call them below. That caused:
#   NameError: name 'pdf_page_to_image' is not defined
# on every PDF page / image that needed OCR (i.e. every scanned page).
# ──────────────────────────────────────────────────────────────────────────────
from ocr_service.pipeline import run_ocr_on_image
from ocr_service.preprocessing.image_processor import pdf_page_to_image


# ──────────────────────────────────────────────────────────────────────────────
# Table utilities import
# ──────────────────────────────────────────────────────────────────────────────

from tasks.table_utils import (
    table_to_nl_rows,
    group_nl_rows,
    detect_table_title,
    markdown_table_to_data,
    df_to_data,
)


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
# Camelot table extraction helper
# ──────────────────────────────────────────────────────────────────────────────

def _extract_tables_camelot(file_path: str, page_num: int, page_height: float) -> list[dict]:
    """
    Extracts tables from a single PDF page using Camelot (lattice + stream).
    Bounding boxes are converted to PyMuPDF's top-left coordinate system.

    Returns a list of dicts with keys:
        - 'data': list[list[str]] — raw table data (headers as row 0)
        - 'accuracy': float — Camelot's parsing accuracy score
        - 'bbox': tuple — (x0, y0, x1, y1) bounding box in PyMuPDF coords
        - 'flavor': str — 'lattice' or 'stream'

    Falls back to empty list if Camelot fails.
    """
    try:
        import camelot
    except ImportError:
        logger.warning(
            "camelot-py not installed — falling back to PyMuPDF for table extraction. "
            "Install with: pip install camelot-py[base]"
        )
        return []

    page_str = str(page_num + 1)  # Camelot uses 1-indexed pages
    results = []

    try:
        # Try lattice first (ruled/bordered tables — highest accuracy)
        tables = camelot.read_pdf(file_path, pages=page_str, flavor='lattice')
        for t in tables:
            accuracy = t.parsing_report.get('accuracy', 0)
            if accuracy >= 30:  # Skip low-confidence false positives
                data = df_to_data(t.df)
                if len(data) >= 2:  # Need at least header + 1 row
                    bbox = t._bbox if hasattr(t, '_bbox') else (0, 0, 0, 0)
                    if bbox and bbox != (0, 0, 0, 0):
                        cx0, cy0, cx1, cy1 = bbox
                        px0 = cx0
                        py0 = page_height - cy1
                        px1 = cx1
                        py1 = page_height - cy0
                        bbox = (px0, py0, px1, py1)
                    results.append({
                        'data': data,
                        'accuracy': accuracy,
                        'bbox': bbox,
                        'flavor': 'lattice',
                    })

        # If no good lattice tables, try stream (borderless tables)
        if not results:
            tables = camelot.read_pdf(file_path, pages=page_str, flavor='stream')
            for t in tables:
                accuracy = t.parsing_report.get('accuracy', 0)
                if accuracy >= 30:
                    data = df_to_data(t.df)
                    if len(data) >= 2:
                        bbox = t._bbox if hasattr(t, '_bbox') else (0, 0, 0, 0)
                        if bbox and bbox != (0, 0, 0, 0):
                            cx0, cy0, cx1, cy1 = bbox
                            px0 = cx0
                            py0 = page_height - cy1
                            px1 = cx1
                            py1 = page_height - cy0
                            bbox = (px0, py0, px1, py1)
                        results.append({
                            'data': data,
                            'accuracy': accuracy,
                            'bbox': bbox,
                            'flavor': 'stream',
                        })

        if results:
            logger.info(
                "Camelot page %d: found %d table(s), accuracies: %s",
                page_num + 1, len(results),
                [f"{r['accuracy']:.1f}% ({r['flavor']})" for r in results],
            )

    except Exception as e:
        logger.warning(
            "Camelot failed on page %d: %s — will fall back to PyMuPDF",
            page_num + 1, e,
        )
        return []

    return results


def _extract_tables_pymupdf_fallback(page, page_num: int) -> list[dict]:
    """
    Fallback: extract tables using PyMuPDF's find_tables() when Camelot is unavailable.
    Converts to the same format as _extract_tables_camelot().
    """
    import fitz

    results = []
    tables = page.find_tables()
    for t in tables:
        raw_data = t.extract()
        if not raw_data:
            continue
        # Convert to clean string data
        data = []
        for row in raw_data:
            cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
            data.append(cells)
        if len(data) >= 2:
            results.append({
                'data': data,
                'accuracy': 70.0,  # Estimated — PyMuPDF doesn't report accuracy
                'bbox': t.bbox,
                'flavor': 'pymupdf',
            })
    return results


# ──────────────────────────────────────────────────────────────────────────────
# PDF — hybrid: Camelot for tables, PyMuPDF for text, OCR for scanned pages
# ──────────────────────────────────────────────────────────────────────────────

def _extract_pdf(file_path: str) -> list[dict]:
    """
    Page-by-page PDF extraction.
    Uses Camelot (lattice+stream) for tables and PyMuPDF for text,
    merging them by vertical layout order (y-coordinate).
    """

    doc   = fitz.open(file_path)
    pages = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_height = page.rect.height

            # Try Camelot lattice/stream first (normalized to PyMuPDF coordinates)
            camelot_tables = _extract_tables_camelot(file_path, page_num, page_height)

            # Fallback to PyMuPDF's layout finder if Camelot failed or found nothing
            if not camelot_tables:
                camelot_tables = _extract_tables_pymupdf_fallback(page, page_num)


            # ── Build text blocks from PyMuPDF ──
            raw_blocks = page.get_text("blocks")
            text_block_dicts = []
            for b in raw_blocks:
                if b[6] != 0:  # Skip image blocks
                    continue
                bbox = (b[0], b[1], b[2], b[3])
                text = b[4].strip()
                if not text:
                    continue
                text_block_dicts.append({"bbox": bbox, "text": text, "y0": bbox[1]})

            # ── Convert Camelot tables to NL + MD blocks ──
            table_blocks = []
            if camelot_tables:
                for tbl in camelot_tables:
                    tbl_data = tbl['data']
                    tbl_bbox = tbl['bbox']

                    # Detect table title from text blocks above
                    title = detect_table_title(text_block_dicts, tbl_bbox)

                    # Generate NL row descriptions
                    nl_rows = table_to_nl_rows(
                        tbl_data, table_title=title,
                        page_num=page_num + 1, source_type="pdf",
                    )
                    nl_chunks = group_nl_rows(nl_rows)

                    # Generate markdown version for BM25
                    md_lines = []
                    for r_idx, row in enumerate(tbl_data):
                        vals = [str(cell or "").replace("\n", " ").replace("|", "\\|") for cell in row]
                        md_lines.append("| " + " | ".join(vals) + " |")
                        if r_idx == 0:
                            md_lines.append("| " + " | ".join(["---"] * len(vals)) + " |")
                    md_table = "\n".join(md_lines)

                    # Calculate vertical position for sorting
                    y0 = tbl_bbox[1] if tbl_bbox and len(tbl_bbox) >= 2 else 0

                    # Add NL chunks
                    for nl_chunk in nl_chunks:
                        table_blocks.append({
                            "bbox": tbl_bbox,
                            "text": f"[TABLE_NL]\n{nl_chunk}\n[/TABLE_NL]",
                            "y0": y0,
                        })

                    # Add MD version
                    table_blocks.append({
                        "bbox": tbl_bbox,
                        "text": f"[TABLE_MD]\n{md_table}\n[/TABLE_MD]",
                        "y0": y0 + 0.1,  # Slightly after NL for sort order
                    })

            # ── Filter text blocks that overlap with tables ──
            filtered_text_blocks = []
            for tb in text_block_dicts:
                rect_b = fitz.Rect(*tb["bbox"])
                is_inside_table = False

                for tbl in camelot_tables:
                    tbl_bbox = tbl.get('bbox')
                    if not tbl_bbox or len(tbl_bbox) < 4:
                        continue
                    try:
                        rect_t = fitz.Rect(*tbl_bbox)
                        overlap = rect_b & rect_t
                        if overlap.is_valid and (overlap.get_area() > 0.5 * rect_b.get_area()):
                            is_inside_table = True
                            break
                    except Exception:
                        continue

                if not is_inside_table:
                    filtered_text_blocks.append(tb)

            # ── Combine and sort vertically ──
            all_elements = table_blocks + filtered_text_blocks
            if all_elements:
                all_elements.sort(key=lambda x: x["y0"])
                page_text = "\n\n".join(el["text"] for el in all_elements).strip()
            else:
                page_text = ""

            if page_text:
                pages.append({"page": page_num + 1, "text": page_text})
                logger.debug(
                    "PDF page %d: layout merged (%d chars, %d tables)",
                    page_num + 1, len(page_text), len(camelot_tables),
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
# DOCX — python-docx, table-aware element stream with NL conversion
# ──────────────────────────────────────────────────────────────────────────────

def _extract_docx(file_path: str) -> list[dict]:
    """
    Extracts text and tables from a .docx file using python-docx.
    Iterates through body elements in document order to capture layout correctly.
    Tables are converted to NL row descriptions for semantic search.
    """
    import docx

    doc = docx.Document(file_path)
    pages, current_segment, segment_index, char_count = [], [], 1, 0
    last_heading = ""

    def format_docx_table(tbl) -> str:
        markdown = []
        for r_idx, row in enumerate(tbl.rows):
            row_cells = [cell.text.strip().replace("\n", " ").replace("|", "\\|") for cell in row.cells]
            if not any(row_cells):
                continue
            markdown.append("| " + " | ".join(row_cells) + " |")
            if r_idx == 0:
                markdown.append("| " + " | ".join(["---"] * len(row_cells)) + " |")
        return "\n".join(markdown)

    for child in doc.element.body:
        if child.tag.endswith('p'):
            para = docx.text.paragraph.Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue

            # Track headings for table title detection
            if para.style.name.startswith("Heading"):
                last_heading = text

            current_segment.append(text)
            char_count += len(text)

            if para.style.name.startswith("Heading") or char_count > 1500:
                pages.append({"page": segment_index, "text": "\n".join(current_segment)})
                current_segment, char_count = [], 0
                segment_index += 1

        elif child.tag.endswith('tbl'):
            table = docx.table.Table(child, doc)
            md_text = format_docx_table(table)

            if md_text.strip():
                # Parse markdown back to data for NL conversion
                table_data = markdown_table_to_data(md_text)
                table_title = last_heading if last_heading else "Table"

                if table_data and len(table_data) >= 2:
                    # Generate NL descriptions
                    nl_rows = table_to_nl_rows(
                        table_data, table_title=table_title,
                        page_num=segment_index, source_type="docx",
                    )
                    nl_chunks = group_nl_rows(nl_rows)

                    # Add NL chunks
                    for nl_chunk in nl_chunks:
                        current_segment.append(f"[TABLE_NL]\n{nl_chunk}\n[/TABLE_NL]")
                        char_count += len(nl_chunk)

                    # Add MD version for BM25
                    current_segment.append(f"[TABLE_MD]\n{md_text}\n[/TABLE_MD]")
                    char_count += len(md_text)
                else:
                    # Fallback: keep as old-style [TABLE] block
                    current_segment.append(f"[TABLE]\n{md_text}\n[/TABLE]")
                    char_count += len(md_text)

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
# Excel — pandas sheet-by-sheet with NL descriptions, no size limits
# ──────────────────────────────────────────────────────────────────────────────

def _extract_excel(file_path: str) -> list[dict]:
    """
    Extracts data from Excel files sheet-by-sheet.
    No size limits — processes all rows via batched reads.
    Generates NL row descriptions for semantic search + MD for BM25.
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

        # Adaptive batch size based on column count
        num_cols = len(df.columns)
        if num_cols <= 5:
            batch_size = 10
        elif num_cols <= 15:
            batch_size = 3
        else:
            batch_size = 1

        # Larger batch for MD chunks
        md_batch_size = 25

        # Process in batches for NL descriptions
        for i in range(0, len(df), batch_size):
            sub_df = df.iloc[i: i + batch_size]
            data = df_to_data(sub_df)

            nl_rows = table_to_nl_rows(
                data, table_title=f"Sheet: {sheet_name}",
                page_num=page_index, source_type="xlsx",
            )
            nl_chunks = group_nl_rows(nl_rows)

            for nl_chunk in nl_chunks:
                pages.append({
                    "page": page_index,
                    "text": f"Sheet: {sheet_name}\n[TABLE_NL]\n{nl_chunk}\n[/TABLE_NL]",
                })
                page_index += 1

        # Also produce MD chunks for BM25
        for i in range(0, len(df), md_batch_size):
            sub_df = df.iloc[i: i + md_batch_size]
            md_table = _df_to_markdown(sub_df)
            row_start = i + 1
            row_end = min(i + md_batch_size, len(df))
            pages.append({
                "page": page_index,
                "text": f"Sheet: {sheet_name} (rows {row_start}-{row_end})\n[TABLE_MD]\n{md_table}\n[/TABLE_MD]",
            })
            page_index += 1

    logger.info(
        "Excel: %d logical pages from '%s'", len(pages), os.path.basename(file_path)
    )
    return pages


# ──────────────────────────────────────────────────────────────────────────────
# CSV — pandas streamed, no size limits, NL descriptions
# ──────────────────────────────────────────────────────────────────────────────

def _extract_csv(file_path: str) -> list[dict]:
    """
    Extracts data from CSV files with no size limits.
    Uses streaming chunked reads for large files.
    Generates NL row descriptions for semantic search + MD for BM25.
    """
    import pandas as pd

    pages = []
    page_index = 1

    try:
        # Peek at columns to determine adaptive batch sizes
        peek_df = pd.read_csv(file_path, nrows=1)
        num_cols = len(peek_df.columns)
    except Exception as e:
        logger.warning("Failed to parse CSV file %s: %s", file_path, e)
        return []

    # Adaptive batch size for NL descriptions
    if num_cols <= 5:
        nl_batch_size = 10
    elif num_cols <= 15:
        nl_batch_size = 3
    else:
        nl_batch_size = 1

    # Stream in chunks of 500 rows — no size limits
    try:
        reader = pd.read_csv(file_path, chunksize=500)
    except Exception as e:
        logger.warning("Failed to stream CSV file %s: %s", file_path, e)
        return []

    total_rows = 0
    for chunk_df in reader:
        chunk_df = chunk_df.dropna(how='all')
        if chunk_df.empty:
            continue

        total_rows += len(chunk_df)

        # Generate NL descriptions in sub-batches
        for i in range(0, len(chunk_df), nl_batch_size):
            sub_df = chunk_df.iloc[i: i + nl_batch_size]
            data = df_to_data(sub_df)

            nl_rows = table_to_nl_rows(
                data, table_title="CSV Data",
                page_num=page_index, source_type="csv",
            )
            nl_chunks = group_nl_rows(nl_rows)

            for nl_chunk in nl_chunks:
                pages.append({
                    "page": page_index,
                    "text": f"[TABLE_NL]\n{nl_chunk}\n[/TABLE_NL]",
                })
                page_index += 1

        # Also produce MD chunk for BM25 (one per 500-row stream chunk)
        md_table = _df_to_markdown(chunk_df)
        row_start = total_rows - len(chunk_df) + 1
        row_end = total_rows
        pages.append({
            "page": page_index,
            "text": f"CSV rows {row_start}-{row_end}\n[TABLE_MD]\n{md_table}\n[/TABLE_MD]",
        })
        page_index += 1

    logger.info(
        "CSV: %d logical pages (%d rows) from '%s'",
        len(pages), total_rows, os.path.basename(file_path),
    )
    return pages