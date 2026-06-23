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

---

## 1. Project Overview

### What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain Retrieval-Augmented Generation (RAG) system**. It is a backend + frontend application that allows organizations to:

- Create separate **knowledge domains** (isolated knowledge bases, e.g., "HR Policies", "Tech Support", "Legal Contracts")
- Upload **documents** (PDF, DOCX, CSV, and images) into those domains
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
A user uploads a document (PDF, DOCX, CSV, or image) to a domain. Here's what happens internally:

1. **`ingestion-service`** receives the file, validates its type (PDF, DOCX, CSV, PNG, JPG, JPEG), and saves it to disk
2. It creates a `documents` record in PostgreSQL (status = `pending`)
3. It enqueues an async job into **Redis** (Celery task queue)
4. **`worker-service`** picks up the job and:
   - **Extracts text** using a format-specific extractor: PyMuPDF for PDFs with native text layers, python-docx for DOCX files, pandas for CSVs. For **scanned PDF pages and standalone images**, text is extracted via the embedded **OCR pipeline** — a PaddleOCR fast path with automatic Surya fallback for low-confidence results (see [Section 8: OCR Pipeline](#8-ocr-pipeline))
   - **Splits the text into chunks** using semantic chunking with embedding similarity (topic-boundary detection)
   - **Generates embedding vectors** for each chunk using the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model.
   - **Stores vectors in Qdrant** — one collection per domain, each point contains the chunk text, document ID, page number, chunk index, filename, and source type
   - **Stores chunks in PostgreSQL** — with a `TSVECTOR` column for BM25 full-text search and a `source_type` column for format tracking
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
9. The response includes: the answer text, citations (which chunk, which document filename, which page, source type, relevance score), the model used, and whether it was a cache hit

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-domain isolation | Each domain has its own documents, members, configuration, and vector collection. Complete data separation. |
| Role-based access (RBAC) | Three-layer security: Keycloak JWT tokens at the gateway + per-domain role checks in each service including retrieval |
| Hybrid retrieval | Combines dense vector search (semantic meaning) + sparse BM25 search (exact keywords) + cross-encoder reranking for highest accuracy |
| AI answer generation | Groq (cloud, fast, free tier) or Ollama (local, offline). Per-domain LLM routing — some domains can use cloud, others local. |
| Multi-format ingestion | Supports PDF, DOCX, CSV, and image (PNG/JPG) uploads. Format-specific extractors with a PaddleOCR + Surya OCR pipeline for scanned content. |
| Async document processing | Documents are processed in background via Celery + Redis. The user gets immediate `202 Accepted` and can poll status. |
| Intelligent caching | Redis caches both retrieval results and generated answers. Identical repeat queries return instantly. |
| Citation grounding | Every AI answer includes citations with document filename, source type, page/row number, and relevance score |
| Multi-language OCR | Scanned pages and images are OCR'd via PaddleOCR/Surya with configurable language models (`OCR_LANG` / `OCR_LANGS`), not limited to a single language |
| Graceful degradation | If Redis is down → uses in-memory cache. If Groq is down → falls back to Ollama. If Keycloak is down → uses dev auth. |
| React chat UI | Full-featured web interface for login, domain management, document upload, and interactive Q&A |

### How It Works (Simple Analogy)

Think of the system as a **smart library with an AI librarian**:

1. **You create a shelf (domain)** — a labeled section of the library for one topic.
2. **You add books (PDFs, Word docs, spreadsheets, images)** — the system scans each item, splits it into paragraphs (chunks), and indexes them in two ways: by meaning (vectors) and by keywords (full-text search).
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
flowchart TD
    Client["Client (Browser / API)"]
    Gateway["Traefik Gateway"]
    KC["Keycloak Auth Provider"]
    GS["generation-service"]
    RD["Redis Cache"]
    DS["domain-service"]
    RS["retrieval-service"]
    QD["Qdrant (Vector DB)"]
    PG["PostgreSQL (BM25 DB)"]
    LLM["LLM (Groq / Ollama)"]

    Client -->|1. Submit RAG Query| Gateway
    Gateway -->|2. Validate Token| KC
    KC -->|3. Token Validated| Gateway
    Gateway -->|4. Forward Request| GS
    GS -->|5. Check Cache| RD
    
    RD -->|Cache Hit: Return Answer| Client
    
    RD -->|Cache Miss| GS
    GS -->|6. Fetch Config| DS
    GS -->|7. Request Retrieval| RS
    
    RS -->|8a. Dense Search| QD
    RS -->|8b. Sparse Search| PG
    QD & PG -->|9. Fusion & Rerank| RS
    RS -->|10. Return Chunks| GS
    
    GS -->|11. Build Prompt & Call| LLM
    LLM -->|12. Generate Answer| GS
    GS -->|13. Log Query| PG
    GS -->|14. Cache Response| RD
    GS -->|15. Return Final Answer| Client
    
    style KC fill:#f9a825,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style LLM fill:#7c3aed,color:#fff
```

*   **What it is**: The Query Flow represents the runtime execution path of a user's question, tracing the lifecycle from initial gateway entry, cache lookup, hybrid document retrieval, LLM prompt assembly, to audit logging and response generation.
*   **How it works**:
    1.  The **Client** submits an HTTP `POST` request containing the question and target domain ID to the API Gateway.
    2.  The **Traefik Gateway** validates the Bearer JWT token against **Keycloak** (or falls back to local RSA validation in dev mode) before forwarding the query.
    3.  Once verified, the request is routed to the **`generation-service`**.
    4.  The `generation-service` checks **Redis** for a cached query response. If found, it returns the answer instantly (Cache Hit).
    5.  On a cache miss, the service fetches the target domain's LLM configuration and confidence thresholds from the **`domain-service`**.
    6.  It requests document context from the **`retrieval-service`**, which performs a parallel hybrid search: dense vector search in **Qdrant** and sparse keyword search in **PostgreSQL**.
    7.  The retrieved chunks are fused via Reciprocal Rank Fusion (RRF) and re-scored with a Cross-Encoder reranking model.
    8.  The `generation-service` constructs a RAG prompt grounding the question in the retrieved passages and sends it to the configured **LLM** (Groq cloud or local Ollama).
    9.  The LLM-generated answer is cached in Redis, logged in PostgreSQL for audit trailing, and returned to the client with full citation metadata.

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

    Client -->|1. POST /ingest| IS
    IS -->|2. Validate Type| IS
    IS -->|3. Check Access| DS
    IS -->|4. Create Pending Doc| PG
    IS -->|5. Enqueue Job| RD
    IS -->|6. Return 202 Accepted| Client

    RD -->|7. Dequeue Job| WS
    WS -->|8. Extract Text| WS
    WS -->|9. Chunk & Embed| WS
    WS -->|10. Index Vectors| QD
    WS -->|11. Store Chunks & FTS| PG
    WS -->|12. Set Status = done| PG

    style DS fill:#60a5fa,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style WS fill:#a78bfa,color:#000
```

*   **What it is**: The Ingestion Pipeline is an asynchronous document processing workflow that validates uploads, performs format-specific text extraction, semantically segments paragraphs, generates high-dimensional vector embeddings, and builds dual database search indexes.
*   **How it works**:
    1.  A contributor uploads a document (PDF, DOCX, CSV, PNG, JPG, or JPEG) to the **`ingestion-service`**.
    2.  The service performs type validation and checks user permissions by querying the **`domain-service`**.
    3.  It registers the document in **PostgreSQL** with a `pending` status and publishes a processing task to **Redis**.
    4.  The client receives an immediate `202 Accepted` response with the `document_id` to poll for completion.
    5.  The background **`worker-service`** (Celery worker running on a solo pool) picks up the task from Redis.
    6.  It extracts raw text using format-specific extractors: PyMuPDF for PDF pages with a native text layer, python-docx for DOCX, pandas for CSV. Scanned PDF pages and standalone images go through the **OCR pipeline** (PaddleOCR with Surya fallback — see [Section 8](#8-ocr-pipeline)).
    7.  The text is split into semantic paragraphs, and each chunk is embedded using the E5 transformer model.
    8.  The worker writes vectors and provenance metadata into **Qdrant** and stores full-text search indexes in **PostgreSQL**.
    9.  The document status is updated to `done` (or `failed` with populated error logs if processing fails).

---

### 2.4 Authentication Flow

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    Gateway["Traefik Gateway"]
    KC["Keycloak Auth Provider"]
    DS["domain-service (Dev Auth)"]
    Service["Microservice (retrieve/ingest/etc)"]

    subgraph KeycloakMode ["Option A: Keycloak Mode"]
        Client -->|1. Authenticate| KC
        KC -->|2. Return JWT access_token| Client
        Client -->|3. Call Request + Bearer JWT| Gateway
        Gateway -->|4. forwardAuth| KC
        KC -->|5. Verify User| Gateway
        Gateway -->|6. Forward Request| Service
    end

    subgraph DevAuthMode ["Option B: Dev Auth Mode (Local Fallback)"]
        Client -->|"1. POST /domains/auth/login {user_id}"| DS
        DS -->|2. Mint & Return Dev JWT| Client
        Client -->|3. Call Request + Bearer Dev JWT| Service
    end

    Service -->|Verify signature & extract claims| Service
    Service -->|Check Domain Access| DS

    style KC fill:#f9a825,color:#000
    style DS fill:#60a5fa,color:#000
    style Gateway fill:#7c3aed,color:#fff
    style Service fill:#a78bfa,color:#000
```

*   **What it is**: The Authentication Flow provides a two-tier JWT-based authorization and domain-level Role-Based Access Control (RBAC). It supports both a production-ready Keycloak integration and a lightweight local development authentication fallback.
*   **How it works**:
    *   **Option A (Keycloak)**: In production, the client exchanges credentials with **Keycloak** for an access token. Every gateway call is validated on the fly by Traefik gateway using Keycloak's user info before passing the request to microservices.
    *   **Option B (Dev Auth)**: In local dev mode, the client logs in directly via the **`domain-service`** by passing a user ID. The service verifies the user in PostgreSQL and mints a local JWT token signed with an RSA key.
    *   **Access Enforcement**: The receiving service decodes the JWT signature and queries the `/internal/check-access` endpoint of the `domain-service` using a shared key to verify the caller has the required domain membership (e.g. `reader` for retrieval, `contributor` for ingestion).

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

### Decision 10: OCR Is a PaddleOCR + Surya Ensemble, Not Tesseract

Scanned pages and image uploads are handled by an embedded OCR pipeline (`ocr_service/`, used by `worker-service/tasks/extract.py`) instead of Tesseract. PaddleOCR runs first as a fast path; if its confidence score falls below a threshold, Surya (a transformer-based, layout-aware OCR model) runs as a fallback and the higher-scoring result is kept. This keeps average latency low while improving accuracy on low-quality or mixed-language scans. See [Section 8: OCR Pipeline](#8-ocr-pipeline) for full details.

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
| PDF extraction | PyMuPDF | — | Native text extraction for digital PDF pages |
| OCR (fast path) | PaddleOCR | — | Primary OCR engine for scanned pages and images, multi-language via `OCR_LANG`/`OCR_LANGS` |
| OCR (fallback) | Surya | — | Layout-aware transformer OCR, used when PaddleOCR confidence is below threshold |
| DOCX extraction | python-docx | 1.1.2 | Word document text extraction |
| CSV extraction | pandas | 2.2.3 | Tabular data text extraction |
| ML runtime | PyTorch CPU | 2.6.0+ | Embedding model and Surya inference |

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
| worker-service | — | Celery worker | Extract (incl. OCR) → chunk → embed → index |
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

**What it does:** Runs in the background as a Celery worker. Picks up ingestion jobs from Redis and does the heavy lifting: text extraction (including OCR), chunking, embedding, and indexing.

**How it works internally (step by step):**
1. **Text extraction:** Uses PyMuPDF (`fitz`) to extract text from PDF pages with a native text layer. For scanned/image-only PDF pages and for standalone image uploads (PNG/JPG/JPEG), text is extracted via the embedded **OCR pipeline** — PaddleOCR first, with an automatic Surya fallback for low-confidence pages. See [Section 8: OCR Pipeline](#8-ocr-pipeline).
2. **Semantic chunking:** Splits extracted text into chunks of ~512 characters (configurable per domain) with 64-character overlap. The overlap ensures sentences aren't cut in half at chunk boundaries.
3. **Embedding generation:** Runs each chunk through the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model architecture.
4. **Vector indexing (Qdrant):** Stores embeddings in Qdrant with payloads containing the chunk text, document ID, page number, and chunk index. Each domain gets its own Qdrant collection (named by domain ID).
5. **BM25 indexing (PostgreSQL):** Inserts chunks into the `document_chunks` table with a `search_vec` TSVECTOR column for full-text keyword search.
6. **Status update:** Sets the document status to `done` (or `failed` with an error message).

**Important:** On Windows, Celery runs with `--pool=solo` (no fork support). This means one job at a time, but it's reliable.

**Key files:** `tasks/extract.py` (text + OCR extraction routing), `tasks/index.py` (the main ingestion task), `celery_app.py` (Celery configuration), `tasks/ocr-service/ocr_service/` (OCR pipeline — see [Section 8](#8-ocr-pipeline)).

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
9. **Starts worker-service** — Celery worker (only if `--worker` flag is used). This is the process that loads PaddleOCR and Surya (see [Section 8](#8-ocr-pipeline)).
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
> If Redis is running + `--worker`: starts Celery worker for async ingestion, and OCR (PaddleOCR/Surya) is available.

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
        text search_vec
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
        timestamp created_at
    }

    domains ||--|| domain_configs : "has-config"
    domains ||--o{ domain_roles : "has-members"
    domains ||--o{ documents : "contains"
    documents ||--o{ document_chunks : "split-into"
```

### Table Details

| Table | Purpose | Key Columns |
|---|---|---|
| `users` | User profiles and global roles | `id` (login ID), `role` (system_admin, domain_admin, contributor, reader) |
| `domains` | Knowledge domain workspaces | `name` (unique), `status` (active/archived), `created_by` |
| `domain_configs` | Per-domain RAG settings | `llm_route` (api/local), `chunk_size`, `confidence_threshold` |
| `domain_roles` | Domain-level RBAC memberships | Unique constraint on `(domain_id, user_id)` |
| `documents` | Uploaded file metadata | `status` (pending → processing → done/failed) |
| `document_chunks` | Searchable text segments | `source_type` (pdf/docx/csv/png), `search_vec` (TSVECTOR for BM25), GIN index |
| `rag_query_logs` | Query audit trail | `query`, `answer`, `llm_route`, `model` |

> For the complete database schema and seed data, see the initialization script [migrations/init_db.sql](file:///d:/Personal/Fixed%20Solutions/Project%20Files/Main/migrations/init_db.sql) and the instructions in [Section 12](#12-complete-from-scratch-setup--run-guide).

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

## 8. OCR Pipeline

Scanned PDF pages and standalone image uploads (PNG/JPG/JPEG) don't have a native text layer, so they're routed through an embedded **OCR pipeline** before chunking. The pipeline lives under `services/worker-service/tasks/ocr-service/ocr_service/` and is consumed by `tasks/extract.py` (and its drop-in variant `tasks/extract_with_ocr.py`).

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
| 1. Format routing | `tasks/extract.py` | `.docx` → python-docx, `.csv` → pandas, `.pdf`/`.png`/`.jpg`/`.jpeg` → checked for native text first |
| 2. Native text check | PyMuPDF (`fitz`) | Each PDF page's text layer is checked. If text exists, it's used directly — no OCR, no model overhead |
| 3. Preprocessing | `ocr_service/preprocessing/image_processor.py` | For pages/images with no native text: denoise (`fastNlMeansDenoisingColored`), deskew (Hough line transform, ±15° correction), adaptive resize (upscale below 1000px, downscale above 4096px) |
| 4. Fast OCR path | `ocr_service/engines/paddle_engine.py` (PaddleOCR) | Runs first on every page. Loaded once per language as a singleton and reused across requests |
| 5. Confidence scoring | `ocr_service/scoring/ocr_scorer.py` | PaddleOCR score = `0.7 × avg_confidence + 0.3 × valid_word_ratio`. Surya score = weighted blend of text density, language consistency, and noise penalty |
| 6. Routing decision | `ocr_service/routing/ocr_router.py` | If PaddleOCR score ≥ `OCR_CONFIDENCE_THRESHOLD` (default `0.85`), return immediately. Otherwise run Surya and keep whichever result scores higher |
| 7. Fallback OCR | `ocr_service/engines/surya_engine.py` (Surya) | Layout-aware transformer OCR, used only when PaddleOCR is below threshold — keeps average latency low while catching low-quality scans |
| 8. Output | `ocr_service/pipeline.py` | Returns `{"page": N, "text": ..., "model_used": "paddle"|"surya", "confidence_score": ..., "processing_time_ms": ...}` per page, which feeds into chunking exactly like native-text pages |

### 8.3 Multi-Language Support

OCR language is configurable via environment variables (read by `paddle_engine.py`):

| Variable | Purpose | Default |
|---|---|---|
| `OCR_LANG` | Single-language mode — PaddleOCR loads one language model | `en` |
| `OCR_LANGS` | Comma-separated list (e.g. `ar,en`) — PaddleOCR runs once per language and keeps the result with the highest average confidence | unset (single-language mode) |

`OCR_LANGS` takes precedence over `OCR_LANG` when set. Each language's PaddleOCR pipeline is loaded once and cached as a singleton (under `~/.paddlex/official_models/` on first run), so subsequent pages don't re-download or re-initialize models. Using more languages in `OCR_LANGS` means one extra OCR pass per image per language — keep the list to 2-3 languages you actually expect in your documents.

### 8.4 Platform Notes (Windows CPU)

PaddleOCR on Windows CPU requires a couple of non-obvious settings to run reliably with `paddlepaddle==3.3.1` / `paddleocr>=3.7.0`:

- **`enable_mkldnn=False`** — avoids a oneDNN/PIR crash (`NotImplementedError: ConvertPirAttribute2RuntimeAttribute ... pir::ArrayAttribute<DoubleAttribute>`) that occurs on the first `predict()` call with mkldnn enabled on this build.
- **`text_detection_model_name="PP-OCRv5_mobile_det"`** — the default server detection model causes a native access violation (process exit code `0xC0000005`) when mkldnn is disabled; the mobile detection model avoids this and is also faster on CPU.
- The **Microsoft Visual C++ Redistributable** (latest x64) must be installed for `paddlepaddle`'s and `torch`'s native extensions (`libpaddle.pyd`, `shm.dll`) to load correctly.

These are already applied in `ocr_service/engines/paddle_engine.py` — listed here for anyone debugging a fresh Windows setup.

---

## 9. Authentication & RBAC

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

## 10. API Reference

All requests through the API gateway require: `Authorization: Bearer <JWT_ACCESS_TOKEN>`

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
| POST | `/api/v1/retrieve` | `reader`+ (RBAC enforced) | Hybrid retrieval (query + domain_id). Requires Bearer JWT; verifies domain access. |
| GET | `/health` | Public | Health check |

### 10.4 generation-service (port 8004)

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
  "citations": [{"chunk_id": "...", "document_id": "...", "filename": "policy.pdf", "source_type": "pdf", "chunk_index": 2, "page": 3, "score": 0.87, "text": "..."}],
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

---

## 11. Prerequisites

### Required

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | [Download](https://www.python.org/downloads/). Check "Add Python to PATH". |
| **PostgreSQL** | 16 | [Download](https://www.postgresql.org/download/windows/). Keep default port 5432. |
| **Java** | 17+ | [Adoptium Temurin](https://adoptium.net/). Required for Keycloak. |
| **Groq API key** | Free tier | [Get one](https://console.groq.com). Primary LLM provider. |
| **Microsoft Visual C++ Redistributable** | Latest x64 | [Download](https://aka.ms/vs/17/release/vc_redist.x64.exe). Required for PaddleOCR/PyTorch native extensions on Windows. |
| **RAM** | 8 GB min, 16 GB recommended | Embedding, reranking, PaddleOCR, and Surya models load into memory |
| **Disk** | ~10 GB free | ML model caches (incl. OCR models) + infra downloads |
| **WSL2 + PostgreSQL 17 + Apache AGE** | Sprint 3 only | Required only for the Graph layer — see [Section 17](#17-sprint-3--hybrid-graph-rag-apache-age). Runs alongside the PG16 install above, on a separate port (5434), and does not affect Sprints 1–2. |

### Auto-Downloaded (by `run_services.py` / on first OCR run)

| Component | Port | Notes |
|---|---|---|
| **Redis** | 6379 | Portable Redis for Windows, downloaded to `tools/redis/` |
| **Keycloak** | 8180 | Downloaded to `tools/keycloak/` on first run (~150 MB) |
| **Qdrant** | — | Embedded at `data/qdrant` automatically (no server needed) |
| **PaddleOCR / Surya models** | — | Downloaded to `~/.paddlex/official_models/` on first OCR call per language (small "mobile" models, a few MB to tens of MB each) |

### Optional

| Requirement | When needed |
|---|---|
| **Node.js + npm** | React frontend (`rag-ui/`) |
| **Ollama** | Local/offline LLM fallback when Groq is unavailable |

---

## 12. Complete From-Scratch Setup & Run Guide

Follow this guide to set up the complete system (databases, identity provider, message queues, backend services, and frontend UI) from scratch on a new machine.

---

### 12.1 Python Environment Setup

The backend microservices and worker are written in Python.

1. **Install Python 3.11–3.13** from [python.org](https://www.python.org/downloads/). Ensure you check the box to **"Add Python to PATH"** during installation.
2. **Open PowerShell** and set up the virtual environment:

🟦 **Run in PowerShell:**
```powershell
# Create virtual environment (first time only)
python -m venv .venv

# Activate the virtual environment
.venv\Scripts\activate

pip install -U pip setuptools wheel

# Install all required Python packages (includes ML models & extractors)
pip install -r requirements.txt
```
> [!NOTE]
> Installing dependencies may take 10–20 minutes as it downloads PyTorch CPU, PaddleOCR/PaddlePaddle, Surya, and other ML inference libraries (approx. 2-3 GB total).

---

### 12.2 Environment Configuration

All services read from a single, shared configuration file at the project root.

1. **Create the environment file:**

🟦 **Run in PowerShell:**
```powershell
copy .env.example .env
```

2. **Edit the `.env` file** and configure the following required parameters:
   - `POSTGRES_PASSWORD`: Change this to the password you choose for PostgreSQL (see step 12.3).
   - `GROQ_API_KEY`: Provide a valid Groq API key from [console.groq.com](https://console.groq.com).
   - `OCR_LANG` / `OCR_LANGS` (optional): set the language(s) PaddleOCR should use for scanned pages/images — see [Section 8.3](#83-multi-language-support).

---

### 12.3 PostgreSQL Installation & Database Generation

PostgreSQL stores domains, user lists, configurations, upload logs, text chunks (with search vectors), and query audit trails.

#### 1. Install PostgreSQL 16
- Download the installer from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/).
- Run the installer. Remember the password you set for the `postgres` superuser.
- Keep the default port **5432**.
- Verify installation:

🟦 **Run in PowerShell:**
```powershell
psql -U postgres -V
```
If the command is not found, add the PostgreSQL bin folder (typically `C:\Program Files\PostgreSQL\16\bin`) to your user PATH environment variable.

#### 2. Create the Database

🟦 **Run in PowerShell:**
```powershell
# Temporarily set password in terminal to bypass prompt
$env:PGPASSWORD="your_postgres_password"

# Create the main application database
psql -U postgres -c "CREATE DATABASE domain_db;"
```

#### 3. Initialize Schema & Seed Data
Generate the entire database structure (including the `source_type` column added in Sprint 2) and populate all tables with initial test data in one command:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -f migrations/init_db.sql
```

#### 4. Verify Database Setup
Confirm that all tables were created and populated correctly:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -c "SELECT 'users=' || count(*) FROM users UNION ALL SELECT 'domains=' || count(*) FROM domains UNION ALL SELECT 'configs=' || count(*) FROM domain_configs UNION ALL SELECT 'roles=' || count(*) FROM domain_roles UNION ALL SELECT 'documents=' || count(*) FROM documents UNION ALL SELECT 'chunks=' || count(*) FROM document_chunks UNION ALL SELECT 'logs=' || count(*) FROM rag_query_logs;"
```

**Expected Output:**
```text
 users=6
 domains=3
 configs=3
 roles=3
 documents=3
 chunks=3
 logs=1
```

#### 5. Database Migrations (For Upgrades)
If you already have a database from Sprint 1 and wish to upgrade it without wiping and re-initializing, run the Sprint 2 migration script:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -f migrations/sprint2_migration.sql
```

---

### 12.4 Java & Keycloak Identity Setup

Keycloak serves as the central identity provider to enforce Role-Based Access Control (RBAC).

#### 1. Prerequisite: Java Installation
Keycloak requires Java 17 or higher.
- Download and install JDK 17+ from [Adoptium](https://adoptium.net/) (Temurin).
- Verify installation:

🟦 **Run in PowerShell:**
```powershell
java -version
```

#### 2. Starting Keycloak

- **Option A (Automatic):** When you run the launcher script (`python run_services.py`), it automatically downloads Keycloak 26.5.0 to the `tools/keycloak/` folder and imports the realm settings.
- **Option B (Manual):** Download Keycloak 26.5.0 from github and extract it to `tools/keycloak/`. Copy the realm configuration file and launch:

🟦 **Run in PowerShell:**
```powershell
mkdir "tools\keycloak\data\import" -Force
copy "services\auth\realm-export.json" "tools\keycloak\data\import\realm-export.json"

$env:KC_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD="admin"
.\tools\keycloak\bin\kc.bat start-dev --http-port=8180 --import-realm
```

#### 3. Seeding Realm Accounts
Initial users are automatically imported from the exported realm file:

| Username | Password | Realm Role | Description |
|---|---|---|---|
| `admin` | `admin` | `system_admin` | Full system administrator |
| `reader1` | `reader1` | `reader` | Regular viewer account |

To add additional test accounts (e.g., manager, contributor), use the Keycloak Admin CLI:

🟦 **Run in PowerShell:**
```powershell
# Login to the CLI
.\tools\keycloak\bin\kcadm.bat config credentials --server http://localhost:8180 --realm master --user admin --password admin

# Create users in 'rag-system' realm
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=manager -s enabled=true
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=user1 -s enabled=true
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username manager --new-password manager
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username user1 --new-password user1

# Assign roles
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername admin --rolename system_admin
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername manager --rolename reader
```

---

### 12.5 Redis Setup

Redis works as the asynchronous task message broker (Celery) and caching layer.

- **Option A (Automatic):** `run_services.py` automatically downloads a portable version of Redis for Windows to `tools/redis/` on first startup and runs it on port 6379.
- **Option B (Manual Installation):** If you prefer to install it globally:
  - Install via Windows Package Manager: `winget install Redis.Redis`
  - Start the service using `redis-server`.

---

### 12.6 Qdrant Vector Database

No manual setup is required. Qdrant runs in embedded mode inside the Python processes. Vector embeddings and collections are stored as binary files in the `data/qdrant/` folder.

---

### 12.7 React Frontend Setup

The frontend provides an interactive chat interface to ask questions, view citations, manage domains, and upload documents.

🟦 **Run in PowerShell:**
```powershell
cd rag-ui
npm install
npm run dev
```
Navigate to **http://localhost:5173** to access the UI. You can sign in using `admin`, `manager`, or `user` user IDs.

---

### 12.8 Launching the Backend Microservices

Launch all services plus the background Celery ingestion worker simultaneously:

🟦 **Run in PowerShell:**
```powershell
.venv\Scripts\activate
python run_services.py --worker
```

> The `--worker` flag is required for OCR — PaddleOCR and Surya are loaded inside the worker process. The first OCR call per language downloads its model (cached afterward under `~/.paddlex/official_models/`).

---

### 12.9 End-to-End System Verification

Once everything is running, verify system functionality:

#### 1. Ingestion File validation (DOCX/CSV/PDF/Image)
Obtain an access token and test document upload:

🟦 **Run in PowerShell:**
```powershell
# Get a JWT dev token
$token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"admin"}').token

# Upload a valid DOCX file (Should return 202 Accepted)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@test.docx" -F "domain_id=11111111-1111-1111-1111-111111111111"

# Upload a scanned PDF or image to exercise the OCR pipeline (Should return 202 Accepted)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@scanned_document.pdf" -F "domain_id=11111111-1111-1111-1111-111111111111"

# Upload an invalid file type (Should return 400 Bad Request)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@test.exe" -F "domain_id=11111111-1111-1111-1111-111111111111"
```

After uploading the scanned PDF, poll `GET /ingest/{document_id}` until `status` is `done`. The worker log will show which OCR engine was used per page (`model_used=paddle` or `model_used=surya`) and its `confidence_score`.

#### 2. Querying & Citations
Verify that questions return answers with full metadata:

🟦 **Run in PowerShell:**
```powershell
curl.exe -X POST http://localhost:8004/generate/query -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d '{"query": "How many leave days do we get?", "domain_id": "11111111-1111-1111-1111-111111111111"}'
```
**Expected Response:** A JSON payload containing the answer text and a `citations` array, with `filename`, `source_type`, and `chunk_index` details.

#### 3. Verification of RBAC on Retrieval
Ensure that users without access roles on a domain are blocked:

🟦 **Run in PowerShell:**
```powershell
# Authenticate as viewer (only has access to domain 22222222...)
$viewer_token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"viewer"}').token

# Access domain 11111111... (should fail with 403 Forbidden)
curl.exe -X POST http://localhost:8003/api/v1/retrieve -H "Authorization: Bearer $viewer_token" -H "Content-Type: application/json" -d '{"query": "vacation", "domain_id": "11111111-1111-1111-1111-111111111111"}'
```

---

## 13. Environment Variables

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
| `OCR_LANG` | Single-language PaddleOCR model | `en` |
| `OCR_LANGS` | Comma-separated languages (e.g. `ar,en`) — overrides `OCR_LANG`, picks best-confidence result per page | unset |
| `OCR_CONFIDENCE_THRESHOLD` | PaddleOCR score above which Surya fallback is skipped | `0.85` |

> See `.env.example` for inline comments explaining where to get each value.

---

## 14. Troubleshooting

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
- For scanned PDFs/images, the first page can take longer the first time per language as PaddleOCR/Surya models download and load — check the worker log for `Loading PaddleOCR...` / `Loading Surya OCR models...`

### OCR — `DLL load failed while importing libpaddle` (Windows)

- Install the latest **Microsoft Visual C++ Redistributable (x64)**: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Restart your terminal (or reboot) after installing, then retry `python run_services.py --worker`

### OCR — `NotImplementedError: ConvertPirAttribute2RuntimeAttribute ... pir::ArrayAttribute<DoubleAttribute>`

Known `paddlepaddle==3.3.x` CPU issue with PP-OCRv5 models under oneDNN. Already worked around in `paddle_engine.py` via `enable_mkldnn=False`. If you see this, confirm you're on the version of `paddle_engine.py` that sets `enable_mkldnn=False`.

### OCR — worker exits with code `3221225477` (0xC0000005, access violation)

Caused by the default `PP-OCRv5_server_det` detection model when `enable_mkldnn=False`. Already worked around in `paddle_engine.py` via `text_detection_model_name="PP-OCRv5_mobile_det"`.

### OCR — wrong language / poor accuracy on non-English documents

Set `OCR_LANG=<code>` (e.g. `ar` for Arabic) or `OCR_LANGS=ar,en` for mixed-language documents — see [Section 8.3](#83-multi-language-support).

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

## 15. Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example                      # environment template (copy to .env)
├── .gitignore                        # comprehensive ignore rules
├── requirements.txt                  # unified Python dependencies
├── run_services.py                   # main launcher (starts everything)
├── delete_chunks.py                  # database + vector store reset tool
├── README.md                         # this file — complete project guide
├── migrations/                       # database SQL scripts
│   ├── init_db.sql                   # creates schema & populates seed data from scratch
│   ├── sprint2_migration.sql         # Sprint 2 migration (source_type column)
│   └── sprint3_foundation.sql        # Sprint 3 — Apache AGE setup + ontology (run on PG17, not PG16)
├── data/                             # auto-created runtime data (gitignored)
│   ├── qdrant/                       # embedded vector DB
│   ├── uploads/                      # uploaded documents (PDF, DOCX, CSV, images)
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
    ├── retrieval-service/            # port 8003 (+ RBAC filtering)
    ├── generation-service/           # port 8004
    ├── evaluation-service/           # port 8005 (optional)
    └── worker-service/               # Celery worker (multi-format + OCR + Sprint 3 graph extraction)
        ├── ontology.py                # Sprint 3 — entity/relation type definitions (single source of truth)
        ├── ner.py                     # Sprint 3 — GLiNER entity extraction (lazy-loaded)
        ├── relation_extraction.py     # Sprint 3 — LLM-based relation extraction (grouped, 20 chunks/call)
        └── tasks/
            ├── extract.py            # format routing, calls OCR pipeline for scans/images
            ├── process.py            # 6-step pipeline: extract → chunk → embed → index → NER → relations
            └── ocr-service/
                └── ocr_service/
                    ├── pipeline.py            # top-level OCR entry point
                    ├── preprocessing/
                    │   └── image_processor.py # denoise, deskew, resize
                    ├── engines/
                    │   ├── paddle_engine.py   # PaddleOCR (fast path, multi-language)
                    │   └── surya_engine.py    # Surya (fallback)
                    ├── scoring/
                    │   └── ocr_scorer.py      # confidence scoring for both engines
                    └── routing/
                        └── ocr_router.py      # PaddleOCR -> Surya routing decision
```

---

## 16. Quick Reference Card

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
  3. POST /ingest                           → upload PDF / image
  4. GET  /ingest/{document_id}             → wait for "done" (OCR runs here for scans/images)
  5. POST /generate/query                   → get AI answer with citations

OCR config:
  OCR_LANG=en           → single language (default)
  OCR_LANGS=ar,en       → multi-language, picks best-confidence result per page
```

---

## 17. Sprint 3 — Hybrid Graph RAG (Apache AGE)

### 17.1 What This Sprint Adds

Sprints 1–2 retrieve answers two ways: **dense vector search** (meaning) and **sparse BM25 search** (keywords). Both search *text similarity*. Neither can answer relationship questions like *"Who manages Project Alpha?"* — that requires a **knowledge graph**: entities (Person, Project, Department...) connected by typed relationships (MANAGES, BELONGS_TO...).

Sprint 3 adds that graph layer on top of the existing system **without modifying any Sprint 1–2 code path**. If the graph layer fails for any reason, document ingestion and querying continue to work exactly as before — this is a deliberate design constraint, not an accident (see [17.6](#176-failure-isolation--why-the-graph-layer-cant-break-the-core-system)).

### 17.2 Why Apache AGE Instead of Neo4j

The original Sprint 3 requirements named Neo4j. This implementation uses **Apache AGE** instead — a PostgreSQL extension that adds graph capabilities to a regular Postgres database, rather than running a separate graph database server.

| | Neo4j | Apache AGE (chosen) |
|---|---|---|
| Infrastructure | Separate DB server, separate container | Extension inside PostgreSQL — same engine already used by the rest of this project |
| Domain isolation (RBAC) | Needs to be re-implemented for the graph DB | Reuses the same `domain_id` pattern already used in every other table |
| Chunk↔Graph linking | Cross-database join required | Same database — can eventually be joined with `document_chunks` directly |
| New failure point | Yes — an entire new service to keep running | No — same Postgres instance, same uptime story |

### 17.3 Why a Second PostgreSQL Instance (via WSL2)

Apache AGE has no prebuilt Windows binary for PostgreSQL 16 (the version this project already uses on port 5432). Two real options were evaluated:

1. **Upgrade the existing PG16 to PG17 in place** — risks the working Sprint 1–2 database.
2. **Run a second, separate PostgreSQL 17 + AGE instance inside WSL2** — zero risk to the existing PG16/port 5432 setup. **(Chosen.)**

The original PostgreSQL 16 instance on port 5432 is **untouched** and keeps serving `domain-service`, `ingestion-service`, `retrieval-service`, and `generation-service` exactly as documented in [Section 12](#12-complete-from-scratch-setup--run-guide). The new PostgreSQL 17 + AGE instance runs inside WSL2 on **port 5434**, reachable from Windows like any other database — Windows-side Python services don't need to know WSL2 is involved.

### 17.4 Setting Up PostgreSQL 17 + Apache AGE (WSL2)

> [!NOTE]
> This section is only required to use the Graph layer. Skipping it does not affect Sprints 1–2.

#### 1. Open WSL2 (Ubuntu)

If WSL2 with an Ubuntu distribution isn't installed yet, install it from an elevated PowerShell:

🟦 **Run in PowerShell (as Administrator):**
```powershell
wsl --install -d Ubuntu-22.04
```

#### 2. Run the setup script

The setup script (`wsl2_setup_v2.sh`, included in this repo) installs PostgreSQL 17, builds Apache AGE from source against it, configures it to accept connections from Windows, and creates the `rag_graph` graph.

🟩 **Run inside WSL2 (Ubuntu terminal):**

**Open ubuntu**
```bash
wsl -d Ubuntu-22.04
```
**Run script**
```bash
chmod +x ~/wsl2_setup.sh
~/wsl2_setup.sh
```

> [!NOTE]
> This script builds Apache AGE from source (`git checkout PG17/v1.6.0-rc0`, the official PG17-compatible release), which takes a few minutes. It also sets the `postgres` user's password — open the script before running it if you want a password other than the placeholder it ships with.

What the script does, step by step:
- Installs PostgreSQL 17 from the official PGDG APT repository (Ubuntu 22.04 only ships PG14 by default)
- Installs build dependencies and compiles Apache AGE against PG17
- Sets `listen_addresses = '*'` and adds a `pg_hba.conf` rule for `0.0.0.0/0` — required so Windows-side services (outside WSL2) can connect; still password-protected (`md5`)
- Configures PostgreSQL to run on **port 5434** (avoids any collision with the existing PG16 on 5432)
- Creates the `rag_graph` graph via `ag_catalog.create_graph()`

#### 3. Start Ubuntu WSL2 and PostgreSQL

Apache AGE runs inside Ubuntu WSL2. Before running any PostgreSQL commands from Windows, make sure the Ubuntu instance is running.

🟦 **Run in PowerShell (Windows):**

```powershell
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
```

Wait a few seconds, then verify:

```powershell
wsl -l -v
```

Expected:

```text
Ubuntu-22.04      Running
```

PostgreSQL is configured to start automatically inside Ubuntu via systemd.

#### 4. Verify Windows can reach PostgreSQL

🟦 **Run in PowerShell (Windows):**

```powershell
psql -h localhost -p 5434 -U postgres -c "SELECT version();"
```

Expected output includes:

```text
PostgreSQL 17.x
```

If the connection fails, verify that Ubuntu is running:

```powershell
wsl -l -v
```

If Ubuntu is stopped, start it again using Step 3.

#### 5. Create the application database

🟦 **Run in PowerShell (Windows):**

```powershell
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
```

If the database already exists, PostgreSQL will return:

```text
ERROR: database "domain_db" already exists
```

which is safe to ignore.

#### 6. Run the ontology migration

This creates the AGE extension inside `domain_db`, the entity/relation vertex and edge labels, and supporting indexes.

🟦 **Run in PowerShell (Windows):**

```powershell
psql -h localhost -p 5434 -U postgres -d domain_db -f migrations/sprint3_foundation.sql
```

**Expected output (abridged):**
```text
CREATE EXTENSION
LOAD
SET
DO
 create_vlabel   (×7 — Person, Project, Department, Policy, Role, Location, Skill)
 create_elabel   (×8 — MANAGES, BELONGS_TO, REPORTS_TO, OWNS, HAS_ROLE, WORKS_ON, HAS_SKILL, BASED_AT)
CREATE INDEX     (×7)
```

#### 6. Add Graph environment variables

Append to `services/worker-service/.env` (create it from `.env.example` first if it doesn't exist yet — see [Section 12.2](#122-environment-configuration)):

```text
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_API_KEY=                              # same key used in Section 13
GROQ_MODEL=llama-3.3-70b-versatile

AGE_DATABASE_DSN=postgresql://postgres:YOUR_PASSWORD@localhost:5434/domain_db
AGE_GRAPH_NAME=rag_graph
```

### 17.5 The Ontology

Entity and relation types are defined **once**, in `services/worker-service/ontology.py`, and every Sprint 3 component (NER, relation extraction, the eventual graph writer) imports from that single file rather than hardcoding label strings.

| Entity Types (7) | Relation Types (8) |
|---|---|
| Person, Project, Department, Policy, Role, Location, Skill | MANAGES, BELONGS_TO, REPORTS_TO, OWNS, HAS_ROLE, WORKS_ON, HAS_SKILL, BASED_AT |

Adding a new type requires editing **both** `ontology.py` (Python) and re-running an updated `sprint3_foundation.sql` (creates the matching `vlabel`/`elabel` in AGE) — the two are not auto-synced.

### 17.6 Failure Isolation — Why the Graph Layer Can't Break the Core System

`tasks/process.py`'s pipeline grew from 4 steps (Sprint 2) to 6 steps:

```text
[1/6] Extract text          ─┐
[2/6] Chunk (semantic)        │  Sprints 1–2 — unchanged,
[3/6] Embed                   │  core RAG functionality
[4/6] Index (Qdrant + PG)    ─┘
[5/6] Extract entities (NER)  ─┐  Sprint 3 — additive.
[6/6] Extract relations (LLM) ─┘  Wrapped in try/except; a failure here
                                   logs a warning and the document still
                                   completes with status="done".
```

Steps 5 and 6 run **after** indexing, deliberately — by the time the graph layer runs, the document is already fully searchable via Vector/BM25. If GLiNER or the relation-extraction LLM call fails (model download hiccup, missing `GROQ_API_KEY`, network issue), the document is **not** marked `failed`; it simply has 0 entities/relations and remains queryable through the existing Sprint 1–2 pipeline.

### 17.7 Entity & Relation Extraction Pipeline

#### Step 5 — NER (`ner.py`)

Uses **GLiNER multilingual** (`urchade/gliner_multi-v2.1`), loaded lazily on first use (same pattern as `embed.py`'s embedding model, to avoid loading multiple ML models' worth of PyTorch/safetensors at Celery startup — see `embed.py`'s docstring for the Windows paging-file issue this avoids).

Labels are sent to GLiNER as natural-language descriptions (e.g. `"a person name"` rather than the bare word `"Person"`) — a spike test on this project's actual Arabic documents (`Neuropsychology.pdf`) showed this phrasing produces measurably higher-confidence, more accurate matches than bare ontology words, since GLiNER is zero-shot and infers each label's meaning from the string itself.

| Spike test result (real document sentences) | Entity | Confidence |
|---|---|---|
| "يُعد جون جاكسون أول من وضع الأساس..." | جون جاكسون → Person | 0.92 |
| "أحمد محمد يدير مشروع تطوير النظام..." | أحمد محمد → Person | 0.97 |
| (same sentence) | مشروع تطوير النظام → Project | 0.52 |
| (same sentence) | قسم تقنية المعلومات → Department | 0.89 |

`CONFIDENCE_THRESHOLD = 0.6` (in `ner.py`) filters out low-confidence noise (e.g. a borderline `"خبرة"` / "experience" match scored 0.62 in testing — too generic a noun to be a reliable `Skill` entity on its own).

#### Step 6 — Relation Extraction (`relation_extraction.py`)

Takes the entities Step 5 found and determines the **relationships** between them — e.g. turning `[Person: أحمد محمد]` + `[Project: مشروع تطوير النظام]` into the triple `(أحمد محمد, MANAGES, مشروع تطوير النظام)`.

**Why an LLM (Groq) instead of spaCy dependency parsing:** spaCy has no official Arabic dependency parser — `ar_core_news_sm` does not exist in spaCy's model catalog. Community attempts require manually symlinking files into spaCy's own package directory and are not production-viable. Published academic Arabic dependency-parsing accuracy tops out around 76% (vs ~88% for English) even with dedicated research tooling spaCy doesn't have. An LLM that reads the sentence directly was the more reliable option investigated for this sprint.

**Why grouped, not per-chunk or whole-document:** calling Groq once per chunk would mean ~200 calls for a 200-chunk document (slow, costly); one call for an entire document risks exceeding context limits. `CHUNKS_PER_GROUP = 20` batches chunks in original document order — roughly 10 LLM calls for a 200-chunk document. This is a deliberate trade-off: a relation whose subject and object fall in different groups (e.g. chunk 18 and chunk 22) will be missed. No fallback LLM (e.g. local Ollama) is configured for this step yet — if `GROQ_API_KEY` is unset or the call fails, that group's relations are simply skipped (see [17.6](#176-failure-isolation--why-the-graph-layer-cant-break-the-core-system)).

Every triple the LLM returns is validated against `ontology.RELATION_TYPES` before being kept — a hallucinated relation type outside the 8 defined ones is dropped with a logged warning, not passed downstream.

### 17.8 Query-Time Graph Retrieval & Routing

At query time, the system uses a hybrid retrieval strategy that combines vector search, BM25 keyword search, and Apache AGE graph traversal.

1. **Retrieval Router Decisions**: The `retrieval-service` uses `RetrievalRouter` to decide which engines to run. If `AGE_DATABASE_DSN` is configured, it scans the incoming query for relational keywords (e.g., "manages", "works in", "who is", "من يدير", "يعمل في", "مدير", "قسم" etc.) and checks for entities. If found, it automatically enables **Graph Retrieval** (`use_graph = True`).
2. **Query-Time NER & Substring Matching**: The query is normalized (Arabic Harakat stripped, spelling variants unified) and checked against all node names in the current domain graph in Apache AGE to find matches.
3. **Graph Traversal (1-Hop Match)**: For every matched entity, the `GraphRetriever` runs a Cypher traversal to fetch the entity, its relationships, and its immediately adjacent neighbors:
   ```cypher
   MATCH (v:{label})
   WHERE v.normalized_name = $normalized_name AND v.domain_id = $domain_id
   OPTIONAL MATCH (v)-[r]-(neighbor)
   RETURN properties(v), properties(r), properties(neighbor)
   ```
4. **Source Chunk Resolution**: The retriever gathers the unique `chunk_ids` associated with the matched entities, relationships, and neighbors, then retrieves the raw text chunks and metadata from PostgreSQL `document_chunks`.
5. **Score Fusion**: The graph-derived chunks are assigned a high priority score (`0.95`) and fused with Vector and BM25 results, ensuring highly relational context is prioritized in the prompt.

### 17.9 Performance Measurement & Evaluation

You can evaluate the accuracy, quality, and performance of the RAG system using the built-in services and logs.

#### 1. LLM-as-a-Judge Evaluation
- Start the optional evaluation service:
  ```powershell
  python run_services.py --evaluation
  ```
- Send a request to `POST http://localhost:8005/evaluate` containing the query, the generated answer, and the list of retrieved context chunks.
- The service uses the judge model to perform semantic grading, returning a score from `0.0` (poorest) to `1.0` (perfect grounding) along with a detailed explanation of the faithfulness and completeness of the answer.

#### 2. Query Performance & Audit Logging
- Every RAG query and generated answer is logged to the `rag_query_logs` table in PostgreSQL.
- To measure latency, cache hit ratios, and model efficiency, query the logs:
  ```sql
  -- Calculate total requests, cache hit rate, and breakdown by model:
  SELECT 
      model, 
      COUNT(*) as total_queries, 
      SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::float / COUNT(*) as cache_hit_rate
  FROM rag_query_logs 
  GROUP BY model;
  ```

### 17.10 Sprint 3 Troubleshooting

#### Windows can't reach `localhost:5434`


- Confirm the WSL2 PostgreSQL service is running: inside WSL2, `sudo service postgresql status`
- Confirm `listen_addresses = '*'` is set: `sudo cat /etc/postgresql/17/main/postgresql.conf | grep listen_addresses`
- Confirm the `pg_hba.conf` rule exists: `sudo cat /etc/postgresql/17/main/pg_hba.conf | grep 0.0.0.0`
- Restart PostgreSQL inside WSL2: `sudo service postgresql restart`

#### `extension "age" is not available`

The AGE build didn't install correctly. Re-run the build step from `wsl2_setup_v2.sh` (Steps 4–5) inside WSL2 and check for compile errors in the output.

#### NER step is very slow on first document

Expected — GLiNER downloads (~500 MB–1 GB) and loads on the **first** document processed after a worker restart, same lazy-loading behavior as the embedding and reranker models. Subsequent documents are fast.

#### Step 6 logs `GROQ_API_KEY not set — skipping relation extraction`

Add `GROQ_API_KEY` to `services/worker-service/.env` (see [17.4, step 6](#174-setting-up-postgresql-17--apache-age-wsl2)). Until then, Step 5 (NER) still runs normally — only relation extraction is skipped.

#### `UnicodeDecodeError: 'charmap' codec can't decode byte ...` in worker logs

Same root cause as the existing [Unicode errors in worker output](#unicode-errors-in-worker-output-on-windows) entry in Section 14 — Windows' terminal defaults to `cp1252`, which can't render Arabic log output. Run before starting:

🟦 **Run in PowerShell:**
```powershell
$env:PYTHONUTF8=1
chcp 65001
```

---



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
8. [OCR Pipeline](#8-ocr-pipeline)
9. [Authentication & RBAC](#9-authentication--rbac)
10. [API Reference](#10-api-reference)
11. [Prerequisites](#11-prerequisites)
12. [Complete From-Scratch Setup & Run Guide](#12-complete-from-scratch-setup--run-guide)
13. [Environment Variables](#13-environment-variables)
14. [Troubleshooting](#14-troubleshooting)
15. [Directory Layout](#15-directory-layout)
16. [Quick Reference Card](#16-quick-reference-card)

---

## 1. Project Overview

### What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain Retrieval-Augmented Generation (RAG) system**. It is a backend + frontend application that allows organizations to:

- Create separate **knowledge domains** (isolated knowledge bases, e.g., "HR Policies", "Tech Support", "Legal Contracts")
- Upload **documents** (PDF, DOCX, CSV, and images) into those domains
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
A user uploads a document (PDF, DOCX, CSV, or image) to a domain. Here's what happens internally:

1. **`ingestion-service`** receives the file, validates its type (PDF, DOCX, CSV, PNG, JPG, JPEG), and saves it to disk
2. It creates a `documents` record in PostgreSQL (status = `pending`)
3. It enqueues an async job into **Redis** (Celery task queue)
4. **`worker-service`** picks up the job and:
   - **Extracts text** using a format-specific extractor: PyMuPDF for PDFs with native text layers, python-docx for DOCX files, pandas for CSVs. For **scanned PDF pages and standalone images**, text is extracted via the embedded **OCR pipeline** — a PaddleOCR fast path with automatic Surya fallback for low-confidence results (see [Section 8: OCR Pipeline](#8-ocr-pipeline))
   - **Splits the text into chunks** using semantic chunking with embedding similarity (topic-boundary detection)
   - **Generates embedding vectors** for each chunk using the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model.
   - **Stores vectors in Qdrant** — one collection per domain, each point contains the chunk text, document ID, page number, chunk index, filename, and source type
   - **Stores chunks in PostgreSQL** — with a `TSVECTOR` column for BM25 full-text search and a `source_type` column for format tracking
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
9. The response includes: the answer text, citations (which chunk, which document filename, which page, source type, relevance score), the model used, and whether it was a cache hit

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-domain isolation | Each domain has its own documents, members, configuration, and vector collection. Complete data separation. |
| Role-based access (RBAC) | Three-layer security: Keycloak JWT tokens at the gateway + per-domain role checks in each service including retrieval |
| Hybrid retrieval | Combines dense vector search (semantic meaning) + sparse BM25 search (exact keywords) + cross-encoder reranking for highest accuracy |
| AI answer generation | Groq (cloud, fast, free tier) or Ollama (local, offline). Per-domain LLM routing — some domains can use cloud, others local. |
| Multi-format ingestion | Supports PDF, DOCX, CSV, and image (PNG/JPG) uploads. Format-specific extractors with a PaddleOCR + Surya OCR pipeline for scanned content. |
| Async document processing | Documents are processed in background via Celery + Redis. The user gets immediate `202 Accepted` and can poll status. |
| Intelligent caching | Redis caches both retrieval results and generated answers. Identical repeat queries return instantly. |
| Citation grounding | Every AI answer includes citations with document filename, source type, page/row number, and relevance score |
| Multi-language OCR | Scanned pages and images are OCR'd via PaddleOCR/Surya with configurable language models (`OCR_LANG` / `OCR_LANGS`), not limited to a single language |
| Graceful degradation | If Redis is down → uses in-memory cache. If Groq is down → falls back to Ollama. If Keycloak is down → uses dev auth. |
| React chat UI | Full-featured web interface for login, domain management, document upload, and interactive Q&A |

### How It Works (Simple Analogy)

Think of the system as a **smart library with an AI librarian**:

1. **You create a shelf (domain)** — a labeled section of the library for one topic.
2. **You add books (PDFs, Word docs, spreadsheets, images)** — the system scans each item, splits it into paragraphs (chunks), and indexes them in two ways: by meaning (vectors) and by keywords (full-text search).
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
flowchart TD
    Client["Client (Browser / API)"]
    Gateway["Traefik Gateway"]
    KC["Keycloak Auth Provider"]
    GS["generation-service"]
    RD["Redis Cache"]
    DS["domain-service"]
    RS["retrieval-service"]
    QD["Qdrant (Vector DB)"]
    PG["PostgreSQL (BM25 DB)"]
    LLM["LLM (Groq / Ollama)"]

    Client -->|1. Submit RAG Query| Gateway
    Gateway -->|2. Validate Token| KC
    KC -->|3. Token Validated| Gateway
    Gateway -->|4. Forward Request| GS
    GS -->|5. Check Cache| RD
    
    RD -->|Cache Hit: Return Answer| Client
    
    RD -->|Cache Miss| GS
    GS -->|6. Fetch Config| DS
    GS -->|7. Request Retrieval| RS
    
    RS -->|8a. Dense Search| QD
    RS -->|8b. Sparse Search| PG
    QD & PG -->|9. Fusion & Rerank| RS
    RS -->|10. Return Chunks| GS
    
    GS -->|11. Build Prompt & Call| LLM
    LLM -->|12. Generate Answer| GS
    GS -->|13. Log Query| PG
    GS -->|14. Cache Response| RD
    GS -->|15. Return Final Answer| Client
    
    style KC fill:#f9a825,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style LLM fill:#7c3aed,color:#fff
```

*   **What it is**: The Query Flow represents the runtime execution path of a user's question, tracing the lifecycle from initial gateway entry, cache lookup, hybrid document retrieval, LLM prompt assembly, to audit logging and response generation.
*   **How it works**:
    1.  The **Client** submits an HTTP `POST` request containing the question and target domain ID to the API Gateway.
    2.  The **Traefik Gateway** validates the Bearer JWT token against **Keycloak** (or falls back to local RSA validation in dev mode) before forwarding the query.
    3.  Once verified, the request is routed to the **`generation-service`**.
    4.  The `generation-service` checks **Redis** for a cached query response. If found, it returns the answer instantly (Cache Hit).
    5.  On a cache miss, the service fetches the target domain's LLM configuration and confidence thresholds from the **`domain-service`**.
    6.  It requests document context from the **`retrieval-service`**, which performs a parallel hybrid search: dense vector search in **Qdrant** and sparse keyword search in **PostgreSQL**.
    7.  The retrieved chunks are fused via Reciprocal Rank Fusion (RRF) and re-scored with a Cross-Encoder reranking model.
    8.  The `generation-service` constructs a RAG prompt grounding the question in the retrieved passages and sends it to the configured **LLM** (Groq cloud or local Ollama).
    9.  The LLM-generated answer is cached in Redis, logged in PostgreSQL for audit trailing, and returned to the client with full citation metadata.

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

    Client -->|1. POST /ingest| IS
    IS -->|2. Validate Type| IS
    IS -->|3. Check Access| DS
    IS -->|4. Create Pending Doc| PG
    IS -->|5. Enqueue Job| RD
    IS -->|6. Return 202 Accepted| Client

    RD -->|7. Dequeue Job| WS
    WS -->|8. Extract Text| WS
    WS -->|9. Chunk & Embed| WS
    WS -->|10. Index Vectors| QD
    WS -->|11. Store Chunks & FTS| PG
    WS -->|12. Set Status = done| PG

    style DS fill:#60a5fa,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style WS fill:#a78bfa,color:#000
```

*   **What it is**: The Ingestion Pipeline is an asynchronous document processing workflow that validates uploads, performs format-specific text extraction, semantically segments paragraphs, generates high-dimensional vector embeddings, and builds dual database search indexes.
*   **How it works**:
    1.  A contributor uploads a document (PDF, DOCX, CSV, PNG, JPG, or JPEG) to the **`ingestion-service`**.
    2.  The service performs type validation and checks user permissions by querying the **`domain-service`**.
    3.  It registers the document in **PostgreSQL** with a `pending` status and publishes a processing task to **Redis**.
    4.  The client receives an immediate `202 Accepted` response with the `document_id` to poll for completion.
    5.  The background **`worker-service`** (Celery worker running on a solo pool) picks up the task from Redis.
    6.  It extracts raw text using format-specific extractors: PyMuPDF for PDF pages with a native text layer, python-docx for DOCX, pandas for CSV. Scanned PDF pages and standalone images go through the **OCR pipeline** (PaddleOCR with Surya fallback — see [Section 8](#8-ocr-pipeline)).
    7.  The text is split into semantic paragraphs, and each chunk is embedded using the E5 transformer model.
    8.  The worker writes vectors and provenance metadata into **Qdrant** and stores full-text search indexes in **PostgreSQL**.
    9.  The document status is updated to `done` (or `failed` with populated error logs if processing fails).

---

### 2.4 Authentication Flow

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    Gateway["Traefik Gateway"]
    KC["Keycloak Auth Provider"]
    DS["domain-service (Dev Auth)"]
    Service["Microservice (retrieve/ingest/etc)"]

    subgraph KeycloakMode ["Option A: Keycloak Mode"]
        Client -->|1. Authenticate| KC
        KC -->|2. Return JWT access_token| Client
        Client -->|3. Call Request + Bearer JWT| Gateway
        Gateway -->|4. forwardAuth| KC
        KC -->|5. Verify User| Gateway
        Gateway -->|6. Forward Request| Service
    end

    subgraph DevAuthMode ["Option B: Dev Auth Mode (Local Fallback)"]
        Client -->|"1. POST /domains/auth/login {user_id}"| DS
        DS -->|2. Mint & Return Dev JWT| Client
        Client -->|3. Call Request + Bearer Dev JWT| Service
    end

    Service -->|Verify signature & extract claims| Service
    Service -->|Check Domain Access| DS

    style KC fill:#f9a825,color:#000
    style DS fill:#60a5fa,color:#000
    style Gateway fill:#7c3aed,color:#fff
    style Service fill:#a78bfa,color:#000
```

*   **What it is**: The Authentication Flow provides a two-tier JWT-based authorization and domain-level Role-Based Access Control (RBAC). It supports both a production-ready Keycloak integration and a lightweight local development authentication fallback.
*   **How it works**:
    *   **Option A (Keycloak)**: In production, the client exchanges credentials with **Keycloak** for an access token. Every gateway call is validated on the fly by Traefik gateway using Keycloak's user info before passing the request to microservices.
    *   **Option B (Dev Auth)**: In local dev mode, the client logs in directly via the **`domain-service`** by passing a user ID. The service verifies the user in PostgreSQL and mints a local JWT token signed with an RSA key.
    *   **Access Enforcement**: The receiving service decodes the JWT signature and queries the `/internal/check-access` endpoint of the `domain-service` using a shared key to verify the caller has the required domain membership (e.g. `reader` for retrieval, `contributor` for ingestion).

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

### Decision 10: OCR Is a PaddleOCR + Surya Ensemble, Not Tesseract

Scanned pages and image uploads are handled by an embedded OCR pipeline (`ocr_service/`, used by `worker-service/tasks/extract.py`) instead of Tesseract. PaddleOCR runs first as a fast path; if its confidence score falls below a threshold, Surya (a transformer-based, layout-aware OCR model) runs as a fallback and the higher-scoring result is kept. This keeps average latency low while improving accuracy on low-quality or mixed-language scans. See [Section 8: OCR Pipeline](#8-ocr-pipeline) for full details.

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
| PDF extraction | PyMuPDF | — | Native text extraction for digital PDF pages |
| OCR (fast path) | PaddleOCR | — | Primary OCR engine for scanned pages and images, multi-language via `OCR_LANG`/`OCR_LANGS` |
| OCR (fallback) | Surya | — | Layout-aware transformer OCR, used when PaddleOCR confidence is below threshold |
| DOCX extraction | python-docx | 1.1.2 | Word document text extraction |
| CSV extraction | pandas | 2.2.3 | Tabular data text extraction |
| ML runtime | PyTorch CPU | 2.6.0+ | Embedding model and Surya inference |

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
| worker-service | — | Celery worker | Extract (incl. OCR) → chunk → embed → index |
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

**What it does:** Runs in the background as a Celery worker. Picks up ingestion jobs from Redis and does the heavy lifting: text extraction (including OCR), chunking, embedding, and indexing.

**How it works internally (step by step):**
1. **Text extraction:** Uses PyMuPDF (`fitz`) to extract text from PDF pages with a native text layer. For scanned/image-only PDF pages and for standalone image uploads (PNG/JPG/JPEG), text is extracted via the embedded **OCR pipeline** — PaddleOCR first, with an automatic Surya fallback for low-confidence pages. See [Section 8: OCR Pipeline](#8-ocr-pipeline).
2. **Semantic chunking:** Splits extracted text into chunks of ~512 characters (configurable per domain) with 64-character overlap. The overlap ensures sentences aren't cut in half at chunk boundaries.
3. **Embedding generation:** Runs each chunk through the `intfloat/multilingual-e5-small` model (384-dimensional vectors). Each chunk is prefixed with `passage:` as required by the E5 model architecture.
4. **Vector indexing (Qdrant):** Stores embeddings in Qdrant with payloads containing the chunk text, document ID, page number, and chunk index. Each domain gets its own Qdrant collection (named by domain ID).
5. **BM25 indexing (PostgreSQL):** Inserts chunks into the `document_chunks` table with a `search_vec` TSVECTOR column for full-text keyword search.
6. **Status update:** Sets the document status to `done` (or `failed` with an error message).

**Important:** On Windows, Celery runs with `--pool=solo` (no fork support). This means one job at a time, but it's reliable.

**Key files:** `tasks/extract.py` (text + OCR extraction routing), `tasks/index.py` (the main ingestion task), `celery_app.py` (Celery configuration), `tasks/ocr-service/ocr_service/` (OCR pipeline — see [Section 8](#8-ocr-pipeline)).

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
9. **Starts worker-service** — Celery worker (only if `--worker` flag is used). This is the process that loads PaddleOCR and Surya (see [Section 8](#8-ocr-pipeline)).
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
> If Redis is running + `--worker`: starts Celery worker for async ingestion, and OCR (PaddleOCR/Surya) is available.

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
        text search_vec
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
        timestamp created_at
    }

    domains ||--|| domain_configs : "has-config"
    domains ||--o{ domain_roles : "has-members"
    domains ||--o{ documents : "contains"
    documents ||--o{ document_chunks : "split-into"
```

### Table Details

| Table | Purpose | Key Columns |
|---|---|---|
| `users` | User profiles and global roles | `id` (login ID), `role` (system_admin, domain_admin, contributor, reader) |
| `domains` | Knowledge domain workspaces | `name` (unique), `status` (active/archived), `created_by` |
| `domain_configs` | Per-domain RAG settings | `llm_route` (api/local), `chunk_size`, `confidence_threshold` |
| `domain_roles` | Domain-level RBAC memberships | Unique constraint on `(domain_id, user_id)` |
| `documents` | Uploaded file metadata | `status` (pending → processing → done/failed) |
| `document_chunks` | Searchable text segments | `source_type` (pdf/docx/csv/png), `search_vec` (TSVECTOR for BM25), GIN index |
| `rag_query_logs` | Query audit trail | `query`, `answer`, `llm_route`, `model` |

> For the complete database schema and seed data, see the initialization script [migrations/init_db.sql](file:///d:/Personal/Fixed%20Solutions/Project%20Files/Main/migrations/init_db.sql) and the instructions in [Section 12](#12-complete-from-scratch-setup--run-guide).

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

## 8. OCR Pipeline

Scanned PDF pages and standalone image uploads (PNG/JPG/JPEG) don't have a native text layer, so they're routed through an embedded **OCR pipeline** before chunking. The pipeline lives under `services/worker-service/tasks/ocr-service/ocr_service/` and is consumed by `tasks/extract.py` (and its drop-in variant `tasks/extract_with_ocr.py`).

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
| 1. Format routing | `tasks/extract.py` | `.docx` → python-docx, `.csv` → pandas, `.pdf`/`.png`/`.jpg`/`.jpeg` → checked for native text first |
| 2. Native text check | PyMuPDF (`fitz`) | Each PDF page's text layer is checked. If text exists, it's used directly — no OCR, no model overhead |
| 3. Preprocessing | `ocr_service/preprocessing/image_processor.py` | For pages/images with no native text: denoise (`fastNlMeansDenoisingColored`), deskew (Hough line transform, ±15° correction), adaptive resize (upscale below 1000px, downscale above 4096px) |
| 4. Fast OCR path | `ocr_service/engines/paddle_engine.py` (PaddleOCR) | Runs first on every page. Loaded once per language as a singleton and reused across requests |
| 5. Confidence scoring | `ocr_service/scoring/ocr_scorer.py` | PaddleOCR score = `0.7 × avg_confidence + 0.3 × valid_word_ratio`. Surya score = weighted blend of text density, language consistency, and noise penalty |
| 6. Routing decision | `ocr_service/routing/ocr_router.py` | If PaddleOCR score ≥ `OCR_CONFIDENCE_THRESHOLD` (default `0.85`), return immediately. Otherwise run Surya and keep whichever result scores higher |
| 7. Fallback OCR | `ocr_service/engines/surya_engine.py` (Surya) | Layout-aware transformer OCR, used only when PaddleOCR is below threshold — keeps average latency low while catching low-quality scans |
| 8. Output | `ocr_service/pipeline.py` | Returns `{"page": N, "text": ..., "model_used": "paddle"|"surya", "confidence_score": ..., "processing_time_ms": ...}` per page, which feeds into chunking exactly like native-text pages |

### 8.3 Multi-Language Support

OCR language is configurable via environment variables (read by `paddle_engine.py`):

| Variable | Purpose | Default |
|---|---|---|
| `OCR_LANG` | Single-language mode — PaddleOCR loads one language model | `en` |
| `OCR_LANGS` | Comma-separated list (e.g. `ar,en`) — PaddleOCR runs once per language and keeps the result with the highest average confidence | unset (single-language mode) |

`OCR_LANGS` takes precedence over `OCR_LANG` when set. Each language's PaddleOCR pipeline is loaded once and cached as a singleton (under `~/.paddlex/official_models/` on first run), so subsequent pages don't re-download or re-initialize models. Using more languages in `OCR_LANGS` means one extra OCR pass per image per language — keep the list to 2-3 languages you actually expect in your documents.

### 8.4 Platform Notes (Windows CPU)

PaddleOCR on Windows CPU requires a couple of non-obvious settings to run reliably with `paddlepaddle==3.3.1` / `paddleocr>=3.7.0`:

- **`enable_mkldnn=False`** — avoids a oneDNN/PIR crash (`NotImplementedError: ConvertPirAttribute2RuntimeAttribute ... pir::ArrayAttribute<DoubleAttribute>`) that occurs on the first `predict()` call with mkldnn enabled on this build.
- **`text_detection_model_name="PP-OCRv5_mobile_det"`** — the default server detection model causes a native access violation (process exit code `0xC0000005`) when mkldnn is disabled; the mobile detection model avoids this and is also faster on CPU.
- The **Microsoft Visual C++ Redistributable** (latest x64) must be installed for `paddlepaddle`'s and `torch`'s native extensions (`libpaddle.pyd`, `shm.dll`) to load correctly.

These are already applied in `ocr_service/engines/paddle_engine.py` — listed here for anyone debugging a fresh Windows setup.

---

## 9. Authentication & RBAC

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

## 10. API Reference

All requests through the API gateway require: `Authorization: Bearer <JWT_ACCESS_TOKEN>`

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
| POST | `/api/v1/retrieve` | `reader`+ (RBAC enforced) | Hybrid retrieval (query + domain_id). Requires Bearer JWT; verifies domain access. |
| GET | `/health` | Public | Health check |

### 10.4 generation-service (port 8004)

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
  "citations": [{"chunk_id": "...", "document_id": "...", "filename": "policy.pdf", "source_type": "pdf", "chunk_index": 2, "page": 3, "score": 0.87, "text": "..."}],
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

---

## 11. Prerequisites

### Required

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | [Download](https://www.python.org/downloads/). Check "Add Python to PATH". |
| **PostgreSQL** | 16 | [Download](https://www.postgresql.org/download/windows/). Keep default port 5432. |
| **Java** | 17+ | [Adoptium Temurin](https://adoptium.net/). Required for Keycloak. |
| **Groq API key** | Free tier | [Get one](https://console.groq.com). Primary LLM provider. |
| **Microsoft Visual C++ Redistributable** | Latest x64 | [Download](https://aka.ms/vs/17/release/vc_redist.x64.exe). Required for PaddleOCR/PyTorch native extensions on Windows. |
| **Ghostscript** | 10.x | [Download](https://www.ghostscript.com/releases/gsdnld.html). Required by Camelot for table extraction. After installation, verify with `gswin64c --version`. |
| **RAM** | 8 GB min, 16 GB recommended | Embedding, reranking, PaddleOCR, and Surya models load into memory |
| **Disk** | ~10 GB free | ML model caches (incl. OCR models) + infra downloads |

### Auto-Downloaded (by `run_services.py` / on first OCR run)

| Component | Port | Notes |
|---|---|---|
| **Redis** | 6379 | Portable Redis for Windows, downloaded to `tools/redis/` |
| **Keycloak** | 8180 | Downloaded to `tools/keycloak/` on first run (~150 MB) |
| **Qdrant** | — | Embedded at `data/qdrant` automatically (no server needed) |
| **PaddleOCR / Surya models** | — | Downloaded to `~/.paddlex/official_models/` on first OCR call per language (small "mobile" models, a few MB to tens of MB each) |

### Optional

| Requirement | When needed |
|---|---|
| **Node.js + npm** | React frontend (`rag-ui/`) |
| **Ollama** | Local/offline LLM fallback when Groq is unavailable |

---

## 12. Complete From-Scratch Setup & Run Guide

Follow this guide to set up the complete system (databases, identity provider, message queues, backend services, and frontend UI) from scratch on a new machine.

---

### 12.1 Python Environment Setup

The backend microservices and worker are written in Python.

1. **Install Python 3.11–3.13** from [python.org](https://www.python.org/downloads/). Ensure you check the box to **"Add Python to PATH"** during installation.
2. **Open PowerShell** and set up the virtual environment:

🟦 **Run in PowerShell:**
```powershell
# Create virtual environment (first time only)
python -m venv .venv

# Activate the virtual environment
.venv\Scripts\activate

pip install -U pip setuptools wheel

# Install all required Python packages (includes ML models & extractors)
pip install -r requirements.txt
```
3. **Install Ghostscript** (required by Camelot for table extraction):
   - Download the installer from the [Ghostscript Downloads Page](https://www.ghostscript.com/releases/gsdnld.html).
   - Run the installer to complete setup.
   - Verify the installation in your terminal:
     ```powershell
     gswin64c --version
     ```
   - Ensure the Ghostscript bin directory (typically `C:\Program Files\gs\gs10.XX.X\bin`) is added to your user/system `PATH` environment variable.
> [!NOTE]
> Installing dependencies may take 10–20 minutes as it downloads PyTorch CPU, PaddleOCR/PaddlePaddle, Surya, and other ML inference libraries (approx. 2-3 GB total).

---

### 12.2 Environment Configuration

All services read from a single, shared configuration file at the project root.

1. **Create the environment file:**

🟦 **Run in PowerShell:**
```powershell
copy .env.example .env
```

2. **Edit the `.env` file** and configure the following required parameters:
   - `POSTGRES_PASSWORD`: Change this to the password you choose for PostgreSQL (see step 12.3).
   - `GROQ_API_KEY`: Provide a valid Groq API key from [console.groq.com](https://console.groq.com).
   - `OCR_LANG` / `OCR_LANGS` (optional): set the language(s) PaddleOCR should use for scanned pages/images — see [Section 8.3](#83-multi-language-support).

---

### 12.3 PostgreSQL Installation & Database Generation

PostgreSQL stores domains, user lists, configurations, upload logs, text chunks (with search vectors), and query audit trails.

#### 1. Install PostgreSQL 16
- Download the installer from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/).
- Run the installer. Remember the password you set for the `postgres` superuser.
- Keep the default port **5432**.
- Verify installation:

🟦 **Run in PowerShell:**
```powershell
psql -U postgres -V
```
If the command is not found, add the PostgreSQL bin folder (typically `C:\Program Files\PostgreSQL\16\bin`) to your user PATH environment variable.

#### 2. Create the Database

🟦 **Run in PowerShell:**
```powershell
# Temporarily set password in terminal to bypass prompt
$env:PGPASSWORD="your_postgres_password"

# Create the main application database
psql -U postgres -c "CREATE DATABASE domain_db;"
```

#### 3. Initialize Schema & Seed Data
Generate the entire database structure (including the `source_type` column added in Sprint 2) and populate all tables with initial test data in one command:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -f migrations/init_db.sql
```

#### 4. Verify Database Setup
Confirm that all tables were created and populated correctly:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -c "SELECT 'users=' || count(*) FROM users UNION ALL SELECT 'domains=' || count(*) FROM domains UNION ALL SELECT 'configs=' || count(*) FROM domain_configs UNION ALL SELECT 'roles=' || count(*) FROM domain_roles UNION ALL SELECT 'documents=' || count(*) FROM documents UNION ALL SELECT 'chunks=' || count(*) FROM document_chunks UNION ALL SELECT 'logs=' || count(*) FROM rag_query_logs;"
```

**Expected Output:**
```text
 users=6
 domains=3
 configs=3
 roles=3
 documents=3
 chunks=3
 logs=1
```

#### 5. Database Migrations (For Upgrades)
If you are upgrading the database schema to support task tracking and metadata logging without dropping your tables, run the migration python script:

🟦 **Run in PowerShell:**
```powershell
.venv\Scripts\activate
python run_migration.py
```

---

### 12.4 Java & Keycloak Identity Setup

Keycloak serves as the central identity provider to enforce Role-Based Access Control (RBAC).

#### 1. Prerequisite: Java Installation
Keycloak requires Java 17 or higher.
- Download and install JDK 17+ from [Adoptium](https://adoptium.net/) (Temurin).
- Verify installation:

🟦 **Run in PowerShell:**
```powershell
java -version
```

#### 2. Starting Keycloak

- **Option A (Automatic):** When you run the launcher script (`python run_services.py`), it automatically downloads Keycloak 26.5.0 to the `tools/keycloak/` folder and imports the realm settings.
- **Option B (Manual):** Download Keycloak 26.5.0 from github and extract it to `tools/keycloak/`. Copy the realm configuration file and launch:

🟦 **Run in PowerShell:**
```powershell
mkdir "tools\keycloak\data\import" -Force
copy "services\auth\realm-export.json" "tools\keycloak\data\import\realm-export.json"

$env:KC_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD="admin"
.\tools\keycloak\bin\kc.bat start-dev --http-port=8180 --import-realm
```

#### 3. Seeding Realm Accounts
Initial users are automatically imported from the exported realm file:

| Username | Password | Realm Role | Description |
|---|---|---|---|
| `admin` | `admin` | `system_admin` | Full system administrator |
| `reader1` | `reader1` | `reader` | Regular viewer account |

To add additional test accounts (e.g., manager, contributor), use the Keycloak Admin CLI:

🟦 **Run in PowerShell:**
```powershell
# Login to the CLI
.\tools\keycloak\bin\kcadm.bat config credentials --server http://localhost:8180 --realm master --user admin --password admin

# Create users in 'rag-system' realm
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=manager -s enabled=true
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=user1 -s enabled=true
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username manager --new-password manager
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username user1 --new-password user1

# Assign roles
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername admin --rolename system_admin
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername manager --rolename reader
```

---

### 12.5 Redis Setup

Redis works as the asynchronous task message broker (Celery) and caching layer.

- **Option A (Automatic):** `run_services.py` automatically downloads a portable version of Redis for Windows to `tools/redis/` on first startup and runs it on port 6379.
- **Option B (Manual Installation):** If you prefer to install it globally:
  - Install via Windows Package Manager: `winget install Redis.Redis`
  - Start the service using `redis-server`.

---

### 12.6 Qdrant Vector Database

No manual setup is required. Qdrant runs in embedded mode inside the Python processes. Vector embeddings and collections are stored as binary files in the `data/qdrant/` folder.

---

### 12.7 React Frontend Setup

The frontend provides an interactive chat interface to ask questions, view citations, manage domains, and upload documents.

🟦 **Run in PowerShell:**
```powershell
cd rag-ui
npm install
npm run dev
```
Navigate to **http://localhost:5173** to access the UI. You can sign in using `admin`, `manager`, or `user` user IDs.

---

### 12.8 Launching the Backend Microservices

Launch all services plus the background Celery ingestion worker simultaneously:

🟦 **Run in PowerShell:**
```powershell
.venv\Scripts\activate
python run_services.py --worker
```

> The `--worker` flag is required for OCR — PaddleOCR and Surya are loaded inside the worker process. The first OCR call per language downloads its model (cached afterward under `~/.paddlex/official_models/`).

---

### 12.9 End-to-End System Verification

Once everything is running, verify system functionality:

#### 1. Ingestion File validation (DOCX/CSV/PDF/Image)
Obtain an access token and test document upload:

🟦 **Run in PowerShell:**
```powershell
# Get a JWT dev token
$token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"admin"}').token

# Upload a valid DOCX file (Should return 202 Accepted)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@test.docx" -F "domain_id=11111111-1111-1111-1111-111111111111"

# Upload a scanned PDF or image to exercise the OCR pipeline (Should return 202 Accepted)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@scanned_document.pdf" -F "domain_id=11111111-1111-1111-1111-111111111111"

# Upload an invalid file type (Should return 400 Bad Request)
curl.exe -X POST http://localhost:8002/ingest -H "Authorization: Bearer $token" -F "file=@test.exe" -F "domain_id=11111111-1111-1111-1111-111111111111"
```

After uploading the scanned PDF, poll `GET /ingest/{document_id}` until `status` is `done`. The worker log will show which OCR engine was used per page (`model_used=paddle` or `model_used=surya`) and its `confidence_score`.

#### 2. Querying & Citations
Verify that questions return answers with full metadata:

🟦 **Run in PowerShell:**
```powershell
curl.exe -X POST http://localhost:8004/generate/query -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d '{"query": "How many leave days do we get?", "domain_id": "11111111-1111-1111-1111-111111111111"}'
```
**Expected Response:** A JSON payload containing the answer text and a `citations` array, with `filename`, `source_type`, and `chunk_index` details.

#### 3. Verification of RBAC on Retrieval
Ensure that users without access roles on a domain are blocked:

🟦 **Run in PowerShell:**
```powershell
# Authenticate as viewer (only has access to domain 22222222...)
$viewer_token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"viewer"}').token

# Access domain 11111111... (should fail with 403 Forbidden)
curl.exe -X POST http://localhost:8003/api/v1/retrieve -H "Authorization: Bearer $viewer_token" -H "Content-Type: application/json" -d '{"query": "vacation", "domain_id": "11111111-1111-1111-1111-111111111111"}'
```

---

## 13. Environment Variables

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
| `OCR_LANG` | Single-language PaddleOCR model | `en` |
| `OCR_LANGS` | Comma-separated languages (e.g. `ar,en`) — overrides `OCR_LANG`, picks best-confidence result per page | unset |
| `OCR_CONFIDENCE_THRESHOLD` | PaddleOCR score above which Surya fallback is skipped | `0.85` |

> See `.env.example` for inline comments explaining where to get each value.

---

## 14. Troubleshooting

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
- For scanned PDFs/images, the first page can take longer the first time per language as PaddleOCR/Surya models download and load — check the worker log for `Loading PaddleOCR...` / `Loading Surya OCR models...`

### OCR — `DLL load failed while importing libpaddle` (Windows)

- Install the latest **Microsoft Visual C++ Redistributable (x64)**: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Restart your terminal (or reboot) after installing, then retry `python run_services.py --worker`

### OCR — `NotImplementedError: ConvertPirAttribute2RuntimeAttribute ... pir::ArrayAttribute<DoubleAttribute>`

Known `paddlepaddle==3.3.x` CPU issue with PP-OCRv5 models under oneDNN. Already worked around in `paddle_engine.py` via `enable_mkldnn=False`. If you see this, confirm you're on the version of `paddle_engine.py` that sets `enable_mkldnn=False`.

### OCR — worker exits with code `3221225477` (0xC0000005, access violation)

Caused by the default `PP-OCRv5_server_det` detection model when `enable_mkldnn=False`. Already worked around in `paddle_engine.py` via `text_detection_model_name="PP-OCRv5_mobile_det"`.

### OCR — wrong language / poor accuracy on non-English documents

Set `OCR_LANG=<code>` (e.g. `ar` for Arabic) or `OCR_LANGS=ar,en` for mixed-language documents — see [Section 8.3](#83-multi-language-support).

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

## 15. Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example                      # environment template (copy to .env)
├── .gitignore                        # comprehensive ignore rules
├── requirements.txt                  # unified Python dependencies
├── run_services.py                   # main launcher (starts everything)
├── delete_chunks.py                  # database + vector store reset tool
├── README.md                         # this file — complete project guide
├── migrations/                       # database SQL scripts
│   ├── init_db.sql                   # creates schema & populates seed data from scratch
│   └── sprint2_migration.sql         # Sprint 2 migration (source_type column)
├── data/                             # auto-created runtime data (gitignored)
│   ├── qdrant/                       # embedded vector DB
│   ├── uploads/                      # uploaded documents (PDF, DOCX, CSV, images)
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
    ├── retrieval-service/            # port 8003 (+ RBAC filtering)
    ├── generation-service/           # port 8004
    ├── evaluation-service/           # port 8005 (optional)
    └── worker-service/               # Celery worker (multi-format + OCR)
        └── tasks/
            ├── extract.py            # format routing, calls OCR pipeline for scans/images
            └── ocr-service/
                └── ocr_service/
                    ├── pipeline.py            # top-level OCR entry point
                    ├── preprocessing/
                    │   └── image_processor.py # denoise, deskew, resize
                    ├── engines/
                    │   ├── paddle_engine.py   # PaddleOCR (fast path, multi-language)
                    │   └── surya_engine.py    # Surya (fallback)
                    ├── scoring/
                    │   └── ocr_scorer.py      # confidence scoring for both engines
                    └── routing/
                        └── ocr_router.py      # PaddleOCR -> Surya routing decision
```

---

## 16. Quick Reference Card

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
  3. POST /ingest                           → upload PDF / image
  4. GET  /ingest/{document_id}             → wait for "done" (OCR runs here for scans/images)
  5. POST /generate/query                   → get AI answer with citations

OCR config:
  OCR_LANG=en           → single language (default)
  OCR_LANGS=ar,en       → multi-language, picks best-confidence result per page
```

---

*Last updated: June 2026 — Chatbot-Fixed-Team2 / Fixed Solutions AI Internship*