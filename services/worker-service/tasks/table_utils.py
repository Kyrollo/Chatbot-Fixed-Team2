"""
tasks/table_utils.py
────────────────────
Shared table utilities for converting extracted tables into
natural-language (NL) row descriptions for semantic search.

Functions:
    table_to_nl_rows   — list-of-lists → NL description per row
    group_nl_rows      — group NL rows into embedding-window-sized chunks
    detect_table_title  — find heading text above a table bbox
    markdown_table_to_data — parse [TABLE]...[/TABLE] markdown → list-of-lists
    df_to_data         — pandas DataFrame → list-of-lists (headers as row 0)
"""
from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Convert raw table data to natural-language row descriptions
# ──────────────────────────────────────────────────────────────────────────────

def table_to_nl_rows(
    data: list[list[str]],
    table_title: str = "Table",
    page_num: int = 1,
    source_type: str = "pdf",
) -> list[str]:
    """
    Converts raw table data (list of lists) into self-contained NL descriptions.

    Args:
        data:         List of lists, where data[0] is the header row.
        table_title:  Title/heading found above the table.
        page_num:     Page number in the source document.
        source_type:  File format: pdf, docx, csv, xlsx, etc.

    Returns:
        List of NL description strings, one per data row.

    Example output:
        Table: Single or Married Filing Separately (page 4)
        Row: Higher Paying Job $200,000–$249,999
        - Lower Paying Job $70,000–$79,999: $17,900
        - Lower Paying Job $80,000–$89,999: $19,200
    """
    if not data or len(data) < 2:
        return []

    # data[0] is the header row
    headers = [str(cell).strip() if cell else "" for cell in data[0]]

    # Detect if the first column is a row header (label) vs data
    # Heuristic: if first column values are unique and non-numeric, treat as row header
    first_col_values = [str(row[0]).strip() if row and row[0] else "" for row in data[1:]]
    has_row_header = _looks_like_row_header(first_col_values)

    nl_rows: list[str] = []
    prev_row_header = ""

    for row_idx, row in enumerate(data[1:], start=1):
        cells = [str(cell).strip() if cell else "" for cell in row]

        # Skip completely empty rows
        if not any(cells):
            continue

        # Determine row identifier
        if has_row_header and cells[0]:
            row_header = cells[0]
            value_start = 1
        elif has_row_header and not cells[0]:
            # Merged cell — use previous row header
            row_header = f"{prev_row_header} (continued)"
            value_start = 1
        else:
            row_header = f"Row {row_idx}"
            value_start = 0

        prev_row_header = cells[0] if cells[0] else prev_row_header

        # Build NL description
        lines = [f"Table: {table_title} (page {page_num})"]
        lines.append(f"Row: {row_header}")

        for col_idx in range(value_start, len(cells)):
            col_header = headers[col_idx] if col_idx < len(headers) else f"Column {col_idx + 1}"
            cell_value = cells[col_idx]

            if not cell_value:
                continue  # Skip empty cells

            lines.append(f"- {col_header}: {cell_value}")

        if len(lines) > 2:  # Has at least one value beyond header
            nl_rows.append("\n".join(lines))

    logger.debug(
        "table_to_nl_rows: %d data rows → %d NL descriptions (title='%s')",
        len(data) - 1, len(nl_rows), table_title,
    )
    return nl_rows


def _looks_like_row_header(values: list[str]) -> bool:
    """
    Heuristic to detect whether the first column contains row headers.
    Returns True if values are mostly non-numeric and mostly unique.
    """
    if not values:
        return False

    non_empty = [v for v in values if v]
    if not non_empty:
        return False

    # Check uniqueness (> 70% unique suggests labels, not data)
    unique_ratio = len(set(non_empty)) / len(non_empty) if non_empty else 0

    # Check if mostly non-numeric
    numeric_count = sum(1 for v in non_empty if _is_numeric(v))
    numeric_ratio = numeric_count / len(non_empty) if non_empty else 0

    return unique_ratio > 0.5 and numeric_ratio < 0.8


def _is_numeric(value: str) -> bool:
    """Check if a string is purely numeric (ignoring currency symbols, commas)."""
    cleaned = value.replace("$", "").replace(",", "").replace("%", "").replace(" ", "").strip()
    if not cleaned:
        return False
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 2. Group NL rows into embedding-window-sized chunks
# ──────────────────────────────────────────────────────────────────────────────

def group_nl_rows(nl_rows: list[str], max_tokens: int = 400) -> list[str]:
    """
    Groups consecutive NL row descriptions into chunks that fit
    within the embedding model's token window (~512 tokens, target 400).

    For simple lookup tables with short rows: 1 row per chunk.
    For wider tables: groups until approaching the token budget.

    Args:
        nl_rows:    List of NL description strings.
        max_tokens: Target max tokens per chunk (uses ~4 chars/token estimate).

    Returns:
        List of chunk strings, each a concatenation of grouped NL rows.
    """
    if not nl_rows:
        return []

    max_chars = max_tokens * 4  # Rough estimate: ~4 chars per token
    groups: list[str] = []
    current_group: list[str] = []
    current_chars = 0

    for nl_row in nl_rows:
        row_chars = len(nl_row)

        # If a single row exceeds the budget, it becomes its own chunk
        if row_chars >= max_chars:
            if current_group:
                groups.append("\n\n".join(current_group))
                current_group = []
                current_chars = 0
            groups.append(nl_row)
            continue

        # If adding this row would exceed budget, flush current group
        if current_chars + row_chars + 2 > max_chars and current_group:
            groups.append("\n\n".join(current_group))
            current_group = []
            current_chars = 0

        current_group.append(nl_row)
        current_chars += row_chars + 2  # +2 for the "\n\n" separator

    # Flush remaining
    if current_group:
        groups.append("\n\n".join(current_group))

    logger.debug(
        "group_nl_rows: %d NL rows → %d chunks (max_tokens=%d)",
        len(nl_rows), len(groups), max_tokens,
    )
    return groups


# ──────────────────────────────────────────────────────────────────────────────
# 3. Detect table title from surrounding text blocks
# ──────────────────────────────────────────────────────────────────────────────

def detect_table_title(
    text_blocks: list[dict],
    table_bbox: tuple,
    default: str = "Table",
) -> str:
    """
    Finds the nearest text block above a table that looks like a title.

    Args:
        text_blocks: List of dicts with keys 'text', 'bbox' (x0, y0, x1, y1).
        table_bbox:  Bounding box of the table (x0, y0, x1, y1).
        default:     Fallback title if nothing found.

    Returns:
        The detected title string, or the default.
    """
    if not text_blocks or not table_bbox:
        return default

    table_y0 = table_bbox[1]  # Top of the table

    # Find text blocks above the table, sorted by distance (closest first)
    candidates = []
    for block in text_blocks:
        block_y1 = block["bbox"][3]  # Bottom of the text block
        block_text = block["text"].strip()

        # Must be above the table (block bottom < table top)
        # We allow a small overlap tolerance of 10px in case the block extends slightly into the table
        if block_y1 <= table_y0 + 10 and block_text:
            distance = table_y0 - block_y1
            candidates.append((distance, block_text))

    if not candidates:
        return default

    candidates.sort(key=lambda x: x[0])  # Sort by distance, closest first

    # Take the closest text block if it contains a title
    for distance, text in candidates:
        # Only consider blocks close to the table (within ~150px / ~2 inch)
        if distance > 150:
            break

        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            continue

        # Check from the last line of the block upwards, since the heading
        # is most likely at the end of the block immediately preceding the table.
        for line in reversed(lines):
            # A good title is reasonably short, not just numeric, and contains letters
            if 3 < len(line) < 100 and any(c.isalpha() for c in line):
                return line

    return default


# ──────────────────────────────────────────────────────────────────────────────
# 4. Parse markdown table back to list-of-lists
# ──────────────────────────────────────────────────────────────────────────────

def markdown_table_to_data(md_text: str) -> list[list[str]]:
    """
    Parses a markdown table (possibly wrapped in [TABLE]...[/TABLE] markers)
    back into a list-of-lists.

    Args:
        md_text: Markdown table text, e.g.:
            | Col1 | Col2 |
            | --- | --- |
            | val1 | val2 |

    Returns:
        List of lists, where data[0] is the header row.
    """
    # Strip table markers if present
    text = md_text.strip()
    for marker in ("[TABLE]", "[/TABLE]", "[TABLE_MD]", "[/TABLE_MD]",
                    "[TABLE_NL]", "[/TABLE_NL]"):
        text = text.replace(marker, "")
    text = text.strip()

    if not text:
        return []

    data: list[list[str]] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip separator rows (| --- | --- |)
        if re.match(r'^\|[\s\-:|]+\|$', line):
            continue

        # Parse pipe-delimited row
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            data.append(cells)

    return data


# ──────────────────────────────────────────────────────────────────────────────
# 5. DataFrame to list-of-lists
# ──────────────────────────────────────────────────────────────────────────────

def df_to_data(df) -> list[list[str]]:
    """
    Converts a pandas DataFrame into a list-of-lists with headers as row 0.

    Args:
        df: A pandas DataFrame (from Camelot, CSV, or Excel).

    Returns:
        List of lists, where data[0] is the header row.
    """
    headers = [str(c).strip() for c in df.columns]
    data: list[list[str]] = [headers]

    for _, row in df.iterrows():
        cells = [str(v).strip() if v is not None and str(v).strip() != "nan" else ""
                 for v in row.values]
        data.append(cells)

    return data
