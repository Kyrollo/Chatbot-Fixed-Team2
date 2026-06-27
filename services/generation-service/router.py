import asyncio
import json
import logging
import uuid
from typing import Any

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

_http = httpx.AsyncClient(
    timeout=httpx.Timeout(
        float(settings.RETRIEVAL_TIMEOUT_SECONDS),
        connect=10.0,
    )
)
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
                    citation_chunk_ids TEXT[] DEFAULT '{}',
                    retrieval_diagnostics JSONB,
                    evaluation_status TEXT DEFAULT 'pending',
                    cache_hit BOOLEAN DEFAULT FALSE,
                    correlation_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )
        await conn.execute(text("ALTER TABLE rag_query_logs ADD COLUMN IF NOT EXISTS citation_chunk_ids TEXT[] DEFAULT '{}'"))
        await conn.execute(text("ALTER TABLE rag_query_logs ADD COLUMN IF NOT EXISTS retrieval_diagnostics JSONB"))
        await conn.execute(text("ALTER TABLE rag_query_logs ADD COLUMN IF NOT EXISTS evaluation_status TEXT DEFAULT 'pending'"))
        await conn.execute(text("ALTER TABLE rag_query_logs ADD COLUMN IF NOT EXISTS correlation_id TEXT"))


async def log_query(
    *,
    domain_id: str,
    user_id: str,
    query: str,
    answer: str,
    llm_route: str,
    model: str,
    citation_chunk_ids: list[str],
    retrieval_diagnostics: dict[str, Any] | None,
    evaluation_status: str,
    correlation_id: str,
) -> int:
    async with _session_factory() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO rag_query_logs (
                    domain_id, user_id, query, answer, llm_route, model,
                    citation_chunk_ids, retrieval_diagnostics, evaluation_status, correlation_id
                )
                VALUES (
                    :domain_id, :user_id, :query, :answer, :llm_route, :model,
                    :citation_chunk_ids, CAST(:retrieval_diagnostics AS JSONB),
                    :evaluation_status, :correlation_id
                )
                RETURNING id
                """
            ),
            {
                "domain_id": domain_id,
                "user_id": user_id,
                "query": query,
                "answer": answer,
                "llm_route": llm_route,
                "model": model,
                "citation_chunk_ids": citation_chunk_ids,
                "retrieval_diagnostics": json.dumps(retrieval_diagnostics or {}),
                "evaluation_status": evaluation_status,
                "correlation_id": correlation_id,
            },
        )
        await session.commit()
        return int(result.scalar_one())


async def _update_evaluation_status(query_log_id: int, status_value: str) -> None:
    async with _session_factory() as session:
        await session.execute(
            text("UPDATE rag_query_logs SET evaluation_status = :status WHERE id = :query_log_id"),
            {"status": status_value, "query_log_id": query_log_id},
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


async def _fetch_retrieval(request: QueryRequest, token: str) -> tuple[list[Citation], dict[str, Any] | None]:
    url = f"{settings.RETRIEVAL_SERVICE_URL}/api/v1/retrieve"
    try:
        response = await _http.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": request.query,
                "domain_id": request.domain_id,
                "top_k_retrieve": request.top_k_retrieve or settings.TOP_K_RETRIEVE,
                "top_k_rerank": request.top_k_rerank or settings.TOP_K_RERANK,
            },
        )
    except httpx.ReadTimeout as exc:
        logger.error("Retrieval timed out calling %s after %ss", url, settings.RETRIEVAL_TIMEOUT_SECONDS)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Retrieval timed out.") from exc
    except (httpx.ReadError, httpx.RequestError) as exc:
        logger.error("Retrieval request failed for %s: %s", url, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Retrieval request failed.") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Failed to retrieve context: {response.text}",
        )
    payload = response.json()
    return [Citation(**item) for item in payload.get("results", [])], payload.get("diagnostics")


async def _submit_evaluation(
    query: str,
    answer: str,
    citations: list[Citation],
    query_log_id: int,
    correlation_id: str,
) -> None:
    try:
        response = await _http.post(
            f"{settings.EVALUATION_SERVICE_URL}/evaluate",
            json={
                "query": query,
                "answer": answer,
                "context_chunks": [citation.text for citation in citations],
                "query_id": query_log_id,
            },
            timeout=httpx.Timeout(float(settings.EVALUATION_TIMEOUT_SECONDS), connect=10.0),
        )
        response.raise_for_status()
        logger.info("Evaluation submitted for query_log_id=%s correlation_id=%s", query_log_id, correlation_id)
        await _update_evaluation_status(query_log_id, "completed")
    except Exception as exc:
        logger.warning(
            "Evaluation call failed for query_log_id=%s correlation_id=%s: %s",
            query_log_id, correlation_id, exc,
        )
        await _update_evaluation_status(query_log_id, "failed")


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


@router.post("/query", response_model=QueryResponse)
async def generate_query(request: QueryRequest, user: CurrentUser) -> QueryResponse | StreamingResponse:
    correlation_id = str(uuid.uuid4())
    logger.info(
        "query_submitted: correlation_id=%s domain=%s user=%s",
        correlation_id, request.domain_id, user["user_id"],
    )

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
        logger.info("cache_hit: correlation_id=%s", correlation_id)
        return cached

    domain_config, retrieval_data = await asyncio.gather(
        _fetch_domain_config(request.domain_id, user["token"]),
        _fetch_retrieval(request, user["token"]),
    )
    citations, retrieval_diagnostics = retrieval_data
    logger.info(
        "retrieval_completed: correlation_id=%s chunks=%d",
        correlation_id, len(citations),
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
                evaluation_status = "skipped" if not settings.EVALUATE_ON_GENERATION else "queued"
                query_log_id = await log_query(
                    domain_id=request.domain_id,
                    user_id=user["user_id"],
                    query=request.query,
                    answer=answer,
                    llm_route=route_name,
                    model=model,
                    citation_chunk_ids=[citation.chunk_id for citation in citations],
                    retrieval_diagnostics=retrieval_diagnostics,
                    evaluation_status=evaluation_status,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "generation_completed: correlation_id=%s query_log_id=%s route=%s model=%s",
                    correlation_id, query_log_id, route_name, model,
                )
                if settings.EVALUATE_ON_GENERATION:
                    logger.info("evaluation_enqueued: correlation_id=%s query_log_id=%s", correlation_id, query_log_id)
                    if settings.EVALUATE_SYNC:
                        await _submit_evaluation(request.query, answer, citations, query_log_id, correlation_id)
                    else:
                        asyncio.create_task(_submit_evaluation(request.query, answer, citations, query_log_id, correlation_id))

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
    evaluation_status = "skipped" if not settings.EVALUATE_ON_GENERATION else "queued"
    query_log_id = await log_query(
        domain_id=request.domain_id,
        user_id=user["user_id"],
        query=request.query,
        answer=result.answer,
        llm_route=route_name,
        model=model,
        citation_chunk_ids=[citation.chunk_id for citation in citations],
        retrieval_diagnostics=retrieval_diagnostics,
        evaluation_status=evaluation_status,
        correlation_id=correlation_id,
    )
    logger.info(
        "generation_completed: correlation_id=%s query_log_id=%s route=%s model=%s citations=%d",
        correlation_id, query_log_id, route_name, model, len(citations),
    )
    if settings.EVALUATE_ON_GENERATION:
        logger.info("evaluation_enqueued: correlation_id=%s query_log_id=%s", correlation_id, query_log_id)
        if settings.EVALUATE_SYNC:
            await _submit_evaluation(request.query, result.answer, citations, query_log_id, correlation_id)
        else:
            asyncio.create_task(_submit_evaluation(request.query, result.answer, citations, query_log_id, correlation_id))
    return result


async def close_router_resources() -> None:
    await _cache.close()
    await _http.aclose()
    await _llm_router.close()
    await _engine.dispose()
