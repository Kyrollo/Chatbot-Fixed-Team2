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

                _model = GLiNER.from_pretrained(resolved_model_name)
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


def extract_entities(text: str) -> list[dict]:
    """
    Runs NER on a single chunk of text using this project's ontology
    (services/worker-service/ontology.py — Person, Project, Department,
    Policy, Role, Location, Skill, Document, Form, Organization, Agency,
    Regulation, TaxTerm, Date, Identifier, Requirement, Procedure),
    via GLiNER's semantic label phrasing (see GLINER_LABELS above).

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
    raw_entities = model.predict_entities(text, GLINER_LABELS, threshold=CONFIDENCE_THRESHOLD)
    return _process_raw_entities(raw_entities)


def extract_entities_for_chunks(chunks: list[dict]) -> list[dict]:
    """
    Batch entry point matching the embed_chunks() pattern used elsewhere
    in this pipeline — takes chunks from chunk.py (after embed_chunks()
    has run), runs GLiNER batch inference with batch_size=16, adds an 'entities' key to each.

    [
        {
            "chunk_id":  "...",
            "text":      "...",
            "embedding": [...],
            "entities": [
                {"text": "أحمد محمد", "label": "Person", "score": 0.97}
            ],
            ...
        }
    ]
    """
    print(f"  Extracting entities from {len(chunks)} chunks...")

    # Collect non-empty texts and their original indices
    non_empty_indices = []
    non_empty_texts = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        if text and text.strip():
            non_empty_indices.append(i)
            non_empty_texts.append(text)
        else:
            chunk["entities"] = []

    if non_empty_texts:
        model = get_ner_model()
        total_entities = 0
        try:
            raw_entities_list = model.batch_predict_entities(
                non_empty_texts,
                GLINER_LABELS,
                threshold=CONFIDENCE_THRESHOLD
            )
            for idx, raw_entities in zip(non_empty_indices, raw_entities_list):
                entities = _process_raw_entities(raw_entities)
                chunks[idx]["entities"] = entities
                total_entities += len(entities)
        except Exception as e:
            print(f"  Warning: GLiNER batch inference failed: {e}. Falling back to sequential.")
            for idx, text in zip(non_empty_indices, non_empty_texts):
                try:
                    raw_entities = model.predict_entities(text, GLINER_LABELS, threshold=CONFIDENCE_THRESHOLD)
                    entities = _process_raw_entities(raw_entities)
                    chunks[idx]["entities"] = entities
                    total_entities += len(entities)
                except Exception as seq_e:
                    print(f"  Warning: failed to extract entities for chunk {idx}: {seq_e}")
                    chunks[idx]["entities"] = []
        
        print(f"  Extracted {total_entities} entities across {len(chunks)} chunks")
    else:
        print("  No non-empty chunks found for entity extraction.")

    # Clear GLiNER model from RAM immediately after task completion
    global _model
    if _model is not None:
        import gc
        _model = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    return chunks
