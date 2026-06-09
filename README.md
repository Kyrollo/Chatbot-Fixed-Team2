# Chatbot-Fixed-Team2

Multi-user, multi-domain RAG system for the Fixed Solutions AI Internship 2026.

The stack now includes the full backend path for domain management, ingestion, retrieval, answer generation, and evaluation. The chat UI is intentionally skipped in this sprint; all workflows are exposed through HTTP APIs.

## What Is Implemented

| Service | Port | Purpose | Status |
|---|---:|---|---|
| `traefik` | `80`, `8080` | Gateway and dashboard | working |
| `keycloak` | `8180` | Auth and JWT issuance | working |
| `postgres` | `5432` | Domain metadata, documents, chunks, query logs | working |
| `redis` | `6379` | Queue broker, results backend, semantic cache | working |
| `qdrant` | `6333` | Dense vector search | working |
| `domain-service` | `8001` | Domain CRUD, members, per-domain config, internal RBAC checks | working |
| `ingestion-service` | `8002` | Upload PDFs, validate access, enqueue jobs, poll status | working |
| `worker-service` | queue | Extract, chunk, embed, index into Qdrant and PostgreSQL FTS | working |
| `retrieval-service` | `8003` | Vector search + BM25 + RRF + reranking + retrieval cache | working |
| `generation-service` | `8004` | Query orchestration, answer generation, answer cache, query logs | working |
| `evaluation-service` | `8005` | LLM-as-judge scoring stub | optional profile |

## Architecture

```text
Client
  -> Traefik
     -> Keycloak-authenticated request
        -> generation-service
           -> retrieval-service
              -> Qdrant vector search
              -> PostgreSQL BM25 search
              -> Redis retrieval cache
           -> domain-service config lookup
           -> Groq API or local Ollama
           -> Redis answer cache
           -> PostgreSQL query log

Upload flow
  -> Traefik
     -> ingestion-service
        -> domain-service internal access check
        -> PostgreSQL documents table
        -> Redis/Celery queue
        -> worker-service
           -> PDF extract
           -> semantic chunk
           -> embedding
           -> Qdrant index
           -> PostgreSQL document_chunks FTS index
```

## Prerequisites

- Docker Desktop with Compose
- Python 3.11 if you want to run service code directly
- A Groq API key for cloud generation:
  - set `GROQ_API_KEY=gsk_...` in `.env`
- Optional local Ollama fallback:
  - install Ollama on the host machine
  - pull `llama3.2:3b`
  - the containers call `http://host.docker.internal:11434/v1`

## Environment

The repo uses one root `.env` file.

Important variables:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | async Postgres URL for FastAPI services |
| `SYNC_DATABASE_URL` | sync Postgres URL for worker/indexing code |
| `REDIS_URL` | Redis queue and cache |
| `QDRANT_URL` | Qdrant endpoint |
| `KEYCLOAK_ISSUER` | comma-separated issuer list, Docker internal first |
| `DOMAIN_SERVICE_URL` | internal domain-service URL |
| `RETRIEVAL_SERVICE_URL` | internal retrieval-service URL |
| `GENERATION_SERVICE_URL` | internal generation-service URL |
| `EVALUATION_SERVICE_URL` | internal evaluation-service URL |
| `GROQ_API_KEY` | primary cloud LLM key |
| `OLLAMA_BASE_URL` | local fallback endpoint on the host |
| `TOP_K_RETRIEVE` | retrieval candidate count before reranking |
| `TOP_K_RERANK` | final chunk count after reranking |
| `CACHE_TTL_SECONDS` | Redis TTL for retrieval and generation cache |

## Start The Stack

Core stack:

```bash
docker compose up --build
```

Core stack plus evaluation service:

```bash
docker compose --profile evaluation up --build
```

Stop everything:

```bash
docker compose down
```

## Key URLs

- Traefik dashboard: `http://localhost:8080/dashboard/`
- Keycloak: `http://localhost:8180`
- Domain service docs: `http://localhost:8001/docs`
- Ingestion service docs: `http://localhost:8002/docs`
- Retrieval service docs: `http://localhost:8003/docs`
- Generation service docs: `http://localhost:8004/docs`
- Evaluation service docs: `http://localhost:8005/docs`

## End-to-End Query Flow

1. Client sends `POST /generate/query`.
2. `generation-service` validates JWT and checks Redis answer cache.
3. On cache miss, it fetches domain config from `domain-service`.
4. It calls `retrieval-service`.
5. `retrieval-service` embeds the query, runs:
   - Qdrant dense retrieval
   - PostgreSQL BM25 search on `document_chunks.search_vec`
   - Reciprocal Rank Fusion
   - cross-encoder reranking
6. `generation-service` builds the RAG prompt.
7. It routes to:
   - Groq if `llm_route=api` and `GROQ_API_KEY` exists
   - Ollama otherwise
8. It returns the answer with citations, caches it, and logs it to PostgreSQL.

## Ingestion Flow

1. Client uploads a PDF to `POST /ingest`.
2. `ingestion-service` validates JWT and domain membership.
3. File is written under `/data/uploads`.
4. Metadata is inserted into PostgreSQL `documents`.
5. Celery enqueues `worker.tasks.process_document`.
6. `worker-service`:
   - extracts PDF text
   - semantically chunks it
   - embeds chunks with `multilingual-e5-base`
   - indexes vectors into Qdrant
   - indexes chunk text into PostgreSQL `document_chunks`
   - updates document status

## API Usage

### 1. Get a token from Keycloak

```bash
curl -X POST "http://localhost:8180/realms/rag-system/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=domain-service" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password"
```

### 2. Create a domain

```bash
curl -X POST "http://localhost/domains" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Knowledge Base",
    "description": "Internal support documents"
  }'
```

### 3. Upload a PDF

```bash
curl -X POST "http://localhost/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -F "domain_id=$DOMAIN_ID" \
  -F "file=@sample.pdf"
```

### 4. Poll ingestion status

```bash
curl "http://localhost/ingest/$DOCUMENT_ID" \
  -H "Authorization: Bearer $TOKEN"
```

### 5. Retrieve chunks directly

```bash
curl -X POST "http://localhost/retrieve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the refund policy?",
    "domain_id": "'"$DOMAIN_ID"'",
    "top_k_retrieve": 10,
    "top_k_rerank": 5
  }'
```

### 6. Generate an answer

```bash
curl -X POST "http://localhost/generate/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarize the refund policy",
    "domain_id": "'"$DOMAIN_ID"'",
    "top_k_retrieve": 10,
    "top_k_rerank": 5
  }'
```

### 7. Evaluate an answer

```bash
curl -X POST "http://localhost/evaluate" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Summarize the refund policy",
    "answer": "The policy allows returns within 30 days.",
    "context_chunks": [
      "Refunds are allowed within 30 days with proof of purchase."
    ]
  }'
```

## Smoke / Manual Test

Recommended manual flow:

1. `docker compose up --build`
2. fetch JWT
3. create a domain
4. upload a PDF
5. poll `/ingest/{document_id}` until `done`
6. call `/generate/query`
7. repeat the same query and confirm a cache hit in the response

Basic gateway smoke test still exists:

```bash
cd services/gateway
python smoke_test.py
```

## Web UI

The UI is intentionally not included in this sprint. The intended UI would provide:

- login via Keycloak
- domain picker
- chat panel
- citation sidebar
- upload view
- ingestion status view
- admin screens for domain members and per-domain LLM routing

For now, the backend is operated directly through the documented APIs.
