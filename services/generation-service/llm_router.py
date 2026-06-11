import json
from typing import AsyncIterator

import httpx

from config import settings


class LLMRouter:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    def choose_route(self, llm_route: str | None) -> tuple[str, str, str, dict[str, str]]:
        normalized = (llm_route or "").strip().lower()
        if normalized == "local" or (normalized != "api" and not settings.GROQ_API_KEY):
            return (
                "local",
                settings.OLLAMA_BASE_URL.rstrip("/"),
                settings.OLLAMA_MODEL,
                {},
            )

        return (
            "api",
            settings.GROQ_BASE_URL.rstrip("/"),
            settings.GROQ_MODEL,
            {"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        )

    async def complete(
        self,
        *,
        llm_route: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str, str]:
        route_name, base_url, model, headers = self.choose_route(llm_route)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        response = await self._client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        answer = data["choices"][0]["message"]["content"]
        return route_name, model, answer

    async def stream(
        self,
        *,
        llm_route: str | None,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str, AsyncIterator[str]]:
        route_name, base_url, model, headers = self.choose_route(llm_route)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async def iterator() -> AsyncIterator[str]:
            async with self._client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta

        return route_name, model, iterator()

    async def close(self) -> None:
        await self._client.aclose()
