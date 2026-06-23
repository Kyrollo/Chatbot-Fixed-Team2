"""
Relation Extraction — Task 2.3

Takes the entities already extracted by ner.py (Task 2.1/2.2) and
determines the RELATIONSHIPS between them, producing graph triples:
    (subject, relation, object)

WHY GROUPED, NOT PER-CHUNK:
────────────────────────────────────────────────────────────────────
Calling the LLM once per chunk would mean ~200 calls for a 200-chunk
document — slow and expensive. Calling it once for the whole document
risks exceeding context limits on long documents. This module batches
chunks into fixed-size groups (CHUNKS_PER_GROUP) and makes ONE LLM
call per group — e.g. ~10 calls for a 200-chunk document instead of
200 or 1.

WHY LLM AND NOT spaCy DEPENDENCY PARSING:
────────────────────────────────────────────────────────────────────
A spike investigation found spaCy has no official Arabic dependency
parser — `ar_core_news_sm` doesn't exist in spaCy's model catalog at
all, and the unofficial community attempts require manual, fragile
installation (symlinking files into spaCy's own package directory).
Published Arabic dependency-parsing accuracy in academic benchmarks
tops out around 76% (vs ~88% for English) even with dedicated
research tooling spaCy doesn't have. An LLM (Groq, already used by
retrieval_router.py) understands sentence meaning directly rather
than depending on a parser that doesn't reliably exist for Arabic.

WHY NO OVERLAP BETWEEN GROUPS:
────────────────────────────────────────────────────────────────────
A relation whose subject and object fall in different groups (e.g.
chunk 18 and chunk 22, split across two 20-chunk groups) will be
missed. This is a deliberate, documented trade-off — see the Sprint 3
discussion — not an oversight. Revisit CHUNKS_PER_GROUP or add overlap
if testing shows this loses relations that matter in practice.
"""
from __future__ import annotations

import json
import os

import httpx
from dotenv import load_dotenv

from ontology import RELATION_TYPES

load_dotenv()

# Matches the env var names already used by retrieval-service/generation-service
# (see services/generation-service/config.py) for consistency across the
# project, but read via os.getenv like the rest of worker-service/tasks/process.py
# rather than pydantic_settings — worker-service has no config.py/Settings
# class of its own, so this follows its existing plain-os.getenv pattern
# instead of introducing a new one for just this module.
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Decided in the Sprint 3 discussion: balances LLM call count (cost/time)
# against context size. ~600 words/chunk * 20 chunks ≈ 12k words of
# source text per call, comfortably within Groq's context window.
CHUNKS_PER_GROUP = 20

_RELATION_TYPES_STR = ", ".join(RELATION_TYPES)

_SYSTEM_PROMPT = f"""\
You are a relation extraction assistant for a knowledge graph.

You will receive a list of entities (already extracted by a separate NER
step) and the source text they came from. Identify relationships between
the entities using ONLY these relation types:
{_RELATION_TYPES_STR}

Reply ONLY with a JSON array of triples and nothing else:
[
  {{"subject": "...", "relation": "...", "object": "..."}}
]

Rules:
- subject and object must be entity texts exactly as given in the input list — do not paraphrase or translate them.
- relation must be exactly one of the allowed relation types above — no other values.
- Only extract relations that are actually stated or clearly implied in the text. Do not guess.
- If no clear relations exist between the given entities, return an empty array: []
"""


def _group_chunks(chunks: list[dict], group_size: int) -> list[list[dict]]:
    """Splits chunks into fixed-size, sequential, non-overlapping groups."""
    return [chunks[i:i + group_size] for i in range(0, len(chunks), group_size)]


def _build_user_message(group: list[dict]) -> str | None:
    """
    Builds the LLM prompt for one group: entities found in the group
    (deduplicated by text+label) plus the source text for context.
    Returns None if the group has no entities at all — nothing to relate.
    """
    seen = set()
    entities = []
    for chunk in group:
        for ent in chunk.get("entities", []):
            key = (ent["text"], ent["label"])
            if key not in seen:
                seen.add(key)
                entities.append(ent)

    if not entities:
        return None

    entity_lines = "\n".join(f'- "{e["text"]}" ({e["label"]})' for e in entities)
    source_text = "\n\n".join(chunk["text"] for chunk in group)

    return (
        f"Entities found in this text:\n{entity_lines}\n\n"
        f"Source text:\n{source_text}"
    )


def _call_llm(user_message: str) -> list[dict]:
    """
    Single Groq call. Returns [] on any failure (timeout, bad JSON,
    network error, missing API key) — relation extraction is additive
    graph data, not core RAG functionality, so a failed group should
    not raise and should not block the rest of the document. Mirrors
    the fallback philosophy in retrieval_router.py.
    """
    if not GROQ_API_KEY:
        print("  [WARN] GROQ_API_KEY not set — skipping relation extraction for this group")
        return []

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{GROQ_BASE_URL.rstrip('/')}/chat/completions",
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 1500,
                },
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

        triples = json.loads(raw)
        if not isinstance(triples, list):
            print(f"  [WARN] LLM returned non-list JSON, skipping group: {raw[:200]}")
            return []

        # Validate every triple against the ontology — an LLM that
        # hallucinates a relation type outside RELATION_TYPES must not
        # silently reach graph_writer.py (Task 2.4) later.
        valid = []
        for t in triples:
            if not isinstance(t, dict):
                continue
            if t.get("relation") not in RELATION_TYPES:
                print(f"  [WARN] dropping triple with invalid relation type: {t}")
                continue
            if not t.get("subject") or not t.get("object"):
                continue
            valid.append({
                "subject": t["subject"],
                "relation": t["relation"],
                "object": t["object"],
            })
        return valid

    except Exception as exc:
        print(f"  [WARN] Relation extraction LLM call failed for this group: {exc}")
        return []


def extract_relations_for_chunks(chunks: list[dict]) -> list[dict]:
    """
    Entry point — call after ner.py's extract_entities_for_chunks() has
    already populated each chunk's 'entities' key.

    Groups chunks (CHUNKS_PER_GROUP at a time, in document order),
    makes one LLM call per group, and returns all triples found across
    the whole document:
    [
        {"subject": "أحمد محمد", "relation": "MANAGES", "object": "مشروع تطوير النظام"},
        ...
    ]

    Returns an empty list if no entities were found anywhere in the
    document (e.g. NER step 5 failed upstream) — not an error.
    """
    groups = _group_chunks(chunks, CHUNKS_PER_GROUP)
    print(f"  Extracting relations from {len(chunks)} chunks in {len(groups)} group(s) of up to {CHUNKS_PER_GROUP}...")

    all_triples: list[dict] = []
    for i, group in enumerate(groups, start=1):
        user_message = _build_user_message(group)
        if user_message is None:
            continue  # no entities in this group — nothing to relate

        triples = _call_llm(user_message)
        print(f"    Group {i}/{len(groups)}: {len(triples)} relation(s) found")
        all_triples.extend(triples)

    print(f"  Extracted {len(all_triples)} total relations across {len(chunks)} chunks")
    return all_triples
