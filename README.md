# Chatbot-Fixed-Team2

**Multi-user, multi-domain Retrieval-Augmented Generation (RAG) system** — Fixed Solutions AI Internship 2026.

A complete backend + frontend stack for domain management, document ingestion, hybrid retrieval (Vector + BM25 + Graph), AI answer generation with citations, automated evaluation, TLS termination, and telemetry. All workflows are exposed through HTTP APIs, secure HTTPS proxies, and a React chat UI.

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
12. [Complete Setup, Security & Run Guide](#12-complete-setup-security--run-guide)
13. [Environment Variables](#13-environment-variables)
14. [Troubleshooting](#14-troubleshooting)
15. [Directory Layout](#15-directory-layout)
16. [Quick Reference Card](#16-quick-reference-card)
17. [Sprint 3 — Hybrid Graph RAG (Apache AGE)](#17-sprint-3--hybrid-graph-rag-apache-age)
18. [Monitoring — Prometheus + Grafana](#18-monitoring--prometheus--grafana)
19. [Load Testing — Infrastructure Monitoring](#19-load-testing--infrastructure-monitoring)

---

## 1. Project Overview

### What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain Retrieval-Augmented Generation (RAG) system**. It allows organizations to:

- Create separate **knowledge domains** (isolated knowledge bases, e.g., "HR Policies", "Tech Support", "Legal Contracts")
- Upload **documents** (PDF, DOCX, CSV, PNG, JPG) into those domains
- Ask **natural language questions** and receive **AI-generated answers with citations** grounded in the uploaded documents
- Evaluate answer quality automatically using an LLM-as-judge pipeline
- Monitor the entire stack in real time with Prometheus, Grafana dashboards, and Alertmanager.

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

---

## 2. System Architecture

### 2.1 Service Topology

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    Caddy["Caddy Proxy/Gateway :8000"]
    KC["Keycloak :8180"]
    KCGateway["Keycloak Caddy Proxy :8443"]
    DS["monolith-service (FastAPI) :8001"]
    WS["worker-service (Celery)"]
    PG["PostgreSQL 17 :5434"]
    PGWSL["PostgreSQL 17 + AGE\n(WSL2) :5434"]
    RD["Redis :6379"]
    QD["Qdrant (embedded)"]
    LLM["Groq API / Ollama"]

    Client -->|JWT auth| KCGateway
    KCGateway --> KC
    Client --> Caddy
    Caddy --> DS

    DS --> PG
    DS --> RD
    RD --> WS
    WS --> PG
    WS --> QD
    WS -->|Sprint 3: NER + Relations| PGWSL

    style KC fill:#f9a825,color:#000
    style KCGateway fill:#f59e0b,color:#000
    style PG fill:#336791,color:#fff
    style PGWSL fill:#2c5f8a,color:#fff
    style RD fill:#dc382d,color:#fff
    style QD fill:#24b47e,color:#fff
    style LLM fill:#7c3aed,color:#fff
    style WS fill:#a78bfa,color:#000
```

### 2.2 Query Flow

```mermaid
flowchart TD
    Client["Client (Browser / API)"]
    Caddy["Caddy Gateway :8000"]
    KC["Keycloak Auth Provider"]
    GS["monolith-service :8001"]
    RD["Redis Cache"]
    QD["Qdrant (Vector DB)"]
    PG["PostgreSQL (BM25 DB)"]
    AGE["Apache AGE (Graph DB)"]
    LLM["LLM (Groq / Ollama)"]

    Client -->|1. Submit RAG Query| Caddy
    Caddy --> GS
    GS -->|2. Validate JWT| KC
    GS -->|3. Check Cache| RD

    RD -->|Cache Hit: Return Answer| GS
    GS -->|Return Answer| Client

    RD -->|Cache Miss| GS
    GS -->|4. Hybrid Retrieval| QD
    GS -->|5. Sparse Search| PG
    GS -->|6. Graph Traversal Sprint3| AGE
    QD & PG & AGE -->|7. Fusion + Rerank| GS

    GS -->|8. Build Prompt + Call| LLM
    LLM -->|9. Generate Answer| GS
    GS -->|10. Log Query| PG
    GS -->|11. Cache Response| RD
    GS -->|12. Return Final Answer| Client

    style KC fill:#f9a825,color:#000
    style RD fill:#dc382d,color:#fff
    style PG fill:#336791,color:#fff
    style QD fill:#24b47e,color:#fff
    style AGE fill:#2c5f8a,color:#fff
    style LLM fill:#7c3aed,color:#fff
```

---

## 3. Architecture Decisions

### Decision 1: Single Root `.env`
All services consume the same root `.env` loaded by `run_services.py`. One source of truth for local development.

### Decision 2: PostgreSQL 17 on Port 5434
The project uses port **5434** to avoid conflicts with default PostgreSQL 5432 instances. This matches both the Windows PostgreSQL 17 instance and the WSL2 Apache AGE instance.

### Decision 3: Three-Signal Retrieval Pipeline
Retrieval implements: dense vector search (Qdrant) → sparse keyword search (PostgreSQL BM25) → Reciprocal Rank Fusion (RRF) → cross-encoder reranking → Redis cache.

### Decision 4: Unified FastAPI Monolith (Merged Microservices)
To simplify deployment, reduce network overhead, and consolidate memory usage, all backend microservices (Domain, Ingestion, Retrieval, Generation, and Evaluation) are mounted as sub-apps into a single FastAPI Monolith listening on port **8001**.

### Decision 5: Groq First, Ollama Fallback
Generation uses Groq when `GROQ_API_KEY` is configured, falling back to Ollama otherwise. Both expose an OpenAI-compatible API.

### Decision 6: Worker Maintains Dual Indexes
Celery Worker writes chunks into both Qdrant (dense) and PostgreSQL `document_chunks` (BM25) during document ingestion.

### Decision 7: Redis Is Shared Across Queue and Cache
Redis serves as Celery broker, Celery result backend, and caching engine.

### Decision 8: Apache AGE Instead of Neo4j (Sprint 3)
Apache AGE is a PostgreSQL extension — same engine already used by the rest of this project. This reuses existing database instances and maintains strict domain isolation.

### Decision 9: OCR Is a PaddleOCR + Surya Ensemble
PaddleOCR runs first (fast path); if its confidence score falls below `0.85`, Surya runs as a fallback.

### Decision 10: Scripts Directory Contains Shared Runtime Modules
Auxiliary files (`clear_database.py`, `delete_chunks.py`, `download_onnx.py`, `wsl2_setup_v2.sh`) have been relocated to the `scripts/` folder to keep the root directory neat and tidy.

---

## 4. Technology Stack

| Component | Technology | Version | Purpose |
|---|---|---|---|
| Language | Python | 3.11–3.13 | Backend runtime |
| Web framework | FastAPI + Uvicorn | 0.115.6 / 0.34.0 | Monolith service |
| Frontend | React + Vite + TS | — | Chat UI at `rag-ui/` |
| Database | PostgreSQL | **17** (port 5434) | Domains, documents, chunks, query logs |
| Graph DB | PostgreSQL 17 + AGE | AGE v1.6.0-rc0 | Knowledge graph (Sprint 3, WSL2) |
| Vector DB | Qdrant | 1.12.1 | Embedded dense vector search |
| Cache / Queue | Redis | 5.x | Celery broker + caching |
| Task queue | Celery | 5.4.0 | Ingestion & batch evaluation |
| Auth | Keycloak | 26.5.0 | OAuth2/OIDC, JWT tokens |
| Proxy / TLS | Caddy | 2.x | HTTPS gateway & TLS termination |
| Embeddings | `multilingual-e5-small` | — | 384-dim multilingual embeddings |
| Reranker | `mmarco-mMiniLMv2` | — | Cross-encoder reranking |
| NER (Sprint 3) | `gliner_multi-v2.1` | — | Zero-shot entity extraction |
| Local LLM | Ollama | — | `llama3.2:3b` (offline fallback) |
| OCR | PaddleOCR + Surya | — | Document layout-aware text extraction |

---

## 5. Services Reference

### Service Port Map

| Component | Port(s) | Type | Route / URL | Purpose |
|---|---:|---|---|---|
| **Caddy Gateway** | `8000` | HTTPS Proxy | `https://localhost:8000` | Entry point for backend API traffic |
| **FastAPI Monolith** | `8001` | HTTP API | `http://localhost:8001` | Direct backend containing all endpoints |
| **Keycloak Gateway** | `8443` | HTTPS Proxy | `https://localhost:8443` | Secure OIDC Auth entry point |
| **Keycloak (Auth)** | `8180` | OIDC Server | `http://localhost:8180` | Raw identity provider server |
| **PostgreSQL 17** | `5434` | Relational DB | `localhost:5434` | Database storage (domains, chunks, graphs) |
| **Redis** | `6379` | Cache/Queue | `localhost:6379` | Celery broker, cache engine |
| **React UI (Caddy)** | `3001` | HTTPS server | `https://localhost:3001` | Compiled production frontend |
| **Vite Dev Server** | `5173` | HTTP server | `http://localhost:5173` | Local frontend dev server |
| **Prometheus** | `9092` | Metrics | `http://localhost:9092` | Telemetry database |
| **Grafana** | `3000` | Dashboards | `http://localhost:3000` | Telemetry charts & dashboards |

---

## 6. Database Schema

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

---

## 7. Retrieval Pipeline

The retrieval pipeline processes incoming queries via three distinct tracks before rank fusion and cross-encoder reranking:

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

1. **Embedding**: Model `intfloat/multilingual-e5-small` converts queries with a `query:` prefix.
2. **Dense Vector Search**: Qdrant executes cosine similarity to capture semantic similarity.
3. **Sparse Keyword Search**: PostgreSQL full-text search (`TSVECTOR`) recovers exact keyword matches.
4. **Graph Traversal (Sprint 3)**: Apache AGE 1-hop traversal resolves relational entities when triggered.
5. **Fusion**: Reciprocal Rank Fusion (RRF, `k=60`) merges list candidates.
6. **Reranking**: `mmarco-mMiniLMv2-L12-H384-v1` re-scores top candidates before returning.

---

## 8. OCR Pipeline

Scanned PDF pages and standalone image uploads (PNG/JPG/JPEG) are routed through a high-performance preprocessing and OCR ensemble:

```mermaid
flowchart TD
    F["File arrives at worker\n(PDF / DOCX / CSV / Image)"]
    EXT["tasks/extract.py"]
    DOCX["DOCX -> python-docx"]
    CSV["CSV -> pandas"]
    NATIVE{"Native text layer?\n(PyMuPDF)"}
    DIRECT["Use native text directly"]
    PRE["Preprocessing\nDenoise -> Deskew -> Adaptive Resize"]
    PADDLE["PaddleOCR\n(fast path)"]
    PSCORE{"Paddle score >= 0.85?"}
    EXIT["Early exit: use Paddle result"]
    SURYA["Surya OCR\n(transformer fallback)"]
    PICK["Pick higher-scoring result"]
    OUT["{page, text, confidence_score}"]
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
```

* **Preprocessing**: Image processor performs bilateral denoising, Hough line deskewing, and adaptive resizing.
* **PaddleOCR**: Loaded as a cached singleton per language (`en`, `ar`).
* **Surya Fallback**: Transformer-based OCR triggered if PaddleOCR confidence falls below `0.85`.

---

## 9. Authentication & RBAC

The system employs a two-layer security approach:
1. **JWT Signature Layer**: Every API call requires a Bearer token.
2. **Service Access Checks**: Internal service-to-service calls use the shared `INTERNAL_API_KEY` header.

### Realm Roles & Permissions

| Role | Meaning | Action Permissions |
|---|---|---|
| `system_admin` | Platform-wide administrator | Create domains, update global settings, bypass RBAC checks |
| `domain_admin` | Domain-specific manager | Manage domain members and domain configs |
| `contributor` | Editor/Writer | Upload documents, run manual ingestion, query domains |
| `reader` | Viewer/Consumer | Query domain documents and view citations |

### Dev Auth Mode (Local Fallback)
If Keycloak is not running, `run_services.py` spawns local mock JWT authentication. Users can authenticate by entering pre-configured User IDs: `admin`, `manager`, `contributor`, `viewer`.

---

## 10. API Reference

All backend endpoints are integrated into the FastAPI monolith running at port `8001` (exposed externally on HTTPS port `8000` via Caddy).

* **Swagger Interactive Docs**: [http://localhost:8001/docs](http://localhost:8001/docs)
* **Key endpoints**:
  * `POST /domains/auth/login` — Mint JWT token (Dev Auth fallback).
  * `POST /domains` — Create a knowledge domain (Admin only).
  * `GET /domains` — List accessible domains.
  * `POST /ingest` — Upload PDF/DOCX/CSV/Image (`file` + `domain_id`).
  * `GET /ingest/{id}` — Poll document status (`pending` -> `processing` -> `done`/`failed`).
  * `POST /generate/query` — RAG question answering.
  * `POST /evaluate` — Trigger LLM judge evaluation.

---

## 11. Prerequisites

### Required System Software
* **Python**: `3.11` to `3.13`. Ensure it is added to your environment `PATH`.
* **PostgreSQL**: Version **17**. Stop any running services on 5432/5433. Database port must be set to `5434`.
* **Java**: JDK `17` or newer (required for Keycloak OIDC server).
* **Microsoft Visual C++ Redistributable (x64)**: Required for PaddleOCR/PyTorch DLL bindings.
* **Groq API Key**: Obtain a free-tier key from [console.groq.com](https://console.groq.com).

### Auto-Configured (Downloaded by Orchestrator)
* **Redis**: Portable Windows Redis server, placed inside `tools/redis/`.
* **Keycloak**: Server archives unpacked under `tools/keycloak/` (first run).
* **Qdrant**: Embedded engine writing vector files directly to `data/qdrant/`.

---

## 12. Complete Setup, Security & Run Guide

Follow these steps chronologically to initialize the databases, launch authentication and gateway proxies, start backend servers, boot monitoring stacks, and deploy the React frontend.

### 12.1 Stop Legacy PostgreSQL Services
The database **must** run on port `5434` via PostgreSQL 17. Stop and disable legacy PostgreSQL instances (ports 5432/5433):

Run as **Administrator** in CMD:
```cmd
:: Stop older active installations
net stop postgresql-x64-14
net stop postgresql-x64-15
net stop postgresql-x64-16

:: Disable auto-start to prevent port conflicts
sc config postgresql-x64-14 start= disabled
sc config postgresql-x64-15 start= disabled
sc config postgresql-x64-16 start= disabled

:: Ensure PostgreSQL 17 is active
net start postgresql-x64-17
sc query postgresql-x64-17 | findstr "STATE"
```

---

### 12.2 Python Environment Setup & Dependencies
Activate the virtual environment and install packages:

**In CMD / PowerShell:**
```powershell
# Create virtual environment (run once)
python -m venv .venv

# Activate (PowerShell)
.venv\Scripts\Activate.ps1

# Activate (CMD)
.venv\Scripts\activate.bat

# Install core packaging tools and libraries
pip install -U pip setuptools wheel
pip install -r requirements.txt
```

---

### 12.3 Environment Configuration & Secrets Check
1. Copy the example environment template:
   ```powershell
   copy .env.example .env
   ```
2. Edit `.env` and configure:
   * `POSTGRES_PASSWORD` — Your PostgreSQL superuser password (e.g. `1234`).
   * `DATABASE_URL` — Port `5434` (`postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5434/domain_db`).
   * `SYNC_DATABASE_URL` — Port `5434` (`postgresql://postgres:YOUR_PASSWORD@localhost:5434/domain_db`).
   * `GROQ_API_KEY` — Your cloud LLM key (`gsk_...`).
   * `INTERNAL_API_KEY` — Generate a strong 32-character random key:
     ```powershell
     python -c "import secrets; print(secrets.token_hex(32))"
     ```
3. Run the validation checks manually to confirm secrets are secure:
   ```powershell
   python scripts/secrets_check.py
   ```

---

### 12.4 Database Initialization
Create the database and execute schemas:

**In CMD:**
```cmd
set PGPASSWORD=YOUR_PASSWORD
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
```

**In virtual environment:**
```powershell
python scripts/run_migration.py
```
*To erase all tables and chunks back to a clean slate, run:*
```powershell
python scripts/clear_database.py
python scripts/run_migration.py
```

---

### 12.5 Dev JWT Keys Generation
If using Dev Auth Mode, initialize the local RSA key pair (Keycloak replacement):
```powershell
python scripts/gen_dev_keys.py
```

---

### 12.6 Start Infrastructure Services (Docker)
The project dockerizes the default gateway and authentication layers to guarantee consistent routing.

From the project root directory, run:
```powershell
docker compose up -d
```
Verify the services are active:
- **Keycloak Administration:** [http://localhost:8180](http://localhost:8180) (Default Credentials: `admin` / `admin`)
- **Traefik Dashboard:** [http://localhost:8088](http://localhost:8088)

---

### 12.7 Starting Backend Services (Host Monolith)
Start the main orchestrator (APIs, celery task queue, and evaluation runner):
```powershell
# WSL2 Ubuntu (Optional - run in background if Apache AGE graph DB is active)
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"

# Run backend services (worker + evaluation metrics enabled)
python run_services.py --worker --evaluation --skip-infra
```
Verify monolith health:
```cmd
curl http://localhost:8001/health
:: Expected: {"status":"ok","service":"monolith-service"}
```

---

### 12.8 Launch Caddy Reverse Proxy & TLS Termination
Caddy secures all frontdoors under local SSL/TLS certificates.

1. Build the production UI bundle:
   ```powershell
   cd rag-ui
   npm run build
   cd ..
   ```
2. Spawn Caddy using the binary located in `tools/`:
   ```powershell
   # In PowerShell environment binding
   $env:UI_PORT="3001"
   $env:GATEWAY_PORT="8000"
   $env:KEYCLOAK_GATEWAY_PORT="8443"
   $env:KEYCLOAK_PORT="8180"
   $env:DOMAIN_SERVICE_PORT="8001"

   tools\caddy\caddy.exe run --config Caddyfile
   ```
   ```cmd
   :: In CMD environment binding
   set UI_PORT=3001
   set GATEWAY_PORT=8000
   set KEYCLOAK_GATEWAY_PORT=8443
   set KEYCLOAK_PORT=8180
   set DOMAIN_SERVICE_PORT=8001

   tools\caddy\caddy.exe run --config Caddyfile
   ```
3. Trust the local CA certificate (Run once as Administrator to resolve browser warnings):
   ```powershell
   tools\caddy\caddy.exe trust
   ```
Open browser: **[https://localhost:3001](https://localhost:3001)** (UI) or **[https://localhost:8000/docs](https://localhost:8000/docs)** (API docs via Caddy).

---

### 12.9 Launch Monitoring Stack & Baseline Metric Collection
Deploy Prometheus and Grafana to track stack resources:

1. Deploy the monitoring agents in Docker:
   ```powershell
   docker compose -f monitoring/docker-compose.monitoring.yml up -d
   ```
   * **Grafana Console:** [http://localhost:3000](http://localhost:3000) (Default: `admin` / `admin`)
   * **Prometheus UI:** [http://localhost:9092](http://localhost:9092)
   * **Alertmanager:** [http://localhost:9093](http://localhost:9093)

2. Run pre-test baseline collection (execute in Git Bash or WSL2 Ubuntu):
   ```bash
   bash monitoring/scripts/baseline.sh
   ```
   *This command writes and prints results automatically to:* `monitoring/baseline_results.txt`.

---

### 12.10 Start Locust Load Test & Apply Tuning
Simulate concurrent users querying the backend endpoints.

1. Start Locust (ensure virtual environment is active):
   * **Option A: Direct Monolith load testing (bypassing HTTPS Caddy gateway):**
     ```powershell
     locust -f tests/load_test.py --host=http://localhost:8001
     ```
   * **Option B: Gateway load testing (requires Caddy running):**
     ```powershell
     locust -f tests/load_test.py --host=https://localhost:8000
     ```
     Open **[http://localhost:8089](http://localhost:8089)** to configure user count and spawn rate.
   * **Option C: Headless direct monolith test (3 minutes duration):**
     ```powershell
     locust -f tests/load_test.py --host=http://localhost:8001 --users 50 --spawn-rate 5 --run-time 3m --headless --csv=tests/load_results
     ```
2. Apply bottleneck tuning:
   If telemetry reveals high latency, execute the tuning scripts inside Git Bash or WSL2:
   ```bash
   bash monitoring/scripts/tuning.sh A    # Fix PostgreSQL Connection Pool Exhaustion
   bash monitoring/scripts/tuning.sh B    # Fix Redis Memory Limit / Evictions
   bash monitoring/scripts/tuning.sh C    # Increase Uvicorn/Celery Worker Processes
   bash monitoring/scripts/tuning.sh D    # Run EXPLAIN ANALYZE on Slow SQL Queries
   bash monitoring/scripts/tuning.sh E    # Add Missing Databases Indexes
   bash monitoring/scripts/tuning.sh ALL  # Run all diagnostics and index additions
   ```

---

### 12.11 React UI Serving (3 Deployment Options)

* **💻 Option A: Vite Development Server**
  Best for active code editing and local development.
  ```powershell
  cd rag-ui
  npm install
  npm run dev
  ```
  Open browser: **[http://localhost:5173](http://localhost:5173)**

* **🐊 Option B: Production Gateway serving with Caddy**
  Serves the compiled production bundle via HTTPS on port `3001` (Uses Step 12.8 above).
  Open browser: **[https://localhost:3001](https://localhost:3001)**

* **🖥️ Option C: Production Serving via Windows IIS**
  Serves the React app and proxies backend routes using IIS's native modules.

  1. **Enable IIS** on your Windows system:
     * Search for **"Turn Windows features on or off"** in the Windows Start menu.
     * Check **Internet Information Services**.
     * Expand and check **Common HTTP Features** (Static Content) and **Application Development Features** (.NET Extensibility, ASP.NET).
  2. **Install URL Rewrite Module:**
     * Download and install: [IIS URL Rewrite Download](https://www.iis.net/downloads/microsoft/url-rewrite).
  3. **Install Application Request Routing (ARR 3.0):**
     * Download and install: [IIS ARR Download](https://www.iis.net/downloads/microsoft/application-request-routing).
     * Open IIS Manager, click on your server node, select **Application Request Routing Cache**, click **Server Proxy Settings** in the right pane, check **Enable proxy**, and click **Apply**.
  4. **Deployment Setup:**
     * Build the frontend production folder:
       ```powershell
       cd rag-ui
       npm run build
       ```
       *This generates output files in `rag-ui/dist/` alongside a pre-configured `web.config` file.*
     * **Add a new IIS Website:**
       * Open **IIS Manager** (`inetmgr`).
       * Right-click **Sites** -> **Add Website**.
       * **Site name:** `rag-ui`
       * **Physical path:** `d:\Personal\Fixed Solutions\Project Files\Last Version\rag-ui\dist`
       * **Port:** `3001` (matching UI_PORT from your `.env`).
     * **Configure Rewrite Proxy Endpoint in `web.config`:**
       * If IIS does not have the system environment variables configured, open the compiled `rag-ui/dist/web.config` file, locate the API Proxy rule action:
         `url="http://localhost:{ENV:DOMAIN_SERVICE_PORT}/{R:0}"`
         and hardcode your backend monolith port (`8001`):
         `url="http://localhost:8001/{R:0}"`
     * **Permissions Configuration:**
       * Ensure the IIS Application Pool identity (typically `IIS_IUSRS` or `IUSR`) has read permissions to the `rag-ui/dist` directory.
     * **Access the Application:**
       Open browser: **[http://localhost:3001](http://localhost:3001)**


---

## 13. Environment Variables

All services load environment variables from the shared root `.env`.

### Core Options Reference

| Variable | Default Value | Purpose |
|---|---|---|
| `POSTGRES_PORT` | `5434` | Target port for PostgreSQL 17 |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async SQLAlchemy database driver DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery message broker & Cache DSN |
| `QDRANT_PATH` | `data/qdrant` | Embedded vector database directory |
| `INTERNAL_API_KEY` | (needs change) | Secret key protecting microservice communication |
| `AGE_DATABASE_DSN` | (empty) | DSN mapping to Apache AGE WSL2 instance (Sprint 3) |
| `EVALUATE_ON_GENERATION` | `true` | Auto-trigger LLM evaluation per answer |
| `OCR_LANGS` | `ar,en` | Supported OCR scanner languages |

---

## 14. Troubleshooting

* **PostgreSQL Port conflicts (5434 already in use)**:
  Check for hidden Postgres services:
  ```cmd
  netstat -ano | findstr :5434
  taskkill /PID <PID_FROM_NETSTAT> /F
  ```
* **SSL Certificate Warnings in Browser**:
  Verify Caddy certificate was installed:
  ```powershell
  tools\caddy\caddy.exe trust
  ```
* **Celery worker billiard errors on Windows**:
  Windows does not support default celery prefork pool. Ensure orchestrator starts Celery with `--pool=solo`.
* **OCR DLL Load Errors (`libpaddle`)**:
  Make sure you have installed the Microsoft Visual C++ Redistributable (x64) and restarted your command prompt.

---

## 15. Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example
├── .env
├── .gitignore
├── requirements.txt
├── run_services.py
├── Caddyfile
├── docker-compose.yml
├── dockerignore
├── data/                             # runtime vectors, uploads, and local JWT keys
├── docs/                             # architectural guides and documents
│   ├── run_guide.md
│   ├── full_execution_guide.md
│   └── TLS_Secrets_RAG_System.md
├── scripts/                          # helper management scripts
│   ├── clear_database.py             # wipes database tables
│   ├── delete_chunks.py              # wipes Qdrant collections + database
│   ├── download_onnx.py              # helper to download GLiNER model
│   ├── run_migration.py              # run database migrations and reset scripts
│   ├── wsl2_setup_v2.sh              # setup Postgres 17 + AGE inside WSL2
│   ├── dev_auth.py
│   ├── infra_manager.py
│   └── secrets_check.py
├── tools/                            # binaries & services
│   ├── caddy/
│   │   └── caddy.exe                 # Local TLS gateway executable
│   ├── helm/
│   │   └── helm.exe                  # Kubernetes Helm CLI
│   ├── redis/                        # Portable Redis
│   └── keycloak/                     # Keycloak server files
├── rag-ui/                           # React frontend
└── services/                         # FastAPI monolith source code
```

---

## 16. Quick Reference Card

```text
═══════════════════════════════════════════════════════════════
  RAG System — Quick Reference Commands
═══════════════════════════════════════════════════════════════

CLEAN RESET:
  python scripts/delete_chunks.py       # reset Qdrant + postgres tables
  python scripts/clear_database.py      # reset database tables only
  python scripts/run_migration.py       # run migration scripts

RUN BACKEND MONOLITH:
  python run_services.py --worker --evaluation --skip-infra

RUN TLS PROXY GATEWAY:
  $env:UI_PORT="3001"
  $env:GATEWAY_PORT="8000"
  $env:KEYCLOAK_GATEWAY_PORT="8443"
  $env:KEYCLOAK_PORT="8180"
  $env:DOMAIN_SERVICE_PORT="8001"
  tools\caddy\caddy.exe run --config Caddyfile

MONITORING (Docker):
  cd monitoring
  docker compose -f docker-compose.monitoring.yml up -d
  bash scripts/baseline.sh              # collect idle baselines

FRONTEND DEV SERVER:
  cd rag-ui
  npm install && npm run dev            # http://localhost:5173
═══════════════════════════════════════════════════════════════
```

---

## 17. Sprint 3 — Hybrid Graph RAG (Apache AGE)

### 17.1 What This Sprint Adds

Sprint 3 implements an additive **Knowledge Graph** layer using **Apache AGE** on top of the vector and BM25 channels. It connects extracted entities (Person, Project, Department, Policy, Role, Location, Skill) through structured typed relations (MANAGES, BELONGS_TO, REPORTS_TO, OWNS, HAS_ROLE, WORKS_ON, HAS_SKILL, BASED_AT).

### 17.2 WSL2 Apache AGE Setup

1. Enable WSL2 Ubuntu 22.04:
   ```powershell
   wsl --install -d Ubuntu-22.04
   ```
2. Copy and execute the setup script:
   ```bash
   # Inside WSL2 Ubuntu terminal
   cp /mnt/d/Personal/Fixed\ Solutions/Project\ Files/Last\ Version/scripts/wsl2_setup_v2.sh ~/wsl2_setup.sh
   chmod +x ~/wsl2_setup.sh
   ~/wsl2_setup.sh
   ```
3. Initialize the database schema:
   ```cmd
   set PGPASSWORD=55555
   psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
   psql -h localhost -p 5434 -U postgres -d domain_db -f migrations/sprint3_foundation.sql
   ```
4. Enable `AGE_DATABASE_DSN` inside `.env`:
   ```ini
   AGE_DATABASE_DSN=postgresql://postgres:55555@localhost:5434/domain_db
   AGE_GRAPH_NAME=rag_graph
   ```

---

## 18. Monitoring — Prometheus + Grafana

### 18.1 Grafana Telemetry Dashboard Panels

The monitoring stack launches Prometheus scraping metrics from all monolith modules, exposing 4 dashboards inside Grafana ([http://localhost:3000](http://localhost:3000)):

* **Service Health**: Tracks query rates, HTTP latencies (p50/p95/p99), and HTTP status codes.
* **Evaluation Quality**: Visualizes LLM judge metrics (Faithfulness, Relevance, and Completeness) scored 0.0 to 1.0.
* **Infra Overview**: Monitours database connection pool size, active Redis caching memory, memory saturation, and CPU loads.
* **Retrieval Pipeline**: Details the latency breakdown per search stage (dense vector lookup, BM25 query time, fusion rank calculation, and reranker engine).

---

## 19. Load Testing — Infrastructure Monitoring

Locust tests simulate concurrent user traffic (`tests/load_test.py`) to benchmark the system under heavy Q&A volumes.

### 19.1 System Tuning Commands

Run specific sections of `scripts/tuning.sh` via Git Bash / WSL2 if bottlenecks are observed:
* `bash monitoring/scripts/tuning.sh A` — Scale database connection pools (`max_connections`).
* `bash monitoring/scripts/tuning.sh B` — Optimize Redis memory boundaries and evictions.
* `bash monitoring/scripts/tuning.sh C` — Scale backend Uvicorn processes and worker pools.
* `bash monitoring/scripts/tuning.sh D` — Execute diagnostics and `EXPLAIN ANALYZE` on database queries.
* `bash monitoring/scripts/tuning.sh E` — Apply missing indexes to `document_chunks` GIN/B-Tree fields.
