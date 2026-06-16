import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from cache import get_generation_cache
from config import settings
from dependencies import CurrentUser, check_domain_access
from llm_router import LLMRouter
from prompt_builder import build_messages
from schemas import Citation, QueryRequest, QueryResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/generate", tags=["generation"])

_http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
_llm_router = LLMRouter()
_cache = get_generation_cache()
_engine = create_async_engine(settings.DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def ensure_query_log_table() -> None:
    async with _engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rag_query_logs (
                    id BIGSERIAL PRIMARY KEY,
                    domain_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    llm_route TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )


async def log_query(*, domain_id: str, user_id: str, query: str, answer: str, llm_route: str, model: str) -> None:
    async with _session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO rag_query_logs (domain_id, user_id, query, answer, llm_route, model)
                VALUES (:domain_id, :user_id, :query, :answer, :llm_route, :model)
                """
            ),
            {
                "domain_id": domain_id,
                "user_id": user_id,
                "query": query,
                "answer": answer,
                "llm_route": llm_route,
                "model": model,
            },
        )
        await session.commit()


async def _fetch_domain_config(domain_id: str, token: str) -> dict:
    response = await _http.get(
        f"{settings.DOMAIN_SERVICE_URL}/domains/{domain_id}/config",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to fetch domain config: {response.text}",
        )
    return response.json()


async def _fetch_retrieval(request: QueryRequest, token: str) -> list[Citation]:
    response = await _http.post(
        f"{settings.RETRIEVAL_SERVICE_URL}/api/v1/retrieve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": request.query,
            "domain_id": request.domain_id,
            "top_k_retrieve": request.top_k_retrieve or settings.TOP_K_RETRIEVE,
            "top_k_rerank": request.top_k_rerank or settings.TOP_K_RERANK,
        },
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to retrieve context: {response.text}",
        )
    payload = response.json()
    return [Citation(**item) for item in payload.get("results", [])]


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@router.post("/query", response_model=QueryResponse)
async def generate_query(request: QueryRequest, user: CurrentUser) -> QueryResponse | StreamingResponse:
    # Domain-level RBAC: user must have at least reader access
    allowed = await check_domain_access(
        user_id=user["user_id"],
        domain_id=request.domain_id,
        required_role="reader",
        is_system_admin=user.get("is_system_admin", False),
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have reader or higher access to this domain.",
        )

    cached = await _cache.get(domain_id=request.domain_id, query=request.query)
    if cached is not None and not request.stream:
        return cached

    domain_config, citations = await asyncio.gather(
        _fetch_domain_config(request.domain_id, user["token"]),
        _fetch_retrieval(request, user["token"]),
    )

    messages = build_messages(request.query, citations)
    llm_route = domain_config.get("llm_route", "api")

    if request.stream:
        route_name, model, stream_iter = await _llm_router.stream(
            llm_route=llm_route,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        await _cache.incr(f"rag:metrics:llm:{route_name}")

        async def event_stream():
            answer_parts: list[str] = []
            async for chunk in stream_iter:
                answer_parts.append(chunk)
                yield chunk

            answer = "".join(answer_parts).strip()
            if answer:
                response = QueryResponse(
                    answer=answer,
                    citations=citations,
                    cache_hit=False,
                    llm_route=route_name,
                    model=model,
                )
                await _cache.set(domain_id=request.domain_id, query=request.query, response=response)
                await log_query(
                    domain_id=request.domain_id,
                    user_id=user["user_id"],
                    query=request.query,
                    answer=answer,
                    llm_route=route_name,
                    model=model,
                )

        return StreamingResponse(event_stream(), media_type="text/plain")

    route_name, model, answer = await _llm_router.complete(
        llm_route=llm_route,
        messages=messages,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )
    await _cache.incr(f"rag:metrics:llm:{route_name}")
    result = QueryResponse(
        answer=answer.strip(),
        citations=citations,
        cache_hit=False,
        llm_route=route_name,
        model=model,
    )
    await _cache.set(domain_id=request.domain_id, query=request.query, response=result)
    await log_query(
        domain_id=request.domain_id,
        user_id=user["user_id"],
        query=request.query,
        answer=result.answer,
        llm_route=route_name,
        model=model,
    )
    return result


async def close_router_resources() -> None:
    await _cache.close()
    await _http.aclose()
    await _llm_router.close()
    await _engine.dispose()
