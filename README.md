# Chatbot-Fixed-Team2

**Multi-user, multi-domain Retrieval-Augmented Generation (RAG) system** — Fixed Solutions AI Internship 2026.

A complete backend + frontend stack for domain management, document ingestion, hybrid retrieval (Vector + BM25 + Graph), AI answer generation with citations, and automated evaluation. All workflows are exposed through HTTP APIs and a React chat UI.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Architecture Decisions](#3-architecture-decisions)
4. [Technology Stack](#4-technology-stack)
5. [Services Reference](#5-services-reference)
6. [Database Schema](#6-database-schema)
7. [Retrieval Pipeline](#7-retrieval-pipeline)
8. [OCR Pipeline](#8-ocr-pipeline)
9. [Authentication & RBAC](#9-authentication--rbac)
10. [API Reference](#10-api-reference)
11. [Prerequisites](#11-prerequisites)
12. [Complete From-Scratch Setup & Run Guide](#12-complete-from-scratch-setup--run-guide)
13. [Environment Variables](#13-environment-variables)
14. [Troubleshooting](#14-troubleshooting)
15. [Directory Layout](#15-directory-layout)
16. [Quick Reference Card](#16-quick-reference-card)
17. [Sprint 3 — Hybrid Graph RAG (Apache AGE)](#17-sprint-3--hybrid-graph-rag-apache-age)
18. [Monitoring — Prometheus + Grafana](#18-monitoring--prometheus--grafana)

---

## 1. Project Overview

### What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain Retrieval-Augmented Generation (RAG) system**. It allows organizations to:

- Create separate **knowledge domains** (isolated knowledge bases, e.g., "HR Policies", "Tech Support", "Legal Contracts")
- Upload **documents** (PDF, DOCX, CSV, PNG, JPG) into those domains
- Ask **natural language questions** and receive **AI-generated answers with citations** grounded in the uploaded documents
- Evaluate answer quality automatically using an LLM-as-judge pipeline

### What Is RAG? (Retrieval-Augmented Generation)

RAG combines **information retrieval** (searching your own documents) with **language model generation** (AI writing). Instead of relying on the AI's general knowledge (which can be wrong or outdated), RAG:

1. **Retrieves** the most relevant passages from YOUR documents
2. **Gives those passages to the AI** as context
3. **The AI generates an answer** using ONLY those passages as evidence
4. **Cites the source** — which document, page, and paragraph the answer came from

### How the System Works — End to End

#### Stage 1: Domain Setup
An admin creates a **knowledge domain** — a named workspace that isolates one topic's documents, members, and settings. Each domain has its own RAG configuration (LLM route, chunk size, confidence thresholds). Users are assigned roles (admin, contributor, reader) per domain.

#### Stage 2: Document Ingestion (Upload → Extract → Chunk → Index)
A user uploads a document (PDF, DOCX, CSV, or image) to a domain:

1. **`ingestion-service`** receives the file, validates its type, saves to disk
2. Creates a `documents` record in PostgreSQL (status = `pending`)
3. Enqueues an async Celery job into Redis
4. **`worker-service`** picks up the job and:
   - **Extracts text**: PyMuPDF for digital PDFs, python-docx for DOCX, pandas for CSVs. Scanned PDFs and images → **OCR pipeline** (PaddleOCR → Surya fallback)
   - **Splits into chunks**: semantic chunking with ~512-char chunks and 64-char overlap
   - **Generates embeddings**: `intfloat/multilingual-e5-small` (384-dim). Each chunk prefixed with `passage:`
   - **Sprint 3**: Extracts entities (GLiNER NER) and relations (Groq LLM) for the knowledge graph
   - **Stores vectors** in Qdrant (one collection per domain)
   - **Stores chunks** in PostgreSQL with `TSVECTOR` column for BM25 full-text search
   - Updates document status to `done` (or `failed` with error details)

#### Stage 3: Question Answering (Query → Retrieve → Generate)
A user asks a question:

1. **`generation-service`** receives the query and domain ID
2. Checks **Redis cache** — cache hit returns answer instantly
3. Calls **`retrieval-service`** — 6-stage hybrid pipeline (embed → dense → sparse → RRF → rerank → cache)
4. **Sprint 3**: If query contains relational keywords, also runs **Apache AGE graph traversal**
5. Fetches domain LLM config from **`domain-service`**
6. Builds **RAG prompt** with retrieved chunks as numbered evidence paragraphs
7. Calls **LLM** (Groq cloud or Ollama local) via OpenAI-compatible API
8. Returns answer + citations (filename, page, chunk index, relevance score)
9. **Caches response** in Redis and **logs in PostgreSQL** for audit

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-domain isolation | Each domain has its own documents, members, configuration, and vector collection. Complete data separation. |
| Role-based access (RBAC) | Three-layer security: Keycloak JWT tokens at the gateway + per-domain role checks in each service |
| Hybrid retrieval (Sprint 1–2) | Dense vector search (semantic) + sparse BM25 (keywords) + cross-encoder reranking |
| Graph RAG (Sprint 3) | Apache AGE knowledge graph for relationship queries: "Who manages Project Alpha?" |
| AI answer generation | Groq (cloud, fast, free tier) or Ollama (local, offline). Per-domain LLM routing. |
| Multi-format ingestion | PDF, DOCX, CSV, PNG, JPG — format-specific extractors + PaddleOCR/Surya for scanned content |
| Async document processing | Celery + Redis background processing. Immediate `202 Accepted` + status polling. |
| Intelligent caching | Redis caches retrieval results and generated answers. Repeat queries return instantly. |
| Citation grounding | Every answer includes citations: filename, source type, page number, relevance score |
| Multi-language OCR | PaddleOCR/Surya configurable via `OCR_LANG`/`OCR_LANGS` — supports Arabic, English, French, etc. |
| Graceful degradation | Redis down → in-memory cache. Groq down → Ollama. Keycloak down → dev auth. AGE down → vector+BM25 only. |
| Automated evaluation | LLM-as-judge scoring for faithfulness, relevance, and completeness with Celery Beat scheduling |
| React chat UI | Full-featured web interface for login, domain management, document upload, and interactive Q&A |

---

## 2. System Architecture

### 2.1 Service Topology

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    KC["Keycloak :8180"]
    DS["domain-service :8001"]
    IS["ingestion-service :8002"]
    RS["retrieval-service :8003"]
    GS["generation-service :8004"]
    ES["evaluation-service :8005"]
    WS["worker-service (Celery)"]
    PG["PostgreSQL 17 :5434"]
    PGWSL["PostgreSQL 17 + AGE\n(WSL2) :5434"]
    RD["Redis :6379"]
    QD["Qdrant (embedded)"]
    LLM["Groq API / Ollama"]

    Client -->|JWT auth| KC
    Client --> DS
    Client --> IS
    Client --> GS
    Client --> ES

    DS --> PG
    IS --> PG
    IS --> RD
    RD --> WS
    WS --> PG
    WS --> QD
    WS -->|Sprint 3: NER + Relations| PGWSL

    GS --> RS
    GS --> DS
    GS --> LLM
    GS --> RD
    GS --> PG

    RS --> QD
    RS --> PG
    RS --> RD
    RS -->|Sprint 3: Graph Traversal| PGWSL

    ES --> PG
    ES --> LLM

    style KC fill:#f9a825,color:#000
    style PG fill:#336791,color:#fff
    style PGWSL fill:#2c5f8a,color:#fff
    style RD fill:#dc382d,color:#fff
    style QD fill:#24b47e,color:#fff
    style LLM fill:#7c3aed,color:#fff
    style WS fill:#a78bfa,color:#000
    style ES fill:#f59e0b,color:#000
```

### 2.2 Query Flow

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    KC["Keycloak Auth Provider"]
    GS["generation-service"]
    RD["Redis Cache"]
    DS["domain-service"]
    RS["retrieval-service"]
    QD["Qdrant (Vector DB)"]
    PG["PostgreSQL (BM25 DB)"]
    AGE["Apache AGE (Graph DB)"]
    LLM["LLM (Groq / Ollama)"]

    Client -->|1. Submit RAG Query| GS
    GS -->|2. Validate JWT| KC
    KC -->|3. Token OK| GS
    GS -->|4. Check Cache| RD

    RD -->|Cache Hit: Return Answer| Client

    RD -->|Cache Miss| GS
    GS -->|5. Fetch Config| DS
    GS -->|6. Request Retrieval| RS

    RS -->|7a. Dense Search| QD
    RS -->|7b. Sparse Search| PG
    RS -->|7c. Graph Traversal Sprint3| AGE
    QD & PG & AGE -->|8. Fusion + Rerank| RS
    RS -->|9. Return Chunks| GS

    GS -->|10. Build Prompt + Call| LLM
    LLM -->|11. Generate Answer| GS
    GS -->|12. Log Query| PG
    GS -->|13. Cache Response| RD
    GS -->|14. Return Final Answer| Client

    style KC fill:#f9a825,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style AGE fill:#2c5f8a,color:#fff
    style LLM fill:#7c3aed,color:#fff
```

**How it works:**
1. Client submits HTTP `POST` with question + domain ID
2. JWT token validated against Keycloak (or dev auth fallback)
3. `generation-service` checks Redis — cache hit returns instantly
4. On miss: fetches domain LLM config, calls `retrieval-service`
5. `retrieval-service` runs parallel hybrid search: dense (Qdrant) + sparse (PostgreSQL BM25) + graph (Apache AGE, if relational query)
6. Results fused via RRF and re-scored with cross-encoder reranking
7. `generation-service` builds RAG prompt and calls the LLM
8. Answer cached in Redis, logged in PostgreSQL, returned with full citation metadata

---

### 2.3 Ingestion Pipeline

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    IS["ingestion-service"]
    DS["domain-service"]
    RD["Redis Queue"]
    PG["PostgreSQL Database"]
    WS["worker-service (Celery)"]
    QD["Qdrant (Vector DB)"]
    AGE["Apache AGE (Graph)"]

    Client -->|1. POST /ingest| IS
    IS -->|2. Validate File Type| IS
    IS -->|3. Check Access| DS
    IS -->|4. Create Pending Doc| PG
    IS -->|5. Enqueue Job| RD
    IS -->|6. Return 202 Accepted| Client

    RD -->|7. Dequeue Job| WS
    WS -->|8. Extract Text + OCR| WS
    WS -->|9. Semantic Chunk + Embed| WS
    WS -->|10. Index Vectors| QD
    WS -->|11. Store Chunks + FTS| PG
    WS -->|12. NER - Sprint 3| WS
    WS -->|13. Relation Extraction - Sprint 3| AGE
    WS -->|14. Set Status = done| PG

    style DS fill:#60a5fa,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style WS fill:#a78bfa,color:#000
    style AGE fill:#2c5f8a,color:#fff
```

**Steps 12–13 (Sprint 3)** are wrapped in `try/except` — a failure in NER or relation extraction logs a warning but does **not** fail the document. It still completes with `status=done` and is fully searchable through vector+BM25.

---

### 2.4 Authentication Flow

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    KC["Keycloak Auth Provider"]
    DS["domain-service (Dev Auth)"]
    Service["Microservice (retrieve/ingest/etc)"]

    subgraph KeycloakMode ["Option A: Keycloak Mode (Production)"]
        Client -->|1. Authenticate| KC
        KC -->|2. Return JWT access_token| Client
        Client -->|3. Call Request + Bearer JWT| Service
        Service -->|4. Validate JWT locally| Service
    end

    subgraph DevAuthMode ["Option B: Dev Auth Mode (Local Fallback)"]
        Client -->|"1. POST /domains/auth/login {user_id}"| DS
        DS -->|2. Mint & Return Dev JWT| Client
        Client -->|3. Call Request + Bearer Dev JWT| Service
    end

    Service -->|Verify signature + extract claims| Service
    Service -->|Check Domain Access| DS

    style KC fill:#f9a825,color:#000
    style DS fill:#60a5fa,color:#000
    style Service fill:#a78bfa,color:#000
```

- **Option A (Keycloak):** Client exchanges credentials with Keycloak → gets JWT. Every service validates the token locally against Keycloak's public key.
- **Option B (Dev Auth):** Client POSTs a `user_id` to `domain-service` → gets a locally-signed JWT. Auto-enabled when Keycloak is missing or `KEYCLOAK_PUBLIC_KEY` is blank.

---

### 2.5 Evaluation Pipeline

```mermaid
flowchart TD
    GS["generation-service"]
    ES["evaluation-service :8005"]
    CB["Celery Beat (Scheduler)"]
    EW["Celery Evaluation Worker"]
    LLM["LLM Judge (Groq)"]
    PG["PostgreSQL (eval_results)"]
    UI["React Dashboard"]

    GS -->|Auto-submit on generation| ES
    CB -->|Every 30 min: batch eval| EW
    ES --> LLM
    EW --> LLM
    LLM -->|Score: faithfulness, relevance, completeness| ES
    ES --> PG
    EW --> PG
    PG --> UI

    style LLM fill:#7c3aed,color:#fff
    style PG fill:#336791,color:#fff
    style CB fill:#f59e0b,color:#000
    style EW fill:#a78bfa,color:#000
```

---

## 3. Architecture Decisions

### Decision 1: Single Root `.env`
All services consume the same root `.env` loaded by `run_services.py`. One source of truth for local development. `pydantic-settings` tolerates extra variables with `extra="ignore"`. Per-service overrides (ports, names) are injected by the launcher.

### Decision 2: PostgreSQL 17 on Port 5434
The project uses port **5434** (not the PostgreSQL default 5432) to avoid conflicts with older PostgreSQL versions that may be installed. This matches both the Windows PostgreSQL 17 instance and the WSL2 Apache AGE instance, which are kept in sync by the `AGE_DATABASE_DSN` env var.

### Decision 3: Three-Signal Retrieval Pipeline
`retrieval-service` implements: dense vector search (Qdrant) → sparse keyword search (PostgreSQL BM25) → Reciprocal Rank Fusion → cross-encoder reranking → Redis cache. Vector search catches semantic similarity; BM25 recovers exact keywords and acronyms; RRF keeps fusion robust; reranking improves final context quality.

### Decision 4: Generation Service Stays Separate
Answer generation is its own FastAPI service (not embedded in retrieval). Retrieval and generation have different dependencies and scaling behavior. Per-domain LLM routing, answer caching, query logging, and streaming belong in the generation boundary.

### Decision 5: Groq First, Ollama Fallback
Generation uses Groq when `GROQ_API_KEY` is configured, falls back to Ollama otherwise. Both expose an OpenAI-compatible API, so the routing layer stays small. Groq keeps interactive latency practical on dev hardware; Ollama remains available for offline usage.

### Decision 6: Worker Maintains Dual Indexes
Worker writes chunks into both Qdrant (dense) and PostgreSQL `document_chunks` (BM25). Indexing once at ingestion time keeps query-time work small. Dense and sparse retrieval layers stay consistent with the same chunk payloads.

### Decision 7: Redis Is Shared Across Queue and Cache
Redis serves as Celery broker, Celery result backend, retrieval cache, and generation cache. When unavailable, the system gracefully degrades: in-memory TTL cache replaces Redis cache; sync subprocess replaces Celery async ingestion.

### Decision 8: Apache AGE Instead of Neo4j (Sprint 3)
Apache AGE is a PostgreSQL extension — same engine already used by the rest of this project. This reuses existing `domain_id` isolation patterns, avoids a separate DB server process, and keeps the same uptime story. The graph layer is **additive and failure-isolated** — a failure in NER or relation extraction does not fail document ingestion.

### Decision 9: OCR Is a PaddleOCR + Surya Ensemble
Scanned pages and image uploads use an embedded OCR pipeline instead of Tesseract. PaddleOCR runs first (fast path); if its confidence score falls below the threshold, Surya (transformer-based, layout-aware) runs as a fallback. This keeps average latency low while improving accuracy on low-quality or mixed-language scans.

### Decision 10: Scripts Directory Contains Shared Runtime Modules
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
| Frontend | React + Vite + TypeScript + Tailwind | — | Chat UI at `rag-ui/` |
| Database | PostgreSQL | **17** (port 5434) | Domains, documents, chunks, query logs |
| Graph DB | PostgreSQL 17 + Apache AGE | AGE v1.6.0-rc0 | Knowledge graph (Sprint 3, WSL2) |
| Vector DB | Qdrant | 1.12.1 | Embedded dense vector search |
| Cache / Queue | Redis | 5.x | Celery broker + retrieval/answer cache |
| Task queue | Celery | 5.4.0 | Async document ingestion + evaluation |
| Task scheduler | Celery Beat | 5.4.0 | Periodic batch evaluation (every 30 min) |
| Auth | Keycloak | 26.5.0 | OAuth2/OIDC, JWT tokens |
| Embeddings | `intfloat/multilingual-e5-small` | — | 384-dim multilingual embeddings |
| Reranker | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | — | Cross-encoder reranking |
| NER (Sprint 3) | `urchade/gliner_multi-v2.1` | — | Zero-shot multilingual entity extraction |
| Cloud LLM | Groq | — | `llama-3.3-70b-versatile` — generation + eval |
| Local LLM | Ollama | — | `llama3.2:3b` (offline fallback) |
| PDF extraction | PyMuPDF | — | Native text extraction for digital PDFs |
| OCR (fast path) | PaddleOCR | ≥3.7.0 | Primary OCR for scanned pages and images |
| OCR (fallback) | Surya | — | Layout-aware transformer OCR |
| DOCX extraction | python-docx | 1.1.2 | Word document text extraction |
| CSV extraction | pandas | 2.2.3 | Tabular data text extraction |
| ML runtime | PyTorch CPU | 2.6.0+ | Embedding, reranker, Surya inference |

---

## 5. Services Reference

### Service Map and Ports

| Component | Port(s) | Type | Purpose |
|---|---:|---|---|
| Keycloak | 8180 | Identity provider | Login, JWT token issuance |
| PostgreSQL 17 (Windows) | 5434 | Database | Domains, documents, chunks, query logs |
| PostgreSQL 17 + AGE (WSL2) | 5434 | Graph Database | Knowledge graph (Sprint 3) |
| Redis | 6379 | Cache + queue | Celery broker, retrieval cache, answer cache |
| Qdrant | — | Vector database | Dense embedding search (embedded, no server) |
| domain-service | 8001 | FastAPI | Domain CRUD, members, config, RBAC |
| ingestion-service | 8002 | FastAPI | PDF upload, job enqueue, status polling |
| worker-service | — | Celery worker | Extract (incl. OCR) → chunk → embed → index → NER → relations |
| retrieval-service | 8003 | FastAPI | Hybrid search pipeline (vector + BM25 + graph) |
| generation-service | 8004 | FastAPI | RAG orchestration and LLM answers |
| evaluation-service | 8005 | FastAPI | LLM-as-judge scoring (optional) |

### Deep Dive: What Each Service Does

#### 🟦 domain-service (Port 8001) — The Brain of the System

**What it does:** Manages all knowledge domains, user memberships, and domain-level configuration. Central authority that other services call to verify permissions.

**How it works internally:**
- **Domain CRUD:** Create, list, archive knowledge domains. Each domain is an isolated workspace with its own documents, members, and RAG settings.
- **RBAC enforcement:** Other services call `domain-service /internal/check-access` to verify user roles.
- **Configuration management:** Each domain has a `domain_configs` record controlling: LLM route (`api`=Groq, `local`=Ollama), chunk size, chunk overlap, confidence threshold.
- **Dev auth:** In dev mode, provides `/domains/auth/login` endpoint where you POST a `user_id` and get a locally-signed JWT.
- **Database:** SQLAlchemy async ORM with PostgreSQL. Tables auto-created on startup.

**Key files:** `main.py`, `routes/`, `models/`, `auth/`

#### 🟧 ingestion-service (Port 8002) — The Document Receiver

**What it does:** Receives uploads, validates access, saves files to disk, and enqueues background processing jobs.

**How it works internally:**
- **Upload handling:** Accepts multipart file uploads (max 50 MB). Saves to `data/uploads/{document_id}/{filename}`.
- **Access check:** Verifies `contributor` or higher role via `domain-service /internal/check-access`.
- **Job enqueue:** Creates `documents` record (status=`pending`) + pushes Celery task to Redis. Returns `202 Accepted` immediately.
- **Status polling:** `GET /ingest/{document_id}` — returns `pending` → `processing` → `done` or `failed`.
- **Sync fallback:** If Redis is not running, processes document synchronously in a subprocess.

**Key files:** `main.py`, `routes/ingest.py`

#### 🟪 worker-service (Celery Worker) — The Document Processor

**What it does:** Runs as a Celery worker. Picks up ingestion jobs from Redis and does heavy lifting: text extraction, chunking, embedding, indexing, and Sprint 3 graph extraction.

**6-step pipeline** (in `tasks/process.py`):
1. **Extract text:** PyMuPDF for digital PDFs, python-docx for DOCX, pandas for CSV. Scanned PDFs and images → **OCR pipeline** (PaddleOCR + Surya fallback)
2. **Semantic chunk:** ~512-char chunks with 64-char overlap
3. **Embed:** `intfloat/multilingual-e5-small` (384-dim), each chunk prefixed with `passage:`
4. **Index:** Vectors → Qdrant; chunks → PostgreSQL `document_chunks` with `TSVECTOR`
5. **NER (Sprint 3):** GLiNER multilingual extracts entities (Person, Project, Department, Policy, Role, Location, Skill) from each chunk
6. **Relation extraction (Sprint 3):** Groq LLM extracts typed relations (MANAGES, BELONGS_TO, etc.) in batches of 20 chunks → writes to Apache AGE graph

> **Important:** Steps 5–6 are wrapped in `try/except`. Failure here logs a warning but does **not** fail the document — it completes with `status=done` and remains searchable.

> **On Windows:** Celery runs with `--pool=solo` (no fork support). One job at a time, but reliable.

**Key files:** `tasks/extract.py`, `tasks/process.py`, `celery_app.py`, `ner.py`, `relation_extraction.py`, `ontology.py`, `tasks/ocr-service/ocr_service/`

#### 🟩 retrieval-service (Port 8003) — The Search Engine

**What it does:** Implements the 6-stage hybrid retrieval pipeline. Given a query and domain ID, finds the most relevant document chunks.

**6-stage pipeline:**
1. **Query embedding:** E5 model with `query:` prefix (matches `passage:` prefix from indexing)
2. **Dense vector search (Qdrant):** Cosine similarity — finds semantically similar chunks
3. **Sparse keyword search (PostgreSQL BM25):** Full-text search on `search_vec` TSVECTOR — finds exact keyword matches
4. **Reciprocal Rank Fusion (RRF):** Merges dense + sparse results: `score = Σ 1/(k + rank_i)` with k=60
5. **Cross-encoder reranking:** `mmarco-mMiniLMv2` re-scores top candidates (sees query AND chunk together — more accurate than bi-encoders)
6. **Redis caching:** Final results cached with TTL (default 1 hour)

**Sprint 3 addition:** If `AGE_DATABASE_DSN` is configured and the query contains relational keywords, the `RetrievalRouter` enables graph traversal (1-hop Cypher match on the knowledge graph). Graph-derived chunks are assigned a score of `0.95` and fused with vector+BM25 results.

**Key files:** `services/qdrant_search.py`, `services/bm25_search.py`, `services/reranker.py`, `services/hybrid_retrieval.py`, `services/graph_retriever.py`

#### 🟥 generation-service (Port 8004) — The AI Answer Writer

**What it does:** Orchestrates the full RAG pipeline: gets domain config, calls retrieval, builds prompt, calls LLM, returns answer with citations.

**How it works:**
1. **Cache check:** Redis lookup for (query, domain_id) pair
2. **Domain config:** LLM route, confidence threshold
3. **Retrieval:** Calls `retrieval-service` → ranked chunks with scores
4. **Confidence filtering:** Drops chunks below `confidence_threshold`
5. **Prompt construction:** System prompt + numbered evidence paragraphs
6. **LLM call:** `api` → Groq cloud (`llama-3.3-70b-versatile`); `local` → Ollama (`llama3.2:3b`)
7. **Response assembly:** Answer text + citations + model used + cache status + timing
8. **Cache + log:** Redis answer cache + PostgreSQL `rag_query_logs`

**Key files:** `main.py`, `routes/generate.py`, `services/llm_client.py`

#### 🟨 evaluation-service (Port 8005, Optional) — The Quality Judge

**What it does:** Uses an LLM to score RAG answer quality. Scores answers on relevance, faithfulness, and completeness. Supports live (per-query) and batch (Celery Beat, every 30 minutes) evaluation modes.

**When to use:** Started with `python run_services.py --evaluation`. Set `EVALUATE_ON_GENERATION=true` to auto-evaluate every generated answer.

**Key files:** `main.py`, `routes/evaluate.py`, `celery_app.py` (Beat schedule)

### Infrastructure Services

#### Keycloak (Port 8180) — Identity & Access Management
OAuth2/OpenID Connect identity provider. Handles user login, issues JWT access tokens. Auto-downloaded on first run by `run_services.py` (~150 MB). Requires Java 17+.

#### PostgreSQL 17 (Port 5434) — Relational Database
Stores all structured data. Used by all services. **Must be version 17** — older versions (14, 15, 16) must be stopped. See `run_guide.md` for stopping instructions.

#### Redis (Port 6379) — Cache & Message Queue
Serves four purposes: Celery broker, Celery result backend, retrieval cache, answer cache. Auto-downloaded portable Redis on first run. Graceful degradation to in-memory cache if unavailable.

#### Qdrant (Embedded) — Vector Database
No separate server. The `qdrant-client` library opens `data/qdrant/` directly. One Qdrant collection per domain, 384-dimensional vectors.

### What `run_services.py` Does (in order)

1. **Loads `.env`** — sets all environment variables
2. **Downloads + starts Redis** (first run) — portable Redis to `tools/redis/`
3. **Downloads + starts Keycloak** (first run) — to `tools/keycloak/`
4. **Graceful degradation:** If Java missing → dev JWT mock; If Redis fails → in-memory cache
5. **Purges stale Celery tasks** — removes leftover jobs
6. **Sets ML environment vars:** `HF_HUB_OFFLINE=1`, `KMP_DUPLICATE_LIB_OK=TRUE`, `CUDA_VISIBLE_DEVICES=""`, etc.
7. **Starts services in order:** domain-service (8001) → ingestion-service (8002) → retrieval-service (8003) → generation-service (8004) → [worker-service if `--worker`] → [evaluation-service (8005) if `--evaluation`]
8. **Monitors all processes** — logs crashes and keeps running

### Launcher Flags

```powershell
python run_services.py                       # APIs + infra only (no worker)
python run_services.py --worker              # also start Celery ingestion worker (required for OCR)
python run_services.py --evaluation          # also start evaluation-service on :8005
python run_services.py --worker --evaluation # full stack
python run_services.py --no-reload           # disable Uvicorn reload (save memory)
python run_services.py --skip-infra          # skip Redis/Keycloak (if already running externally)
```

> If Redis is not running: uses in-memory cache and sync PDF ingestion.
> If Redis is running + `--worker`: starts Celery worker for async ingestion; OCR (PaddleOCR/Surya) available.

---

## 6. Database Schema

### Entity Relationship Diagram

```mermaid
erDiagram
    users {
        varchar id PK
        varchar name
        varchar role
    }

    domains {
        uuid id PK
        varchar name UK
        text description
        varchar status
        varchar created_by
        timestamp created_at
        timestamp updated_at
    }

    domain_configs {
        uuid id PK
        uuid domain_id FK
        varchar llm_route
        int chunk_size
        int chunk_overlap
        float confidence_threshold
        json extra_settings
        timestamp updated_at
    }

    domain_roles {
        uuid id PK
        uuid domain_id FK
        varchar user_id
        varchar role
        varchar assigned_by
        timestamp assigned_at
    }

    documents {
        varchar id PK
        varchar domain_id
        varchar user_id
        varchar filename
        varchar file_path
        varchar status
        text error_msg
        timestamp created_at
    }

    document_chunks {
        text id PK
        text document_id
        text domain_id
        int page_num
        int chunk_index
        text content
        text source_type
        tsvector search_vec
        timestamp created_at
    }

    rag_query_logs {
        bigint id PK
        text domain_id
        text user_id
        text query
        text answer
        text llm_route
        text model
        bool cache_hit
        timestamp created_at
    }

    eval_results {
        bigint id PK
        text domain_id
        text query_log_id FK
        float faithfulness_score
        float relevance_score
        float completeness_score
        text judge_model
        timestamp created_at
    }

    domains ||--|| domain_configs : "has-config"
    domains ||--o{ domain_roles : "has-members"
    domains ||--o{ documents : "contains"
    documents ||--o{ document_chunks : "split-into"
    rag_query_logs ||--o{ eval_results : "evaluated-by"
```

### Table Details

| Table | Purpose | Key Columns |
|---|---|---|
| `users` | User profiles and global roles | `id` (login ID), `role` (system_admin, domain_admin, contributor, reader) |
| `domains` | Knowledge domain workspaces | `name` (unique), `status` (active/archived), `created_by` |
| `domain_configs` | Per-domain RAG settings | `llm_route` (api/local), `chunk_size`, `confidence_threshold` |
| `domain_roles` | Domain-level RBAC memberships | Unique constraint on `(domain_id, user_id)` |
| `documents` | Uploaded file metadata | `status` (pending → processing → done/failed), `error_msg` |
| `document_chunks` | Searchable text segments | `source_type` (pdf/docx/csv/png), `search_vec` (TSVECTOR/BM25), GIN index |
| `rag_query_logs` | Query audit trail | `query`, `answer`, `llm_route`, `model`, `cache_hit` |
| `eval_results` | LLM-as-judge scores | `faithfulness_score`, `relevance_score`, `completeness_score` |

**Apache AGE Graph Tables (WSL2, Sprint 3):**

| Label | Type | Purpose |
|---|---|---|
| Person, Project, Department, Policy, Role, Location, Skill | Vertex (7 types) | Entity nodes extracted from document chunks |
| MANAGES, BELONGS_TO, REPORTS_TO, OWNS, HAS_ROLE, WORKS_ON, HAS_SKILL, BASED_AT | Edge (8 types) | Typed relationships between entities |

---

## 7. Retrieval Pipeline

The retrieval service implements a 6-stage hybrid pipeline:

```mermaid
flowchart LR
    Q["User Query"]
    E["Embed with E5\n(query: prefix)"]
    D["Dense Search\n(Qdrant cosine)"]
    S["Sparse Search\n(PostgreSQL BM25)"]
    G["Graph Traversal\n(Apache AGE)"]
    F["RRF Fusion\n(k=60)"]
    R["Cross-Encoder\nReranking"]
    C["Redis Cache"]
    O["Top-K Chunks"]

    Q --> E --> D
    Q --> S
    Q --> G
    D --> F
    S --> F
    G --> F
    F --> R --> O
    Q --> C
    C -->|hit| O

    style D fill:#24b47e,color:#fff
    style S fill:#336791,color:#fff
    style G fill:#2c5f8a,color:#fff
    style R fill:#f59e0b,color:#000
    style C fill:#dc382d,color:#fff
```

| Stage | Model / Method | Purpose |
|---|---|---|
| 1. Embedding | `intfloat/multilingual-e5-small` (384d) | Encode query with `query:` prefix |
| 2. Dense search | Qdrant cosine similarity | Semantic similarity matching |
| 3. Sparse search | PostgreSQL `search_vec` FTS | Exact keywords and acronyms |
| 4. Graph traversal (Sprint 3) | Apache AGE Cypher (1-hop) | Relational entity queries |
| 5. Fusion | Reciprocal Rank Fusion (k=60) | Merge all result lists fairly |
| 6. Reranking | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Re-score top candidates |
| 7. Cache | Redis TTL cache | Skip computation for repeated queries |

### Graph Routing Decision (Sprint 3)

```mermaid
flowchart TD
    Q["Incoming Query"]
    RT["RetrievalRouter"]
    KW{"Contains relational\nkeywords?\n(manages, who is, يدير, مدير...)"}
    AGE_EN{"AGE_DATABASE_DSN\nconfigured?"}
    GR["Enable Graph Retrieval"]
    VB["Vector + BM25 only"]
    FUSE["Score Fusion\nGraph chunks score=0.95"]

    Q --> RT --> KW
    KW -->|Yes| AGE_EN
    KW -->|No| VB
    AGE_EN -->|Yes| GR
    AGE_EN -->|No| VB
    GR --> FUSE
    VB --> FUSE

    style GR fill:#2c5f8a,color:#fff
    style FUSE fill:#f59e0b,color:#000
```

---

## 8. OCR Pipeline

Scanned PDF pages and standalone image uploads (PNG/JPG/JPEG) are routed through an embedded OCR pipeline before chunking. Lives under `services/worker-service/tasks/ocr-service/ocr_service/`.

### 8.1 Routing Overview

```mermaid
flowchart TD
    F["File arrives at worker\n(PDF / DOCX / CSV / Image)"]
    EXT["tasks/extract.py"]
    DOCX["DOCX -> python-docx"]
    CSV["CSV -> pandas"]
    NATIVE{"Native text layer?\n(PyMuPDF)"}
    DIRECT["Use native text directly"]
    PRE["Preprocessing\nDenoise -> Deskew -> Adaptive Resize"]
    PADDLE["PaddleOCR\n(fast path, multi-language)"]
    PSCORE{"Paddle score >= threshold?\n(default 0.85)"}
    EXIT["Early exit: use Paddle result"]
    SURYA["Surya OCR\n(transformer fallback)"]
    PICK["Pick higher-scoring result"]
    OUT["{page, text, model_used,\nconfidence_score, ...}"]
    CHUNK["chunk.py -> embed.py -> index.py"]

    F --> EXT
    EXT --> DOCX
    EXT --> CSV
    EXT --> NATIVE
    NATIVE -->|Yes| DIRECT
    NATIVE -->|No| PRE
    PRE --> PADDLE
    PADDLE --> PSCORE
    PSCORE -->|Yes| EXIT
    PSCORE -->|No| SURYA
    SURYA --> PICK
    EXIT --> OUT
    PICK --> OUT
    DIRECT --> OUT
    DOCX --> OUT
    CSV --> OUT
    OUT --> CHUNK

    style PADDLE fill:#24b47e,color:#fff
    style SURYA fill:#7c3aed,color:#fff
    style PRE fill:#f59e0b,color:#000
```

### 8.2 Stages

| Stage | Component | What it does |
|---|---|---|
| 1. Format routing | `tasks/extract.py` | `.docx` → python-docx, `.csv` → pandas, `.pdf`/`.png`/`.jpg`/`.jpeg` → native text check |
| 2. Native text check | PyMuPDF (`fitz`) | Each PDF page's text layer is checked. If text exists, used directly — no OCR |
| 3. Preprocessing | `ocr_service/preprocessing/image_processor.py` | Denoise (`fastNlMeansDenoisingColored`), deskew (Hough line transform, ±15°), adaptive resize |
| 4. Fast OCR path | `ocr_service/engines/paddle_engine.py` | PaddleOCR — loaded once per language as singleton |
| 5. Confidence scoring | `ocr_service/scoring/ocr_scorer.py` | Paddle score = `0.7 × avg_confidence + 0.3 × valid_word_ratio` |
| 6. Routing decision | `ocr_service/routing/ocr_router.py` | If Paddle score ≥ `OCR_CONFIDENCE_THRESHOLD` → return; else run Surya |
| 7. Fallback OCR | `ocr_service/engines/surya_engine.py` | Layout-aware transformer OCR — used only when PaddleOCR is below threshold |
| 8. Output | `ocr_service/pipeline.py` | Returns `{page, text, model_used, confidence_score, processing_time_ms}` per page |

### 8.3 Multi-Language Support

| Variable | Purpose | Default |
|---|---|---|
| `OCR_LANG` | Single-language mode — PaddleOCR loads one language model | `en` |
| `OCR_LANGS` | Comma-separated (e.g. `ar,en`) — runs once per language, keeps highest-confidence result | unset (single-language mode) |

`OCR_LANGS` takes precedence when set. Each language's PaddleOCR pipeline is loaded once and cached as a singleton.

### 8.4 Platform Notes (Windows CPU)

PaddleOCR on Windows CPU requires:
- **`enable_mkldnn=False`** — avoids a oneDNN/PIR crash on first `predict()` call
- **`text_detection_model_name="PP-OCRv5_mobile_det"`** — avoids native access violation (0xC0000005) with mkldnn disabled
- **Microsoft Visual C++ Redistributable (latest x64)** — required for `libpaddle.pyd` and `shm.dll` to load

Already applied in `ocr_service/engines/paddle_engine.py`.

---

## 9. Authentication & RBAC

### Two Layers of Security

1. **JWT layer:** Every API call requires a valid Bearer JWT token. Services decode the token locally to extract `user_id` and roles.
2. **Service layer (FastAPI):** Domain-specific operations additionally call `domain-service /internal/check-access` using the shared `INTERNAL_API_KEY`.

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
| Query / generate answer | `reader` or higher on that domain |
| Manage domain members | `domain_admin` or `system_admin` |
| Update domain config | `domain_admin` or `system_admin` |

### RBAC Verification Matrix

| User | Operation | Expected | Status |
|---|---|---|---|
| `admin` (system_admin) | Create domain | 201 Created | ✅ Allowed |
| `admin` (system_admin) | Change config | 200 OK | ✅ Allowed (bypasses check) |
| `manager` (domain_admin) | Create domain | 403 Forbidden | ❌ Denied |
| `manager` (domain_admin) | Change config | 200 OK | ✅ Allowed on assigned domain |
| `contributor` | Upload PDF | 202 Accepted | ✅ Allowed on assigned domain |
| `contributor` | Change config | 403 Forbidden | ❌ Denied |
| `viewer` (reader) | Query domain | 200 OK | ✅ Allowed on assigned domain |
| `viewer` (reader) | Upload PDF | 403 Forbidden | ❌ Denied |
| `unauth` | Any operation | 401 Unauthorized | ❌ Denied |

### Internal Service-to-Service Calls

Services communicate internally using a shared secret header:
```
X-Internal-Key: <value of INTERNAL_API_KEY in .env>
```

### Dev Auth Fallback

When Keycloak is not running, `run_services.py` automatically uses `scripts/dev_auth.py` for local JWT auth with self-signed keys. In the React UI, sign in by typing the User ID directly into the Quick Access panel.

---

## 10. API Reference

All requests require: `Authorization: Bearer <JWT_ACCESS_TOKEN>`

### 10.1 domain-service (port 8001)

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

### 10.2 ingestion-service (port 8002)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/ingest` | `contributor`+ | Upload document (multipart: `file` + `domain_id`). Supported: PDF, DOCX, CSV, PNG, JPG, JPEG |
| GET | `/ingest/{document_id}` | Authenticated | Poll ingestion status |
| GET | `/health` | Public | Health check |

**Ingestion statuses:** `pending` → `processing` → `done` or `failed`

### 10.3 retrieval-service (port 8003)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/api/v1/retrieve` | `reader`+ (RBAC enforced) | Hybrid retrieval (query + domain_id). |
| GET | `/health` | Public | Health check |

### 10.4 generation-service (port 8004)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/generate/query` | `reader`+ | RAG query with answer + citations |
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
  "citations": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "policy.pdf",
      "source_type": "pdf",
      "chunk_index": 2,
      "page": 3,
      "score": 0.87,
      "text": "...",
      "retrieval_method": "vector"
    }
  ],
  "cache_hit": false,
  "llm_route": "api",
  "model": "llama-3.3-70b-versatile"
}
```

### 10.5 evaluation-service (port 8005, optional)

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
| evaluation-service | http://localhost:8005/docs |

---

## 11. Prerequisites

### Required

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | [Download](https://www.python.org/downloads/). Check "Add Python to PATH". |
| **PostgreSQL** | **17** | [Download](https://www.postgresql.org/download/windows/). Use port **5434**. Stop older versions. |
| **Java** | 17+ | [Adoptium Temurin](https://adoptium.net/). Required for Keycloak. |
| **Groq API key** | Free tier | [Get one](https://console.groq.com). Primary LLM provider. |
| **Microsoft Visual C++ Redistributable** | Latest x64 | [Download](https://aka.ms/vs/17/release/vc_redist.x64.exe). Required for PaddleOCR/PyTorch on Windows. |
| **RAM** | 8 GB min, 16 GB recommended | Embedding, reranking, PaddleOCR, and Surya models load into memory |
| **Disk** | ~10 GB free | ML model caches (incl. OCR models) + infra downloads |

### Auto-Downloaded (by `run_services.py`)

| Component | Port | Notes |
|---|---|---|
| **Redis** | 6379 | Portable Redis for Windows, downloaded to `tools/redis/` |
| **Keycloak** | 8180 | Downloaded to `tools/keycloak/` on first run (~150 MB) |
| **Qdrant** | — | Embedded at `data/qdrant/` (no server needed) |
| **PaddleOCR / Surya models** | — | Downloaded to `~/.paddlex/official_models/` on first OCR call per language |

### Optional

| Requirement | When needed |
|---|---|
| **Node.js + npm** | React frontend (`rag-ui/`) |
| **Ollama** | Local/offline LLM fallback |
| **WSL2 + Ubuntu 22.04** | Apache AGE Graph RAG (Sprint 3 only) |

---

## 12. Complete From-Scratch Setup & Run Guide

### 12.1 Stop & Remove Old PostgreSQL Versions

This project requires **PostgreSQL 17** on port **5434**. First, stop any older versions:

🟦 **Run in CMD as Administrator:**
```cmd
:: Stop old PostgreSQL versions (14, 15, 16)
net stop postgresql-x64-14
net stop postgresql-x64-15
net stop postgresql-x64-16

:: Disable auto-start
sc config postgresql-x64-14 start= disabled
sc config postgresql-x64-15 start= disabled
sc config postgresql-x64-16 start= disabled

:: Verify PostgreSQL 17 is running
sc query postgresql-x64-17 | findstr "STATE"
```

If PostgreSQL 17 is not installed, download from https://www.postgresql.org/download/windows/ and install with:
- Port: **5434**
- Superuser password: (your choice, e.g. `1234`)

---

### 12.2 Python Environment Setup

🟦 **Run in PowerShell:**
```powershell
cd "d:\Personal\Fixed Solutions\git files\Chatbot-Fixed-Team2"

# Create virtual environment (first time only)
python -m venv .venv

# Activate
.venv\Scripts\Activate.ps1

pip install -U pip setuptools wheel
pip install -r requirements.txt
```

> [!NOTE]
> Installing dependencies may take 10–20 minutes (PyTorch CPU, PaddleOCR/PaddlePaddle, Surya, and other ML libraries — approx. 2–3 GB total).

---

### 12.3 Environment Configuration

🟦 **Run in PowerShell:**
```powershell
copy .env.example .env
```

Edit `.env` and configure at minimum:
- `POSTGRES_PASSWORD` — password you set during PostgreSQL 17 installation
- `POSTGRES_PORT=5434` — match your PostgreSQL 17 port
- `DATABASE_URL` and `SYNC_DATABASE_URL` — update port to 5434 and password
- `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com)

---

### 12.4 PostgreSQL 17 Database Setup

🟦 **Run in CMD:**
```cmd
set PGPASSWORD=1234

:: Create the application database
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"

:: Verify it exists
psql -h localhost -p 5434 -U postgres -l
```

🟦 **Run in PowerShell (with virtual environment active):**
```powershell
# Run the smart migration script
python run_migration.py
```

Expected output:
```
Connecting to Relational database: localhost:5434/domain_db
  AGE extension available: False
  Relational migration complete (N statements executed).
```

**Verify tables created:**
```cmd
set PGPASSWORD=1234
psql -h localhost -p 5434 -U postgres -d domain_db -c "SELECT tablename FROM pg_tables WHERE schemaname='public';"
```

Expected tables: `users`, `domains`, `domain_configs`, `domain_roles`, `documents`, `document_chunks`, `rag_query_logs`

---

### 12.5 Java & Keycloak Identity Setup

🟦 **Run in PowerShell:**
```powershell
java -version
```

If not installed, download JDK 17+ from [Adoptium](https://adoptium.net/).

Keycloak is auto-downloaded by `run_services.py` on first run. For manual setup:

```powershell
mkdir "tools\keycloak\data\import" -Force
copy "services\auth\realm-export.json" "tools\keycloak\data\import\realm-export.json"

$env:KC_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD="admin"
.\tools\keycloak\bin\kc.bat start-dev --http-port=8180 --import-realm
```

---

### 12.6 Redis Setup

Redis is auto-downloaded portable on first `python run_services.py`. For manual install:
```powershell
winget install Redis.Redis
redis-server
```

---

### 12.7 WSL2 + Apache AGE Setup (Sprint 3 / Optional)

> [!NOTE]
> Required ONLY for Graph RAG. Skip if using only vector + BM25 retrieval.

🟦 **Run in PowerShell as Administrator (first time only):**
```powershell
wsl --install -d Ubuntu-22.04
```

Reboot, then run the setup script inside Ubuntu:

```bash
# Inside WSL2 Ubuntu terminal
cp /mnt/d/Personal/Fixed\ Solutions/git\ files/Chatbot-Fixed-Team2/wsl2_setup_v2.sh ~/wsl2_setup.sh
chmod +x ~/wsl2_setup.sh
~/wsl2_setup.sh
```

**Start Ubuntu (every session):**
```powershell
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"

# Verify connection
set PGPASSWORD=55555
psql -h localhost -p 5434 -U postgres -c "SELECT version();"
```

**Create graph database:**
```cmd
set PGPASSWORD=55555
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
psql -h localhost -p 5434 -U postgres -d domain_db -f migrations/sprint3_foundation.sql
```

---

### 12.8 Launching the Backend

🟦 **Run in PowerShell:**
```powershell
.venv\Scripts\Activate.ps1

# APIs only (no document processing)
python run_services.py

# Full stack with OCR worker
python run_services.py --worker

# Full stack with evaluation
python run_services.py --worker --evaluation
```

---

### 12.9 React Frontend Setup

🟦 **Run in a NEW PowerShell window:**
```powershell
cd rag-ui
npm install
npm run dev
```

Navigate to **http://localhost:5173**

---

### 12.10 End-to-End System Verification

🟦 **Run in PowerShell:**
```powershell
# Verify all service health endpoints
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8003/health
Invoke-RestMethod http://localhost:8004/generate/health

# Get a dev auth token
$token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"admin"}').token

# Upload a test document (should return 202 Accepted)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@test.pdf" -F "domain_id=11111111-1111-1111-1111-111111111111"

# Query for an answer
curl.exe -X POST http://localhost:8004/generate/query -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d '{"query": "What is the main topic?", "domain_id": "11111111-1111-1111-1111-111111111111"}'
```

---

## 13. Environment Variables

All services read from a single root `.env`. See `.env.example` for detailed inline documentation of every variable.

### Required Variables

| Variable | Purpose | Where to Get |
|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL password | Set during PostgreSQL 17 installation |
| `POSTGRES_PORT` | PostgreSQL port | `5434` (this project's default) |
| `DATABASE_URL` | Async Postgres URL | `postgresql+asyncpg://postgres:<pw>@localhost:5434/domain_db` |
| `SYNC_DATABASE_URL` | Sync Postgres URL | `postgresql://postgres:<pw>@localhost:5434/domain_db` |
| `GROQ_API_KEY` | Cloud LLM API key | [console.groq.com](https://console.groq.com) → API Keys |

### All Variables Summary

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `postgres` | PostgreSQL username |
| `POSTGRES_DB` | `domain_db` | Database name |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `QDRANT_PATH` | `data/qdrant` | Embedded vector store path |
| `KEYCLOAK_ISSUER` | `http://localhost:8180/realms/rag-system` | JWT issuer URL |
| `KEYCLOAK_PUBLIC_KEY` | (blank) | Auto-set by `run_services.py` |
| `INTERNAL_API_KEY` | `rag-internal-dev-key-change-in-prod` | Shared secret for internal endpoints |
| `AGE_DATABASE_DSN` | (blank) | Graph DB connection (Sprint 3 only) |
| `AGE_GRAPH_NAME` | `rag_graph` | Apache AGE graph name |
| `DEFAULT_LLM_ROUTE` | `api` | `api`=Groq, `local`=Ollama |
| `DEFAULT_CHUNK_SIZE` | `512` | Characters per chunk |
| `DEFAULT_CHUNK_OVERLAP` | `64` | Overlap between chunks |
| `DEFAULT_CONFIDENCE_THRESHOLD` | `0.5` | Min retrieval score for LLM context |
| `TOP_K_RETRIEVE` | `20` | Candidates before reranking |
| `TOP_K_RERANK` | `5` | Final chunks sent to LLM |
| `CACHE_TTL_SECONDS` | `3600` | Redis cache TTL |
| `OCR_LANG` | `en` | Single-language PaddleOCR |
| `OCR_LANGS` | (unset) | Multi-language, comma-separated |
| `OCR_CONFIDENCE_THRESHOLD` | `0.85` | Paddle score above which Surya is skipped |
| `EVALUATE_ON_GENERATION` | `true` | Auto-evaluate each generated answer |
| `EVAL_SAMPLE_RATE` | `1.0` | Fraction of queries to evaluate |
| `HF_HOME` | `D:\huggingface_cache` | Hugging Face model cache directory |
| `KMP_DUPLICATE_LIB_OK` | `TRUE` | Prevent OpenMP DLL conflicts |

---

## 14. Troubleshooting

### PostgreSQL 17 — Connection Refused (Port 5434)

```cmd
:: Start Windows PostgreSQL 17
net start postgresql-x64-17
sc query postgresql-x64-17 | findstr "STATE"
```

If using WSL2 PostgreSQL:
```powershell
wsl -l -v
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
```

### Old PostgreSQL Version Still Running

```cmd
net stop postgresql-x64-16
sc config postgresql-x64-16 start= disabled
```

### Redis — Connection Refused

```powershell
# Start manually
tools\redis\redis-server.exe tools\redis\redis.windows.conf
# Check port
netstat -ano | findstr :6379
```

### Keycloak — Not Ready / Slow Start

Keycloak takes **30–90 seconds** on first start. Wait and retry:
```powershell
curl http://localhost:8180/realms/rag-system
```

### Keycloak — Java Not Found

Install Java 17 from https://adoptium.net/ and restart terminal.

### Celery Worker Fails on Windows (billiard errors)

```powershell
python -m celery -A worker worker --loglevel=info -Q ingestion --pool=solo
```

### OpenMP Crash (`KMP_DUPLICATE_LIB_OK`)

```powershell
$env:KMP_DUPLICATE_LIB_OK="TRUE"
```

### OCR — DLL Load Failed (`libpaddle`)

Install Microsoft Visual C++ Redistributable (x64): https://aka.ms/vs/17/release/vc_redist.x64.exe — restart terminal.

### OCR — `NotImplementedError: ConvertPirAttribute2RuntimeAttribute`

Known `paddlepaddle==3.3.x` CPU issue. Already worked around in `paddle_engine.py` via `enable_mkldnn=False`.

### OCR — Worker Exits Code `0xC0000005`

Already worked around in `paddle_engine.py` via `text_detection_model_name="PP-OCRv5_mobile_det"`.

### OCR — Wrong Language / Poor Accuracy

```ini
OCR_LANG=ar         # Arabic-only
OCR_LANGS=ar,en     # Mixed Arabic/English
```

### HuggingFace Model Download — SSL Error

```powershell
.venv\Scripts\pip install truststore
```

### Port Already In Use

```powershell
netstat -ano | findstr ":8001 :8002 :8003 :8004 :5434 :6379 :8180"
taskkill /PID <pid> /F
```

### Unicode Errors in Worker (Windows cp1252)

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8=1
chcp 65001
```

### Ingestion Stuck on `processing`

- Check Celery worker is running: `python run_services.py --worker`
- On Windows, Celery requires `--pool=solo`
- First OCR call per language triggers model download — check worker log for `Loading PaddleOCR...`

### First Query Very Slow

Expected. Retrieval service loads embedding + reranker models on first request (~10–30 seconds). Subsequent queries are fast. Identical queries cached instantly.

### Windows — WinError 1455 / Paging File Too Small

- Increase Virtual Memory to at least 16 GB
- Close Docker Desktop, multiple IDEs
- `run_services.py` staggers startup automatically — let it complete

### WSL2 PostgreSQL Not Reachable (`localhost:5434`)

Inside WSL2 Ubuntu:
```bash
sudo service postgresql status
sudo service postgresql restart
sudo grep listen_addresses /etc/postgresql/17/main/postgresql.conf
sudo grep "0.0.0.0" /etc/postgresql/17/main/pg_hba.conf
```

### 401 Unauthorized

- Token expired (5-min lifespan) — get a fresh token
- Missing `Authorization: Bearer <token>` header
- Keycloak not fully started — wait 30–60 seconds

### 403 Forbidden on Upload

User lacks `contributor` role. Use `admin` user or assign domain membership.

### Database Schema Mismatch

```powershell
psql -h localhost -p 5434 -U postgres -d domain_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Then restart `run_services.py` to recreate clean tables.

### Step 6 Logs `GROQ_API_KEY not set — skipping relation extraction`

Add `GROQ_API_KEY` to `.env`. Step 5 (NER) still runs — only relation extraction is skipped.

---

## 15. Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example                      # environment template with full documentation
├── .env                              # your local config (not committed)
├── .gitignore                        # comprehensive ignore rules
├── requirements.txt                  # unified Python dependencies
├── run_services.py                   # main launcher (starts all services)
├── run_migration.py                  # smart migration runner (handles AGE detection)
├── clear_database.py                 # wipe all data and reset to clean slate
├── delete_chunks.py                  # database + vector store reset tool
├── wsl2_setup_v2.sh                  # WSL2 setup script for PostgreSQL 17 + Apache AGE
├── run_guide.md                      # step-by-step Windows runner guide
├── README.md                         # this file — complete project reference
├── migrations/                       # database SQL scripts
│   ├── setup_all.sql                 # creates schema + seeds initial data (used by run_migration.py)
│   ├── init_db.sql                   # legacy full init script
│   ├── sprint2_migration.sql         # Sprint 2 migration (source_type column)
│   ├── sprint3_foundation.sql        # Sprint 3 — Apache AGE setup + ontology labels
│   └── clear_db.sql                  # wipe all tables (used by run_migration.py)
├── data/                             # auto-created runtime data (gitignored)
│   ├── qdrant/                       # embedded vector DB files
│   ├── uploads/                      # uploaded documents (PDF, DOCX, CSV, images)
│   └── dev/                          # dev JWT RSA key pair (fallback auth)
├── tools/                            # auto-downloaded infra (gitignored)
│   ├── redis/                        # portable Redis for Windows
│   └── keycloak/                     # Keycloak 26.5.0
├── rag-ui/                           # React frontend (Vite + TypeScript + Tailwind)
│   ├── src/
│   │   ├── components/               # UI components (ChatBox, DomainCard, CitationPanel...)
│   │   ├── pages/                    # View pages (Login, Dashboard, Upload, Evaluate)
│   │   ├── store/                    # Zustand state management stores
│   │   └── lib/                      # API clients (axios instances per service)
│   └── vite.config.ts
├── scripts/                          # shared runtime modules
│   ├── dev_auth.py                   # fallback JWT auth (mints dev tokens)
│   ├── infra_manager.py              # downloads + starts Redis + Keycloak
│   ├── memory_cache.py               # in-memory TTL cache (Redis fallback)
│   ├── network_bootstrap.py          # SSL bootstrap for model downloads
│   └── qdrant_client_factory.py      # Qdrant embedded client helpers
└── services/
    ├── auth/realm-export.json        # Keycloak realm configuration
    ├── domain-service/               # port 8001 — domain CRUD + RBAC
    ├── ingestion-service/            # port 8002 — file upload + job queue
    ├── retrieval-service/            # port 8003 — hybrid search pipeline + graph routing
    ├── generation-service/           # port 8004 — RAG orchestration + LLM
    ├── evaluation-service/           # port 8005 — LLM-as-judge + Celery Beat
    └── worker-service/               # Celery worker
        ├── ontology.py               # Sprint 3 — entity/relation type definitions
        ├── ner.py                    # Sprint 3 — GLiNER entity extraction (lazy-loaded)
        ├── relation_extraction.py    # Sprint 3 — LLM-based relation extraction (20 chunks/call)
        └── tasks/
            ├── extract.py            # format routing + OCR pipeline entry point
            ├── process.py            # 6-step pipeline: extract→chunk→embed→index→NER→relations
            └── ocr-service/
                └── ocr_service/
                    ├── pipeline.py            # top-level OCR entry point
                    ├── preprocessing/
                    │   └── image_processor.py # denoise, deskew, resize
                    ├── engines/
                    │   ├── paddle_engine.py   # PaddleOCR (fast path)
                    │   └── surya_engine.py    # Surya (fallback)
                    ├── scoring/
                    │   └── ocr_scorer.py      # confidence scoring
                    └── routing/
                        └── ocr_router.py      # PaddleOCR → Surya routing decision
```

---

## 16. Quick Reference Card

```text
═══════════════════════════════════════════════════════════════
  RAG System — Quick Reference Card
═══════════════════════════════════════════════════════════════

POSTGRES 17 MANAGEMENT:
  net stop postgresql-x64-16          # stop old version
  net start postgresql-x64-17         # ensure PG17 runs (port 5434)
  sc query postgresql-x64-17 | findstr "STATE"

DATABASE SETUP:
  psql -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
  python run_migration.py              # create tables + seed data
  python clear_database.py            # wipe all data (then re-migrate)

WSL2 (Apache AGE — optional):
  start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
  wsl -l -v                           # verify Running state

BACKEND (project root, venv active):
  python run_services.py              # APIs + infra
  python run_services.py --worker     # + OCR Celery worker
  python run_services.py --worker --evaluation  # full stack
  Ctrl+C                              # graceful shutdown

FRONTEND (new terminal):
  cd rag-ui && npm install && npm run dev
  # → http://localhost:5173

HEALTH CHECKS:
  http://localhost:8001/health         # domain-service
  http://localhost:8002/health         # ingestion-service
  http://localhost:8003/health         # retrieval-service
  http://localhost:8004/generate/health# generation-service
  http://localhost:8005/docs           # evaluation Swagger
  http://localhost:8180/realms/rag-system # Keycloak realm

SWAGGER UIs:
  http://localhost:8001/docs  → http://localhost:8004/docs

DEV AUTH USERS:
  admin       → system_admin (create domains, all operations)
  manager     → domain_admin (manage assigned domains)
  contributor → contributor (upload + query)
  viewer      → reader (query only)

TOKEN (dev auth):
  POST http://localhost:8001/domains/auth/login
  Body: {"user_id": "admin"}

TYPICAL FLOW:
  1. Get JWT token (dev auth or Keycloak)
  2. POST /domains              → create domain
  3. POST /ingest               → upload PDF/DOCX/image
  4. GET  /ingest/{document_id} → poll until "done"
  5. POST /generate/query       → get AI answer + citations
  6. GET  /evaluate             → check answer quality score

OCR CONFIG (in .env):
  OCR_LANG=en           → single language (default English)
  OCR_LANGS=ar,en       → multi-language (Arabic + English)
  OCR_CONFIDENCE_THRESHOLD=0.85  → Surya fallback threshold
═══════════════════════════════════════════════════════════════
```

---

## 17. Sprint 3 — Hybrid Graph RAG (Apache AGE)

### 17.1 What This Sprint Adds

Sprints 1–2 retrieve answers via **dense vector search** (meaning) and **sparse BM25 search** (keywords). Both search *text similarity*. Neither can answer relationship questions like *"Who manages Project Alpha?"* — that requires a **knowledge graph**: entities connected by typed relationships.

Sprint 3 adds that graph layer on top of the existing system **without modifying any Sprint 1–2 code path**. If the graph layer fails for any reason, document ingestion and querying continue exactly as before.

### 17.2 Why Apache AGE Instead of Neo4j

| | Neo4j | Apache AGE (chosen) |
|---|---|---|
| Infrastructure | Separate DB server | Extension inside PostgreSQL — same engine already used |
| Domain isolation | Must re-implement | Reuses `domain_id` pattern from every other table |
| Chunk↔Graph linking | Cross-database join | Same database — can join with `document_chunks` directly |
| New failure point | Yes — entire new server | No — same Postgres instance |

### 17.3 Why a Second PostgreSQL Instance (WSL2)

Apache AGE has no prebuilt Windows binary for PostgreSQL (for the Windows version). Two options were evaluated:

1. **Native Windows PostgreSQL + AGE** — no prebuilt binary; requires compiling from source on Windows (complex, fragile)
2. **WSL2 Ubuntu + PostgreSQL 17 + AGE** — official Linux build, zero risk to the existing setup. **(Chosen.)**

The WSL2 PostgreSQL 17 + AGE instance runs on port **5434**, reachable from Windows like any other database. Python services connect via `AGE_DATABASE_DSN`.

### 17.4 Setting Up PostgreSQL 17 + Apache AGE (WSL2)

> [!NOTE]
> Skip this section if you don't need graph-based retrieval.

#### 1. Install WSL2 + Ubuntu

```powershell
# PowerShell as Administrator
wsl --install -d Ubuntu-22.04
# Reboot when prompted
```

#### 2. Run the Setup Script

```bash
# Inside WSL2 Ubuntu terminal
cp /mnt/d/Personal/Fixed\ Solutions/git\ files/Chatbot-Fixed-Team2/wsl2_setup_v2.sh ~/wsl2_setup.sh
chmod +x ~/wsl2_setup.sh
~/wsl2_setup.sh
```

The script:
- Installs PostgreSQL 17 from the official PGDG APT repository
- Clones and compiles Apache AGE (`PG17/v1.6.0-rc0` tag) from source
- Configures PostgreSQL to listen on port `5434`
- Sets the `postgres` user password to `55555`
- Initializes the `rag_graph` in Apache AGE

#### 3. Start Ubuntu Every Session

```powershell
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
wsl -l -v  # → Ubuntu-22.04 Running
```

#### 4. Create the Application Database

```cmd
set PGPASSWORD=55555
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
psql -h localhost -p 5434 -U postgres -d domain_db -f migrations/sprint3_foundation.sql
```

Expected output includes: `CREATE EXTENSION`, `create_vlabel` ×7, `create_elabel` ×8, `CREATE INDEX` ×7

#### 5. Update `.env` for Graph Connection

```ini
AGE_DATABASE_DSN=postgresql://postgres:55555@localhost:5434/domain_db
AGE_GRAPH_NAME=rag_graph
```

#### 6. Re-run Migration

```powershell
python run_migration.py
```

The migration now connects to both databases, automatically detects AGE, and runs graph-specific SQL only on the AGE instance.

---

### 17.5 The Ontology

Entity and relation types are defined **once** in `services/worker-service/ontology.py`. Every Sprint 3 component imports from this single file.

| Entity Types (7 Vertex Labels) | Relation Types (8 Edge Labels) |
|---|---|
| Person | MANAGES |
| Project | BELONGS_TO |
| Department | REPORTS_TO |
| Policy | OWNS |
| Role | HAS_ROLE |
| Location | WORKS_ON |
| Skill | HAS_SKILL |
| | BASED_AT |

---

### 17.6 Failure Isolation

`tasks/process.py` pipeline — 6 steps:

```
[1/6] Extract text          ─┐
[2/6] Chunk (semantic)        │  Sprints 1–2 — unchanged core RAG
[3/6] Embed                   │
[4/6] Index (Qdrant + PG)    ─┘

[5/6] Extract entities (NER)  ─┐  Sprint 3 — additive.
[6/6] Extract relations (LLM) ─┘  Wrapped in try/except.
                                   Failure here → warning logged
                                   Document still completes status="done"
```

Steps 5–6 run **after** indexing — the document is already fully searchable via Vector/BM25 before graph extraction begins.

---

### 17.7 Entity & Relation Extraction Pipeline

#### Step 5 — NER (`ner.py`)

Uses **GLiNER multilingual** (`urchade/gliner_multi-v2.1`), loaded lazily on first use. Labels sent as natural-language descriptions (e.g. `"a person name"` rather than bare `"Person"`) — spike testing on Arabic documents showed this produces measurably higher-confidence matches.

| Spike test (real Arabic document sentences) | Entity | Confidence |
|---|---|---|
| "يُعد جون جاكسون أول من وضع الأساس..." | جون جاكسون → Person | 0.92 |
| "أحمد محمد يدير مشروع تطوير النظام..." | أحمد محمد → Person | 0.97 |
| (same sentence) | مشروع تطوير النظام → Project | 0.52 |
| (same sentence) | قسم تقنية المعلومات → Department | 0.89 |

`CONFIDENCE_THRESHOLD = 0.6` filters out low-confidence noise.

#### Step 6 — Relation Extraction (`relation_extraction.py`)

Takes entities from Step 5 and determines **typed relationships** between them using Groq LLM.

- **Why LLM instead of spaCy?** spaCy has no official Arabic dependency parser. Published Arabic dependency-parsing accuracy tops out ~76%; an LLM reading the sentence directly is more reliable.
- **Batching:** `CHUNKS_PER_GROUP = 20` — roughly 10 LLM calls for a 200-chunk document.
- **Validation:** Every triple is validated against `ontology.RELATION_TYPES` — hallucinated relation types outside the 8 defined ones are dropped.

---

### 17.8 Query-Time Graph Retrieval & Routing

```mermaid
flowchart TD
    Q["User Query"]
    NORM["Normalize\n(strip Harakat, unify spelling)"]
    KW{"Relational keywords?\n(manages, who is, يدير, مدير, قسم...)"}
    NER2["Query-Time NER\n+ Substring Match vs Graph Nodes"]
    CYPHER["1-Hop Cypher Traversal\nMATCH v - r - neighbor"]
    CHUNKS["Resolve chunk_ids\nfrom matched entities"]
    FUSE["Score Fusion\nGraph chunks → 0.95 priority"]

    Q --> NORM --> KW
    KW -->|Yes| NER2 --> CYPHER --> CHUNKS --> FUSE
    KW -->|No| FUSE

    style CYPHER fill:#2c5f8a,color:#fff
    style FUSE fill:#f59e0b,color:#000
```

1. Query normalized (Arabic diacritics stripped, spelling variants unified)
2. Checked against all node names in the domain's graph — substring matching
3. Matched entities → 1-hop Cypher traversal: entity + relationships + neighbors
4. `chunk_ids` from matched entities resolved → raw text chunks from PostgreSQL
5. Graph chunks assigned score `0.95` → fused with Vector + BM25 results

---

### 17.9 Performance & Evaluation

#### LLM-as-Judge Evaluation
```powershell
python run_services.py --evaluation
# POST http://localhost:8005/evaluate → score 0.0–1.0 with faithfulness/completeness explanation
```

#### Query Audit Logs
```sql
-- Cache hit rate by model
SELECT
    model,
    COUNT(*) as total_queries,
    SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::float / COUNT(*) as cache_hit_rate
FROM rag_query_logs
GROUP BY model;
```

---

### 17.10 Sprint 3 Troubleshooting

| Problem | Solution |
|---|---|
| `localhost:5434` unreachable from Windows | Verify Ubuntu is Running: `wsl -l -v`. Start: `start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"` |
| `extension "age" is not available` | AGE build failed — re-run Steps 4–5 of `wsl2_setup_v2.sh` inside WSL2, check for compile errors |
| NER very slow on first document | Expected — GLiNER downloads (~500 MB–1 GB) on first worker restart, cached afterward |
| `GROQ_API_KEY not set — skipping relation extraction` | Add `GROQ_API_KEY` to `.env`. Step 5 (NER) still runs. |
| `UnicodeDecodeError: 'charmap'` in worker logs | Run: `$env:PYTHONUTF8=1; chcp 65001` before starting services |
| `listen_addresses` not set | Inside WSL2: `sudo nano /etc/postgresql/17/main/postgresql.conf` → set `listen_addresses = '*'`, then `sudo service postgresql restart` |

---

## 18. Monitoring — Prometheus + Grafana

Real-time observability for every service: request latency, error rates, evaluation quality scores, retrieval pipeline stage breakdown, and Celery worker health — all visible in pre-built Grafana dashboards with alerting to Slack.

### 18.1 What Gets Monitored

| Service | What is tracked |
|---|---|
| domain-service | Request rate, error rate, p50/p95/p99 latency |
| ingestion-service | Request rate, error rate, latency |
| retrieval-service | Per-stage latency (embed/dense/sparse/RRF/rerank), cache hit rate, stage errors, RRF top score |
| generation-service | Request rate, error rate, latency |
| evaluation-service | Eval score per judge type (custom_judge / ragas), eval latency, judge reachability, failure rate |
| worker-service (Celery) | Task throughput, task duration p50/p95, in-flight tasks, worker UP/DOWN |

### 18.2 Architecture

```
FastAPI services (ports 8001–8005)        Celery worker-service
  each exposes GET /metrics                 exposes /metrics on :9090
         │                                        │
         └──────────────┬─────────────────────────┘
                        ▼
              Prometheus (Docker :9091)
              scrapes every 15 seconds
                        │
          ┌─────────────┴──────────────┐
          ▼                            ▼
  Grafana (Docker :3000)      Alertmanager (Docker :9093)
  4 dashboards pre-loaded      fires Slack alerts on threshold
```

### 18.3 Files Added

```
Chatbot-Fixed-Team2/
├── monitoring/
│   ├── docker-compose.monitoring.yml      ← starts Prometheus + Grafana + Alertmanager
│   ├── prometheus/
│   │   ├── prometheus.yaml                ← scrape config (all 6 targets)
│   │   └── alerts.yaml                    ← 8 alert rules
│   ├── grafana/
│   │   ├── datasources/
│   │   │   └── prometheus-datasource.yaml
│   │   ├── provisioning/
│   │   │   └── dashboards.yaml
│   │   └── dashboards/
│   │       ├── service-health.json        ← Dashboard 1: request rate, errors, latency
│   │       ├── evaluation-quality.json    ← Dashboard 2: eval scores, judge reachability
│   │       ├── infra-overview.json        ← Dashboard 3: Celery tasks, worker health
│   │       └── retrieval-pipeline.json    ← Dashboard 4: per-stage retrieval breakdown
│   └── alertmanager/
│       └── alertmanager.yaml
└── services/
    └── shared/
        └── service_metrics.py             ← shared /metrics endpoint for 4 FastAPI services
```

> `evaluation-service` has its own `metrics.py` (multiprocess-safe, uses `prometheus_fastapi_instrumentator`).
> `retrieval-service` additionally has `retrieval_metrics.py` for pipeline-stage metrics.

### 18.4 Prerequisites

- **Docker Desktop** running
- `pip install prometheus-client` in your venv (add to `requirements.txt`)
- `prometheus-fastapi-instrumentator` already installed (used by evaluation-service)

### 18.5 One-Time Code Setup

#### Step 1 — Add to each service's `main.py` (domain, ingestion, retrieval, generation)

```python
from service_metrics import metrics_router, instrument_app

app = FastAPI(title="generation-service")
instrument_app(app, service_name="generation-service")   # middleware
app.include_router(metrics_router)                        # GET /metrics
```

Change `service_name` per service. The shared `service_metrics.py` lives in `services/shared/` — importable because `run_services.py` adds `services/shared/` to `sys.path`.

#### Step 2 — evaluation-service `main.py`

```python
from metrics import setup_metrics   # uses evaluation-service's own metrics.py

app = FastAPI(title="evaluation-service")
setup_metrics(app)
```

#### Step 3 — Add to `.env`

```ini
PYTHONPATH=services/shared
PROMETHEUS_MULTIPROC_DIR=./tmp/prometheus_multiproc
```

`PROMETHEUS_MULTIPROC_DIR` is required so evaluation-service's Celery worker and FastAPI process share metric state correctly.

### 18.6 Starting the Monitoring Stack

```powershell
# Start your services as normal first
python run_services.py --worker --evaluation

# Then in a NEW terminal — start monitoring
cd monitoring
docker compose -f docker-compose.monitoring.yml up -d

# Verify all 3 containers are running
docker compose -f docker-compose.monitoring.yml ps
```

### 18.7 Verifying Services Are Scraped

```powershell
# Each service should return Prometheus text format
curl http://localhost:8001/metrics -UseBasicParsing
curl http://localhost:8002/metrics -UseBasicParsing
curl http://localhost:8003/metrics -UseBasicParsing
curl http://localhost:8004/metrics -UseBasicParsing
curl http://localhost:8005/metrics -UseBasicParsing
curl http://localhost:9090/metrics -UseBasicParsing   # Celery worker
```

Then open **http://localhost:9091/targets** — all 6 targets should show state **UP**.

### 18.8 Opening the Dashboards

Navigate to **http://localhost:3000** — login with `admin` / `admin`.

Go to **Dashboards → RAG Platform**:

| Dashboard | What you see |
|---|---|
| **Service Health** | Request rate per service, 5xx error rate, p50/p95/p99 latency, requests in-flight |
| **Evaluation Quality** | Eval score by judge type (color-coded 0–1), eval latency, judge UP/DOWN, failure rate % |
| **Infra Overview** | Celery worker status, task throughput success/failure, task duration p50/p95, 24h totals |
| **Retrieval Pipeline** | Per-stage latency (embed/dense/sparse/RRF/rerank), Qdrant vs BM25 vs reranker breakdown, cache hit rate, stage errors |

### 18.9 Alert Rules

8 alerts are pre-configured in `monitoring/prometheus/alerts.yaml`:

| Alert | Condition | Severity |
|---|---|---|
| `JudgeLLMUnreachable` | `eval_judge_reachable == 0` for 2 min | critical |
| `EvalFailureRateHigh` | >10% eval failures over 15 min | warning |
| `GenerationLatencyHigh` | generation p95 > 5 s for 5 min | warning |
| `ServiceHighErrorRate` | >5% 5xx rate for 5 min on any service | critical |
| `CeleryWorkerDown` | `celery_worker_up == 0` for 2 min | critical |
| `CeleryIngestionBacklog` | >10 tasks in-flight for 10 min | warning |
| `RetrievalStageErrors` | any retrieval stage > 0.1 errors/s for 5 min | warning |
| `RetrievalLatencyHigh` | retrieval pipeline p95 > 3 s for 5 min | warning |

### 18.10 Slack Alerts (Optional)

1. Create a Slack webhook at https://api.slack.com/messaging/webhooks
2. Edit `monitoring/alertmanager/alertmanager.yaml`:
   ```yaml
   global:
     slack_api_url: "https://hooks.slack.com/services/YOUR/ACTUAL/WEBHOOK"
   ```
3. Reload: `curl -X POST http://localhost:9093/-/reload`

Critical alerts go to `#rag-alerts-critical`, warnings to `#rag-alerts`.

### 18.11 Stopping the Monitoring Stack

```powershell
cd monitoring
docker compose -f docker-compose.monitoring.yml down

# To also wipe all stored metrics data:
docker compose -f docker-compose.monitoring.yml down -v
```

### 18.12 Monitoring Troubleshooting

| Problem | Fix |
|---|---|
| Target shows `connection refused` in Prometheus | Service not running or `/metrics` not wired in `main.py`. Run `curl localhost:800X/metrics`. |
| Grafana shows "No data" | Datasource URL must be `http://prometheus:9090` (container name). Already set in `prometheus-datasource.yaml`. |
| Celery metrics target DOWN | `start_metrics_server(9090)` not called at worker startup. Check worker logs for `[celery-metrics] Prometheus metrics server started`. |
| `ValueError: Duplicated timeseries` on service start | `service_metrics.py` registered twice. The `_get_or_create()` guard in `service_metrics.py` prevents this — ensure you're importing from `service_metrics`, not `metrics`. |
| evaluation-service metrics always 0 in Grafana | `PROMETHEUS_MULTIPROC_DIR` not set in `.env` or not seen by all 3 evaluation processes (FastAPI + Celery worker + Celery beat). Set it in `.env`, not in the shell. |
| Prometheus config change not picked up | Hot-reload: `curl -X POST http://localhost:9091/-/reload` |