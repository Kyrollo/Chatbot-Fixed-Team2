"""
RetrievalRouter — uses an LLM to decide which retrieval engines to activate.
Falls back to vector+BM25 on any failure.
"""
import json
import logging
from dataclasses import dataclass

import httpx

from config import settings
from .query_analyzer import QueryAnalysis

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    use_vector: bool
    use_bm25: bool
    use_graph: bool
    vector_weight: float
    bm25_weight: float
    graph_weight: float
    decided_by: str


_FALLBACK = RoutingDecision(
    use_vector=True,
    use_bm25=True,
    use_graph=False,
    vector_weight=0.7,
    bm25_weight=0.3,
    graph_weight=0.0,
    decided_by="fallback",
)

_SYSTEM_PROMPT = """\
You are a retrieval routing assistant.
Given a user query and its analysis, decide which search engines to use.

Available engines:
- vector: semantic / meaning-based search (finds conceptually similar content)
- bm25:   keyword-based full-text search  (finds exact matching terms)
- graph:  not yet implemented, always false

Reply ONLY with valid JSON and nothing else:
{
  "use_vector": true or false,
  "use_bm25":   true or false,
  "use_graph":  false
}

Rules:
- use_graph must always be false.
- DEFAULT: use both vector=true and bm25=true together for best results.
- Use vector=true, bm25=false ONLY for pure conversational questions (e.g. "how are you", "what is love").
- Use vector=false, bm25=true ONLY for exact code/ID lookups (e.g. "error code 404", "invoice #1234").
- For ALL other queries including topic searches, named concepts, academic terms, Arabic text → use both: vector=true, bm25=true.
"""


def _weights_from_decision(decision: dict) -> tuple[float, float, float]:
    use_v = decision.get("use_vector", True)
    use_b = decision.get("use_bm25", True)
    if use_v and use_b:
        return 0.7, 0.3, 0.0
    if use_v:
        return 1.0, 0.0, 0.0
    if use_b:
        return 0.0, 1.0, 0.0
    return 0.7, 0.3, 0.0


def _choose_llm() -> tuple[str, str, dict]:
    if settings.GROQ_API_KEY:
        return (
            f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions",
            settings.GROQ_MODEL,
            {"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        )
    return (
        f"{settings.OLLAMA_BASE_URL.rstrip('/')}/chat/completions",
        settings.OLLAMA_MODEL,
        {},
    )


async def route_query(query: str, analysis: QueryAnalysis) -> RoutingDecision:
    user_message = (
        f"Query: {query}\n"
        f"Analysis: query_type={analysis.query_type}, "
        f"contains_entities={analysis.contains_entities}, "
        f"keyword_score={analysis.keyword_score}"
    )
    url, model, headers = _choose_llm()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_message},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                },
                headers=headers,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

        decision = json.loads(raw)

        if not decision.get("use_vector") and not decision.get("use_bm25"):
            logger.warning("LLM returned all engines disabled — using fallback")
            return _FALLBACK

        decision["use_graph"] = False
        v_w, b_w, g_w = _weights_from_decision(decision)

        logger.info(
            "LLM routing: vector=%s bm25=%s (decided_by=llm)",
            decision["use_vector"], decision["use_bm25"],
        )

        return RoutingDecision(
            use_vector=bool(decision["use_vector"]),
            use_bm25=bool(decision["use_bm25"]),
            use_graph=False,
            vector_weight=v_w,
            bm25_weight=b_w,
            graph_weight=g_w,
            decided_by="llm",
        )

    except Exception as exc:
        logger.warning("RetrievalRouter LLM call failed (%s) — using fallback", exc)
        return _FALLBACK