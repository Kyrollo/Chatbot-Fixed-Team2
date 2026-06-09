# Architecture Decisions

## Current Status

This repository now has a working backend RAG path with these implementation states:

| Component | Status | Notes |
|---|---|---|
| Gateway (`traefik`) | implemented | routes domain, ingestion, retrieval, generation, evaluation |
| Auth (`keycloak`) | implemented | seeded realm for local development |
| PostgreSQL | implemented | stores domains, documents, chunks, query logs |
| Redis | implemented | Celery broker/backend plus retrieval and generation cache |
| Qdrant | implemented | dense vector retrieval |
| Domain service | implemented | CRUD, members, config, internal access check |
| Ingestion service | implemented | upload, RBAC, enqueue, status polling |
| Worker service | implemented | extract, chunk, embed, index into Qdrant and PostgreSQL |
| Retrieval service | implemented | vector + BM25 + RRF + reranking + cache |
| Generation service | implemented | retrieval orchestration, prompting, LLM routing, answer cache |
| Evaluation service | implemented as stub | LLM-as-judge endpoint under Compose profile |
| Web UI | intentionally skipped | API-only sprint |

## Service Topology

```text
                           +------------------+
                           |    Keycloak      |
                           +---------+--------+
                                     |
Client -> Traefik -> generation-service -> retrieval-service -> Qdrant
                       |                 \-> PostgreSQL FTS
                       |
                       +-> domain-service -> PostgreSQL
                       |
                       +-> Groq API / Ollama
                       |
                       +-> Redis answer cache
                       |
                       +-> PostgreSQL query logs

Client -> Traefik -> ingestion-service -> Redis queue -> worker-service
                                                |            |
                                                |            +-> Qdrant
                                                |            +-> PostgreSQL document_chunks
                                                +-> status in PostgreSQL
```

## Decision 1: Single Root `.env`

All services now consume the same root `.env` through Compose `env_file`.

Why:

- one source of truth for local development
- fewer mismatches between service folders
- `pydantic-settings` already tolerates extra variables with `extra="ignore"`
- per-service `SERVICE_NAME` and `SERVICE_PORT` are injected through Compose overrides

Result:

- root `.env` is the only local secret/config file required for the stack
- service-level `.env` files are no longer part of the runtime path

## Decision 2: Retrieval Pipeline Uses Three Signals

`retrieval-service` is no longer dense-vector-only.

Implemented pipeline:

1. query embedding with `intfloat/multilingual-e5-base`
2. Qdrant dense search
3. PostgreSQL BM25 search on `document_chunks.search_vec`
4. Reciprocal Rank Fusion
5. cross-encoder reranking with `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
6. Redis retrieval cache

Why:

- vector search catches semantic similarity
- BM25 recovers exact keywords and acronyms
- RRF keeps the fusion logic simple and robust
- reranking improves final context quality before generation

## Decision 3: Generation Service Stays Separate

Answer generation is implemented as its own FastAPI service rather than being embedded into retrieval.

Why:

- retrieval and generation have different dependencies and scaling behavior
- per-domain LLM routing belongs in the generation boundary
- answer caching and query logging are easier to own here
- streaming and provider fallback do not complicate retrieval internals

Current flow:

1. validate JWT
2. check Redis answer cache
3. call `domain-service` for per-domain config
4. call `retrieval-service`
5. build prompt with citations
6. route to Groq or Ollama
7. cache answer and write query log

## Decision 4: Groq First, Ollama Fallback

The generation and evaluation layers use Groq when `GROQ_API_KEY` is configured. They fall back to Ollama when it is not, or when the domain config explicitly requests `local`.

Why:

- Groq keeps interactive latency practical on development hardware
- local Ollama remains available for sensitive domains or fully offline usage
- both expose an OpenAI-compatible API shape, so the routing layer stays small

Operational note:

- the containers expect host Ollama at `http://host.docker.internal:11434/v1`

## Decision 5: Evaluation Service Is a Profile

`evaluation-service` is available behind the Compose `evaluation` profile instead of starting by default.

Why:

- it is not on the core user path
- it can add extra LLM traffic and memory use during local development
- the current implementation is a useful stub, not a full analytics subsystem

Run it with:

```bash
docker compose --profile evaluation up --build
```

## Decision 6: Worker Maintains Dual Indexes

`worker-service` writes chunks into both:

- Qdrant for dense retrieval
- PostgreSQL `document_chunks` for BM25 retrieval

Why:

- indexing once during ingestion keeps query-time work small
- the dense and sparse retrieval layers stay consistent with the same chunk payloads
- retry behavior stays concentrated in the ingestion pipeline instead of the query path

## Decision 7: Redis Is Shared Across Queue and Cache

Redis is used for:

- Celery broker
- Celery result backend
- retrieval cache
- generation cache

Why:

- one operational dependency instead of several
- this fits local Docker development well
- the current scale target does not justify splitting queue and cache infrastructure

## Remaining Gaps

Still intentionally out of scope for this sprint:

- frontend chat UI
- advanced evaluation datasets and RAGAS-style scoring
- production Kubernetes/Helm manifests
- audit/event pipelines beyond query log inserts
- graph retrieval path

## Verification Summary

Validated during implementation:

- updated Python services compile successfully with `python -m compileall`
- `docker compose config` resolves successfully for the updated stack

The remaining runtime verification step is a full `docker compose up --build` with a real Groq key or host Ollama available.
