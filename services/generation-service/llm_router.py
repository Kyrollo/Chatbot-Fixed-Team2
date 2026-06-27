import asyncio
import json
import logging
from typing import AsyncIterator

import httpx

from config import settings

logger = logging.getLogger(__name__)


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

    def _generate_mock_answer(self, messages: list[dict[str, str]]) -> str:
        user_content = messages[1]["content"] if len(messages) > 1 else ""
        query = ""
        context = ""
        
        if "Question:\n" in user_content:
            try:
                parts = user_content.split("Question:\n", 1)[1].split("\n\nRetrieved context:\n", 1)
                query = parts[0].strip()
                if len(parts) > 1:
                    context = parts[1].split("\n\nAnswer the question", 1)[0].strip()
            except Exception:
                pass
        
        if not query:
            query = "your query"
            
        if not context or "No supporting context retrieved" in context:
            return f"Based on the available documents, I could not find direct information to answer: '{query}'."

        # Extract lines that are actual text (skip citations headers)
        lines = []
        for line in context.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if line_str.startswith("[") and (line_str.endswith("]") or "file=" in line_str):
                continue
            if "file=" in line_str or "page=" in line_str or "sheet=" in line_str or "rows=" in line_str:
                continue
            lines.append(line_str)
        
        # Build a nice mock answer
        answer = f"Based on the retrieved context, here is what I found regarding '{query}':\n\n"
        matched_sentences = []
        
        # Look for keywords
        keywords = [w.lower() for w in query.split() if len(w) > 3]
        for line in lines:
            if any(kw in line.lower() for kw in keywords):
                matched_sentences.append(line)
                if len(matched_sentences) >= 3:
                    break
        
        if not matched_sentences:
            # Grab first few lines
            for line in lines:
                if len(line) > 30:
                    matched_sentences.append(line)
                    if len(matched_sentences) >= 2:
                        break
        
        if matched_sentences:
            answer += " • " + "\n • ".join(matched_sentences)
        else:
            answer += "The context contains matching reference files, but no specific text highlights could be extracted."
            
        answer += "\n\n(Note: This response was generated in mock fallback mode because the local LLM service is offline or unreachable.)"
        return answer

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
        try:
            response = await self._client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            return route_name, model, answer
        except Exception as exc:
            logger.warning(f"LLM request to {base_url} failed: {exc}. Falling back to mock response.")
            answer = self._generate_mock_answer(messages)
            return "mock-fallback", "mock-model", answer

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
            fallback_needed = False
            try:
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
            except Exception as exc:
                logger.warning(f"LLM stream to {base_url} failed: {exc}. Falling back to mock response stream.")
                fallback_needed = True

            if fallback_needed:
                mock_answer = self._generate_mock_answer(messages)
                # Split by words to simulate streaming
                words = mock_answer.split(" ")
                for i, word in enumerate(words):
                    yield (word + " ") if i < len(words) - 1 else word
                    await asyncio.sleep(0.02)

        return route_name, model, iterator()

    async def close(self) -> None:
        await self._client.aclose()

