# Architecture Steps
## Multi-User Multi-Domain RAG System
**Fixed Solutions AI Internship 2026 | Version 2.0 | June 2026**

---

## System Context

**Deployment target:** Self-hosted, on-premises, company server  
**Development environment:** Team laptops, CPU-only, no GPU  
**Users:** 100–1,000 across internal teams and external customers  
**Domains:** 10–100 isolated knowledge domains  
**Performance target:** P95 query response < 5 seconds end-to-end  
**Architecture style:** Microservices + Web-Queue-Worker hybrid  

---

## Architecture Style

### Microservices + Web-Queue-Worker Hybrid

The system has three distinct workloads that demand independent scaling and clear security boundaries:

1. **User-facing queries** — latency-sensitive, must not block
2. **Document ingestion** — CPU-intensive, async, parallelizable
3. **Data storage and retrieval** — specialized engines per data type

**Why Microservices:**
- Domain isolation is a security boundary, not just a UI concern. Microservices enforce it at the network level.
- RBAC enforcement happens at the service boundary — no risk of accidental bypass inside a shared codebase.
- Each component has a different scaling profile. Ingestion workers scale independently from the query API.
- Multiple developers own different services without stepping on each other.

**Why Web-Queue-Worker inside it:**
- PDF ingestion cannot block an HTTP request. The queue decouples upload acceptance from processing.
- Worker pool scales horizontally — add more workers when ingestion load grows.

---

## Full System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        QUERY PATH                               │
│                                                                 │
│  User Query                                                     │
│      ↓                                                          │
│  API Gateway (Traefik dev / Kong prod)                          │
│      ↓                                                          │
│  Auth + RBAC check (Keycloak JWT validation)                    │
│      ↓                                                          │
│  Semantic Cache (Redis)  ──hit──→  Return cached answer (~50ms) │
│      ↓ miss                                                     │
│  Query Embedding (multilingual-e5-base)              ~400-800ms        │
│      ↓                                                          │
│  Query-time NER                               ~100ms            │
│      ↓                                                          │
│  Retrieval Router                                               │
│  ├── Vector Search (Qdrant)                   ~100ms            │
│  ├── BM25 Search                              ~150ms            │
│  └── Graph Query (Apache AGE) ← NER-activated ~200ms            │
│      ↓                                                          │
│  RRF Fusion                                   ~50ms             │
│      ↓                                                          │
│  Cross-Encoder Re-ranking                     ~500ms            │
│      ↓                                                          │
│  LLM Router                                                     │
│  ├── Sensitive domain → Ollama (Llama 3.2 3B) ~3000ms           │
│  └── General domain  → Groq API              ~500ms             │
│      ↓                                                          │
│  Stream response to user                                        │
│      ↓                                                          │
│  Store in cache + audit log (async)                             │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      INGESTION PATH                             │
│                                                                 │
│  Document Upload                                                │
│      ↓                                                          │
│  Ingestion Service (validates RBAC, stores metadata)            │
│      ↓                                                          │
│  Low-priority Redis Queue                                       │
│      ↓                                                          │
│  Worker Pool (3-4 parallel Celery workers)                      │
│  Each worker:                                                   │
│  ├── Extract text (+ OCR fallback for scanned PDFs)             │
│  ├── Chunk text (configurable size/overlap per domain)          │
│  ├── Batch embed (32 chunks per batch via multilingual-e5-base)        │
│  ├── Upsert vectors to Qdrant (domain-namespaced collection)    │
│  └── Update document status in PostgreSQL                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Service Map

```
┌─────────────────────────────────────────────────────────────────┐
│                     Docker Compose (Dev)                        │
│                   Kubernetes + Helm (Prod)                      │
│                                                                 │
│  ┌──────────┐  HTTPS  ┌──────────────┐    ┌──────────────────┐  │
│  │ React UI │───────▶│ API Gateway   │──▶│    Keycloak      │  │
│  │(Vite)    │         │Traefik(dev)  │    │  (Auth / OIDC)   │  │
│  └──────────┘         │Kong (prod)   │    └──────────────────┘  │
│                       └───────┬──────┘                          │
│                               │                                 │
│          ┌────────────────────┼───────────────────┐             │
│          ▼                    ▼                   ▼             │
│  ┌──────────────┐  ┌─────────────────┐  ┌──────────────────┐    │
│  │   Domain     │  │   Ingestion     │  │   Retrieval      │    │
│  │   Service    │  │   Service       │  │   Service        │    │
│  │  (FastAPI)   │  │   (FastAPI)     │  │   (FastAPI)      │    │
│  └──────┬───────┘  └───────┬─────────┘  └────────┬─────────┘    │
│         │                  │                     │              │
│         ▼                  ▼                     ▼              │
│  ┌──────────────┐  ┌──────────────┐   ┌──────────────────┐      │
│  │  PostgreSQL  │  │    Redis     │   │   Generation     │      │
│  │  + Apache    │  │ Queue+Cache  │   │   Service        │      │
│  │  AGE (graph) │  └──────┬───────┘   │   (FastAPI)      │      │
│  └──────────────┘         │           └────────┬─────────┘      │
│                            ▼                   │                │
│                     ┌──────────────┐           ▼                │
│                     │Celery Worker │   ┌──────────────────┐     │
│                     │Pool (3-4)    │   │  LLM Router      │     │
│                     └──────┬───────┘   │  ├─ Ollama       │     │
│                            │           │  │ (Llama 3.2 3B)│     │
│                            ▼           │  └─ Groq API     │     │
│                     ┌──────────────┐   └──────────────────┘     │
│                     │   Qdrant     │                            │
│                     │(Vector DB)   │                            │
│                     └──────────────┘                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Evaluation Service                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Monorepo Structure

```
/rag-system
  /services
    /gateway              ← Traefik config (dev) + Kong config (prod)
    /auth                 ← Keycloak realm export + config
    /domain-service       ← FastAPI — domains, RBAC assignments, config
    /ingestion-service    ← FastAPI — upload handling, job enqueue
    /worker-service       ← Celery — chunking, embedding, indexing
    /retrieval-service    ← FastAPI — vector, BM25, graph, RRF, reranking
    /generation-service   ← FastAPI — prompt build, LLM routing, streaming
    /evaluation-service   ← FastAPI — Judge LLM (placeholder Sprint 1)
    /ui                   ← React + Vite — chat UI, citation panel
  /shared
    /schemas              ← Pydantic models shared across services
    /auth                 ← JWT validation utilities
    /config               ← Shared environment config patterns
  /infra
    /docker-compose       ← Dev environment with profiles
    /k8s                  ← Kubernetes manifests
    /helm                 ← Helm chart
    /vault                ← HashiCorp Vault policies (prod secrets)
  /docs
    /adrs                 ← This document + per-decision ADRs
    /openapi              ← OpenAPI specs per service
    /diagrams             ← Architecture diagrams
```

---

## Architecture Steps

---

### Step 1 — Monorepo Structure

**Flat service folders**

All services in one repository, each in its own folder at the root `/services` level. Shared code lives in `/shared`.

**Why this approach:**
- Simple to navigate for an intern team across sprints
- Easy to share Pydantic schemas and auth utilities via `/shared`
- Low setup overhead — the team writes code immediately, not tooling config
- Service boundaries are enforced by folder discipline and code review

---

### Step 2A — Development Orchestration

**Docker Compose with Profiles**

Single `docker-compose.yml` with profiles separating core services from optional ones.

```bash
docker compose up                        # core services only
docker compose --profile evaluation up  # adds evaluation service
docker compose --profile full up        # everything
```

**Why this approach:**
- Requirements explicitly specify Docker Compose for dev/staging
- Profiles keep laptop RAM manageable — don't run Judge LLM during UI development
- Zero learning curve — every developer knows Docker Compose
- Fast startup for focused development on one service

---

### Step 2B — Production Orchestration

**Kubernetes + Helm**

Kubernetes manages containers on the company server. Helm packages all configuration into a deployable chart.

**Why this approach:**
- Requirements explicitly specify Kubernetes + Helm for production
- Auto-scaling: add ingestion worker pods when queue depth grows
- Self-healing: crashed services restart automatically
- Rolling deployments: update one service without downtime
- Network policies enforce service-to-service security
- Helm chart = repeatable, version-controlled deployments

---

### Step 3 — API Gateway

**Traefik (development) + Kong (production)**

**Why Traefik for development:**
Auto-discovers services from Docker Compose labels — zero manual routing config when adding a new service. Built-in dashboard shows all routes visually. Lightweight on laptop RAM.

```yaml
# Just add to your service in docker-compose.yml
labels:
  - "traefik.http.routers.ingestion.rule=PathPrefix(`/ingest`)"
  - "traefik.http.routers.ingestion.middlewares=auth@file"
```

**Why Kong for production:**
JWT validation, rate limiting, OIDC integration, and audit logging all happen at the gateway before requests touch business logic. Plugin ecosystem handles enterprise security requirements out of the box.

**Why this split works:**
Both speak standard HTTP. Your services never know which gateway is in front of them. Zero code change when switching environments.

---

### Step 4 — Authentication & RBAC

**Keycloak**

Self-hosted Java-based identity provider. Industry standard for enterprise OIDC/SAML.

**Why this approach:**
Every requirement maps to a native Keycloak concept:

| Requirement | Keycloak Feature |
|---|---|
| Separate internal / external user pools | Realms |
| OIDC + optional SAML | Built-in, zero code |
| Domain Admin, Contributor, Reader roles | Client roles |
| User holds roles across multiple domains | Composite role mappings |
| Configurable session TTL | Per-realm session settings |
| Server-side enforcement | JWT claims validated in each service |
| Brute force protection | Built-in |
| MFA support | Built-in |

Services validate JWTs against Keycloak's public key at startup — no Keycloak call at request time. Stateless and fast.

---

### Step 5 — Primary Database

**PostgreSQL**

Relational database storing users, domains, document metadata, job status, audit logs, and RBAC assignments.

**Why this approach:**
- ACID transactions guarantee data integrity across RBAC and audit operations
- JSONB columns store flexible domain configuration without schema changes
- Apache AGE extension adds graph capabilities to the same instance (Sprint 3)
- pgvector extension available as fallback if needed
- Row-level security adds an extra RBAC enforcement layer
- Best Python ecosystem: SQLAlchemy, Alembic, asyncpg
- GDPR deletion cascades cleanly via foreign key constraints

---

### Step 6 — ORM & Migrations

**SQLAlchemy (async) + Alembic + asyncpg**

**Why this approach:**
- Industry standard — most Python backend tutorials use it; team finds answers easily
- Async support via asyncpg — non-blocking database calls across all services
- Alembic gives a proper audit trail of schema changes across sprints
- Used consistently across all Python microservices — learn once, apply everywhere
- Full control over complex queries when needed

---

### Step 7 — Async Job Queue

**Celery + Redis**

**Why this approach:**
- Redis is already in the stack for semantic caching — one service, two jobs
- Priority queues built-in: high priority for query-time embedding, low priority for document ingestion
- Horizontal scaling: add more workers in Docker Compose or Kubernetes without code changes
- Retry logic and failure handling built-in
- Flower monitoring dashboard available
- Results backend tracks job status (user can poll ingestion progress)

**Priority queue design:**
```
High priority queue → query-time embedding requests
Low priority queue  → document ingestion jobs

Result: User queries always get CPU first.
        Ingestion runs in background without competing.
```

---

### Step 8 — Embedding Model

**intfloat/multilingual-e5-base**

Microsoft's multilingual E5 embedding model optimized specifically for retrieval tasks.

```
Parameters:  278M
Dimensions:  768
Languages:   100+
CPU Speed:   ~150–350ms per query embedding
             ~1–2s per chunk (mitigated by batch embedding)
RAM:         ~800MB–1GB loaded
```

**Why this approach:**
- Specifically optimized for retrieval workloads rather than general semantic similarity
- Supports over 100 languages
- Better multilingual retrieval performance on benchmarks such as MIRACL and Mr.TyDi
- Faster CPU inference compared with MPNet while maintaining strong retrieval quality
- Strong cross-lingual retrieval capabilities
- 768-dimensional embeddings balance retrieval quality and storage efficiency
- Runs fully offline with no external API dependency
- Faster query embeddings help maintain the <5 second P95 latency target

**Implementation note:**

```python
query_embedding = model.encode(
    f"query: {query}",
    normalize_embeddings=True
)

document_embedding = model.encode(
    f"passage: {chunk}",
    normalize_embeddings=True
)
```

**Warning:** This model should remain fixed after deployment. Changing it requires re-embedding all documents and rebuilding vector indexes. Always use `query:` and `passage:` prefixes consistently.



### Step 9 — Vector Database

**Qdrant**

Purpose-built vector database written in Rust. Self-hosted via Docker.

**Why this approach:**
- Payload filtering is first-class — RBAC domain isolation enforced inside the search engine, not in application code after retrieval
- One collection per domain = namespace isolation built into the data model
- Rust-based — fast even on CPU
- Disk offloading keeps RAM low on laptops while supporting 10M+ vectors
- Sparse vector support enables hybrid dense+sparse in one database (future sprint)
- Excellent Python client

**RBAC enforcement pattern:**
```python
results = client.search(
    collection_name=f"domain_{domain_id}",
    query_vector=query_embedding,
    query_filter=Filter(
        must=[FieldCondition(
            key="domain_id",
            match=MatchValue(value=domain_id)
        )]
    ),
    limit=20
)
# Domain isolation enforced at the vector DB level.
# No application-layer post-filtering needed.
```

---

### Step 10A — Local LLM Runtime

**Ollama**

Packages and serves local LLMs via a simple REST API.

**Why this approach:**
- Zero friction — one command to pull and serve any model
- OpenAI-compatible API format — your generation service switches between Ollama and any external API by changing one config value
- Streaming built-in — user sees tokens appearing, improving perceived latency
- Docker image available — runs as a service in Docker Compose
- Swap models with zero code change

**OpenAI-compatible design benefit:**
```python
# Same code, different base_url:
# Local:  base_url="http://ollama:11434/v1"
# Groq:   base_url="https://api.groq.com/openai/v1"
# OpenAI: base_url="https://api.openai.com/v1"
```

**Evolution path:** When the company server gets a GPU, swap Ollama for vLLM — same API format, dramatically higher throughput. Zero code change.

---

### Step 10B — Local Model

**Llama 3.2 3B (Meta)**

```
Parameters:  3B
RAM needed:  ~2GB (4-bit quantized)
CPU Speed:   ~4-6 tokens/sec
Context:     128K tokens
```

**Why this approach:**
- **128K context window** — massive advantage for RAG; can fit far more retrieved chunks into the prompt without truncation
- Strong multilingual capability — aligns with the system's multilingual requirement (50+ languages via Ollama GGUF)
- Meta's most recent efficient small model with strong instruction-following from RLHF training
- For RAG tasks, the model reads retrieved context and synthesizes — the context does the heavy lifting, so a smaller model is more acceptable than in open-ended generation
- Only ~500MB more RAM than Gemma 2 2B for a dramatically larger context window
- Well maintained, large community, extensive Ollama support

**Honest note:** Even Llama 3.2 3B on CPU will struggle with the 5-second budget for long answers. At 4-6 tok/sec, a 200-token answer takes ~33-50 seconds without streaming. Streaming mitigates perceived latency — user sees first token in ~500ms. Full answer may take longer. For sensitive domains where local LLM is required, communicate this tradeoff to stakeholders. The 128K context window is a significant trade for slightly slower generation speed versus Gemma 2 2B.

---

### Step 10C — API LLM Provider

**Groq API**

Inference API running open-source models (Llama 3, Mixtral) on custom LPU hardware at extremely high speed.

**Why this approach:**
- Fastest API response times of any provider — 300+ tokens/sec
- Runs open-source models — more transparent than proprietary APIs
- Llama 3.3 70B available — far higher quality than any local model
- Cheaper than OpenAI for equivalent throughput
- Same OpenAI-compatible API format — zero integration change
- For general non-sensitive domains, this removes the CPU bottleneck entirely

**LLM Routing logic:**
```
Domain config in PostgreSQL:
  llm_route: "local"  → Ollama (Llama 3.2 3B) — sensitive data stays on-premises
  llm_route: "api"    → Groq API (Llama 3.3 70B) — fast, high quality

Router reads domain config at query time.
Switching a domain between routes = one DB update, zero code change.
```

---

### Step 11 — Web Chat UI

**React + Vite**

**Why this approach:**
- Largest ecosystem in frontend development — the team finds answers, tutorials, and community solutions quickly, which is critical for an intern sprint cadence
- shadcn/ui delivers a production-quality chat interface (message bubbles, citation panel, file upload) in hours rather than days of custom CSS work
- Clean SPA architecture keeps the frontend fully decoupled from FastAPI — no routing confusion between React routes and API endpoints
- Vite's HMR keeps the dev feedback loop under one second; essential when iterating on the chat UI across multiple sprints
- No SSR is required — this is an authenticated internal and customer-facing tool; SEO is irrelevant and initial load performance is not a public concern
- The component model maps cleanly onto the chat panel, citation sidebar, domain selector, and admin views

---

### Step 12 — Inter-Service Communication

**Hybrid: REST for sync, Redis for async**

**Why this approach:**
- The query path is latency-sensitive and needs an immediate response — REST keeps it simple, debuggable with curl or Postman, and maps cleanly to OpenAPI specs per service
- Fire-and-forget operations (audit logging, evaluation triggering, cache invalidation) do not need to block the user — Redis pub/sub handles these without adding any latency to the critical query path
- Redis is already in the stack for semantic caching and the Celery job queue — introducing pub/sub adds zero new infrastructure or operational overhead
- The hybrid pattern matches the actual workload model: synchronous where the caller needs the answer immediately, asynchronous where it does not
- A small intern team can debug REST calls trivially; keeping the query path in REST avoids introducing gRPC complexity before the project earns it

---

### Step 13 — BM25 Search Engine

**PostgreSQL Full-Text Search**

Keyword search to complement vector search. Part of the three-signal retrieval.

**Why this approach:**
- Zero new services — PostgreSQL is already in the stack; adding FTS is a schema change, not a new container to operate
- The team already knows SQL — `tsvector` / `tsquery` integrates into existing SQLAlchemy models without learning a new query language
- ACID consistency means document metadata and the search index stay in sync within the same transaction — no eventual-consistency edge cases to debug across services
- Multilingual stemming is built-in via PostgreSQL's `unaccent` and language-specific text search dictionaries, aligned with the system's multilingual requirement
- Sprint 1 goal is a working end-to-end pipeline — PostgreSQL FTS delivers BM25-style keyword search in one migration rather than a multi-day Elasticsearch setup
- The swap path is clear: if retrieval quality demands it in a later sprint, Elasticsearch can be introduced behind the same retrieval service interface with zero breaking changes

---

### Step 14 — Cross-Encoder Re-ranker

**cross-encoder/mmarco-mMiniLMv2-L12-H384-v1**

**Why this approach:**
- The system explicitly requires multilingual support — the English-only alternative would degrade retrieval quality for every non-English query, undermining a core requirement from day one
- mMARCO fine-tuning covers 13+ languages including Arabic, French, Spanish, and German — directly aligned with the expected user base across internal teams and external customers
- The extra ~300ms over the English-only alternative (800ms vs 500ms) fits within the 5-second P95 budget; the full pipeline lands at ~2,000ms before LLM generation, leaving comfortable headroom
- Re-ranking top-20 candidates to top-5 with a cross-encoder dramatically improves the quality of context passed to the LLM — skipping re-ranking entirely saves 500ms but delivers noticeably worse answers, undermining the entire RAG pipeline's value proposition
- Runs entirely inside the retrieval service via sentence-transformers — no new container, no network hop, no extra infrastructure to operate

After RRF fusion, re-ranks top-20 candidates to produce the final top-5 for the LLM. Dramatically improves retrieval accuracy.

---

### Step 15 — Secrets Management

**`.env` files (development) + HashiCorp Vault (production)**

**Why this approach:**
- Development runs on CPU-only team laptops via Docker Compose — adding a Vault server to every developer machine introduces operational overhead with zero security benefit in a local environment
- `.env` files are the standard Docker Compose secret pattern; the entire team already knows them and they work across all services out of the box without any setup
- The hard constraint is that `.env` files must never be committed to Git — enforced via `.gitignore` at the repo root and a pre-commit hook that blocks files containing secret patterns
- Production is a different threat model: the requirements explicitly mention HashiCorp Vault, and an on-premises deployment serving 100–1,000 users warrants dynamic secrets, automatic rotation, and a full audit log of every credential access
- The split is a clean environment boundary, not a compromise — development and production have fundamentally different security requirements
- Vault's Kubernetes integration via the Vault Agent Injector injects secrets into production pods at runtime — no secret ever touches a config file or sits as a plain environment variable in the cluster

---

## Architecture Summary Table

| # | Step | Solution | Why This Is Perfect for the Solution |
|---|---|---|---|
| 1 | Monorepo Structure | Flat service folders | Simple navigation and easy shared code lets the team write code immediately with zero tooling setup |
| 2A | Dev Orchestration | Docker Compose with Profiles | Specified in requirements; profiles keep laptop RAM manageable while keeping startup fast |
| 2B | Production Orchestration | Kubernetes + Helm | Specified in requirements; delivers auto-scaling, self-healing, and repeatable version-controlled deployments |
| 3 | API Gateway | Traefik (dev) + Kong (prod) | Zero-config routing in dev, enterprise-grade security in prod — same HTTP contract means zero code change across environments |
| 4 | Auth & RBAC | Keycloak | Every security requirement maps to a native Keycloak feature; stateless JWT validation means no auth call on the hot query path |
| 5 | Primary Database | PostgreSQL | ACID, JSONB, graph extension (Apache AGE), row-level security, and the best Python ecosystem all in one service |
| 6 | ORM & Migrations | SQLAlchemy async + Alembic | Industry standard with async-native support and a proper schema audit trail across every sprint |
| 7 | Async Job Queue | Celery + Redis | Redis already in stack; built-in priority queues ensure user queries always get CPU before background ingestion |
| 8 | Embedding Model | paraphrase-multilingual-multilingual-e5-base | 50+ language coverage, 768-dim retrieval quality, and batch embedding making CPU ingestion 14x faster |
| 9 | Vector Database | Qdrant | Domain isolation enforced at the DB level via payload filters; Rust performance on CPU with disk offloading for RAM efficiency |
| 10A | Local LLM Runtime | Ollama | One-command model serving with a fully OpenAI-compatible API — swap models or providers with a single config value change |
| 10B | Local Model | Llama 3.2 3B | 128K context window maximizes the number of retrieved chunks the LLM can reason over; multilingual at only ~2GB RAM |
| 10C | API LLM Provider | Groq API | Fastest inference API available; open-source models on LPU hardware remove the CPU bottleneck for all non-sensitive domains |
| 11 | Web Chat UI | React + Vite | Largest ecosystem for fast answers; shadcn/ui delivers a polished chat UI in hours; sub-1s HMR keeps sprint velocity high |
| 12 | Inter-Service Communication | Hybrid REST + Redis | REST keeps the latency-sensitive query path simple and debuggable; Redis pub/sub handles fire-and-forget ops without blocking users |
| 13 | BM25 Search | PostgreSQL FTS | Zero new services; SQL interface the team already knows; ACID consistency with multilingual stemming — all free from existing infra |
| 14 | Cross-Encoder Re-ranker | mmarco-mMiniLMv2-L12-H384-v1 | Multilingual re-ranking that fits the P95 budget; dramatically improves context quality passed to the LLM with no extra container |
| 15 | Secrets Management | .env (dev) + Vault (prod) | Clean environment boundary — simple zero-overhead local dev; dynamic secrets, rotation, and full audit log in production |

---

## Query Latency Budget

```
Total budget: 5,000ms

Step                              Time        Cumulative
─────────────────────────────── ──────────  ──────────
Cache hit (Redis)                ~50ms       → Return immediately
Query embedding (multilingual-e5-base)  ~600ms      600ms
Query-time NER                   ~100ms      700ms
Vector search (Qdrant)           ~100ms      800ms
BM25 search (PostgreSQL FTS)     ~150ms      950ms
Graph query (Apache AGE)         ~200ms      1,150ms
RRF fusion                       ~50ms       1,200ms
Cross-encoder re-ranking         ~800ms      2,000ms
LLM generation (Groq API)        ~800ms      2,800ms  under budget
LLM generation (Ollama local)    ~3,000ms    5,000ms  at limit (streaming)
Response assembly + audit log    ~50ms       final

Streaming mitigates local LLM latency:
  First token visible: ~500ms after query
  Full answer: up to 5,000ms
  Perceived as fast by user
```

---

## Security Architecture

### Zero Trust Between Services
Every service-to-service call carries a service token. No service trusts another purely because it is inside the same network.

### RBAC Enforced at Every Layer
```
Layer 1: API Gateway       → JWT validity check
Layer 2: Each service      → Domain permission check via Domain Service
Layer 3: Qdrant            → Payload filter enforces domain isolation at vector search
Layer 4: PostgreSQL        → Row-level security as final backstop
```

### Data Never Leaves for Sensitive Domains
```
Sensitive domain → Ollama (local) → Data processed on-premises only
General domain   → Groq API       → Non-sensitive data, fast response
```

### Secrets
```
Development: .env files (never committed to Git)
Production:  HashiCorp Vault (dynamic secrets, audit log, rotation)
```

### Network Segmentation
```
External: Only API Gateway has a public port
Internal: Data layer (PostgreSQL, Qdrant, Redis) not exposed outside cluster
```

---

*Document version 2.0*
