import json

import httpx

from config import settings
from schemas import EvaluationRequest, EvaluationResponse


class JudgeService:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    def _route(self) -> tuple[str, str, str, dict[str, str]]:
        if settings.GROQ_API_KEY:
            return (
                "api",
                settings.GROQ_BASE_URL.rstrip("/"),
                settings.GROQ_MODEL,
                {"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            )
        return ("local", settings.OLLAMA_BASE_URL.rstrip("/"), settings.OLLAMA_MODEL, {})

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        route_used, base_url, model, headers = self._route()
        context = "\n\n".join(request.context_chunks[:5]) or "No context provided."
        prompt = (
            "Score the answer from 0.0 to 1.0 for how well it answers the query using the given context. "
            "Return strict JSON with keys score and explanation.\n\n"
            f"Query:\n{request.query}\n\nAnswer:\n{request.answer}\n\nContext:\n{context}"
        )
        response = await self._client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a strict RAG evaluator. Output JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
        return EvaluationResponse(
            score=max(0.0, min(1.0, float(payload.get("score", 0.0)))),
            explanation=str(payload.get("explanation", "")).strip(),
            route_used=route_used,
            model=model,
        )

    async def close(self) -> None:
        await self._client.aclose()
