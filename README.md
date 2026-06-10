# Chatbot-Fixed-Team2

Multi-user, multi-domain RAG (Retrieval-Augmented Generation) backend for the Fixed Solutions AI Internship 2026.

The stack includes the full backend path for domain management, ingestion, retrieval, answer generation, and evaluation. The chat UI is intentionally skipped in this sprint; all workflows are exposed through HTTP APIs.

---

## Table of Contents

1. [What Is This Project?](#1-what-is-this-project)
2. [How It Works](#2-how-it-works)
3. [System Architecture](#3-system-architecture)
4. [Services Reference](#4-services-reference)
5. [Prerequisites](#5-prerequisites)
6. [Local Setup (Step-by-Step)](#6-local-setup-step-by-step)
7. [Start The Stack](#7-start-the-stack)
8. [Verify Services](#8-verify-services)
9. [End-to-End API Walkthrough](#9-end-to-end-api-walkthrough)
10. [Authentication & Access Control](#10-authentication--access-control)
11. [Environment Variables](#11-environment-variables)
12. [Troubleshooting](#12-troubleshooting)
13. [What Is Not Included](#13-what-is-not-included)
14. [Quick Reference Card](#14-quick-reference-card)

---

## 1. What Is This Project?

**Chatbot-Fixed-Team2** is a **multi-user, multi-domain RAG backend** that lets:

- Organizations create separate **knowledge domains** (e.g. "HR Policies", "Support Docs", "Legal").
- Users upload **PDF documents** into a domain.
- The system reads those documents, breaks them into searchable pieces, and stores them.
- Users ask **questions in natural language**.
- The system finds the most relevant document passages and uses an **AI language model** to write an answer **grounded in those passages**, with **citations** back to the source material.

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-domain isolation | Each domain has its own documents, members, and configuration |
| Role-based access | System-level and per-domain roles control who can manage, upload, or query |
| Hybrid retrieval | Combines semantic (vector) search and keyword (BM25) search for better accuracy |
| AI answer generation | Uses Groq (cloud) or Ollama (local) to generate answers from retrieved context |
| Async document processing | PDF uploads are processed in the background via a job queue |
| Caching | Redis caches retrieval results and generated answers for faster repeat queries |

---

## 2. How It Works

Think of the system as a **smart library with an AI librarian**:

1. **You create a shelf (domain)** — a labeled section of the library for one topic.
2. **You add books (PDFs)** — the system scans each book, splits it into paragraphs (chunks), and indexes them in two ways: by meaning (vectors) and by keywords (full-text search).
3. **You ask a question** — the librarian searches both indexes, picks the best paragraphs, and hands them to an AI writer.
4. **The AI answers** — using only those paragraphs as evidence, and tells you which pages they came from.

Security works like a building with ID badges:

- **Keycloak** issues login tokens (JWTs).
- **Traefik** (the front door) checks your badge before letting you in.
- Each **service** checks your badge again and verifies you have permission for that specific domain.

---

## 3. System Architecture

### High-Level Overview

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

### How Services Connect — Query Flow

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

---

## 4. Services Reference

### Traefik (API Gateway)

| | |
|---|---|
| **What it does** | Single entry point for all external API traffic. Routes requests to the correct microservice and applies Keycloak authentication middleware. |
| **Technology** | Traefik v3.0 |
| **Config** | `services/gateway/traefik/traefik.yml`, `services/gateway/traefik/dynamic/routes.yml` |
| **Dashboard** | http://localhost:8080/dashboard/ |

### Keycloak (Authentication)

| | |
|---|---|
| **What it does** | Manages users, roles, and OAuth2/OpenID Connect tokens. All protected API routes require a valid JWT Bearer token. |
| **Technology** | Keycloak 26.5.0 |
| **Realm** | `rag-system` (auto-imported from `services/auth/realm-export.json`) |
| **Admin console** | http://localhost:8180 (admin / admin) |

**Seeded test users:**

| Username | Password | Realm roles |
|---|---|---|
| `admin` | `admin` | `system_admin` |
| `reader1` | `reader1` | `reader` |

### PostgreSQL (Primary Database)

| | |
|---|---|
| **What it does** | Stores all structured data: domains, memberships, per-domain RAG config, uploaded document metadata, text chunks (with full-text search index), and query logs. |
| **Technology** | PostgreSQL 16 |
| **Database name** | `domain_db` (default) |

### Redis

| | |
|---|---|
| **What it does** | Celery message broker, Celery result backend, and semantic cache for retrieval and generation results. |
| **Technology** | Redis 5.x (portable on Windows) |

### Qdrant (Vector Database)

| | |
|---|---|
| **What it does** | Stores dense vector embeddings of document chunks. Each domain maps to its own collection. |
| **Technology** | Qdrant v1.12.1 (embedded mode, stored at `data/qdrant`) |
| **Embedding model** | `intfloat/multilingual-e5-small` (384 dimensions) |

### domain-service (port 8001)

Creates knowledge domains, assigns members with roles, stores per-domain RAG settings, and exposes an internal endpoint for other services to verify access.

| Method | Path | Who can call |
|---|---|---|
| POST | `/domains` | `system_admin` |
| GET | `/domains` | Authenticated users |
| POST | `/domains/{id}/members` | `domain_admin` or `system_admin` |
| GET/PATCH | `/domains/{id}/config` | Members with appropriate role |
| POST | `/internal/check-access` | Internal services only (`X-Internal-Key` header) |

### ingestion-service (port 8002)

Accepts PDF uploads for a specific domain. Validates the user's JWT and domain membership (contributor or higher). Saves the file, records metadata in PostgreSQL, and enqueues a background processing job.

**Processing statuses:** `pending` → `processing` → `done` or `failed`

### worker-service (Celery)

Background worker that processes uploaded PDFs:

| Step | Detail |
|---|---|
| Extract | PyMuPDF reads text; Tesseract OCR for scanned pages |
| Chunk | Semantic chunking via `multilingual-e5-small` sentence similarity |
| Embed | Vectors with `passage:` prefix (must match retrieval `query:` prefix) |
| Index Qdrant | One collection per domain |
| Index PostgreSQL | Chunks stored with full-text search vector for BM25 |

### retrieval-service (port 8003)

Given a question and domain ID, finds the most relevant document chunks using a multi-stage hybrid retrieval pipeline:

| Stage | Model / method | Purpose |
|---|---|---|
| Dense search | Qdrant + `multilingual-e5-small` | Semantic similarity |
| Sparse search | PostgreSQL `search_vec` FTS | Exact keywords and acronyms |
| Fusion | Reciprocal Rank Fusion (RRF) | Merge both result lists fairly |
| Reranking | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Re-score top candidates for final quality |
| Cache | Redis | Avoid recomputing for identical queries |

### generation-service (port 8004)

The main "ask a question" service. Orchestrates retrieval, builds a RAG prompt with citations, routes to the configured LLM, caches the answer, and logs the query.

| Route | When used | Provider |
|---|---|---|
| `api` | Default when `GROQ_API_KEY` is set | Groq (`llama-3.3-70b-versatile`) |
| `local` | Domain config set to `local`, or no Groq key | Ollama on host (`llama3.2:3b`) |

### evaluation-service (port 8005, optional)

LLM-as-judge scoring stub. Started with `python run_services.py --evaluation`.

---

## 5. Prerequisites

### Required

| Requirement | Version | Notes |
|---|---|---|
| **Python** | 3.11–3.13 | [Download](https://www.python.org/downloads/). Check "Add Python to PATH". |
| **PostgreSQL** | 16 | [Download](https://www.postgresql.org/download/windows/). Keep default port 5432. |
| **Java** | 17+ | [Adoptium Temurin](https://adoptium.net/). Required for Keycloak. |
| **Groq API key** | Free tier available | [Get one](https://console.groq.com). Primary LLM provider. |
| **RAM** | 8 GB minimum, 16 GB recommended | Embedding and reranking models load into memory |
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
| **Ollama** | Local/offline LLM fallback when Groq is unavailable |
| **curl or Postman** | API testing |

---

## 6. Local Setup (Step-by-Step)

### Step 1 — Python Environment

```powershell
# Create venv (first time only)
python -m venv .venv

# Activate
.venv\Scripts\activate

# Install all dependencies (tested on Python 3.13, Windows 10, CPU)
.venv\Scripts\pip install -r requirements.txt
```

> First install downloads ~2 GB (PyTorch CPU + embedding models cache on first run).
> Allow 10–20 minutes on a laptop.

**Verify Python works:**

```powershell
.venv\Scripts\python.exe -c "import uvicorn, fastapi, redis, celery; print('Python OK')"
```

**What's in `requirements.txt`:**

| Package | Version | Why |
|---|---|---|
| torch | 2.6.0+cpu | CPU-only wheel for Windows, no GPU needed |
| sentence-transformers | 3.3.1 | Embeddings for retrieval + worker |
| fastapi / uvicorn | 0.115.6 / 0.34.0 | Web framework for all services |
| celery / redis | 5.4.0 / 5.2.1 | Task queue — works with Redis 5.x portable |
| qdrant-client | 1.12.1 | Vector search (embedded mode) |
| asyncpg | 0.30.0 | Python 3.13 compatible Postgres driver |
| truststore | 0.10.0 | Fixes HuggingFace SSL on Windows |
| pymupdf | 1.25.2 | PDF text extraction in worker |

### Step 2 — Environment File

```powershell
copy .env.example .env
```

Edit `.env` and set at minimum:

```env
POSTGRES_PASSWORD=1234          # match your local Postgres password
GROQ_API_KEY=gsk_YOUR_KEY_HERE  # get from https://console.groq.com
```

> **Security:** Never commit `.env` to git. It is listed in `.gitignore`.

### Step 3 — PostgreSQL

1. Download PostgreSQL 16 from https://www.postgresql.org/download/windows/
2. Run the installer. Remember the password you set for the `postgres` user.
3. Keep the default port **5432**.
4. Create the database:

```powershell
psql -U postgres -c "CREATE DATABASE domain_db;"
```

**Verify:**

```powershell
.venv\Scripts\python.exe -c "import psycopg2; psycopg2.connect('postgresql://postgres:1234@localhost:5432/domain_db'); print('PostgreSQL OK')"
```

If connection is refused, start the service:

```powershell
net start postgresql-x64-16
```

### Step 4 — Java (required for Keycloak)

Download Java 17+ from https://adoptium.net/ (Temurin) and verify:

```powershell
java -version
```

### Step 5 — Redis

**Option A — Automatic (recommended):** `run_services.py` downloads portable Redis automatically on first run to `tools/redis/`.

**Option B — Manual:**

```powershell
winget install Redis.Redis
redis-server
```

**Verify:**

```powershell
.venv\Scripts\python.exe -c "import redis; r=redis.Redis(host='localhost', port=6379, protocol=2); print('Redis OK:', r.ping())"
```

> This project uses Redis 5.x. The Python `redis` library defaults to RESP3 — we use `protocol=2` for compatibility.

### Step 6 — Keycloak (Authentication)

**Option A — Automatic (recommended):** `run_services.py` downloads Keycloak 26.5.0 automatically on first run to `tools/keycloak/`.

> First download is ~150 MB. If the download fails due to SSL errors, use Option B.

**Option B — Manual download:**

1. Download from https://github.com/keycloak/keycloak/releases/download/26.5.0/keycloak-26.5.0.zip
2. Extract to `tools/keycloak/` (the folder should contain `bin/kc.bat`).
3. Copy the realm config and start:

```powershell
mkdir "tools\keycloak\data\import" -Force
copy "services\auth\realm-export.json" "tools\keycloak\data\import\realm-export.json"

$env:KC_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD="admin"
.\tools\keycloak\bin\kc.bat start-dev --http-port=8180 --import-realm
```

**Verify (wait 30–60 seconds after starting):**

```powershell
curl http://localhost:8180/realms/rag-system
```

### Step 7 — Qdrant (Vector Database)

**No separate install needed.** `run_services.py` uses embedded local Qdrant storage at `data/qdrant/`.

---

## 7. Start The Stack

Once PostgreSQL is running and `.env` is configured:

```powershell
# First time only
copy .env.example .env

# Start all services (Redis + Keycloak auto-downloaded)
python run_services.py
```

### What `run_services.py` Does (in order)

1. **Keycloak** — starts on http://localhost:8180 (or uses existing instance)
2. **Redis** — starts on localhost:6379 (or uses existing instance)
3. **domain-service** — http://localhost:8001
4. **ingestion-service** — http://localhost:8002
5. **retrieval-service** — http://localhost:8003
6. **generation-service** — http://localhost:8004
7. **worker-service** — Celery worker (only with `--worker` flag)

### Flags

```powershell
python run_services.py --worker          # also start Celery ingestion worker
python run_services.py --evaluation      # also start evaluation-service on :8005
python run_services.py --no-reload       # faster startup, no auto-reload
python run_services.py --skip-infra      # skip Redis/Keycloak if already running
```

> If Redis is not running: uses in-memory cache and sync PDF ingestion subprocess.
> If Redis is running: starts Celery worker for async ingestion (with `--worker` flag).

---

## 8. Verify Services

### Health Check

```powershell
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/generate/health
```

All should return `{"status":"ok",...}`.

### API Docs (Swagger UI)

| Service | Docs URL |
|---|---|
| domain-service | http://localhost:8001/docs |
| ingestion-service | http://localhost:8002/docs |
| retrieval-service | http://localhost:8003/docs |
| generation-service | http://localhost:8004/docs |

### End-to-End Test

```powershell
.venv\Scripts\python.exe scripts\timed_e2e_test.py
```

### Gateway Smoke Test

```powershell
cd services/gateway
python smoke_test.py
```

---

## 9. End-to-End API Walkthrough

This section walks through a complete real workflow: **authenticate → create domain → upload PDF → wait for processing → ask a question**.

### 9.1 — Get a JWT Token

**From Keycloak:**

```powershell
$response = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8180/realms/rag-system/protocol/openid-connect/token" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body @{
    client_id  = "admin-cli"
    username   = "admin"
    password   = "admin"
    grant_type = "password"
  }

$TOKEN = $response.access_token
Write-Host "Token acquired ($($TOKEN.Length) chars)"
```

**Bash equivalent:**

```bash
TOKEN=$(curl -s -X POST "http://localhost:8180/realms/rag-system/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" \
  | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

**From dev auth (when Keycloak is not running):**

```powershell
python scripts/dev_auth.py
```

> Tokens expire after 5 minutes (300 seconds). Repeat this step if you receive `401 Unauthorized`.

### 9.2 — Create a Knowledge Domain

```bash
curl -s -X POST "http://localhost/domains" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Knowledge Base",
    "description": "Internal support documents and policies"
  }' | python -m json.tool
```

**PowerShell:**

```powershell
$body = @{
  name        = "Support Knowledge Base"
  description = "Internal support documents and policies"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost/domains" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -ContentType "application/json" `
  -Body $body
```

Save the returned `id` value as `$DOMAIN_ID`. The creator is automatically assigned the `domain_admin` role.

### 9.3 — Upload a PDF Document

```bash
curl -s -X POST "http://localhost/ingest" \
  -H "Authorization: Bearer $TOKEN" \
  -F "domain_id=$DOMAIN_ID" \
  -F "file=@sample.pdf" | python -m json.tool
```

**PowerShell:**

```powershell
$form = @{
  domain_id = $DOMAIN_ID
  file      = Get-Item -Path "C:\path\to\sample.pdf"
}

Invoke-RestMethod -Method Post -Uri "http://localhost/ingest" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -Form $form
```

### 9.4 — Poll Ingestion Status

```bash
curl -s "http://localhost/ingest/$DOCUMENT_ID" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

| Status | Meaning |
|---|---|
| `pending` | Queued, not yet picked up by worker |
| `processing` | Worker is extracting, chunking, and indexing |
| `done` | Ready for queries |
| `failed` | Error occurred; check `error_msg` field |

### 9.5 — Retrieve Chunks Directly (optional)

```bash
curl -s -X POST "http://localhost:8003/api/v1/retrieve" \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": \"What is the refund policy?\",
    \"domain_id\": \"$DOMAIN_ID\",
    \"top_k_retrieve\": 10,
    \"top_k_rerank\": 5
  }" | python -m json.tool
```

### 9.6 — Generate an AI Answer

```bash
curl -s -X POST "http://localhost/generate/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": \"Summarize the refund policy\",
    \"domain_id\": \"$DOMAIN_ID\",
    \"top_k_retrieve\": 10,
    \"top_k_rerank\": 5
  }" | python -m json.tool
```

**PowerShell:**

```powershell
$queryBody = @{
  query          = "Summarize the refund policy"
  domain_id      = $DOMAIN_ID
  top_k_retrieve = 10
  top_k_rerank   = 5
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost/generate/query" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -ContentType "application/json" `
  -Body $queryBody
```

**Expected response:**

```json
{
  "answer": "The refund policy allows returns within 30 days of purchase...",
  "citations": [
    {
      "chunk_id": "chunk-uuid-1",
      "document_id": "f47ac10b-...",
      "page": 3,
      "score": 0.87,
      "text": "Refunds are allowed within 30 days with proof of purchase."
    }
  ],
  "cache_hit": false,
  "llm_route": "api",
  "model": "llama-3.3-70b-versatile"
}
```

### 9.7 — Verify Answer Caching

Run the exact same query again. The response should include `"cache_hit": true` and return much faster.

### 9.8 — Update Domain Configuration (optional)

```bash
curl -s -X PATCH "http://localhost/domains/$DOMAIN_ID/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "llm_route": "api",
    "chunk_size": 512,
    "chunk_overlap": 64,
    "confidence_threshold": 0.5
  }' | python -m json.tool
```

### 9.9 — Assign a Domain Member (optional)

```bash
curl -s -X POST "http://localhost/domains/$DOMAIN_ID/members" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "d3794cbc-9bb9-4c06-95e5-33603c71b287",
    "role": "reader"
  }' | python -m json.tool
```

### 9.10 — Evaluate an Answer (optional)

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

---

## 10. Authentication & Access Control

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

| Action | Required role |
|---|---|
| Create a domain | `system_admin` |
| Upload a PDF | `contributor`, `domain_admin`, or `system_admin` on that domain |
| Query / generate answer | `reader` or higher on that domain, or `system_admin` |
| Manage domain members | `domain_admin` or `system_admin` |
| Update domain config | `domain_admin` or `system_admin` |

### Internal Service-to-Service Calls

Services communicate internally using a shared secret header:

```
X-Internal-Key: <value of INTERNAL_API_KEY in .env>
```

### Dev Auth Fallback

When Keycloak is not running, `run_services.py` automatically uses `scripts/dev_auth.py` for local JWT auth with self-signed keys. Use `python scripts/dev_auth.py` to generate dev tokens.

---

## 11. Environment Variables

All services read from a single root `.env` file. Copy `.env.example` to `.env` and edit.

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | PostgreSQL credentials | `postgres` / `postgres` / `domain_db` |
| `DATABASE_URL` | Async Postgres URL for FastAPI services | `postgresql+asyncpg://postgres:postgres@localhost:5432/domain_db` |
| `SYNC_DATABASE_URL` | Sync Postgres URL for Celery worker | `postgresql://postgres:postgres@localhost:5432/domain_db` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `QDRANT_PATH` | Embedded Qdrant storage path | `data/qdrant` |
| `KEYCLOAK_ISSUER` | JWT issuer URL | `http://localhost:8180/realms/rag-system` |
| `KEYCLOAK_PUBLIC_KEY` | Set automatically by `run_services.py` for dev JWT auth | |
| `INTERNAL_API_KEY` | Shared secret for internal endpoints | `rag-internal-dev-key-change-in-prod` |
| `DOMAIN_SERVICE_URL` | Internal domain-service URL | `http://localhost:8001` |
| `RETRIEVAL_SERVICE_URL` | Internal retrieval-service URL | `http://localhost:8003` |
| `GENERATION_SERVICE_URL` | Internal generation-service URL | `http://localhost:8004` |
| `EVALUATION_SERVICE_URL` | Internal evaluation-service URL | `http://localhost:8005` |
| `GROQ_API_KEY` | Groq cloud LLM key | **Required for cloud generation** |
| `GROQ_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `OLLAMA_BASE_URL` | Local Ollama endpoint | `http://localhost:11434/v1` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2:3b` |
| `TOP_K_RETRIEVE` | Candidates before reranking | `20` |
| `TOP_K_RERANK` | Final chunks sent to LLM | `5` |
| `CACHE_TTL_SECONDS` | Redis cache TTL | `3600` |
| `UPLOAD_DIR` | PDF storage path | `data/uploads` |
| `MAX_SIZE_MB` | Max upload size | `50` |

---

## 12. Troubleshooting

### PostgreSQL — connection refused

- Start PostgreSQL service: `net start postgresql-x64-16`
- Check password in `.env` matches your Postgres install
- Confirm database exists: `psql -U postgres -l`

### Redis — connection refused

- Start Redis manually: `tools\redis\redis-server.exe tools\redis\redis.windows.conf`
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

---

## 13. What Is Not Included

This sprint intentionally does **not** include:

| Item | Status |
|---|---|
| Web chat UI | Planned for a future sprint |
| Production Kubernetes / Helm deployment | Not implemented |
| Advanced evaluation (RAGAS metrics) | Not implemented |
| Graph-based retrieval | Not implemented |
| Audit event streaming | Only basic query logs in PostgreSQL |

The backend is fully operable through the documented HTTP APIs. A future UI would connect to the same endpoints behind Traefik, using Keycloak for login.

---

## 14. Quick Reference Card

```text
Start:      python run_services.py
Start+Work: python run_services.py --worker
Stop:       Ctrl+C
Env setup:  copy .env.example .env
E2E test:   python scripts\timed_e2e_test.py

Keycloak:   http://localhost:8180  (admin / admin)
Token:      POST http://localhost:8180/realms/rag-system/protocol/openid-connect/token

Typical flow:
  1. Get JWT token
  2. POST /domains                          → create domain
  3. POST /ingest                           → upload PDF
  4. GET  /ingest/{document_id}             → wait for "done"
  5. POST /generate/query                   → get AI answer with citations
```

## Directory Layout

```
Chatbot-Fixed-Team2/
├── .env.example                      # environment template (copy to .env)
├── .gitignore                        # comprehensive ignore rules
├── requirements.txt                  # unified Python dependencies
├── run_services.py                   # main launcher (starts everything)
├── Architecture_Decisions.md         # architecture rationale
├── README.md                         # this file — complete project guide
├── data/                             # auto-created runtime data (gitignored)
│   ├── qdrant/                       # embedded vector DB
│   ├── uploads/                      # uploaded PDFs
│   └── dev/                          # dev JWT keys (fallback auth)
├── tools/                            # auto-downloaded infra (gitignored)
│   ├── redis/
│   └── keycloak/
├── scripts/
│   ├── dev_auth.py                   # fallback JWT auth
│   ├── infra_manager.py              # starts Redis + Keycloak
│   ├── memory_cache.py               # in-memory TTL cache (Redis fallback)
│   ├── network_bootstrap.py          # SSL bootstrap for model downloads
│   ├── qdrant_client_factory.py      # Qdrant client helpers
│   └── timed_e2e_test.py             # end-to-end integration test
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

*Last updated: June 2026 — Chatbot-Fixed-Team2 / Fixed Solutions AI Internship*
