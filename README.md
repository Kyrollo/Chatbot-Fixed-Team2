# Chatbot-Fixed-Team2

Multi-user, multi-domain RAG (Retrieval-Augmented Generation) system for the Fixed Solutions AI Internship 2026.

A complete backend + frontend stack for domain management, document ingestion, hybrid retrieval, AI answer generation with citations, and evaluation. All workflows are exposed through HTTP APIs and a React chat UI.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Architecture Decisions](#3-architecture-decisions)
4. [Technology Stack](#4-technology-stack)
5. [Services Reference](#5-services-reference)
6. [Database Schema](#6-database-schema)
7. [Retrieval Pipeline](#7-retrieval-pipeline)
8. [Authentication & RBAC](#8-authentication--rbac)
9. [API Reference](#9-api-reference)
10. [Prerequisites](#10-prerequisites)
11. [Quick Start (5 Steps)](#11-quick-start-5-steps)
12. [Environment Variables](#12-environment-variables)
13. [Troubleshooting](#13-troubleshooting)
14. [Directory Layout](#14-directory-layout)
15. [Quick Reference Card](#15-quick-reference-card)

---

## 1. Project Overview

### What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain Retrieval-Augmented Generation (RAG) system**. It is a backend + frontend application that allows organizations to:

- Create separate **knowledge domains** (isolated knowledge bases, e.g., "HR Policies", "Tech Support", "Legal Contracts")
- Upload **PDF documents** into those domains
- Ask **natural language questions** and receive **AI-generated answers with citations** grounded in the uploaded documents

### What Is RAG? (Retrieval-Augmented Generation)

RAG is a technique that combines **information retrieval** (searching your own documents) with **language model generation** (AI writing). Instead of relying on the AI's general knowledge (which can be wrong or outdated), RAG:

1. **Retrieves** the most relevant passages from YOUR documents
2. **Gives those passages to the AI** as context
3. **The AI generates an answer** using ONLY those passages as evidence
4. **Cites the source** — telling you which document, page, and paragraph the answer came from

This means the AI's answers are **grounded in your actual data**, not hallucinated from training data.

### How the System Works — End to End

Here is what happens at each stage, step by step:

#### Stage 1: Domain Setup
An admin creates a **knowledge domain** — a named workspace that isolates one topic's documents, members, and settings. Each domain has its own RAG configuration (which AI model to use, how to split documents, confidence thresholds). Users are assigned roles (admin, contributor, reader) per domain.

#### Stage 2: Document Ingestion (Upload → Chunk → Index)
A user uploads a PDF to a domain. Here's what happens internally:

1. **`ingestion-service`** receives the PDF file and saves it to disk
2. It creates a `documents` record in PostgreSQL (status = `pending`)
3. It enqueues an async job into **Redis** (Celery task queue)
4. **`worker-service`** picks up the job and:
   - **Extracts text** from the PDF using PyMuPDF (+ Tesseract OCR for scanned pages)
   - **Splits the text into chunks** — each ~512 characters with 64-character overlap to avoid cutting sentences
   - **Generates embedding vectors** for each chunk using the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model.
   - **Stores vectors in Qdrant** — one collection per domain, each point contains the chunk text, document ID, page number, and chunk index
   - **Stores chunks in PostgreSQL** — with a `TSVECTOR` column for BM25 full-text search (keyword matching)
   - Updates the document status to `done` (or `failed` if there's an error)

#### Stage 3: Question Answering (Query → Retrieve → Generate)
A user asks a question. Here's the full pipeline:

1. **`generation-service`** receives the query and domain ID
2. It checks **Redis cache** — if this exact query was asked recently, return the cached answer instantly
3. It calls **`retrieval-service`** which runs a 6-stage hybrid retrieval pipeline:
   - **Embed the query** with the E5 model (prefixed with `query:` to match `passage:` prefixed chunks)
   - **Dense search** — cosine similarity search in Qdrant finds chunks with similar meaning
   - **Sparse search** — BM25 keyword search in PostgreSQL finds chunks with matching keywords
   - **RRF Fusion** — Reciprocal Rank Fusion merges both result lists into a single ranked list
   - **Cross-encoder reranking** — a separate reranker model (`mmarco-mMiniLMv2`) re-scores the top candidates for higher precision
   - **Cache results** in Redis for future queries
4. `generation-service` gets the domain's config (which LLM to use) from `domain-service`
5. It builds a **RAG prompt** — the user's question + the retrieved chunks formatted as numbered evidence paragraphs
6. It sends the prompt to the **LLM** (Groq cloud API or Ollama local) via an OpenAI-compatible API
7. The LLM generates an answer grounded in the evidence, with citations like `[1]`, `[2]`
8. The answer is **cached in Redis** and **logged in PostgreSQL** for audit
9. The response includes: the answer text, citations (which chunk, which document, which page, relevance score), the model used, and whether it was a cache hit

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-domain isolation | Each domain has its own documents, members, configuration, and vector collection. Complete data separation. |
| Role-based access (RBAC) | Two-layer security: Keycloak JWT tokens at the gateway + per-domain role checks in each service |
| Hybrid retrieval | Combines dense vector search (semantic meaning) + sparse BM25 search (exact keywords) + cross-encoder reranking for highest accuracy |
| AI answer generation | Groq (cloud, fast, free tier) or Ollama (local, offline). Per-domain LLM routing — some domains can use cloud, others local. |
| Async document processing | PDFs are processed in background via Celery + Redis. The user gets immediate `202 Accepted` and can poll status. |
| Intelligent caching | Redis caches both retrieval results and generated answers. Identical repeat queries return instantly. |
| Citation grounding | Every AI answer includes citations back to the exact chunk, document, and page number |
| Graceful degradation | If Redis is down → uses in-memory cache. If Groq is down → falls back to Ollama. If Keycloak is down → uses dev auth. |
| React chat UI | Full-featured web interface for login, domain management, document upload, and interactive Q&A |

### How It Works (Simple Analogy)

Think of the system as a **smart library with an AI librarian**:

1. **You create a shelf (domain)** — a labeled section of the library for one topic.
2. **You add books (PDFs)** — the system scans each book, splits it into paragraphs (chunks), and indexes them in two ways: by meaning (vectors) and by keywords (full-text search).
3. **You ask a question** — the librarian searches both indexes, picks the best paragraphs, and hands them to an AI writer.
4. **The AI answers** — using only those paragraphs as evidence, and tells you which pages they came from.

---

## 2. System Architecture

### 2.1 Service Topology

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    Traefik["Traefik Gateway :80"]
    KC["Keycloak :8180"]
    DS["domain-service :8001"]
    IS["ingestion-service :8002"]
    RS["retrieval-service :8003"]
    GS["generation-service :8004"]
    ES["evaluation-service :8005"]
    WS["worker-service (Celery)"]
    PG["PostgreSQL :5432"]
    RD["Redis :6379"]
    QD["Qdrant (embedded)"]
    LLM["Groq API / Ollama"]

    Client --> Traefik
    Traefik -->|JWT auth| KC
    Traefik --> DS
    Traefik --> IS
    Traefik --> GS
    Traefik --> ES

    DS --> PG
    IS --> PG
    IS --> RD
    RD --> WS
    WS --> PG
    WS --> QD

    GS --> RS
    GS --> DS
    GS --> LLM
    GS --> RD
    GS --> PG

    RS --> QD
    RS --> PG
    RS --> RD

    style KC fill:#f9a825,color:#000
    style PG fill:#336791,color:#fff
    style RD fill:#dc382d,color:#fff
    style QD fill:#24b47e,color:#fff
    style LLM fill:#7c3aed,color:#fff
```

### 2.2 Query Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant T as Traefik
    participant KC as Keycloak
    participant GS as generation-service
    participant DS as domain-service
    participant RS as retrieval-service
    participant QD as Qdrant
    participant PG as PostgreSQL
    participant LLM as Groq/Ollama
    participant RD as Redis

    C->>T: POST /generate/query (Bearer JWT)
    T->>KC: Validate JWT
    KC-->>T: OK
    T->>GS: Forward request

    GS->>RD: Check answer cache
    alt Cache hit
        RD-->>GS: Cached answer
        GS-->>C: Return answer (cache_hit=true)
    else Cache miss
        GS->>DS: GET /domains/{id}/config
        DS-->>GS: Domain config (llm_route, thresholds)

        GS->>RS: POST /api/v1/retrieve
        RS->>QD: Dense vector search
        RS->>PG: BM25 keyword search
        RS->>RS: RRF Fusion + Reranking
        RS-->>GS: Top-K chunks with scores

        GS->>GS: Build RAG prompt with citations
        GS->>LLM: Generate answer
        LLM-->>GS: AI response

        GS->>RD: Cache answer
        GS->>PG: Log query
        GS-->>C: Return answer + citations
    end
```

### 2.3 Ingestion Pipeline

```mermaid
sequenceDiagram
    participant C as Client
    participant IS as ingestion-service
    participant DS as domain-service
    participant PG as PostgreSQL
    participant RD as Redis
    participant WS as worker-service
    participant QD as Qdrant

    C->>IS: POST /ingest (PDF + domain_id)
    IS->>DS: Check user access (internal)
    DS-->>IS: Access granted

    IS->>PG: Insert document (status=pending)
    IS->>RD: Enqueue job
    IS-->>C: 202 Accepted (document_id)

    RD->>WS: Pick up job
    WS->>WS: Extract text (PyMuPDF + OCR)
    WS->>WS: Semantic chunking (E5 model)
    WS->>WS: Generate embeddings (passage: prefix)
    WS->>QD: Index vectors (1 collection per domain)
    WS->>PG: Store chunks + TSVECTOR index
    WS->>PG: Update status → done
```

### 2.4 Authentication Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant T as Traefik
    participant KC as Keycloak
    participant S as Service

    Note over C,KC: Option A: Keycloak Mode
    C->>KC: POST /token (username + password)
    KC-->>C: JWT access_token (5 min TTL)
    C->>T: Request + Bearer token
    T->>KC: forwardAuth → /userinfo
    KC-->>T: User verified
    T->>S: Forward with JWT
    S->>S: Decode JWT → extract user_id + roles
    S->>S: Check domain membership

    Note over C,S: Option B: Dev Auth Mode (no Keycloak)
    C->>S: POST /domains/auth/login {user_id}
    S-->>C: Dev JWT token
    C->>S: Request + Bearer dev token
    S->>S: Verify with local RSA key
```

---

## 3. Architecture Decisions

### Decision 1: Single Root `.env`

All services consume the same root `.env` loaded by `run_services.py`. One source of truth for local development. `pydantic-settings` tolerates extra variables with `extra="ignore"`. Per-service overrides (ports, names) are injected by the launcher.

### Decision 2: Retrieval Pipeline Uses Three Signals

`retrieval-service` implements a multi-stage hybrid pipeline: dense vector search (Qdrant) → sparse keyword search (PostgreSQL BM25) → Reciprocal Rank Fusion → cross-encoder reranking → Redis cache. Vector search catches semantic similarity; BM25 recovers exact keywords and acronyms; RRF keeps fusion robust; reranking improves final context quality.

### Decision 3: Generation Service Stays Separate

Answer generation is its own FastAPI service (not embedded in retrieval). Retrieval and generation have different dependencies and scaling behavior. Per-domain LLM routing, answer caching, query logging, and streaming belong in the generation boundary.

### Decision 4: Groq First, Ollama Fallback

Generation uses Groq when `GROQ_API_KEY` is configured, falls back to Ollama when not (or when domain config requests `local`). Both expose an OpenAI-compatible API shape, so the routing layer stays small. Groq keeps interactive latency practical on dev hardware; Ollama remains available for sensitive domains or fully offline usage.

### Decision 5: Evaluation Service Is Optional

Started only with `--evaluation` flag. Not on the core user path. Avoids extra LLM traffic during development.

### Decision 6: Worker Maintains Dual Indexes

Worker writes chunks into both Qdrant (dense) and PostgreSQL `document_chunks` (BM25). Indexing once at ingestion time keeps query-time work small. Dense and sparse retrieval layers stay consistent with the same chunk payloads.

### Decision 7: Redis Is Shared Across Queue and Cache

Redis serves as Celery broker, Celery result backend, retrieval cache, and generation cache. When unavailable, the system gracefully degrades: in-memory TTL cache replaces Redis cache; sync subprocess replaces Celery async ingestion.

### Decision 8: Repository Hygiene

All project documentation consolidated into `README.md` (this file) and `database_setup.md`. `.gitignore` covers all generated artifacts.

### Decision 9: Scripts Directory Contains Shared Runtime Modules

`scripts/` contains shared modules imported by services at runtime:

| Script | Used By |
|---|---|
| `dev_auth.py` | `run_services.py`, gateway smoke test |
| `infra_manager.py` | `run_services.py` |
| `memory_cache.py` | `retrieval-service`, `generation-service` |
| `network_bootstrap.py` | `run_services.py`, `retrieval-service` |
| `qdrant_client_factory.py` | `worker-service`, `retrieval-service`, `delete_chunks.py` |

`run_services.py` adds `scripts/` to `PYTHONPATH` so services can import shared modules.

---

## 4. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11–3.13 | Backend runtime |
| Web framework | FastAPI + Uvicorn | 0.115.6 / 0.34.0 | All microservices |
| Frontend | React + Vite + TypeScript | — | Chat UI at `rag-ui/` |
| Database | PostgreSQL | 16 | Domains, documents, chunks, query logs |
| Vector DB | Qdrant | 1.12.1 | Embedded dense vector search |
| Cache / Queue | Redis | 5.x | Celery broker + retrieval/answer cache |
| Task queue | Celery | 5.4.0 | Async document ingestion |
| Auth | Keycloak | 26.5.0 | OAuth2/OIDC, JWT tokens |
| API Gateway | Traefik | 3.0 | Edge routing + auth middleware |
| Embeddings | `intfloat/multilingual-e5-small` | — | 384-dim multilingual embeddings |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | — | Cross-encoder reranking |
| Cloud LLM | Groq | — | `llama-3.3-70b-versatile` |
| Local LLM | Ollama | — | `llama3.2:3b` (offline fallback) |
| PDF extraction | PyMuPDF + Tesseract | 1.25.2 | Text + OCR for scanned pages |
| ML runtime | PyTorch CPU | 2.6.0 | Embedding model inference |

---

## 5. Services Reference

### Service Map and Ports

| Component | Port(s) | Type | Purpose |
|---|---:|---|---|
| Traefik (gateway) | 80, 8080 | Reverse proxy | Routes API traffic, enforces auth at the edge |
| Keycloak | 8180 | Identity provider | Login, JWT token issuance |
| PostgreSQL | 5432 | Database | Domains, documents, chunks, query logs |
| Redis | 6379 | Cache + queue | Celery broker, retrieval cache, answer cache |
| Qdrant | — | Vector database | Dense embedding search (embedded, no server) |
| domain-service | 8001 | FastAPI | Domain CRUD, members, config, RBAC |
| ingestion-service | 8002 | FastAPI | PDF upload, job enqueue, status polling |
| worker-service | — | Celery worker | PDF extract → chunk → embed → index |
| retrieval-service | 8003 | FastAPI | Hybrid search pipeline |
| generation-service | 8004 | FastAPI | RAG orchestration and LLM answers |
| evaluation-service | 8005 | FastAPI | LLM-as-judge scoring (optional) |

### Deep Dive: What Each Service Does

#### 🟦 domain-service (Port 8001) — The Brain of the System

**What it does:** Manages all knowledge domains, user memberships, and domain-level configuration. It is the central authority that other services call to verify permissions.

**How it works internally:**
- **Domain CRUD:** Create, list, archive knowledge domains. Each domain is an isolated workspace with its own documents, members, and RAG settings.
- **RBAC enforcement:** When a user tries to upload a PDF or ask a question, the other services call `domain-service /internal/check-access` to verify the user has the right role on that domain.
- **Configuration management:** Each domain has a `domain_configs` record that controls: which LLM to use (`api` = Groq cloud, `local` = Ollama), chunk size, chunk overlap, and confidence threshold.
- **Dev auth:** In dev mode (no Keycloak), provides a `/domains/auth/login` endpoint where you POST a `user_id` and get a JWT token signed with a local RSA key.
- **Database:** Uses SQLAlchemy async ORM with PostgreSQL. Tables are auto-created on startup via `Base.metadata.create_all`.

**Key files:** `main.py` (FastAPI app), `routes/` (API endpoints), `models/` (SQLAlchemy models), `auth/` (JWT verification).

#### 🟧 ingestion-service (Port 8002) — The Document Receiver

**What it does:** Receives PDF uploads, validates access, saves files to disk, and enqueues background processing jobs.

**How it works internally:**
- **Upload handling:** Accepts multipart file uploads (max 50 MB by default). Saves the file to `data/uploads/{document_id}/{filename}`.
- **Access check:** Calls `domain-service /internal/check-access` to verify the user has `contributor` or higher role on the target domain.
- **Job enqueue:** Creates a `documents` record in PostgreSQL (status=`pending`) and pushes a Celery task into Redis. Returns `202 Accepted` immediately — the actual processing happens in the worker.
- **Status polling:** Provides `GET /ingest/{document_id}` to check if processing is `pending`, `processing`, `done`, or `failed`.
- **Sync fallback:** If Redis is not running, processes the document synchronously in a subprocess instead of enqueueing.

**Key files:** `main.py`, `routes/ingest.py` (upload + status endpoints).

#### 🟪 worker-service (Celery Worker) — The Document Processor

**What it does:** Runs in the background as a Celery worker. Picks up ingestion jobs from Redis and does the heavy lifting: text extraction, chunking, embedding, and indexing.

**How it works internally (step by step):**
1. **Text extraction:** Uses PyMuPDF (`fitz`) to extract text from PDF pages. For scanned/image PDFs, falls back to Tesseract OCR.
2. **Semantic chunking:** Splits extracted text into chunks of ~512 characters (configurable per domain) with 64-character overlap. The overlap ensures sentences aren't cut in half at chunk boundaries.
3. **Embedding generation:** Runs each chunk through the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model architecture.
4. **Vector indexing (Qdrant):** Stores embeddings in Qdrant with payloads containing the chunk text, document ID, page number, and chunk index. Each domain gets its own Qdrant collection (named by domain ID).
5. **BM25 indexing (PostgreSQL):** Inserts chunks into the `document_chunks` table with a `search_vec` TSVECTOR column for full-text keyword search.
6. **Status update:** Sets the document status to `done` (or `failed` with an error message).

**Important:** On Windows, Celery runs with `--pool=solo` (no fork support). This means one job at a time, but it's reliable.

**Key files:** `tasks/index.py` (the main ingestion task), `celery_app.py` (Celery configuration).

#### 🟩 retrieval-service (Port 8003) — The Search Engine

**What it does:** Implements the 6-stage hybrid retrieval pipeline. Given a user query and domain ID, it finds the most relevant document chunks.

**How it works internally (the 6 stages):**
1. **Query embedding:** Encodes the user's question using the E5 model with `query:` prefix (matching the `passage:` prefix used during indexing).
2. **Dense vector search (Qdrant):** Performs cosine similarity search in the domain's Qdrant collection. Returns the top-K most semantically similar chunks. Good at finding chunks with similar meaning even if they use different words.
3. **Sparse keyword search (PostgreSQL BM25):** Performs full-text search on the `search_vec` TSVECTOR column. Returns chunks that contain the same keywords. Good at finding exact term matches, abbreviations, and acronyms that vector search might miss.
4. **Reciprocal Rank Fusion (RRF):** Merges the dense and sparse result lists into a single ranked list using the RRF formula: `score = Σ 1/(k + rank_i)` with k=60. This is fairer than simple score averaging because it doesn't require the two search methods to produce comparable scores.
5. **Cross-encoder reranking:** Takes the top candidates from RRF and re-scores them using a cross-encoder model (`mmarco-mMiniLMv2`). Cross-encoders are more accurate than bi-encoders because they see the query AND the chunk simultaneously, but they're slower — that's why we only rerank the top candidates, not all chunks.
6. **Redis caching:** The final ranked results are cached in Redis with a TTL (default 1 hour). Identical queries skip all computation.

**Model loading:** The embedding model and reranker model are loaded into memory on first request (lazy loading). This makes the first query slow (~10-30 seconds) but subsequent queries fast.

**Key files:** `services/qdrant_search.py` (Qdrant client), `services/bm25_search.py` (PostgreSQL FTS), `services/reranker.py` (cross-encoder), `services/hybrid_retrieval.py` (orchestrates all stages).

#### 🟥 generation-service (Port 8004) — The AI Answer Writer

**What it does:** Orchestrates the full RAG pipeline: gets domain config, calls retrieval, builds the prompt, calls the LLM, and returns the answer with citations.

**How it works internally:**
1. **Cache check:** First checks Redis for a cached answer for this exact (query, domain_id) pair.
2. **Domain config:** Calls `domain-service` to get the domain's `llm_route` (api or local), confidence threshold, and other settings.
3. **Retrieval:** Calls `retrieval-service` with the query and domain ID. Gets back ranked chunks with relevance scores.
4. **Confidence filtering:** Drops chunks below the domain's `confidence_threshold`.
5. **Prompt construction:** Builds a system prompt instructing the LLM to answer ONLY from the provided evidence. Formats each chunk as numbered evidence with page references.
6. **LLM call:** Based on `llm_route`:
   - `api` → Calls Groq cloud API (fast, `llama-3.3-70b-versatile`)
   - `local` → Calls Ollama local API (`llama3.2:3b`)
   - Both use OpenAI-compatible `/v1/chat/completions` endpoints
7. **Response assembly:** Packages the answer, citations, model used, cache status, and timing.
8. **Caching + logging:** Caches the answer in Redis and logs the query/answer in `rag_query_logs`.

**Key files:** `main.py`, `routes/generate.py` (query endpoint), `services/llm_client.py` (Groq/Ollama abstraction).

#### 🟨 evaluation-service (Port 8005, Optional) — The Quality Judge

**What it does:** Uses an LLM to evaluate the quality of RAG answers. Scores answers on relevance, faithfulness, and completeness.

**When to use:** Started only with `--evaluation` flag. Not on the core user path. Used for testing and quality assurance.

**Key files:** `main.py`, `routes/evaluate.py`.

### Infrastructure Services

#### Keycloak (Port 8180) — Identity & Access Management

**What it does:** OAuth2/OpenID Connect identity provider. Handles user login, issues JWT access tokens, and manages realm roles.

**How it fits:** Traefik's `forwardAuth` middleware calls Keycloak's `/userinfo` endpoint on every request to verify the JWT token. Each FastAPI service then decodes the JWT locally to extract the `user_id` and `realm_access.roles`.

#### Traefik (Ports 80, 8080) — API Gateway

**What it does:** Reverse proxy that routes incoming HTTP requests to the correct service based on URL path. Enforces authentication at the edge before requests reach services.

**How it fits:** All client requests go through Traefik → Keycloak auth check → forwarded to the target service. The dashboard is at http://localhost:8080.

#### PostgreSQL (Port 5432) — Relational Database

**What it does:** Stores all structured data: domains, users, documents, chunks (with TSVECTOR for BM25), configs, RBAC roles, and query logs.

**Used by:** `domain-service` (domains, users, configs, roles), `ingestion-service` (documents), `worker-service` (chunks, document status), `generation-service` (query logs), `retrieval-service` (BM25 search on chunks).

#### Redis (Port 6379) — Cache & Message Queue

**What it does:** Serves four purposes simultaneously:
1. **Celery broker** — delivers ingestion jobs from `ingestion-service` to `worker-service`
2. **Celery result backend** — stores job results
3. **Retrieval cache** — caches search results to avoid re-computing on repeated queries
4. **Answer cache** — caches generated answers to avoid re-calling the LLM

**Graceful degradation:** If Redis is not running, the system still works. `scripts/memory_cache.py` provides an in-memory TTL cache, and ingestion falls back to synchronous processing.

#### Qdrant (Embedded) — Vector Database

**What it does:** Stores and searches dense embedding vectors. Each domain gets its own collection. Vectors are 384-dimensional (from the E5 model).

**How it runs:** In embedded mode — no separate server process. The `qdrant-client` library opens a local directory (`data/qdrant/`) directly. Created and managed by `scripts/qdrant_client_factory.py` which handles file locks and retries.

### What `run_services.py` Does (in order)

`run_services.py` is the main orchestrator that starts everything:

1. **Loads `.env`** — reads the root `.env` file and sets all environment variables
2. **Starts Keycloak** — downloads (first run) and launches on http://localhost:8180
3. **Starts Redis** — downloads (first run) and launches on localhost:6379
4. **Purges stale Celery tasks** — removes leftover jobs from previous runs that would cause errors
5. **Starts domain-service** — launches Uvicorn on port 8001
6. **Starts ingestion-service** — launches Uvicorn on port 8002 (waits between launches to avoid memory contention)
7. **Starts retrieval-service** — launches Uvicorn on port 8003
8. **Starts generation-service** — launches Uvicorn on port 8004
9. **Starts worker-service** — Celery worker (only if `--worker` flag is used)
10. **Monitors all processes** — if any service crashes, logs the error and keeps running

The staggered startup and memory management are critical on Windows to avoid DLL collisions and paging file exhaustion.

### Launcher Flags

```powershell
python run_services.py                 # APIs + infra only (no worker)
python run_services.py --worker        # also start Celery ingestion worker
python run_services.py --evaluation    # also start evaluation-service on :8005
python run_services.py --no-reload     # faster startup, no auto-reload
python run_services.py --skip-infra    # skip Redis/Keycloak if already running
```

> If Redis is not running: uses in-memory cache and sync PDF ingestion.
> If Redis is running + `--worker`: starts Celery worker for async ingestion.

---

## 6. Database Schema

### Entity Relationship Diagram

```mermaid
erDiagram
    users {
        VARCHAR id PK
        VARCHAR name
        VARCHAR role
    }

    domains {
        UUID id PK
        VARCHAR name UK
        TEXT description
        ENUM status
        VARCHAR created_by
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    domain_configs {
        UUID id PK
        UUID domain_id FK
        VARCHAR llm_route
        INTEGER chunk_size
        INTEGER chunk_overlap
        FLOAT confidence_threshold
        JSONB extra_settings
        TIMESTAMPTZ updated_at
    }

    domain_roles {
        UUID id PK
        UUID domain_id FK
        VARCHAR user_id
        ENUM role
        VARCHAR assigned_by
        TIMESTAMPTZ assigned_at
    }

    documents {
        VARCHAR id PK
        VARCHAR domain_id
        VARCHAR user_id
        VARCHAR filename
        VARCHAR file_path
        VARCHAR status
        TEXT error_msg
        TIMESTAMP created_at
    }

    document_chunks {
        TEXT id PK
        TEXT document_id
        TEXT domain_id
        INTEGER page_num
        INTEGER chunk_index
        TEXT text
        TSVECTOR search_vec
        TIMESTAMPTZ created_at
    }

    rag_query_logs {
        BIGSERIAL id PK
        TEXT domain_id
        TEXT user_id
        TEXT query
        TEXT answer
        TEXT llm_route
        TEXT model
        TIMESTAMPTZ created_at
    }

    domains ||--|| domain_configs : "has config"
    domains ||--o{ domain_roles : "has members"
    domains ||--o{ documents : "contains"
    documents ||--o{ document_chunks : "split into"
```

### Table Details

| Table | Purpose | Key Columns |
|---|---|---|
| `users` | User profiles and global roles | `id` (login ID), `role` (system_admin, domain_admin, contributor, reader) |
| `domains` | Knowledge domain workspaces | `name` (unique), `status` (active/archived), `created_by` |
| `domain_configs` | Per-domain RAG settings | `llm_route` (api/local), `chunk_size`, `confidence_threshold` |
| `domain_roles` | Domain-level RBAC memberships | Unique constraint on `(domain_id, user_id)` |
| `documents` | Uploaded file metadata | `status` (pending → processing → done/failed) |
| `document_chunks` | Searchable text segments | `search_vec` (TSVECTOR for BM25), GIN index |
| `rag_query_logs` | Query audit trail | `query`, `answer`, `llm_route`, `model` |

> For complete DDL and seed data, see [database_setup.md](database_setup.md).

---

## 7. Retrieval Pipeline

The retrieval service implements a 6-stage hybrid pipeline:

```mermaid
flowchart LR
    Q["User Query"]
    E["Embed with E5\n(query: prefix)"]
    D["Dense Search\n(Qdrant cosine)"]
    S["Sparse Search\n(PostgreSQL BM25)"]
    F["RRF Fusion\n(k=60)"]
    R["Cross-Encoder\nReranking"]
    C["Redis Cache"]
    O["Top-K Chunks"]

    Q --> E --> D
    Q --> S
    D --> F
    S --> F
    F --> R --> O
    Q --> C
    C -->|hit| O

    style D fill:#24b47e,color:#fff
    style S fill:#336791,color:#fff
    style R fill:#f59e0b,color:#000
    style C fill:#dc382d,color:#fff
```

| Stage | Model / Method | Purpose |
|---|---|---|
| 1. Embedding | `intfloat/multilingual-e5-small` (384d) | Encode query with `query:` prefix |
| 2. Dense search | Qdrant cosine similarity | Semantic similarity matching |
| 3. Sparse search | PostgreSQL `search_vec` FTS | Exact keywords and acronyms |
| 4. Fusion | Reciprocal Rank Fusion (k=60) | Merge both result lists fairly |
| 5. Reranking | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Re-score top candidates |
| 6. Cache | Redis TTL cache | Skip computation for repeated queries |

---

## 8. Authentication & RBAC

### Two Layers of Security

1. **Gateway layer (Traefik):** `forwardAuth` calls Keycloak `/userinfo` on every protected request. No valid token → `401` before the request reaches any service.
2. **Service layer (FastAPI):** Each service decodes the JWT locally to extract `user_id` and roles. Domain-specific operations additionally call `domain-service /internal/check-access`.

### Realm Roles

| Role | Meaning |
|---|---|
| `system_admin` | Platform-wide administrator; can create domains and bypass per-domain checks |
| `domain_admin` | Manages one domain's members and configuration |
| `contributor` | Can upload documents to a domain |
| `reader` | Can query/read within a domain |

### Permission Matrix

| Action | Required Role |
|---|---|
| Create a domain | `system_admin` |
| Upload a PDF | `contributor`, `domain_admin`, or `system_admin` on that domain |
| Query / generate answer | `contributor` or higher on that domain, or `system_admin` |
| Manage domain members | `domain_admin` or `system_admin` |
| Update domain config | `domain_admin` or `system_admin` |

### RBAC Verification Matrix

| User | Operation | Expected | Status |
|---|---|---|---|
| `admin` (system_admin) | Create domain | 201 Created | ✅ Allowed |
| `admin` (system_admin) | Change config | 200 OK | ✅ Allowed (bypasses check) |
| `manager` (domain_admin) | Create domain | 403 Forbidden | ❌ Denied |
| `manager` (domain_admin) | Change config | 200 OK | ✅ Allowed on assigned domain |
| `user` (contributor) | Upload PDF | 202 Accepted | ✅ Allowed on assigned domain |
| `user` (contributor) | Change config | 403 Forbidden | ❌ Denied |
| `viewer` (reader) | Query domain | 200 OK | ✅ Allowed on assigned domain |
| `viewer` (reader) | Upload PDF | 403 Forbidden | ❌ Denied |
| `unauth` | Any operation | 401 Unauthorized | ❌ Denied |

### Internal Service-to-Service Calls

Services communicate internally using a shared secret header:

```
X-Internal-Key: <value of INTERNAL_API_KEY in .env>
```

### Dev Auth Fallback

When Keycloak is not running, `run_services.py` automatically uses `scripts/dev_auth.py` for local JWT auth with self-signed keys. Use `python scripts/dev_auth.py` to generate dev tokens. In the React UI, you can sign in by typing the User ID directly.

---

## 9. API Reference

All requests through the API gateway require: `Authorization: Bearer <JWT_ACCESS_TOKEN>`

### 9.1 domain-service (port 8001)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/domains/auth/login` | Public | Dev auth — login by user_id |
| POST | `/domains` | `system_admin` | Create a knowledge domain |
| GET | `/domains` | Authenticated | List domains (filtered by role) |
| POST | `/domains/{id}/members` | `domain_admin`+ | Assign user role in domain |
| GET | `/domains/{id}/config` | Members | Get domain RAG config |
| PATCH | `/domains/{id}/config` | `domain_admin`+ | Update domain RAG config |
| POST | `/internal/check-access` | Internal only | Verify user access (X-Internal-Key) |
| GET | `/health` | Public | Health check |

### 9.2 ingestion-service (port 8002)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/ingest` | `contributor`+ | Upload PDF (multipart: `file` + `domain_id`) |
| GET | `/ingest/{document_id}` | Authenticated | Poll ingestion status |
| GET | `/health` | Public | Health check |

**Ingestion statuses:** `pending` → `processing` → `done` or `failed`

### 9.3 retrieval-service (port 8003)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/api/v1/retrieve` | Internal/Authenticated | Hybrid retrieval (query + domain_id) |
| GET | `/health` | Public | Health check |

### 9.4 generation-service (port 8004)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/generate/query` | `contributor`+ | RAG query with answer + citations |
| GET | `/generate/health` | Public | Health check |

**Query payload:**
```json
{
  "query": "What is the refund policy?",
  "domain_id": "UUID",
  "top_k_retrieve": 10,
  "top_k_rerank": 5
}
```

**Response:**
```json
{
  "answer": "The refund policy allows returns within 30 days...",
  "citations": [{"chunk_id": "...", "document_id": "...", "page": 3, "score": 0.87, "text": "..."}],
  "cache_hit": false,
  "llm_route": "api",
  "model": "llama-3.3-70b-versatile"
}
```

### 9.5 evaluation-service (port 8005, optional)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/evaluate` | Authenticated | LLM-as-judge scoring |
| GET | `/evaluate/health` | Public | Health check |

### Swagger UI (Interactive API Docs)

| Service | URL |
|---|---|
| domain-service | http://localhost:8001/docs |
| ingestion-service | http://localhost:8002/docs |
| retrieval-service | http://localhost:8003/docs |
| generation-service | http://localhost:8004/docs |

---

## 10. Prerequisites

### Required

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | [Download](https://www.python.org/downloads/). Check "Add Python to PATH". |
| **PostgreSQL** | 16 | [Download](https://www.postgresql.org/download/windows/). Keep default port 5432. |
| **Java** | 17+ | [Adoptium Temurin](https://adoptium.net/). Required for Keycloak. |
| **Groq API key** | Free tier | [Get one](https://console.groq.com). Primary LLM provider. |
| **RAM** | 8 GB min, 16 GB recommended | Embedding and reranking models load into memory |
| **Disk** | ~10 GB free | ML model caches + infra downloads |

### Auto-Downloaded (by `run_services.py`)

| Component | Port | Notes |
|---|---|---|
| **Redis** | 6379 | Portable Redis for Windows, downloaded to `tools/redis/` |
| **Keycloak** | 8180 | Downloaded to `tools/keycloak/` on first run (~150 MB) |
| **Qdrant** | — | Embedded at `data/qdrant` automatically (no server needed) |

### Optional

| Requirement | When needed |
|---|---|
| **Node.js + npm** | React frontend (`rag-ui/`) |
| **Ollama** | Local/offline LLM fallback when Groq is unavailable |
| **Tesseract OCR** | Better OCR for scanned PDFs |

---

## 11. Quick Start (5 Steps)

### Step 1 — Python Environment

```powershell
# Create venv (first time only)
python -m venv .venv

# Activate
.venv\Scripts\activate

# Install all dependencies
.venv\Scripts\pip install -r requirements.txt
```

> First install downloads ~2 GB (PyTorch CPU + embedding models). Allow 10–20 minutes.

### Step 2 — Environment File

```powershell
copy .env.example .env
```

Edit `.env` and set **at minimum**:

```env
POSTGRES_PASSWORD=1234          # match your local PostgreSQL password
GROQ_API_KEY=gsk_YOUR_KEY_HERE  # get from https://console.groq.com
```

### Step 3 — PostgreSQL

```powershell
# Create the database
psql -U postgres -c "CREATE DATABASE domain_db;"
```

> For complete schema + seed data setup, see [database_setup.md](database_setup.md).

### Step 4 — Start the Stack

```powershell
python run_services.py
```

> Redis and Keycloak are auto-downloaded on first run. Java 17+ must be installed for Keycloak.

### Step 5 — Verify

```powershell
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/generate/health
```

All should return `{"status":"ok",...}`.

### (Optional) Start React Frontend

```powershell
cd rag-ui
npm install
npm run dev
```

Navigate to http://localhost:5173.

---

## 12. Environment Variables

All services read from a single root `.env` file. Copy `.env.example` to `.env` and edit.

### Required

| Variable | Purpose | Where to Get |
|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL password | Set during [PostgreSQL install](https://www.postgresql.org/download/windows/) |
| `GROQ_API_KEY` | Cloud LLM API key | [console.groq.com](https://console.groq.com) → API Keys → Create |

### All Variables

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL credentials | `postgres` / `postgres` / `domain_db` |
| `DATABASE_URL` | Async Postgres URL for FastAPI | `postgresql+asyncpg://postgres:postgres@localhost:5432/domain_db` |
| `SYNC_DATABASE_URL` | Sync Postgres URL for Celery worker | `postgresql://postgres:postgres@localhost:5432/domain_db` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `QDRANT_PATH` | Embedded Qdrant storage path | `data/qdrant` |
| `KEYCLOAK_ISSUER` | JWT issuer URL | `http://localhost:8180/realms/rag-system` |
| `KEYCLOAK_PUBLIC_KEY` | Auto-set by `run_services.py` | (leave blank) |
| `INTERNAL_API_KEY` | Shared secret for internal endpoints | `rag-internal-dev-key-change-in-prod` |
| `DOMAIN_SERVICE_URL` | Internal domain-service URL | `http://localhost:8001` |
| `RETRIEVAL_SERVICE_URL` | Internal retrieval-service URL | `http://localhost:8003` |
| `GROQ_API_KEY` | Groq cloud LLM key | **Required for cloud generation** |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `OLLAMA_BASE_URL` | Local Ollama endpoint | `http://localhost:11434/v1` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2:3b` |
| `TOP_K_RETRIEVE` | Candidates before reranking | `20` |
| `TOP_K_RERANK` | Final chunks sent to LLM | `5` |
| `CACHE_TTL_SECONDS` | Redis cache TTL | `3600` |
| `UPLOAD_DIR` | PDF storage path | `data/uploads` |
| `MAX_SIZE_MB` | Max upload size | `50` |

> See `.env.example` for inline comments explaining where to get each value.

---

## 13. Troubleshooting

### PostgreSQL — connection refused

- Start the service: `net start postgresql-x64-16`
- Check password in `.env` matches your Postgres install
- Confirm database exists: `psql -U postgres -l`

### Redis — connection refused

- Start manually: `tools\redis\redis-server.exe tools\redis\redis.windows.conf`
- Or run: `.venv\Scripts\python.exe scripts\infra_manager.py`
- Check port: `netstat -ano | findstr :6379`

### Redis — HELLO command error

Redis 5.x does not support RESP3. The project handles this with `protocol=2`.

### Keycloak — not ready / slow start

Keycloak takes **30–90 seconds** on first start. Wait and retry:

```powershell
curl http://localhost:8180/realms/rag-system
```

### Keycloak — download failed (SSL error on Windows)

Download manually from https://github.com/keycloak/keycloak/releases/tag/26.5.0 and extract to `tools/keycloak/`.

### Keycloak — Java not found

Install Java 17 from https://adoptium.net/ and restart your terminal.

### HuggingFace model download — SSL error

```powershell
.venv\Scripts\pip install truststore
```

First retrieval-service start downloads ~500 MB of embedding models — be patient.

### Ingestion stuck on `processing`

- Check Celery worker is running (started with `python run_services.py --worker`)
- On Windows, Celery uses `--pool=solo` (required — no fork support)
- Ensure `PYTHONIOENCODING=utf-8` is set (handled by launcher)

### Port already in use

```powershell
netstat -ano | findstr "LISTENING" | findstr ":8001 :8002 :8003 :8004 :6379 :8180"
taskkill /PID <pid> /F
```

### Unicode errors in worker output on Windows

```powershell
$env:PYTHONIOENCODING="utf-8"
```

### 401 Unauthorized on API calls

- Token expired (5-minute lifespan). Get a fresh token.
- Missing `Authorization: Bearer <token>` header.
- Keycloak not fully started. Wait 30–60 seconds.

### 403 Forbidden on upload

- User lacks `contributor` role on the domain.
- Use the `admin` user (`system_admin`) or assign the user as a domain member.

### First query is very slow

Expected behavior. The retrieval service loads embedding and reranker models on first request. Subsequent queries are faster. Answer caching makes identical repeat queries near-instant.

### Windows — WinError 1455 / Paging File Too Small

- Let `run_services.py` manage service staggering (it sleeps between launches)
- Increase Virtual Memory / Paging file size to at least 16 GB
- Close heavy background processes (Docker Desktop, multiple IDEs)

### Database schema mismatch

The `domain-service` automatically creates tables on startup via `Base.metadata.create_all`. If errors persist:

```powershell
psql -U postgres -d domain_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Restart `run_services.py` to recreate clean tables.

---

## 14. Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example                      # environment template (copy to .env)
├── .gitignore                        # comprehensive ignore rules
├── requirements.txt                  # unified Python dependencies
├── run_services.py                   # main launcher (starts everything)
├── delete_chunks.py                  # database + vector store reset tool
├── README.md                         # this file — complete project guide
├── database_setup.md                 # from-scratch database & infra setup
├── data/                             # auto-created runtime data (gitignored)
│   ├── qdrant/                       # embedded vector DB
│   ├── uploads/                      # uploaded PDFs
│   └── dev/                          # dev JWT keys (fallback auth)
├── tools/                            # auto-downloaded infra (gitignored)
│   ├── redis/
│   └── keycloak/
├── rag-ui/                           # React frontend (Vite + TypeScript)
│   ├── src/
│   │   ├── components/               # UI components
│   │   ├── pages/                    # View pages
│   │   ├── store/                    # Zustand state stores
│   │   └── lib/                      # API clients
│   └── vite.config.ts
├── scripts/
│   ├── dev_auth.py                   # fallback JWT auth
│   ├── infra_manager.py              # starts Redis + Keycloak
│   ├── memory_cache.py               # in-memory TTL cache (Redis fallback)
│   ├── network_bootstrap.py          # SSL bootstrap for model downloads
│   └── qdrant_client_factory.py      # Qdrant client helpers
└── services/
    ├── auth/realm-export.json        # Keycloak realm config
    ├── gateway/                      # Traefik config + smoke test
    ├── domain-service/               # port 8001
    ├── ingestion-service/            # port 8002
    ├── retrieval-service/            # port 8003
    ├── generation-service/           # port 8004
    ├── evaluation-service/           # port 8005 (optional)
    └── worker-service/               # Celery worker
```

---

## 15. Quick Reference Card

```text
Start:       python run_services.py
Start+Work:  python run_services.py --worker
Stop:        Ctrl+C
Env setup:   copy .env.example .env
Frontend:    cd rag-ui && npm install && npm run dev
DB reset:    python delete_chunks.py

Keycloak:    http://localhost:8180  (admin / admin)
Token:       POST http://localhost:8180/realms/rag-system/protocol/openid-connect/token

Typical flow:
  1. Get JWT token (Keycloak or dev auth)
  2. POST /domains                          → create domain
  3. POST /ingest                           → upload PDF
  4. GET  /ingest/{document_id}             → wait for "done"
  5. POST /generate/query                   → get AI answer with citations
```

---

*Last updated: June 2026 — Chatbot-Fixed-Team2 / Fixed Solutions AI Internship*
