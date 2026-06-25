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

ALLOW_MOCK_JUDGE (config setting):
  - False (default): LLM failures raise exceptions rather than returning
    fake scores. This makes failures visible in logs and dashboards.
  - True: falls back to a deterministic mock score when the LLM is offline.
    Set to True in dev environments where no LLM is configured.
"""
import json
import logging
import asyncio

import httpx

from config import settings
from schemas import EvaluationRequest, EvaluationResponse

logger = logging.getLogger(__name__)


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
    _, _, model, _ = _route()
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


# ── Judge health check ────────────────────────────────────────────────────

async def check_judge_health() -> dict:
    """
    Probes the configured LLM endpoint to verify connectivity.
    Returns a dict with keys: reachable (bool), route (str), model (str), error (str|None).
    Used by GET /evaluate/judge-health.
    """
    route_label, base_url, model, headers = _route()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(
                base_url.replace("/openai/v1", "") + "/models" if route_label == "local" else base_url + "/models",
                headers=headers,
            )
            reachable = resp.status_code < 500
    except Exception as exc:
        return {"reachable": False, "route": route_label, "model": model, "error": str(exc)}
    return {"reachable": reachable, "route": route_label, "model": model, "error": None}


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

    Raises RuntimeError if the LLM call fails and ALLOW_MOCK_JUDGE is False.
    """
    route_label, base_url, model, headers = _route()
    payload = _build_payload(query, answer, context)

    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
    except Exception as exc:
        if settings.ALLOW_MOCK_JUDGE:
            logger.error(
                "Judge LLM call failed — returning MOCK scores "
                "(ALLOW_MOCK_JUDGE=True). Real evaluation is NOT running. "
                "Error: %s", exc
            )
            parsed = {
                "faithfulness": 0.85,
                "relevance": 0.90,
                "completeness": 0.80,
                "explanation": f"[MOCK — judge offline: {exc}]",
            }
            raw = json.dumps(parsed)
        else:
            logger.error("Judge LLM call failed (ALLOW_MOCK_JUDGE=False — raising): %s", exc)
            raise RuntimeError(f"Judge LLM call failed: {exc}") from exc

    return {
        "faithfulness":  max(0.0, min(1.0, float(parsed.get("faithfulness", 0.0)))),
        "relevance":     max(0.0, min(1.0, float(parsed.get("relevance", 0.0)))),
        "completeness":  max(0.0, min(1.0, float(parsed.get("completeness", 0.0)))),
        "raw_response":  raw,
        "is_mock":       parsed.get("explanation", "").startswith("[MOCK"),
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
        payload["model"] = model

        try:
            response = await self._client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(raw)
        except Exception as exc:
            if settings.ALLOW_MOCK_JUDGE:
                logger.warning(
                    "Judge LLM call failed (ALLOW_MOCK_JUDGE=True — returning mock): %s", exc
                )
                parsed = {
                    "score": 0.88,
                    "explanation": f"Mock evaluation (judge offline: {exc}).",
                }
                route_used = "mock-fallback"
                model = "mock-model"
            else:
                logger.error(
                    "Judge LLM call failed (ALLOW_MOCK_JUDGE=False): route=%s model=%s error=%s",
                    route_used, model, exc,
                )
                raise RuntimeError(f"Judge LLM unavailable: {exc}") from exc

        faithfulness = None
        if "faithfulness" in parsed:
            try:
                faithfulness = max(0.0, min(1.0, float(parsed["faithfulness"])))
            except (ValueError, TypeError):
                pass

        completeness = None
        if "completeness" in parsed:
            try:
                completeness = max(0.0, min(1.0, float(parsed["completeness"])))
            except (ValueError, TypeError):
                pass

        return EvaluationResponse(
            score=max(0.0, min(1.0, float(parsed.get("relevance", parsed.get("score", 0.0))))),
            explanation=str(parsed.get("explanation", "")).strip(),
            route_used=route_used,
            model=model,
            faithfulness=faithfulness,
            completeness=completeness,
        )

    async def close(self) -> None:
        await self._client.aclose()
