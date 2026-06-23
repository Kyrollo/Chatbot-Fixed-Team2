"""
QueryAnalyzer — lightweight local analysis of the incoming query.
"""
import re
from dataclasses import dataclass


_ENTITY_PATTERNS = [
    r'"([^"]{2,80})"',
    r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b",
    r"\b(?:Form\s+)?[A-Z]{1,3}-?\d{1,4}\b",
    r"\b\d{4}\b",
    r"[\u0600-\u06FF]{2,}(?:\s+[\u0600-\u06FF]{2,}){0,3}",
]

_RELATIONAL_PATTERNS = [
    # Original patterns
    "manages", "belongs to", "reports to", "owns", "works on", "works in", "related to",
    "has role", "has skill", "based at", "who manages", "what is related to",
    # Phase 4: expanded relation types
    "defined by", "requires", "applies to", "part of", "references", "issued by",
    "has section", "defines", "required for", "referenced in", "under",
    # Arabic original
    "من يدير", "يتبع", "يتبع ل", "مسؤول عن", "مرتبط", "علاقه", "علاقة", "يعمل في",
    # Arabic expanded
    "معرّف بواسطة", "يتطلب", "ينطبق على", "جزء من", "يشير إلى", "صادر عن",
]

_KEYWORD_STOPWORDS = {
    "what", "when", "where", "who", "why", "how",
    "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "would", "should", "will",
    "the", "a", "an", "in", "of", "for", "to", "and",
    "ما", "هل", "كيف", "متى", "أين", "من", "لماذا",
}


def normalize_query_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\u064B-\u0652]", "", text)
    text = re.sub(r"[أإآٱ]", "ا", text)
    text = re.sub(r"ى", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = text.casefold()
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = re.sub(r"[^\w\s\-\u0600-\u06FF]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_query_entities(query: str) -> list[str]:
    candidates: list[str] = []
    for pattern in _ENTITY_PATTERNS:
        for match in re.finditer(pattern, query):
            value = match.group(1) if match.groups() else match.group(0)
            normalized = normalize_query_text(value)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates[:8]


@dataclass
class QueryAnalysis:
    query_type: str
    contains_entities: bool
    keyword_score: float
    relationship_intent: bool
    extracted_entities: list[str]


def analyze_query(query: str) -> QueryAnalysis:
    words = query.strip().split()
    word_count = len(words)
    lower_words = {w.lower().strip(".,?!") for w in words}
    stopword_count = len(lower_words & _KEYWORD_STOPWORDS)
    stopword_ratio = stopword_count / max(word_count, 1)
    length_score = max(0.0, 1.0 - (word_count / 15))
    keyword_score = round((length_score * 0.5) + ((1 - stopword_ratio) * 0.5), 2)
    keyword_score = max(0.0, min(1.0, keyword_score))
    extracted_entities = extract_query_entities(query)
    contains_entities = bool(extracted_entities)
    query_lower = normalize_query_text(query)
    relationship_intent = any(pattern in query_lower for pattern in _RELATIONAL_PATTERNS)

    if contains_entities or relationship_intent:
        query_type = "entity"
    elif keyword_score >= 0.6:
        query_type = "keyword"
    else:
        query_type = "semantic"

    return QueryAnalysis(
        query_type=query_type,
        contains_entities=contains_entities,
        keyword_score=keyword_score,
        relationship_intent=relationship_intent,
        extracted_entities=extracted_entities,
    )
