"""
judge.py
---------
LLM-as-judge scorer.

Exports TWO things:
  - JudgeService class  → used by router.py for the live /evaluate endpoint
  - evaluate_answer()   → used by tasks/evaluate_batch.py for the batch job

Both use the same routing logic (Groq if key is set, Ollama otherwise) and
the same prompt. The function wrapper exists so evaluate_batch.py can call
the judge without instantiating or managing an async HTTP client — it just
calls evaluate_answer(query=, answer=, context=) and gets a dict back.

FIX: The original judge.py only had JudgeService. evaluate_batch.py imports
`from judge import evaluate_answer` (a module-level function), so the service
would crash at the first batch run with ImportError. This version adds that
function using a shared synchronous httpx call so it works from Celery tasks
(which are sync by default in Celery 5).
"""
import json
import asyncio

import httpx

from config import settings
from schemas import EvaluationRequest, EvaluationResponse


# ── Shared routing logic ────────────────────────────────────────────────────

def _route() -> tuple[str, str, str, dict[str, str]]:
    """Returns (route_label, base_url, model, headers)."""
    if settings.GROQ_API_KEY:
        return (
            "api",
            settings.GROQ_BASE_URL.rstrip("/"),
            settings.GROQ_MODEL,
            {"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        )
    return ("local", settings.OLLAMA_BASE_URL.rstrip("/"), settings.OLLAMA_MODEL, {})


def _build_payload(query: str, answer: str, context: str | None) -> dict:
    context_text = context or "No context provided."
    prompt = (
        "Score the answer from 0.0 to 1.0 on three dimensions:\n"
        "  - faithfulness: is every claim in the answer supported by the context?\n"
        "  - relevance: does the answer actually address the question?\n"
        "  - completeness: does the answer cover the full scope of the question?\n\n"
        "Return strict JSON with exactly these keys:\n"
        '  {"faithfulness": <float>, "relevance": <float>, "completeness": <float>, "explanation": "<string>"}\n\n'
        f"Query:\n{query}\n\nAnswer:\n{answer}\n\nContext:\n{context_text}"
    )
    route_label, _, model, _ = _route()
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict RAG evaluator. Output JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }


# ── Sync function for Celery tasks ──────────────────────────────────────────

def evaluate_answer(query: str, answer: str, context: str | None = None) -> dict:
    """
    Synchronous judge call for use from Celery tasks (evaluate_batch.py).

    Returns:
        {
            "faithfulness":  0.0-1.0,
            "relevance":     0.0-1.0,
            "completeness":  0.0-1.0,
            "raw_response":  "<full LLM JSON string>",
        }

    `context` will be None for every row coming from the batch job
    (rag_query_logs has no context column). The prompt treats None as
    "No context provided" and the LLM scores faithfulness as best it can
    from the answer alone — scores will be weaker than if real context
    were available, but will not error.
    """
    route_label, base_url, model, headers = _route()
    payload = _build_payload(query, answer, context)

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    raw = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(raw)

    return {
        "faithfulness":  max(0.0, min(1.0, float(parsed.get("faithfulness", 0.0)))),
        "relevance":     max(0.0, min(1.0, float(parsed.get("relevance", 0.0)))),
        "completeness":  max(0.0, min(1.0, float(parsed.get("completeness", 0.0)))),
        "raw_response":  raw,
    }


# ── Async class for the live /evaluate FastAPI endpoint ─────────────────────

class JudgeService:
    """Used by router.py — keeps a persistent async HTTP client."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        route_used, base_url, model, headers = _route()
        context = "\n\n".join(request.context_chunks[:5]) or "No context provided."
        payload = _build_payload(request.query, request.answer, context)
        payload["model"] = model  # override in case _build_payload used a cached value

        response = await self._client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)

        return EvaluationResponse(
            score=max(0.0, min(1.0, float(parsed.get("relevance", parsed.get("score", 0.0))))),
            explanation=str(parsed.get("explanation", "")).strip(),
            route_used=route_used,
            model=model,
        )

    async def close(self) -> None:
        await self._client.aclose()
