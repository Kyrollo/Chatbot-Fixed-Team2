import os
import json
import uuid
import logging
import psycopg2
from ner import normalize_arabic
from ontology import ENTITY_TYPES, RELATION_TYPES

logger = logging.getLogger(__name__)

def get_connection():
    dsn = os.getenv("AGE_DATABASE_DSN")
    if not dsn:
        logger.warning("AGE_DATABASE_DSN is not configured. Graph operations will be skipped.")
        return None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        conn.set_client_encoding('UTF8')
        with conn.cursor() as cur:
            cur.execute("LOAD 'age';")
            cur.execute("SET search_path = ag_catalog, '$user', public;")
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to Apache AGE database: {e}")
        return None

def execute_cypher(cur, query: str, params: dict = None) -> list:
    """
    Executes a Cypher query on Apache AGE using a temporary prepared statement
    to ensure parameters are correctly parsed by the cypher() function.
    """
    if params is None:
        cur.execute(query)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return []
            
    stmt_name = f"stmt_{uuid.uuid4().hex}"
    param_str = json.dumps(params, ensure_ascii=False)
    prep_query = f"PREPARE {stmt_name} (agtype) AS {query}"
    
    cur.execute(prep_query)
    try:
        cur.execute(f"EXECUTE {stmt_name} (%s);", (param_str,))
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return []
    finally:
        try:
            cur.execute(f"DEALLOCATE {stmt_name};")
        except Exception:
            pass

def write_to_graph(chunks: list[dict], triples: list[dict], domain_id: str, document_id: str) -> dict:
    """
    Persists entities and relationships (triples) extracted from chunks into Apache AGE.
    Resolves provenance links and handles Arabic text normalization.
    """
    conn = get_connection()
    if not conn:
        return {"status": "skipped", "reason": "No database connection"}
        
    try:
        with conn.cursor() as cur:
            # Build helper map of normalized entity names to labels and original names
            entity_label_map = {}
            for chunk in chunks:
                for ent in chunk.get("entities", []):
                    norm = ent["normalized_text"]
                    entity_label_map[norm] = ent["label"]
            
            # Step 1: Upsert Vertices
            logger.info("Upserting entities into Apache AGE knowledge graph...")
            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                for ent in chunk.get("entities", []):
                    label = ent["label"]
                    if label not in ENTITY_TYPES:
                        continue
                    
                    name = ent["text"]
                    norm_name = ent["normalized_text"]
                    
                    # Check if vertex already exists
                    match_query = f"""
                        SELECT * FROM cypher('rag_graph', $$
                            MATCH (v:{label})
                            WHERE v.normalized_name = $normalized_name AND v.domain_id = $domain_id
                            RETURN properties(v)
                        $$, $1) AS (properties agtype);
                    """
                    match_res = execute_cypher(cur, match_query, {
                        "normalized_name": norm_name,
                        "domain_id": domain_id
                    })
                    
                    if match_res:
                        # Vertex exists: merge chunk_ids
                        props = json.loads(match_res[0][0])
                        existing_chunks = props.get("chunk_ids", [])
                        if chunk_id not in existing_chunks:
                            new_chunks = list(existing_chunks) + [chunk_id]
                            update_query = f"""
                                SELECT * FROM cypher('rag_graph', $$
                                    MATCH (v:{label})
                                    WHERE v.normalized_name = $normalized_name AND v.domain_id = $domain_id
                                    SET v.chunk_ids = $chunk_ids
                                    RETURN id(v)
                                $$, $1) AS (id agtype);
                            """
                            execute_cypher(cur, update_query, {
                                "normalized_name": norm_name,
                                "domain_id": domain_id,
                                "chunk_ids": new_chunks
                            })
                    else:
                        # Vertex doesn't exist: create new
                        create_query = f"""
                            SELECT * FROM cypher('rag_graph', $$
                                CREATE (v:{label} {{
                                    name: $name,
                                    normalized_name: $normalized_name,
                                    domain_id: $domain_id,
                                    document_id: $document_id,
                                    chunk_ids: $chunk_ids
                                }})
                                RETURN id(v)
                            $$, $1) AS (id agtype);
                        """
                        execute_cypher(cur, create_query, {
                            "name": name,
                            "normalized_name": norm_name,
                            "domain_id": domain_id,
                            "document_id": document_id,
                            "chunk_ids": [chunk_id]
                        })
            
            # Step 2: Upsert Edges
            logger.info("Upserting relation triples into Apache AGE knowledge graph...")
            edge_count = 0
            for triple in triples:
                relation_type = triple.get("relation")
                if relation_type not in RELATION_TYPES:
                    logger.warning(f"Skipping unregistered relation type: {relation_type}")
                    continue
                    
                sub_norm = normalize_arabic(triple.get("subject", ""))
                obj_norm = normalize_arabic(triple.get("object", ""))
                
                sub_label = entity_label_map.get(sub_norm)
                obj_label = entity_label_map.get(obj_norm)
                
                if not sub_label or not obj_label:
                    # Skip relations referring to entities that were filtered out during NER
                    logger.debug(f"Skipping triple with missing labels: {triple}")
                    continue
                
                # Determine chunk co-occurrence for provenance link
                edge_chunks = []
                for chunk in chunks:
                    chunk_text_norm = normalize_arabic(chunk["text"])
                    if sub_norm in chunk_text_norm and obj_norm in chunk_text_norm:
                        edge_chunks.append(chunk["chunk_id"])
                        
                if not edge_chunks:
                    # Fallback to chunks where they were individually seen
                    for chunk in chunks:
                        chunk_ents = [e["normalized_text"] for e in chunk.get("entities", [])]
                        if sub_norm in chunk_ents or obj_norm in chunk_ents:
                            edge_chunks.append(chunk["chunk_id"])
                
                # Check if edge already exists
                match_edge_query = f"""
                    SELECT * FROM cypher('rag_graph', $$
                        MATCH (a:{sub_label})-[r:{relation_type}]->(b:{obj_label})
                        WHERE a.normalized_name = $sub_name AND a.domain_id = $domain_id
                          AND b.normalized_name = $obj_name AND b.domain_id = $domain_id
                        RETURN properties(r)
                    $$, $1) AS (properties agtype);
                """
                match_edge_res = execute_cypher(cur, match_edge_query, {
                    "sub_name": sub_norm,
                    "obj_name": obj_norm,
                    "domain_id": domain_id
                })
                
                if match_edge_res:
                    # Edge exists: update chunk_ids
                    props = json.loads(match_edge_res[0][0])
                    existing_chunks = props.get("chunk_ids", [])
                    merged_chunks = list(set(existing_chunks + edge_chunks))
                    
                    update_edge_query = f"""
                        SELECT * FROM cypher('rag_graph', $$
                            MATCH (a:{sub_label})-[r:{relation_type}]->(b:{obj_label})
                            WHERE a.normalized_name = $sub_name AND a.domain_id = $domain_id
                              AND b.normalized_name = $obj_name AND b.domain_id = $domain_id
                            SET r.chunk_ids = $chunk_ids
                            RETURN id(r)
                        $$, $1) AS (id agtype);
                    """
                    execute_cypher(cur, update_edge_query, {
                        "sub_name": sub_norm,
                        "obj_name": obj_norm,
                        "domain_id": domain_id,
                        "chunk_ids": merged_chunks
                    })
                else:
                    # Edge doesn't exist: create new
                    create_edge_query = f"""
                        SELECT * FROM cypher('rag_graph', $$
                            MATCH (a:{sub_label}), (b:{obj_label})
                            WHERE a.normalized_name = $sub_name AND a.domain_id = $domain_id
                              AND b.normalized_name = $obj_name AND b.domain_id = $domain_id
                            CREATE (a)-[r:{relation_type} {{
                                domain_id: $domain_id,
                                document_id: $document_id,
                                chunk_ids: $chunk_ids
                            }}]->(b)
                            RETURN id(r)
                        $$, $1) AS (id agtype);
                    """
                    execute_cypher(cur, create_edge_query, {
                        "sub_name": sub_norm,
                        "obj_name": obj_norm,
                        "domain_id": domain_id,
                        "document_id": document_id,
                        "chunk_ids": edge_chunks
                    })
                edge_count += 1
                
            logger.info(f"Graph population completed: created/updated relations ({edge_count})")
            return {"status": "success", "edges": edge_count}
            
    except Exception as e:
        logger.error(f"Error occurred while writing to AGE graph: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        conn.close()
