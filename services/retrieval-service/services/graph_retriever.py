import logging
import json
import re
import asyncpg
from typing import Optional
from config import settings
from schemas.retrieval import ChunkResult
from .base_retriever import BaseRetriever

logger = logging.getLogger(__name__)

def normalize_arabic(text: str) -> str:
    """
    Standardize Arabic text to handle spelling variants, diacritics, and spaces.
    """
    if not text:
        return ""
    # Strip diacritics (Harakat)
    text = re.sub(r'[\u064B-\u0652]', '', text)
    # Normalize Alif variants (أ, إ, آ, ٱ) -> ا
    text = re.sub(r'[أإآٱ]', 'ا', text)
    # Normalize Alif Maqsura (ى) -> Ya (ي)
    text = re.sub(r'ى', 'ي', text)
    # Normalize Ta Marbuta (ة) -> Ha (ه)
    text = re.sub(r'ة', 'ه', text)
    # Collapse multiple whitespaces and strip
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_agtype(val: str):
    """
    Parses agtype JSON values returned from Apache AGE queries.
    """
    if not val:
        return None
    if isinstance(val, (dict, list)):
        return val
    # Strip trailing ::agtype if present
    if isinstance(val, str) and val.endswith("::agtype"):
        val = val[:-8]
    try:
        return json.loads(val)
    except Exception:
        if isinstance(val, str) and val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        return val

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
            except Exception as e:
                logger.error(f"Failed to create asyncpg pool for Apache AGE: {e}")
                return None
        return self._age_pool

    async def _get_pg_pool(self) -> asyncpg.Pool:
        if self._pg_pool is None:
            dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
            self._pg_pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
        return self._pg_pool

    async def search(self, query: str, domain_id: str, top_k: int) -> list[ChunkResult]:
        """
        Executes query-time entity extraction, traverses the graph in Apache AGE,
        and fetches matching source chunks from PostgreSQL.
        """
        age_pool = await self._get_age_pool()
        if not age_pool:
            return []

        pg_pool = await self._get_pg_pool()

        try:
            # 1. Query-Time NER: Retrieve all vertices in this domain and find matches
            normalized_query = normalize_arabic(query)
            if not normalized_query:
                return []

            params = json.dumps({"domain_id": domain_id})
            
            async with age_pool.acquire() as age_conn:
                await age_conn.execute("LOAD 'age';")
                await age_conn.execute("SET search_path = ag_catalog, '$user', public;")
                
                # Fetch all vertices in the domain to perform fast substring entity matching
                vertices_rows = await age_conn.fetch("""
                    SELECT * FROM cypher('rag_graph', $$
                        MATCH (v)
                        WHERE v.domain_id = $domain_id
                        RETURN v.normalized_name, labels(v)
                    $$, $1) AS (normalized_name agtype, labels agtype);
                """, params)

                matched_entities = []
                for row in vertices_rows:
                    norm_name = parse_agtype(row["normalized_name"])
                    labels_list = parse_agtype(row["labels"])
                    
                    if norm_name and norm_name in normalized_query:
                        label = labels_list[0] if labels_list else "Entity"
                        matched_entities.append((norm_name, label))

                if not matched_entities:
                    logger.info("No entities matched in query-time NER. Skipping graph traversal.")
                    return []

                logger.info(f"Query-time NER matched: {matched_entities}")

                # 2. Graph Traversal: For each matched entity, do a 1-hop traversal to collect chunk_ids
                chunk_ids = set()
                for norm_name, label in matched_entities:
                    traverse_params = json.dumps({
                        "domain_id": domain_id,
                        "normalized_name": norm_name
                    }, ensure_ascii=False)

                    # Optional match to handle entities with and without connections
                    traverse_query = f"""
                        SELECT * FROM cypher('rag_graph', $$
                            MATCH (v:{label})
                            WHERE v.normalized_name = $normalized_name AND v.domain_id = $domain_id
                            OPTIONAL MATCH (v)-[r]-(neighbor)
                            RETURN properties(v), properties(r), properties(neighbor)
                        $$, $1) AS (v_props agtype, r_props agtype, n_props agtype);
                    """
                    traverse_rows = await age_conn.fetch(traverse_query, traverse_params)

                    for row in traverse_rows:
                        v_props = parse_agtype(row["v_props"])
                        r_props = parse_agtype(row["r_props"])
                        n_props = parse_agtype(row["n_props"])

                        if isinstance(v_props, dict):
                            chunk_ids.update(v_props.get("chunk_ids", []))
                        if isinstance(r_props, dict):
                            chunk_ids.update(r_props.get("chunk_ids", []))
                        if isinstance(n_props, dict):
                            chunk_ids.update(n_props.get("chunk_ids", []))

            if not chunk_ids:
                logger.info("No chunk_ids associated with matching graph entities.")
                return []

            logger.info(f"Graph retrieval matched chunk_ids: {chunk_ids}")

            # 3. Chunk Lookup: Retrieve full texts and metadata from PostgreSQL
            async with pg_pool.acquire() as pg_conn:
                sql = """
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
                """
                chunks_rows = await pg_conn.fetch(sql, domain_id, list(chunk_ids), top_k)

            results = [
                ChunkResult(
                    chunk_id=row["id"],
                    document_id=row["document_id"],
                    filename=row["filename"],
                    source_type=row["source_type"],
                    chunk_index=row["chunk_index"] or 0,
                    page=row["page_num"],
                    text=row["text"],
                    score=0.95,  # High score for explicit graph-linked context
                    source="graph",
                )
                for row in chunks_rows
            ]
            
            logger.info(f"Graph retrieval returned {len(results)} chunks")
            return results

        except Exception as e:
            logger.exception("GraphRetriever failed during execution")
            return []

    async def close(self) -> None:
        if self._age_pool is not None:
            await self._age_pool.close()
        if self._pg_pool is not None:
            await self._pg_pool.close()
