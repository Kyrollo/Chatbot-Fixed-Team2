# Architecture Decisions

## Current Status

This repository has a working backend RAG path running locally (no Docker). All services are launched via `run_services.py`.

| Component | Status | Notes |
|---|---|---|
| Gateway (`traefik`) | implemented | routes domain, ingestion, retrieval, generation, evaluation |
| Auth (`keycloak`) | implemented | seeded realm, auto-downloaded by `run_services.py` |
| PostgreSQL | implemented | stores domains, documents, chunks, query logs |
| Redis | implemented | Celery broker/backend plus retrieval and generation cache; auto-downloaded portable version on Windows |
| Qdrant | implemented | embedded local storage at `data/qdrant` (no server needed) |
| Domain service | implemented | CRUD, members, config, internal access check |
| Ingestion service | implemented | upload, RBAC, enqueue, status polling |
| Worker service | implemented | extract, chunk, embed, index into Qdrant and PostgreSQL |
| Retrieval service | implemented | vector + BM25 + RRF + reranking + cache |
| Generation service | implemented | retrieval orchestration, prompting, LLM routing, answer cache |
| Evaluation service | implemented as stub | LLM-as-judge endpoint, started with `--evaluation` flag |
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

All services consume the same root `.env` loaded by `run_services.py`.

Why:

- one source of truth for local development
- fewer mismatches between service folders
- `pydantic-settings` already tolerates extra variables with `extra="ignore"`
- per-service overrides (ports, names) are injected by the launcher

Result:

- root `.env` is the only local secret/config file required for the stack
- service-level `.env` files are no longer part of the runtime path

## Decision 2: Retrieval Pipeline Uses Three Signals

`retrieval-service` is not dense-vector-only. It implements a multi-stage hybrid pipeline:

1. query embedding with `intfloat/multilingual-e5-small` (384 dimensions)
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

- Ollama runs on the host machine at `http://localhost:11434/v1`

## Decision 5: Evaluation Service Is Optional

`evaluation-service` is started only when the `--evaluation` flag is passed to `run_services.py`.

Why:

- it is not on the core user path
- it can add extra LLM traffic and memory use during local development
- the current implementation is a useful stub, not a full analytics subsystem

Run it with:

```bash
python run_services.py --evaluation
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

When Redis is unavailable, the system gracefully degrades:

- in-memory TTL cache (`scripts/memory_cache.py`) replaces Redis cache
- sync subprocess replaces Celery async ingestion

Why:

- one operational dependency instead of several
- graceful degradation means Redis is not a hard requirement for local development
- the current scale target does not justify splitting queue and cache infrastructure

## Decision 8: Repository Hygiene — Single README, Comprehensive `.gitignore`

All project documentation (setup, usage, API walkthrough, troubleshooting) is consolidated in a single `README.md`.

Why:

- a single entry point is easier for new developers to find and follow
- eliminates duplicate/conflicting instructions across multiple files
- `.gitignore` now covers all generated artifacts: `__pycache__`, `.venv`, `data/`, `tools/`, `infra/`, `scripts/fixtures/`, and IDE files

Result:

- previous files `LOCAL_SETUP.md`, `PORTS_AND_RUN.md`, and `PROJECT_GUIDE.md` are merged into `README.md` and removed
- `Architecture_Decisions.md` remains separate as a living architectural record

## Decision 9: Scripts Directory Contains Shared Runtime Modules

The `scripts/` directory contains both test scripts and shared modules imported by services at runtime:

| Script | Used by |
|---|---|
| `dev_auth.py` | `run_services.py`, `timed_e2e_test.py`, `smoke_test.py` |
| `infra_manager.py` | `run_services.py`, `timed_e2e_test.py` |
| `memory_cache.py` | `retrieval-service`, `generation-service` |
| `network_bootstrap.py` | `run_services.py`, `retrieval-service` |
| `qdrant_client_factory.py` | `worker-service`, `retrieval-service` |
| `timed_e2e_test.py` | standalone end-to-end test |

Why:

- `run_services.py` adds `scripts/` to `PYTHONPATH` so services can import shared modules
- avoids duplicating utility code across each service directory
- the shared modules are small, focused, and have no external dependencies beyond the project's `requirements.txt`

## Remaining Gaps

Still intentionally out of scope for this sprint:

- frontend chat UI
- advanced evaluation datasets and RAGAS-style scoring
- production Kubernetes/Helm manifests
- audit/event pipelines beyond query log inserts
- graph retrieval path

## Verification Summary

Validated during implementation:

- Python services compile successfully with `python -m compileall`
- `run_services.py` starts all API services without errors
- `timed_e2e_test.py` validates the full query flow (auth → domain → ingest → generate)
- embedding model (`intfloat/multilingual-e5-small`, 384 dimensions) is consistent between worker indexing and retrieval querying

*Last updated: June 2026*
