from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Only imported for static analysis — never executed at runtime.
    # chunk_pages receives the model instance from embed.get_model(),
    # so no runtime import of sentence_transformers is needed here.
    from sentence_transformers import SentenceTransformer

# ------------------------------------------------------------------
# Semantic Chunking using multilingual-e5-base
#
# How it works:
#   1. Split page text into sentences
#   2. Embed every sentence using multilingual-e5-base
#   3. Compute cosine similarity between consecutive sentences
#   4. Where similarity drops below threshold → topic shift → new chunk
#   5. Respect min/max chunk size guards to avoid 1-sentence or huge chunks
#
# The model is passed in — load it ONCE on worker startup, not per document.
# ------------------------------------------------------------------

SIMILARITY_THRESHOLD = 0.7   # below this → new chunk
MIN_CHUNK_SENTENCES  = 2     # never cut a chunk smaller than this
MAX_CHUNK_SENTENCES  = 30    # safety cap — force-cut very long chunks


def chunk_pages(
    pages: list[dict],
    document_id: str,
    domain_id: str,
    model: SentenceTransformer,
    chunk_size: int = 512,        # kept for API compatibility — not used in semantic mode
    chunk_overlap: int = 0,       # semantic chunking doesn't use sliding overlap
    source_type: str = "pdf",     # file format: pdf, docx, csv, png, etc.
    filename: str = "",           # original filename for citation provenance
) -> list[dict]:
    """
    Splits pages into semantically coherent chunks, preserving table blocks whole.

    Args:
        pages:        output from extractor.py — list of {page, text}
        document_id:  UUID of the document
        domain_id:    UUID of the domain (for Qdrant namespace)
        model:        loaded SentenceTransformer — pass from worker startup
        chunk_size:   ignored in semantic mode, kept for interface compatibility
        chunk_overlap: ignored in semantic mode
        source_type:  file format string (pdf, docx, csv, png, etc.)
        filename:     original filename for citation provenance

    Returns:
        list of chunk dicts ready for embedder.py
    """
    all_chunks = []
    chunk_index = 0

    for page_data in pages:
        page_num  = page_data["page"]
        page_text = page_data["text"]

        # Parse page text into table blocks and normal text blocks
        parts = re.split(r'(\[TABLE\].*?\[/TABLE\])', page_text, flags=re.DOTALL)
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # If it's a table block, preserve it whole as a single chunk
            if part.startswith("[TABLE]") and part.endswith("[/TABLE]"):
                all_chunks.append({
                    "chunk_id":    f"{document_id}_{chunk_index}",
                    "document_id": document_id,
                    "domain_id":   domain_id,
                    "page":        page_num,
                    "chunk_index": chunk_index,
                    "text":        part,
                    "source_type": source_type,
                    "filename":    filename,
                })
                chunk_index += 1
            else:
                # Semantic chunking for normal text block
                sentences = _split_sentences(part)
                if not sentences:
                    continue

                # Embed all sentences in this block in one batch
                prefixed   = [f"passage: {s}" for s in sentences]
                embeddings = model.encode(prefixed, normalize_embeddings=True)

                # Find cut points based on cosine similarity between adjacent sentences
                cut_points = _find_cut_points(embeddings)

                # Group sentences into chunks using cut points
                groups = _group_by_cuts(sentences, cut_points)

                for group in groups:
                    text = " ".join(group).strip()
                    if not text:
                        continue

                    all_chunks.append({
                        "chunk_id":    f"{document_id}_{chunk_index}",
                        "document_id": document_id,
                        "domain_id":   domain_id,
                        "page":        page_num,
                        "chunk_index": chunk_index,
                        "text":        text,
                        "source_type": source_type,
                        "filename":    filename,
                    })
                    chunk_index += 1

    print(f"  Table-aware semantic chunking: {len(pages)} pages -> {len(all_chunks)} chunks")
    return all_chunks


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    """
    Splits text into sentences on . ! ? and paragraph breaks.
    Also handles Arabic sentence-ending punctuation (؟ ۔ !) and
    Arabic full stop (U+06D4) so Arabic documents are split correctly
    instead of becoming one giant chunk per page.
    """
    # BUG #4 FIX: added Arabic question mark ؟ (U+061F) and Arabic full stop ۔ (U+06D4)
    sentences = re.split(r'(?<=[.!?؟۔])\s+|\n{2,}', text)
    return [s.strip() for s in sentences if s.strip()]


def _find_cut_points(embeddings: np.ndarray) -> list[int]:
    """
    Returns indices AFTER which a new chunk should start.

    Logic:
    - Compute cosine similarity between sentence[i] and sentence[i+1]
    - If similarity < SIMILARITY_THRESHOLD → cut here
    - Enforce MIN_CHUNK_SENTENCES: never cut too soon after last cut
    - Enforce MAX_CHUNK_SENTENCES: force cut if group is getting too long
    """
    cut_points        = []
    last_cut          = 0
    n                 = len(embeddings)

    for i in range(n - 1):
        sentences_since_cut = i - last_cut + 1

        # Force cut if chunk is getting too long
        if sentences_since_cut >= MAX_CHUNK_SENTENCES:
            cut_points.append(i)
            last_cut = i + 1
            continue

        # Skip cut if chunk is still too short
        if sentences_since_cut < MIN_CHUNK_SENTENCES:
            continue

        # Cosine similarity — embeddings are already L2-normalized
        similarity = float(np.dot(embeddings[i], embeddings[i + 1]))

        if similarity < SIMILARITY_THRESHOLD:
            cut_points.append(i)
            last_cut = i + 1

    return cut_points


def _group_by_cuts(sentences: list[str], cut_points: list[int]) -> list[list[str]]:
    """
    Splits sentence list into groups using cut point indices.
    """
    groups = []
    start  = 0

    for cut in cut_points:
        groups.append(sentences[start : cut + 1])
        start = cut + 1

    # Last group (after final cut point)
    if start < len(sentences):
        groups.append(sentences[start:])

    return groups