"""
QueryAnalyzer — lightweight local analysis of the incoming query.
"""
import re
from dataclasses import dataclass


@dataclass
class QueryAnalysis:
    query_type: str
    contains_entities: bool
    keyword_score: float


_ENTITY_PATTERNS = [
    r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b",
    r"\b\d{4}\b",
    r"\b[A-Z]{2,}\b",
]

_KEYWORD_STOPWORDS = {
    "what", "when", "where", "who", "why", "how",
    "is", "are", "was", "were", "do", "does", "did",
    "can", "could", "would", "should", "will",
    "the", "a", "an", "in", "of", "for", "to", "and",
    "ما", "هل", "كيف", "متى", "أين", "من", "لماذا",
}


def analyze_query(query: str) -> QueryAnalysis:
    words = query.strip().split()
    word_count = len(words)
    lower_words = {w.lower().strip(".,?!") for w in words}
    stopword_count = len(lower_words & _KEYWORD_STOPWORDS)
    stopword_ratio = stopword_count / max(word_count, 1)
    length_score = max(0.0, 1.0 - (word_count / 15))
    keyword_score = round((length_score * 0.5) + ((1 - stopword_ratio) * 0.5), 2)
    keyword_score = max(0.0, min(1.0, keyword_score))
    contains_entities = any(re.search(pat, query) for pat in _ENTITY_PATTERNS)

    if contains_entities:
        query_type = "entity"
    elif keyword_score >= 0.6:
        query_type = "keyword"
    else:
        query_type = "semantic"

    return QueryAnalysis(
        query_type=query_type,
        contains_entities=contains_entities,
        keyword_score=keyword_score,
    )
