# Project State — Finish Implementation Tracker

> **Last Updated:** 2026-06-23  
> **Conversation:** Quality Dashboard Evaluations Fix (059c5cdd)

This document tracks everything that has been completed, what was recently fixed, and what still needs to be done to fully finish the project.

---

## ✅ Completed Work

### Infrastructure & Configuration

- [x] `.env` fully configured with local model paths, performance flags, OCR paths, and evaluation settings
- [x] `.env.example` aligned to mirror all current `.env` keys with safe template placeholders
- [x] `docker-compose.yml` present for Redis setup
- [x] `wsl2_setup_v2.sh` for WSL2 Apache AGE graph database setup (port 5434)
- [x] `requirements.txt` at project root
- [x] `run_services.py` — full multi-service launcher (15 KB)

---

### Database Migrations

- [x] `migrations/setup_all.sql` — unified, idempotent schema for the full system:
  - Sprints 1–4 relational tables: `users`, `domains`, `domain_roles`, `domain_configs`, `documents`, `document_chunks`
  - `rag_query_logs` with extended columns: `citation_chunk_ids`, `retrieval_diagnostics`, `evaluation_status`, `cache_hit`, `correlation_id`
  - Evaluation pipeline tables: `evaluation_logs` (with RAGAS columns + `uq_evaluation_logs_query_judge` unique constraint), `moderation_queue` (with `uq_moderation_queue_query_id`), `live_evaluation_cache`, `eval_cursor`, `audit_logs`
  - Phase 4 graph ontology vertex labels: `Document`, `Form`, `Organization`, `Agency`, `Regulation`, `TaxTerm`, `Date`, `Identifier`, `Requirement`, `Procedure`
  - Phase 4 graph ontology edge labels: `DEFINED_BY`, `REQUIRES`, `APPLIES_TO`, `PART_OF`, `REFERENCES`, `ISSUED_BY`, `HAS_SECTION`
  - `domain_id` indexes for all new vertex types in Apache AGE
  - Seed users, domains, domain_configs, domain_roles, documents, document_chunks, `rag_query_logs` seed row, and `eval_cursor` default row
  - Sequence sync: `SELECT setval('rag_query_logs_id_seq', ...)`
- [x] `migrations/sprint4_ontology_expansion.sql` — standalone Sprint 4 patch
- [x] `migrations/sprint3_foundation.sql`, `sprint2_migration.sql` — historical patches
- [x] `migrations/init_db.sql` — original schema baseline
- [x] `migrations/clear_db.sql` — full teardown script
- [x] `run_migration.py` — smart runner that handles AGE detection, dollar-quote blocks, and dual-database topology (port 5432 + port 5434)
- [x] `clear_database.py` — resets both databases via `clear_db.sql`

---

### Evaluation Service (Full Rewrite — Sprint 4 Fixes)

**All three production issues described in `CHANGES.md` are now fixed:**

#### Fix 1 — Retrieved Context Persistence
- [x] `db/models.py` — `LiveEvaluationCache` model (SHA-256 keyed by query+answer, stores `context_chunks` JSON + optional `reference`)
- [x] `db/queries.py` — `save_live_evaluation_cache()`, `get_cached_context()`, `prune_old_cache_entries()`
- [x] `router.py` — calls `save_live_evaluation_cache()` after every successful `POST /evaluate`
- [x] `tasks/evaluate_batch.py` — calls `get_cached_context()` before each judge, passes `context` and `reference` to RAGAS

#### Fix 2 — Duplicate Evaluations Prevented
- [x] `db/models.py` — `UniqueConstraint("query_id", "model_used")` on `EvaluationLog`, `UniqueConstraint("query_id")` on `ModerationQueueItem`
- [x] `db/queries.py` — `save_evaluation_result()` uses `INSERT ... ON CONFLICT (query_id, model_used) DO NOTHING`, `flag_for_moderation()` uses `INSERT ... ON CONFLICT (query_id) DO NOTHING`

#### Fix 3 — Cursor-Based Evaluation Progress Tracking
- [x] `db/models.py` — `EvalCursor` model (`name` PK, `last_query_id`, `updated_at`)
- [x] `db/queries.py` — `get_cursor()`, `advance_cursor()`, `fetch_sample_query_ids()` (cursor-based, not sliding time window; first-run fallback with `EVAL_LOOKBACK_MINUTES`)
- [x] `tasks/evaluate_batch.py` — reads cursor at start, advances cursor at end

#### Other Evaluation Service Work
- [x] `db/models.py` — `AuditLog` model with `JSONB details`
- [x] `db/queries.py` — `log_audit_event()`, `list_audit_logs()`, `list_evaluation_logs()`, `reset_evaluation_data()`
- [x] `router.py` — `GET /evaluate/logs`, `POST /evaluate/reset`, `GET /evaluate/judge-health`
- [x] `routes/moderation.py` — moderation queue API (`GET /moderation/queue`, `POST /moderation/{id}/decide`, `GET /moderation/audit`)
- [x] `judge.py` — custom LLM judge with `evaluate_answer()`, `check_judge_health()`, `ALLOW_MOCK_JUDGE` flag
- [x] `tasks/ragas_judge.py` — RAGAS 0.4.3 pipeline (Group A: `faithfulness`, `answer_relevancy`; Group B: `context_precision`, `context_recall`, `answer_correctness`, `answer_similarity` when reference available)
- [x] `tasks/evaluate_batch.py` — dual-judge: custom_judge + RAGAS, independent failure, moderation flagging
- [x] `metrics.py` — Prometheus counters and gauges
- [x] `celery_app.py` — Beat schedule (every 30 minutes)
- [x] `config.py`, `schemas.py` — full settings and Pydantic schemas
- [x] `CHANGES.md` — detailed root-cause analysis and rollback strategy

---

### Generation Service

- [x] `router.py` — full query pipeline: retrieval → LLM → logging → async evaluation fire-and-forget
- [x] `ensure_query_log_table()` — auto-creates `rag_query_logs` with all extended columns via `ALTER TABLE IF NOT EXISTS` guards
- [x] `log_query()` — saves `citation_chunk_ids`, `retrieval_diagnostics`, `evaluation_status`, `correlation_id`
- [x] `_submit_evaluation()` — calls `POST /evaluate` on evaluation-service after generation, with `query_id`; updates `evaluation_status` to `completed` or `failed`
- [x] `EVALUATE_ON_GENERATION`, `EVALUATE_SYNC` env flags — generation can trigger live evaluation synchronously or async
- [x] `RETRIEVAL_TIMEOUT_SECONDS` — configurable httpx timeout with proper 504/502 error surfaces
- [x] `llm_router.py` — Groq API + local Ollama routing
- [x] `prompt_builder.py`, `cache.py`, `config.py`, `schemas.py`, `dependencies.py`

---

### Domain Service

- [x] Full CRUD for domains, domain roles, domain configs, members
- [x] `monitoring_router.py` — monitoring metrics endpoint
- [x] `alembic/` migrations for domain service
- [x] Auth via Keycloak or `dev_auth.py` dev mode fallback
- [x] `service.py` — business logic (17 KB)

---

### Retrieval Service

- [x] Embedding model loading via `settings.EMBEDDING_MODEL` (local path)
- [x] Reranker loading via `settings.RERANKER_MODEL` (local path)
- [x] `config.py` — `EMBEDDING_MODEL`, `RERANKER_MODEL`, `AGE_GRAPH_NAME`, `QUERY_NER_MODE`, `GRAPH_ENTITY_MATCH_THRESHOLD`, `RETRIEVAL_WARMUP_ON_START`, `ENABLE_RERANKER`
- [x] Graph retrieval wired (AGE/Cypher queries, entity matching)
- [x] `retrieval_router.py`, `graph_retriever.py`, `query_analyzer.py`
- [x] BM25 + vector + graph hybrid retrieval

---

### Worker Service

- [x] `worker.py` — Celery worker (ingestion pipeline)
- [x] `ner.py` — GLiNER entity extraction using `NER_MODEL` env var (local path)
- [x] `graph_writer.py` — writes entities/relations to AGE graph
- [x] `hf_env.py` — sets `HF_HOME`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` from env before any model loads
- [x] `ontology.py`, `relation_extraction.py` — Phase 4 ontology support
- [x] `tasks/` — ingestion sub-tasks

---

### React Frontend (rag-ui)

- [x] Vite + React + TypeScript + Tailwind
- [x] **All 8 pages implemented:**
  - `LoginPage.tsx`
  - `ChatPage.tsx` — streaming chat with citations
  - `DocumentsPage.tsx` — upload, status tracking, chunk viewer (36 KB)
  - `DomainsPage.tsx` — domain CRUD + member management
  - `AdminPage.tsx` — user registry
  - `MonitoringPage.tsx` — service health + metrics
  - `QualityPage.tsx` — **fully wired Quality Dashboard** (eval logs, moderation queue, audit trail, KPI cards, approve/reject, export, reset)
  - `LogsPage.tsx` — system logs
- [x] `src/lib/api.ts` — full API client with `api`, `domainApi`, `ingestApi`, `generateApi`, `evaluateApi`, `healthApi`, `adminApi`, `monitoringApi`, `qualityApi`
- [x] `src/store/authStore.ts` — Zustand auth store
- [x] `App.tsx` — routing with `RequireAuth` + `RequireRole` guards
- [x] `AppLayout` — navigation layout with sidebar

---

### Documentation

- [x] `README.md` — comprehensive project README (89 KB)
- [x] `IMPLEMENTATION_PLAN_MODELS_GRAPH_EVALUATION.md` — full Phase 1–9 implementation plan (29 KB)
- [x] `services/evaluation-service/CHANGES.md` — evaluation pipeline fix log with verification steps and rollback SQL

---

## ⚠️ Known Remaining Gaps

These items are documented and intentional, but not yet implemented:

### 1. Context for Query Rows Never Scored Live (Partial Fix)
- **Status:** Partially fixed.  
- `LiveEvaluationCache` covers rows that went through `POST /evaluate`. Rows written directly to `rag_query_logs` without calling `/evaluate` still have no recoverable context for RAGAS batch scoring.  
- **Complete fix requires:** `generation-service` persisting a `context_chunks` column into `rag_query_logs` directly.

### 2. RAGAS Group B Metrics Need Reference Answers
- **Status:** Architecture in place, data not available.  
- `context_precision`, `context_recall`, `context_entity_recall`, `answer_correctness`, `answer_similarity` require a ground-truth reference answer. Live production traffic never has one.  
- **Complete fix:** Curated test set, or human reviewers supplying reference answers back through `LiveEvaluationCache.reference`.

### 3. `EvaluationRequest` Missing `reference` Field
- **Status:** One-line schema change deferred.  
- `schemas.py` does not expose `reference` in `EvaluationRequest`. Adding it would let generation-service (or test harnesses) send a ground-truth answer at evaluation time for Group B metrics.

### 4. Per-Domain Evaluation Cursors
- **Status:** Schema supports it, code does not use it.  
- `EvalCursor` uses a single `name="default"` cursor for all domains. Multi-cursor per `domain_id` is possible without a schema change.

### 5. `run_guide.md` — Runner Guide Not Created
- **Status:** Planned but not yet written.  
- A premium markdown guide documenting how to run the full RAG system from scratch on Windows (environment setup, Redis/Keycloak, `clear_database.py`, `run_migration.py`, service startup order, Celery workers, frontend) was planned but not yet created.

### 6. Graph Retrieval — Phase 3 Improvements (Planned)
- **Status:** Basic graph retrieval wired; advanced improvements pending.  
- Query-time NER for retrieval (`QUERY_NER_MODE=rules_first`) is configured in env/config but the rules-first extraction path in `graph_retriever.py` / `query_analyzer.py` may still fall back to exact substring matching for many query patterns.  
- Fuzzy matching (`GRAPH_ENTITY_MATCH_THRESHOLD`) and alias support on graph vertices are planned in Phase 3 but not fully implemented.

### 7. Audit Logging — Phase 9 Incomplete
- **Status:** Evaluation-service audit log works. Generation/retrieval events not audited.  
- Audit events for `query_submitted`, `retrieval_completed`, `graph_used/skipped`, `generation_completed`, `evaluation_scheduled/completed` are planned (Phase 9 of implementation plan) but not yet written in `generation-service` or `retrieval-service`.

### 8. Model Warmup Endpoint (Phase 2)
- **Status:** Config present, implementation may be partial.  
- `RETRIEVAL_WARMUP_ON_START=true` is in `.env` and `run_services.py` calls a warmup. Verify that `POST /api/v1/warmup` in retrieval-service actually pre-loads the embedding + reranker before the first user query.

---

## 🗂️ Key File Reference

| File | Purpose |
|------|---------|
| [`migrations/setup_all.sql`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/migrations/setup_all.sql) | Unified idempotent schema (run via `run_migration.py`) |
| [`run_migration.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/run_migration.py) | Migration runner (relational + AGE graph) |
| [`clear_database.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/clear_database.py) | Full DB teardown + reset |
| [`run_services.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/run_services.py) | Multi-service launcher |
| [`services/evaluation-service/CHANGES.md`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/evaluation-service/CHANGES.md) | Eval pipeline fix log + verification steps |
| [`services/evaluation-service/db/models.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/evaluation-service/db/models.py) | `EvaluationLog`, `LiveEvaluationCache`, `EvalCursor`, `AuditLog` |
| [`services/evaluation-service/db/queries.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/evaluation-service/db/queries.py) | All DB operations for evaluation pipeline |
| [`services/evaluation-service/tasks/evaluate_batch.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/evaluation-service/tasks/evaluate_batch.py) | Scheduled Celery batch evaluation task |
| [`services/evaluation-service/router.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/evaluation-service/router.py) | Live `/evaluate` endpoint |
| [`services/generation-service/router.py`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/services/generation-service/router.py) | Generation pipeline + evaluation fire-and-forget |
| [`rag-ui/src/pages/QualityPage.tsx`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/rag-ui/src/pages/QualityPage.tsx) | Quality Dashboard UI |
| [`rag-ui/src/lib/api.ts`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/rag-ui/src/lib/api.ts) | Frontend API client |
| [`IMPLEMENTATION_PLAN_MODELS_GRAPH_EVALUATION.md`](file:///d:/Personal/Fixed%20Solutions/Project%20Files/v5/IMPLEMENTATION_PLAN_MODELS_GRAPH_EVALUATION.md) | Full Phase 1–9 plan (reference only) |

---

## 🚀 What To Do Next (Priority Order)

1. **Write `run_guide.md`** — premium markdown runner guide for Windows. Highest value for usability.
2. **Add `reference` field to `EvaluationRequest`** — one-line schema change; enables Group B RAGAS metrics for test harnesses.
3. **Persist context in `rag_query_logs`** — add a `context_chunks` JSONB column to `rag_query_logs` in `setup_all.sql` + generation-service `log_query()` so batch evaluation always has context, even for rows that bypassed live `/evaluate`.
4. **Verify warmup endpoint** — confirm `POST /api/v1/warmup` is actually implemented in retrieval-service and `run_services.py` calls it correctly.
5. **Phase 3 graph improvements** — rules-first query NER, alias support on graph vertices, fuzzy matching.
6. **Phase 9 audit logging** — generation and retrieval audit events.
