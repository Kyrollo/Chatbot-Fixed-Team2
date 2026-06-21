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

from typing import Any

from ontology import ENTITY_TYPES

# ------------------------------------------------------------------
# Model is loaded ONCE when the first chunk needing NER arrives.
# get_ner_model() returns the same instance every call — no re-loading.
# ------------------------------------------------------------------

_model: Any = None  # GLiNER, lazily loaded

MODEL_NAME = "urchade/gliner_multi-v2.1"

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
    "a person name": "Person",
    "a project name or system": "Project",
    "a department in an organization": "Department",
    "a policy or regulation": "Policy",
    "a job role or position": "Role",
    "a geographical location": "Location",
    "a technical skill or expertise": "Skill",
}

GLINER_LABELS: list[str] = list(_LABEL_TO_ENTITY_TYPE.keys())

# Safety net: if ontology.py ever gains a new EntityType, this catches
# the mismatch immediately at import time instead of silently
# extracting entities under a type that has no matching graph vlabel.
assert set(_LABEL_TO_ENTITY_TYPE.values()) == set(ENTITY_TYPES), (
    "ner.py's GLiNER label mapping is out of sync with ontology.py — "
    "add the new entity type's semantic phrasing to _LABEL_TO_ENTITY_TYPE."
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
        print(f"Loading NER model: {MODEL_NAME}...")
        from gliner import GLiNER  # noqa: PLC0415 — see module docstring

        _model = GLiNER.from_pretrained(MODEL_NAME)
        print("NER model loaded")
    return _model


def extract_entities(text: str) -> list[dict]:
    """
    Runs NER on a single chunk of text using this project's ontology
    (services/worker-service/ontology.py — Person, Project, Department,
    Policy, Role, Location, Skill), via GLiNER's semantic label phrasing
    (see GLINER_LABELS above).

    Returns entities above CONFIDENCE_THRESHOLD only, already mapped
    back to ontology names:
    [
        {"text": "أحمد محمد", "label": "Person", "score": 0.97},
        {"text": "قسم تقنية المعلومات", "label": "Department", "score": 0.89},
        ...
    ]

    Returns an empty list for text with no recognized entities — this
    is the expected, common case (e.g. the spike test's diabetes-guide
    sentence had none), not an error condition.
    """
    if not text or not text.strip():
        return []

    model = get_ner_model()
    raw_entities = model.predict_entities(text, GLINER_LABELS, threshold=CONFIDENCE_THRESHOLD)

    return [
        {
            "text": ent["text"],
            "label": _LABEL_TO_ENTITY_TYPE[ent["label"]],
            "score": round(float(ent["score"]), 4),
        }
        for ent in raw_entities
    ]


def extract_entities_for_chunks(chunks: list[dict]) -> list[dict]:
    """
    Batch entry point matching the embed_chunks() pattern used elsewhere
    in this pipeline — takes chunks from chunk.py (after embed_chunks()
    has run), adds an 'entities' key to each.

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

    total_entities = 0
    for chunk in chunks:
        entities = extract_entities(chunk["text"])
        chunk["entities"] = entities
        total_entities += len(entities)

    print(f"  Extracted {total_entities} entities across {len(chunks)} chunks")
    return chunks
