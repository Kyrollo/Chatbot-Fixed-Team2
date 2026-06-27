import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import asyncpg

from config import settings
from schemas.retrieval import ChunkResult
from .base_retriever import BaseRetriever
from .query_analyzer import extract_query_entities, normalize_query_text

logger = logging.getLogger(__name__)

_GRAPH_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_graph_name() -> str:
    graph_name = settings.AGE_GRAPH_NAME
    if not _GRAPH_NAME_RE.match(graph_name):
        raise ValueError(f"Invalid AGE graph name: {graph_name!r}")
    return graph_name


def parse_agtype(val):
    if not val:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str) and val.endswith("::agtype"):
        val = val[:-8]
    try:
        return json.loads(val)
    except Exception:
        if isinstance(val, str) and val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        return val


def _token_overlap(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))


def _score_match(query_entity: str, candidate: str) -> float:
    if not query_entity or not candidate:
        return 0.0
    if query_entity == candidate:
        return 1.0
    if query_entity in candidate or candidate in query_entity:
        return 0.9
    overlap = _token_overlap(query_entity, candidate)
    similarity = SequenceMatcher(None, query_entity, candidate).ratio()
    return max(overlap, similarity)


def _expand_aliases(name: str) -> list[str]:
    normalized = normalize_query_text(name)
    aliases = {normalized}
    compact = normalized.replace("-", "").replace(" ", "")
    if compact:
        aliases.add(compact)
    form_match = re.match(r"^(?:form\s+)?([a-z]{1,3})-?(\d{1,4})$", normalized)
    if form_match:
        prefix, suffix = form_match.groups()
        aliases.add(f"{prefix}{suffix}")
        aliases.add(f"{prefix}-{suffix}")
        aliases.add(f"form {prefix}-{suffix}")
    return [alias for alias in aliases if alias]


@dataclass
class GraphVertex:
    name: str
    normalized_name: str
    aliases: list[str]
    labels: list[str]
    chunk_ids: list[str]


class GraphRetriever(BaseRetriever):
    def __init__(self) -> None:
        self._age_pool: Optional[asyncpg.Pool] = None
        self._pg_pool: Optional[asyncpg.Pool] = None

    async def _get_age_pool(self) -> Optional[asyncpg.Pool]:
        if self._age_pool is None:
            dsn = settings.AGE_DATABASE_DSN
            if not dsn:
                logger.warning("AGE_DATABASE_DSN is not configured. Graph retrieval is disabled.")
                return None
            try:
                self._age_pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
            except Exception as exc:
                logger.error("Failed to create asyncpg pool for Apache AGE: %s", exc)
                return None
        return self._age_pool

    async def _get_pg_pool(self) -> asyncpg.Pool:
        if self._pg_pool is None:
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            self._pg_pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
        return self._pg_pool

    async def _load_vertices(self, age_conn: asyncpg.Connection, domain_id: str) -> list[GraphVertex]:
        graph_name = _safe_graph_name()
        params = json.dumps({"domain_id": domain_id})
        rows = await age_conn.fetch(
            f"""
            SELECT * FROM cypher('{graph_name}', $$
                MATCH (v)
                WHERE v.domain_id = $domain_id
                RETURN v.name, v.normalized_name, v.aliases, labels(v), v.chunk_ids
            $$, $1) AS (
                name agtype,
                normalized_name agtype,
                aliases agtype,
                labels agtype,
                chunk_ids agtype
            );
            """,
            params,
        )
        vertices: list[GraphVertex] = []
        for row in rows:
            vertices.append(
                GraphVertex(
                    name=parse_agtype(row["name"]) or "",
                    normalized_name=parse_agtype(row["normalized_name"]) or "",
                    aliases=parse_agtype(row["aliases"]) or [],
                    labels=parse_agtype(row["labels"]) or [],
                    chunk_ids=parse_agtype(row["chunk_ids"]) or [],
                )
            )
        return vertices

    async def search(self, query: str, domain_id: str, top_k: int) -> list[ChunkResult]:
        results, _ = await self.search_with_diagnostics(query, domain_id, top_k)
        return results

    async def search_with_diagnostics(self, query: str, domain_id: str, top_k: int) -> tuple[list[ChunkResult], dict]:
        diagnostics = {
            "enabled": bool(settings.AGE_DATABASE_DSN),
            "matched_entities": 0,
            "matched_chunk_ids": 0,
            "returned_chunks": 0,
            "skip_reason": None,
            "query_entities": [],
            "matches": [],
        }

        age_pool = await self._get_age_pool()
        if not age_pool:
            diagnostics["skip_reason"] = "age_not_configured"
            return [], diagnostics

        normalized_query = normalize_query_text(query)
        if not normalized_query:
            diagnostics["skip_reason"] = "empty_query"
            return [], diagnostics

        pg_pool = await self._get_pg_pool()

        try:
            async with age_pool.acquire() as age_conn:
                await age_conn.execute("LOAD 'age';")
                await age_conn.execute("SET search_path = ag_catalog, '$user', public;")
                vertices = await self._load_vertices(age_conn, domain_id)
                if not vertices:
                    diagnostics["skip_reason"] = "graph_has_no_vertices"
                    return [], diagnostics

                query_entities = extract_query_entities(query)
                if not query_entities and settings.QUERY_NER_MODE == "rules_first":
                    query_entities = [
                        normalize_query_text(vertex.name)
                        for vertex in vertices
                        if normalize_query_text(vertex.name)
                        and _token_overlap(normalize_query_text(vertex.name), normalized_query) >= 0.25
                    ][:8]

                diagnostics["query_entities"] = query_entities
                if not query_entities:
                    diagnostics["skip_reason"] = "query_ner_found_no_entities"
                    return [], diagnostics

                candidate_matches: list[tuple[float, str, GraphVertex, str]] = []
                for query_entity in query_entities:
                    for vertex in vertices:
                        candidate_names = {vertex.normalized_name, normalize_query_text(vertex.name)}
                        candidate_names.update(normalize_query_text(alias) for alias in vertex.aliases or [])
                        expanded_candidates = {
                            alias
                            for candidate in candidate_names
                            if candidate
                            for alias in _expand_aliases(candidate)
                        }
                        for candidate in expanded_candidates:
                            score = _score_match(query_entity, candidate)
                            if score >= settings.GRAPH_ENTITY_MATCH_THRESHOLD:
                                candidate_matches.append((score, query_entity, vertex, candidate))

                if not candidate_matches:
                    # Second pass with relaxed threshold for content-word fallback entities
                    relaxed_threshold = min(settings.GRAPH_ENTITY_MATCH_THRESHOLD, 0.3)
                    for query_entity in query_entities:
                        for vertex in vertices:
                            candidate_names = {vertex.normalized_name, normalize_query_text(vertex.name)}
                            candidate_names.update(normalize_query_text(alias) for alias in vertex.aliases or [])
                            expanded_candidates = {
                                alias
                                for candidate in candidate_names
                                if candidate
                                for alias in _expand_aliases(candidate)
                            }
                            for candidate in expanded_candidates:
                                score = _score_match(query_entity, candidate)
                                if score >= relaxed_threshold:
                                    candidate_matches.append((score, query_entity, vertex, candidate))
                    if not candidate_matches:
                        diagnostics["skip_reason"] = "query_entities_found_but_no_graph_match"
                        return [], diagnostics

                candidate_matches.sort(key=lambda item: item[0], reverse=True)
                selected_vertices: list[GraphVertex] = []
                seen_keys: set[tuple[str, str]] = set()
                for score, query_entity, vertex, candidate in candidate_matches:
                    vertex_key = (vertex.normalized_name, "|".join(vertex.labels))
                    if vertex_key in seen_keys:
                        continue
                    seen_keys.add(vertex_key)
                    selected_vertices.append(vertex)
                    diagnostics["matches"].append(
                        {
                            "query_entity": query_entity,
                            "graph_name": vertex.name or vertex.normalized_name,
                            "matched_candidate": candidate,
                            "score": round(score, 3),
                        }
                    )
                    if len(selected_vertices) >= settings.GRAPH_MAX_MATCHED_ENTITIES:
                        break

            chunk_ids: set[str] = set()
            for vertex in selected_vertices:
                chunk_ids.update(vertex.chunk_ids or [])

            diagnostics["matched_entities"] = len(selected_vertices)
            diagnostics["matched_chunk_ids"] = len(chunk_ids)
            if not chunk_ids:
                diagnostics["skip_reason"] = "graph_match_found_but_no_chunk_ids"
                return [], diagnostics

            async with pg_pool.acquire() as pg_conn:
                rows = await pg_conn.fetch(
                    """
                    SELECT
                        c.id,
                        c.document_id,
                        c.page_num,
                        c.chunk_index,
                        c.text,
                        d.filename,
                        COALESCE(c.source_type, 'pdf') AS source_type
                    FROM document_chunks c
                    JOIN documents d ON c.document_id = d.id
                    WHERE c.domain_id = $1 AND c.id = ANY($2::text[])
                    LIMIT $3
                    """,
                    domain_id,
                    list(chunk_ids),
                    top_k,
                )

            if not rows:
                diagnostics["skip_reason"] = "chunk_ids_found_but_missing_from_postgres"
                return [], diagnostics

            results = [
                ChunkResult(
                    chunk_id=row["id"],
                    document_id=row["document_id"],
                    filename=row["filename"],
                    source_type=row["source_type"],
                    chunk_index=row["chunk_index"] or 0,
                    page=row["page_num"],
                    text=row["text"],
                    score=0.95,
                    source="graph",
                )
                for row in rows
            ]
            diagnostics["returned_chunks"] = len(results)
            return results, diagnostics
        except Exception:
            logger.exception("GraphRetriever failed during execution")
            diagnostics["skip_reason"] = "graph_execution_failed"
            return [], diagnostics

    async def close(self) -> None:
        if self._age_pool is not None:
            await self._age_pool.close()
        if self._pg_pool is not None:
            await self._pg_pool.close()
