# Evaluation Service — Production Fixes

## Root Cause Analysis

### Issue 1 — Missing Retrieved Context

**Root cause:**  
`rag_query_logs` has no `context` column — the schema only stores  
`id, domain_id, user_id, query, answer, llm_route, model, created_at`.  
When the live `POST /evaluate` endpoint ran, it received `context_chunks`  
from generation-service, scored the answer, and then **threw the context  
away**. The scheduled batch job (`evaluate_recent_answers`) therefore  
always called both judges with `context=None`, making RAGAS `faithfulness`  
impossible to compute and rendering every Group B metric (`context_precision`,  
`context_recall`, etc.) permanently `None` even for rows that had been  
scored live with real retrieved chunks.

**Fix:**  
`LiveEvaluationCache` table (new) — every time `POST /evaluate` succeeds,  
`router.py` calls `db.queries.save_live_evaluation_cache()`, which upserts  
the `context_chunks` (and optional `reference`) keyed by  
`SHA-256(query + "\u241f" + answer)`. When `evaluate_batch.py` later  
samples that row from `rag_query_logs`, it calls `get_cached_context(query, answer)`,  
computes the same hash, and recovers the real context before calling either  
judge. Rows that were never scored live still return `(None, None)` and  
behave exactly as before.

---

### Issue 2 — Duplicate Evaluations

**Root cause:**  
`save_evaluation_result()` used a plain `INSERT` (no conflict guard).  
`flag_for_moderation()` used a Python `if already_exists: return` pre-check  
with no database-level guard. Under any of the following conditions a  
duplicate row could be created:

- A Celery task is retried after a partial failure (the first attempt  
  committed the row, the retry re-inserted it).  
- Two Celery workers run the same batch simultaneously (both pass the  
  Python pre-check before either commits).  
- A manual trigger overlaps with a scheduled Beat run.

**Fix:**  
`EvaluationLog` now has `UniqueConstraint("query_id", "model_used")` at  
the **database level**. `save_evaluation_result()` uses  
`INSERT … ON CONFLICT (query_id, model_used) DO NOTHING` — a true  
database-level upsert with no race window. `flag_for_moderation()` does  
the same with `UniqueConstraint("query_id")` on `moderation_queue`.  
On conflict, both functions return the existing row's ID so callers  
still have a usable value.

---

### Issue 3 — Evaluation Progress Tracking

**Root cause:**  
The old `fetch_sample_query_ids()` used  
`WHERE created_at >= NOW() - INTERVAL '35 minutes'` on every run. This  
sliding time window had two failure modes:

1. **Miss:** If a run was delayed by more than 35 minutes, rows written  
   during the gap were never evaluated (they fell outside the window by  
   the time the next run started).
2. **Re-scan:** Every run re-checked rows already evaluated, relying  
   solely on the `NOT EXISTS` guard to skip them — wasting DB resources  
   proportional to the size of `rag_query_logs`, not just the size of  
   new traffic.

**Fix:**  
`EvalCursor` table (new) — a single row `name="default"` stores  
`last_query_id`, the highest `rag_query_logs.id` fully processed.  
`fetch_sample_query_ids()` now uses `WHERE id > cursor`.  
`advance_cursor()` is called at the end of each run (only if  
`max_id_seen > cursor_before`) with `SELECT … FOR UPDATE` to prevent  
concurrent writes from regressing the cursor.  
First-run fallback: when `cursor == 0`, a time bound of  
`EVAL_LOOKBACK_MINUTES` is still applied so a fresh install doesn't  
process years of history in one shot.

---

## Modified Files

| File | Action | Reason |
|------|--------|--------|
| `db/models.py` | MODIFY | Added `LiveEvaluationCache`, `EvalCursor`; `UniqueConstraint` on `EvaluationLog` and `ModerationQueueItem`; composite index on `evaluation_logs` |
| `db/queries.py` | MODIFY | Added `save_live_evaluation_cache()`, `get_cached_context()`, `prune_old_cache_entries()`; upsert in `save_evaluation_result()` and `flag_for_moderation()`; cursor-based `fetch_sample_query_ids()` |
| `tasks/evaluate_batch.py` | MODIFY | Now calls `get_cached_context()` before each judge; passes `context` and `reference` to RAGAS; uses upsert-aware `save_evaluation_result()`; advances cursor at end of run |
| `router.py` | MODIFY | Calls `save_live_evaluation_cache()` after every successful evaluation |
| `tasks/moderation.py` | NO CHANGE | Logic unchanged |
| `tasks/ragas_judge.py` | NO CHANGE | Already targets ragas==0.4.3 API correctly |
| `judge.py` | NO CHANGE | Already has `evaluate_answer()` function needed by batch |
| `main.py` | NO CHANGE | Already mounts both routers |
| `celery_app.py` | NO CHANGE | Schedule unchanged |
| `config.py` | NO CHANGE | All env vars already present |
| `schemas.py` | NO CHANGE | All models already present |
| `metrics.py` | NO CHANGE | Metrics already defined correctly |

## New Files

| File | Purpose |
|------|---------|
| `db/models.py` (additions) | `LiveEvaluationCache`, `EvalCursor` models |
| `migrations/001_evaluation_pipeline_fixes.sql` | One-shot SQL migration |
| `CHANGES.md` | This file |

---

## Database Migration Order

Run **before** deploying new code:

```bash
psql "$SYNC_DATABASE_URL" -f migrations/001_evaluation_pipeline_fixes.sql
```

The script is **idempotent** — all statements use `IF NOT EXISTS` /  
`ON CONFLICT DO NOTHING` and can be re-run safely.

Migration steps in order:
1. Create `live_evaluation_cache` + indexes  
2. De-duplicate existing `evaluation_logs` rows (keeps oldest per pair)  
3. Add `UniqueConstraint(query_id, model_used)` to `evaluation_logs`  
4. Add RAGAS metric columns to `evaluation_logs`  
5. De-duplicate existing `moderation_queue` rows  
6. Add `UniqueConstraint(query_id)` to `moderation_queue`  
7. Create `eval_cursor` + seed default row at `last_query_id = 0`

---

## Deployment Order

```
1. Run migration (migrations/001_evaluation_pipeline_fixes.sql)
2. Stop Celery worker + Celery Beat
3. Deploy new evaluation-service code
4. Start FastAPI app  (uvicorn / docker-compose up evaluation-service)
5. Start Celery worker (celery -A celery_app worker -Q evaluation)
6. Start Celery Beat   (celery -A celery_app beat)
```

The FastAPI app and the Celery worker/Beat are independent OS processes —  
start them in the order above to avoid the batch job running against old  
code while the migration is still applying.

---

## Rollback Strategy

### Database rollback

```sql
-- Rollback Fix 1
DROP TABLE IF EXISTS live_evaluation_cache;

-- Rollback Fix 2 (evaluation_logs constraint + columns)
ALTER TABLE evaluation_logs
    DROP CONSTRAINT IF EXISTS uq_evaluation_logs_query_judge,
    DROP COLUMN IF EXISTS ragas_context_precision,
    DROP COLUMN IF EXISTS ragas_context_recall,
    DROP COLUMN IF EXISTS ragas_context_entity_recall,
    DROP COLUMN IF EXISTS ragas_answer_correctness,
    DROP COLUMN IF EXISTS ragas_answer_similarity;
DROP INDEX IF EXISTS ix_evaluation_logs_query_model;

-- Rollback Fix 2 (moderation_queue constraint)
ALTER TABLE moderation_queue
    DROP CONSTRAINT IF EXISTS uq_moderation_queue_query_id;

-- Rollback Fix 3
DROP TABLE IF EXISTS eval_cursor;
```

### Code rollback
Redeploy the previous Docker image / git tag and restart the services in  
the same order as the deployment (Beat → Worker → FastAPI). The rollback  
SQL above must run **before** starting old code against the modified schema,  
or the old plain-INSERT code will violate the new unique constraints.

---

## Verification Steps

### Verify Fix 1 — Retrieved context is persisted correctly

**Step 1 — trigger a live evaluation:**
```bash
curl -s -X POST http://localhost:8005/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "query":  "What is the capital of France?",
    "answer": "Paris is the capital of France.",
    "context_chunks": ["France is a country in Europe. Its capital city is Paris."]
  }' | python -m json.tool
```

Expected: HTTP 200 with `score`, `explanation`, `route_used`, `model`.

**Step 2 — check the cache row was written:**
```sql
SELECT cache_key,
       LEFT(query, 40)  AS query,
       LEFT(answer, 40) AS answer,
       context_chunks,
       consumed,
       created_at
FROM   live_evaluation_cache
ORDER  BY created_at DESC
LIMIT  5;
```

Expected: one row with `consumed = false` and `context_chunks` matching  
the `context_chunks` you sent above.

**Step 3 — trigger the batch job manually:**
```bash
celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers
```

**Step 4 — check context was used:**
```sql
SELECT cache_key, consumed
FROM   live_evaluation_cache
ORDER  BY created_at DESC
LIMIT  5;
```

Expected: `consumed = true` for the row written in Step 1 (if the  
corresponding `rag_query_logs` row was sampled by the batch job).

---

### Verify Fix 2 — Duplicate evaluations are prevented

**Step 1 — run the batch job twice:**
```bash
celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers
celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers
```

**Step 2 — count rows per (query_id, model_used):**
```sql
SELECT query_id, model_used, COUNT(*) AS cnt
FROM   evaluation_logs
GROUP  BY query_id, model_used
HAVING COUNT(*) > 1;
```

Expected: **0 rows** (no duplicates). If any row appears here, the  
`UniqueConstraint` is not in place — re-run the migration.

**Step 3 — verify the constraint exists:**
```sql
SELECT conname, contype
FROM   pg_constraint
WHERE  conrelid = 'evaluation_logs'::regclass
  AND  conname  = 'uq_evaluation_logs_query_judge';
```

Expected: one row with `contype = 'u'` (unique).

**Step 4 — same for moderation_queue:**
```sql
SELECT conname, contype
FROM   pg_constraint
WHERE  conrelid = 'moderation_queue'::regclass
  AND  conname  = 'uq_moderation_queue_query_id';
```

Expected: one row with `contype = 'u'`.

---

### Verify Fix 3 — Evaluation progress tracking works correctly

**Step 1 — check cursor before any run:**
```sql
SELECT name, last_query_id, updated_at
FROM   eval_cursor;
```

Expected: one row, `name = 'default'`, `last_query_id = 0` (on a fresh  
database) or a non-zero value from a previous run.

**Step 2 — note the current max id in rag_query_logs:**
```sql
SELECT MAX(id) AS max_id FROM rag_query_logs;
```

**Step 3 — run the batch job:**
```bash
celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers
```

**Step 4 — check cursor advanced:**
```sql
SELECT name, last_query_id, updated_at
FROM   eval_cursor;
```

Expected: `last_query_id` is now ≥ the `max_id` from Step 2 (it will  
equal the highest id that was actually sampled this run, which may be  
lower if sampling rate < 1.0).

**Step 5 — verify no row is evaluated twice by running again:**
```bash
celery -A celery_app call tasks.evaluate_batch.evaluate_recent_answers
```

In the Celery worker logs you should see:
```
evaluate_recent_answers start — 0 rows sampled (cursor was id > <N>)
```
(0 rows because all rows up to the cursor were already evaluated, and no  
new rows arrived since the last run.)

---

### Verify existing functionality is operational

**Health checks:**
```bash
curl http://localhost:8005/health
curl http://localhost:8005/evaluate/health
curl http://localhost:8005/moderation/health
```

All should return `{"status": "ok", ...}`.

**Moderation queue:**
```bash
curl http://localhost:8005/moderation/queue
```

Expected: `{"count": N, "items": [...]}` — no 404.

**Metrics endpoint:**
```bash
curl http://localhost:8005/metrics | grep evaluation_
```

Expected: lines for `evaluation_runs_total`, `evaluation_rows_evaluated_total`,  
`evaluation_rows_flagged_total`, `evaluation_latest_overall_score`,  
`evaluation_judge_latency_seconds`.

---

## What Is Still NOT Done (Requires generation-service changes)

The following gaps remain and require changes **outside** this service:

1. **Context for rows never scored live.**  
   `LiveEvaluationCache` only helps rows that went through `POST /evaluate`  
   at answer time. Rows written directly to `rag_query_logs` without calling  
   `/evaluate` still have no recoverable context. The complete fix is for  
   generation-service to persist `context_chunks` into `rag_query_logs`  
   as a new column.

2. **Reference answers for live traffic.**  
   RAGAS Group B metrics (`context_precision`, `context_recall`,  
   `context_entity_recall`, `answer_correctness`, `answer_similarity`) require  
   a ground-truth reference answer. Live production traffic never has one.  
   These metrics will remain `NULL` for all production rows until:  
   (a) a curated test set is run through the evaluation pipeline, or  
   (b) human reviewers in the moderation queue supply reference answers  
   that are fed back into the `LiveEvaluationCache.reference` column.

3. **`EvaluationRequest` has no `reference` field.**  
   `schemas.py` defines `EvaluationRequest` without a `reference` field.  
   Adding it would allow generation-service (or test harnesses) to send a  
   ground-truth answer at evaluation time and have it cached for RAGAS  
   Group B metrics — a one-line schema change plus a generation-service  
   integration update.

4. **Per-domain cursors.**  
   `EvalCursor` uses a single `name="default"` cursor for all  
   `rag_query_logs` rows. If you later want to evaluate different domains  
   at different rates or schedules, add a second cursor row per  
   `domain_id` — the schema already supports it (the `name` column is the  
   PK, not a single-column table).
