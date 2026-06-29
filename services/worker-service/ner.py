from __future__ import annotations

# IMPORTANT: gliner (and therefore torch + safetensors) is imported lazily
# inside get_ner_model() — NOT at module level. This follows the exact same
# pattern as tasks/embed.py and for the same reason: this worker process
# already loads multilingual-e5-small (embedding) and the cross-encoder
# reranker model on the retrieval-service side. Loading a THIRD model's
# PyTorch/safetensors machinery at Celery startup — on top of those —
# is what tasks/embed.py's docstring warns exhausts the Windows paging
# file ([WinError 1455]). Deferring the import until the first document
# actually needs entity extraction keeps worker startup lightweight.
#
# hf_env.py (imported first by worker.py, before any task module) already
# patches safetensors.safe_open to avoid mmap-based commit-charge issues.
# That patch is global to the safetensors library, not specific to
# sentence_transformers, so it protects GLiNER's model loading too —
# no duplicate workaround needed here.

import os
import re
import threading
from pathlib import Path
from typing import Any

from ontology import ENTITY_TYPES

# ------------------------------------------------------------------
# Model is loaded ONCE when the first chunk needing NER arrives.
# get_ner_model() returns the same instance every call — no re-loading.
# ------------------------------------------------------------------

_model: Any = None  # GLiNER, lazily loaded
_ner_model_lock = threading.Lock()

REMOTE_MODEL_NAME = "urchade/gliner_multi-v2.1"
MODEL_NAME = os.getenv("NER_MODEL", REMOTE_MODEL_NAME)

# Validated in the Sprint 3 spike test (real sentences from this
# project's uploaded documents): Person and Location scored 0.85-0.97,
# Department scored ~0.56-0.89, Skill scored as low as 0.62 on a
# borderline case ("خبرة" / "experience" — too generic a noun to be a
# real Skill entity). 0.6 balances keeping genuine entities like
# Department while dropping noise like that Skill match.
#
# Kept at 0.6 even after switching to semantic label phrasing below
# (which raised raw scores across the board) — the threshold is a
# deliberate quality bar, not just whatever the labels happen to
# produce, so it doesn't silently drift every time prompting improves.
CONFIDENCE_THRESHOLD = 0.6

# GLiNER is zero-shot: it infers each label's meaning from the label
# string itself rather than from a fixed trained class list. The spike
# test showed that natural-language descriptions ("a person name")
# produce noticeably more confident, accurate matches than bare
# ontology words ("Person") — the descriptive phrasing gives the model
# clearer semantic context to match against.
#
# GLINER_LABELS is therefore NOT the same list as ontology.ENTITY_TYPES.
# _LABEL_TO_ENTITY_TYPE maps GLiNER's output back to our actual
# ontology names so every other Sprint 3 service (graph_writer.py,
# relation extraction, etc.) only ever sees "Person", "Department", etc.
# — never GLiNER's internal phrasing.
_LABEL_TO_ENTITY_TYPE: dict[str, str] = {
    # Original entity types
    "a person name": "Person",
    "a project name or system": "Project",
    "a department in an organization": "Department",
    "a policy or regulation": "Policy",
    "a job role or position": "Role",
    "a geographical location": "Location",
    "a technical skill or expertise": "Skill",
    # Phase 4 expansion: domain-relevant entity types
    "an official document or report": "Document",
    "a tax form or official form": "Form",
    "a company or organization": "Organization",
    "a government agency or authority": "Agency",
    "a law or regulation or legal rule": "Regulation",
    "a tax or payroll or financial concept": "TaxTerm",
    "a date or time period": "Date",
    "an identifier or code or reference number": "Identifier",
    "a legal or policy requirement": "Requirement",
    "a procedure or process or workflow": "Procedure",
}

GLINER_LABELS: list[str] = list(_LABEL_TO_ENTITY_TYPE.keys())

# Safety net: verify that every GLiNER label maps to a valid ontology
# entity type. The label map may be a subset of ENTITY_TYPES (not every
# ontology type needs a GLiNER label), but every mapped value must exist.
_mapped_types = set(_LABEL_TO_ENTITY_TYPE.values())
_missing = _mapped_types - set(ENTITY_TYPES)
assert not _missing, (
    f"ner.py's GLiNER label mapping references entity types not in ontology.py: {_missing}"
)


def get_ner_model() -> Any:
    """
    Returns the shared GLiNER instance.
    Loaded once on first call (when the first chunk needing NER is
    processed), then cached for the worker lifetime — mirrors
    tasks/embed.py's get_model().
    """
    global _model
    if _model is None:
        with _ner_model_lock:
            if _model is None:
                model_path = Path(MODEL_NAME)
                if model_path.exists():
                    resolved_model_name = str(model_path)
                else:
                    offline_enabled = os.getenv("HF_HUB_OFFLINE") == "1" or os.getenv("TRANSFORMERS_OFFLINE") == "1"
                    if MODEL_NAME != REMOTE_MODEL_NAME or offline_enabled:
                        raise FileNotFoundError(
                            f"NER model path does not exist: {MODEL_NAME}. "
                            "Set NER_MODEL to a valid local directory."
                        )
                    resolved_model_name = MODEL_NAME
                print(f"Loading NER model: {resolved_model_name}...")
                from gliner import GLiNER  # noqa: PLC0415 — see module docstring

                onnx_file = Path(resolved_model_name) / "model_quantized.onnx"
                if onnx_file.exists():
                    print("  Detected quantized ONNX model file. Loading with ONNX Runtime...")
                    _model = GLiNER.from_pretrained(
                        resolved_model_name,
                        load_onnx_model=True,
                        onnx_model_file="model_quantized.onnx",
                        local_files_only=True
                    )
                else:
                    _model = GLiNER.from_pretrained(resolved_model_name, local_files_only=True)
                print("NER model loaded")
    return _model


def normalize_arabic(text: str) -> str:
    """
    Standardize Arabic text to handle spelling variants, diacritics, and spaces.
    """
    if not text:
        return ""
    # Strip diacritics (Harakat)
    text = re.sub(r'[\u064B-\u0652]', '', text)
    # Normalize Alif variants (أ, إ, آ, ٱ) -> ا
    text = re.sub(r'[أإآٱ]', 'ا', text)
    # Normalize Alif Maqsura (ى) -> Ya (ي)
    text = re.sub(r'ى', 'ي', text)
    # Normalize Ta Marbuta (ة) -> Ha (ه)
    text = re.sub(r'ة', 'ه', text)
    # Collapse multiple whitespaces and strip
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _process_raw_entities(raw_entities: list[dict]) -> list[dict]:
    results = []
    seen = set()
    for ent in raw_entities:
        ent_text = ent["text"].strip()
        # Noise filtering: min length 2, not purely digits/punctuation
        if len(ent_text) < 2:
            continue
        if re.match(r'^[0-9\s\-_.,/\\()!?*&^%$#@!+=\[\]{}|;:<>`~"\']+$', ent_text):
            continue
        
        label = _LABEL_TO_ENTITY_TYPE[ent["label"]]
        norm = normalize_arabic(ent_text)
        if not norm:
            continue
            
        key = (norm, label)
        if key not in seen:
            seen.add(key)
            results.append({
                "text": ent_text,
                "normalized_text": norm,
                "label": label,
                "score": round(float(ent["score"]), 4),
            })
    return results


NER_MAX_TOKENS = 300     # Safe margin under GLiNER's 384 hard cap
NER_OVERLAP_TOKENS = 50  # Overlap to catch entities on boundaries


def _sub_chunk_for_ner(text: str) -> list[str]:
    """
    Split text into NER-safe sub-chunks if it exceeds NER_MAX_TOKENS tokens.
    Attempts to use the model's actual tokenizer to count tokens exactly,
    falling back to a conservative word count split if the tokenizer is unavailable.
    """
    if not text or not text.strip():
        return []

    model = get_ner_model()
    tokenizer = None
    if hasattr(model, "data_processor") and hasattr(model.data_processor, "transformer_tokenizer"):
        tokenizer = model.data_processor.transformer_tokenizer

    if tokenizer is not None:
        try:
            tokens = tokenizer.encode(text, add_special_tokens=False)
            if len(tokens) <= NER_MAX_TOKENS:
                return [text]
            
            sub_chunks = []
            start = 0
            while start < len(tokens):
                end = min(start + NER_MAX_TOKENS, len(tokens))
                chunk_tokens = tokens[start:end]
                sub_chunks.append(tokenizer.decode(chunk_tokens, skip_special_tokens=True))
                start += NER_MAX_TOKENS - NER_OVERLAP_TOKENS
            return sub_chunks
        except Exception as e:
            print(f"  Warning: token-based sub-chunking failed ({e}), falling back to word count.")

    # Fallback: Word-based split with a very conservative limit to guarantee fitting 384 tokens
    words = text.split()
    max_words = 150
    overlap_words = 30
    if len(words) <= max_words:
        return [text]
    
    sub_chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        sub_chunks.append(" ".join(words[start:end]))
        start += max_words - overlap_words
    return sub_chunks


def extract_entities(text: str) -> list[dict]:
    """
    Runs NER on a single chunk of text using this project's ontology
    (services/worker-service/ontology.py — Person, Project, Department,
    Policy, Role, Location, Skill, Document, Form, Organization, Agency,
    Regulation, TaxTerm, Date, Identifier, Requirement, Procedure),
    via GLiNER's semantic label phrasing (see GLINER_LABELS above).

    Splits the text into sliding window sub-chunks under the 384 token limit
    to prevent truncation.

    Returns entities above CONFIDENCE_THRESHOLD only, normalized and filtered,
    mapped back to ontology names:
    [
        {"text": "أحمد محمد", "normalized_text": "احمد محمد", "label": "Person", "score": 0.97},
        ...
    ]
    """
    if not text or not text.strip():
        return []

    model = get_ner_model()
    all_raw = []
    sub_chunks = _sub_chunk_for_ner(text)
    for sub in sub_chunks:
        raw = model.run(sub, GLINER_LABELS, threshold=CONFIDENCE_THRESHOLD)
        all_raw.extend(raw)
    return _process_raw_entities(all_raw)


def extract_entities_for_chunks(chunks: list[dict]) -> list[dict]:
    """
    Batch entry point matching the embed_chunks() pattern used elsewhere
    in this pipeline — takes chunks from chunk.py, splits long texts into sub-chunks,
    runs GLiNER batch inference with model.run(), adds an 'entities' key to each.
    """
    print(f"  Extracting entities from {len(chunks)} chunks...")

    # Collect non-empty texts and their original indices
    non_empty_indices = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        if text and text.strip():
            non_empty_indices.append(i)
        else:
            chunk["entities"] = []

    if non_empty_indices:
        model = get_ner_model()
        total_entities = 0

        # Build flat list of sub-chunks and map back to parent chunk indices
        flat_sub_chunks = []
        chunk_to_sub_indices = {}
        sub_chunk_count = 0
        for idx in non_empty_indices:
            text = chunks[idx].get("text", "")
            sub_texts = _sub_chunk_for_ner(text)
            chunk_to_sub_indices[idx] = []
            for sub_text in sub_texts:
                flat_sub_chunks.append(sub_text)
                chunk_to_sub_indices[idx].append(sub_chunk_count)
                sub_chunk_count += 1

        try:
            # batch run using model.run on flat_sub_chunks
            raw_entities_list = model.run(
                flat_sub_chunks,
                GLINER_LABELS,
                threshold=CONFIDENCE_THRESHOLD
            )
            for idx in non_empty_indices:
                all_raw_entities = []
                for sub_idx in chunk_to_sub_indices[idx]:
                    all_raw_entities.extend(raw_entities_list[sub_idx])
                entities = _process_raw_entities(all_raw_entities)
                chunks[idx]["entities"] = entities
                total_entities += len(entities)
        except Exception as e:
            print(f"  Warning: GLiNER batch inference failed: {e}. Falling back to sequential.")
            for idx in non_empty_indices:
                try:
                    text = chunks[idx].get("text", "")
                    sub_texts = _sub_chunk_for_ner(text)
                    all_raw_entities = []
                    for sub_text in sub_texts:
                        raw = model.run(sub_text, GLINER_LABELS, threshold=CONFIDENCE_THRESHOLD)
                        all_raw_entities.extend(raw)
                    entities = _process_raw_entities(all_raw_entities)
                    chunks[idx]["entities"] = entities
                    total_entities += len(entities)
                except Exception as seq_e:
                    print(f"  Warning: failed to extract entities for chunk {idx}: {seq_e}")
                    chunks[idx]["entities"] = []
        
        print(f"  Extracted {total_entities} entities across {len(chunks)} chunks")
    else:
        print("  No non-empty chunks found for entity extraction.")

    return chunks
