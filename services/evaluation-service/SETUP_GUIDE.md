# Evaluation Service — Setup & Run Guide (No Docker)

Simple English. Step by step. This guide is for testing the **evaluation
part only** (Part 17.9 in the main project README), separately from the
rest of the chatbot system.

---

## What Changed in This Fix

You asked for 3 things to be fixed. All 3 are done:

| # | Problem | What I did |
|---|---|---|
| 1 | `rag_query_logs` does not store retrieved context or reference answers | Every time the live `POST /evaluate` endpoint runs, it now **saves** the `context_chunks` it receives into a new table called `live_evaluation_cache`. Later, when the scheduled batch job picks up a row from `rag_query_logs`, it looks up that table using the exact `query` + `answer` text and **gets the context back**. |
| 2 | Duplicate evaluation (same answer scored twice) | The database now has a **hard rule** (UNIQUE constraint) that says: one query can only be scored once per judge. Even if the task runs twice by accident, the second attempt is silently skipped — no duplicate row is ever created. |
| 3 | No tracking of the last evaluated record | Added a new table called `eval_cursor`. It remembers the highest `rag_query_logs.id` that has already been evaluated. Each run only looks at rows **newer** than that — no more "look back 35 minutes and hope nothing was missed." |

**Important limit, explained honestly:** the context-saving trick (#1) only
works for answers that were scored *live* through `POST /evaluate` at
least once (this is how `generation-service` is expected to call it).
If a row in `rag_query_logs` was never sent to `/evaluate`, there is still
no way to recover its context after the fact — `rag_query_logs` itself has
no `context` column, and we are not changing `generation-service` in this
fix. If you want 100% reliable context for every single row, the real fix
is to add a `context` column to `rag_query_logs` and have
`generation-service` write to it. That is outside this service.

---

## Part 1 — Files in This Service

```
evaluation-service/
├── .env                     ← copy of .env.example, edit this one
├── .env.example             ← template with comments
├── main.py                  ← FastAPI app (starts /evaluate + /moderation + /metrics)
├── config.py                ← settings
├── judge.py                 ← the custom LLM judge (Groq / Ollama)
├── router.py                ← /evaluate endpoint — NOW also saves context (fix #1)
├── schemas.py                ← request/response models
├── celery_app.py            ← Celery + Beat schedule config
├── metrics.py                ← Prometheus metrics
├── requirements.txt          ← COMPLETE list, install this alone, nothing else needed
├── db/
│   ├── models.py             ← table definitions — 2 NEW tables added (fix #1 and #3)
│   └── queries.py            ← all SQL — cursor logic, dedup, context cache (fixes #1, #2, #3)
├── routes/
│   └── moderation.py         ← /moderation endpoints
└── tasks/
    ├── evaluate_batch.py      ← the scheduled job — now uses the cursor and recovers context
    ├── moderation.py          ← decides if a score is low enough to flag
    └── ragas_judge.py         ← RAGAS metric suite
```

New database tables (created automatically, you don't need to do anything):

- **`live_evaluation_cache`** — holds context chunks from live `/evaluate` calls, waiting to be picked up by the batch job.
- **`eval_cursor`** — one row, remembers "evaluated up to id = X".

---

## Part 2 — Prerequisites

Make sure these are already running (from the main project):

| Service | Port | Needed for |
|---|---|---|
| PostgreSQL | 5432 | `rag_query_logs` table + this service's own tables |
| Redis | 6379 | Celery broker (only needed if you run the batch job — Part 5) |

If you already have the main project running (`python run_services.py`),
both are already up.

---

## Part 3 — Install (One Time)

Open PowerShell in the `evaluation-service` folder.

```powershell
cd evaluation-service

# Create a separate virtual environment just for this service
python -m venv .venv
.venv\Scripts\activate

pip install -U pip
pip install -r requirements.txt
```

> This may take 5–15 minutes the first time — RAGAS pulls in PyTorch CPU
> and sentence-transformers. This is normal.

---

## Part 4 — Configure `.env`

```powershell
copy .env.example .env
notepad .env
```

Edit these two lines to match your real setup:

```ini
GROQ_API_KEY=gsk_your_real_key_here
SYNC_DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/domain_db
```

Everything else already has a working default. Leave `GROQ_API_KEY` empty
if you want to test with Ollama instead (`OLLAMA_BASE_URL` is already set
to `http://localhost:11434/v1`).

---

## Part 5 — Run the Service (3 Terminals)

You need **3 separate PowerShell windows**, all inside `evaluation-service/`
with the venv activated (`.venv\Scripts\activate` in each one).

### Terminal 1 — The API (handles live `/evaluate` calls)

```powershell
python main.py
```

Check it's alive:

```powershell
curl http://localhost:8005/health
curl http://localhost:8005/evaluate/health
```

You should see:
```json
{"status":"ok","service":"evaluation-service"}
```

### Terminal 2 — Celery Worker (runs the scheduled batch job)

```powershell
celery -A celery_app worker --loglevel=info --pool=solo --queues=evaluation
```

> `--pool=solo` is needed on Windows (same as the rest of the project).
> Without `--queues=evaluation`, the worker will never pick up the task.

You should see:
```
[tasks]
  . tasks.evaluate_batch.evaluate_recent_answers
```

### Terminal 3 — Celery Beat (the scheduler/clock)

```powershell
celery -A celery_app beat --loglevel=info
```

You should see:
```
beat: Starting...
Scheduler: Sending due task evaluate-recent-answers
```

By default, Beat fires the batch job every **30 minutes**. You don't have
to wait — see Part 7 to trigger it manually.

---

## Part 6 — Test the Live `/evaluate` Endpoint

This is what `generation-service` calls every time it answers a question.

```powershell
Invoke-RestMethod -Method POST http://localhost:8005/evaluate `
-Headers @{"Content-Type"="application/json"} `
-Body '{
  "query": "What is the capital of France?",
  "answer": "The capital of France is Paris.",
  "context_chunks": [
    "France is a country in Western Europe. Its capital city is Paris."
  ]
}'
```

Expected response:
```json
{
  "score": 0.95,
  "explanation": "The answer directly and accurately addresses the question...",
  "route_used": "api",
  "model": "llama-3.3-70b-versatile"
}
```

**Check the context was saved (fix #1):**

```powershell
$env:PGPASSWORD="your_postgres_password"
psql -U postgres -d domain_db -c "SELECT query, context_chunks, consumed FROM live_evaluation_cache ORDER BY created_at DESC LIMIT 5;"
```

You should see your `context_chunks` saved there, with `consumed = f`
(false) — meaning the batch job hasn't picked it up yet.

---

## Part 7 — Trigger the Batch Job Manually (Don't Wait 30 Minutes)

```powershell
python -c "
from tasks.evaluate_batch import evaluate_recent_answers
result = evaluate_recent_answers.delay()
print('Task ID:', result.id)
import time; time.sleep(5)
print('Result:', result.get(timeout=180))
"
```

Or, simpler, from Terminal 2's machine (no need for `.delay()` / Redis at
all — runs the exact same code directly):

```powershell
python -c "
from tasks.evaluate_batch import evaluate_recent_answers
print(evaluate_recent_answers.run())
"
```

Expected output looks like:
```python
{'evaluated': 3, 'flagged_for_review': 1, 'cursor': 42, 'cache_rows_pruned': 0}
```

- `cursor` is the highest `rag_query_logs.id` now marked as done (fix #3).
- Run it again immediately — `evaluated` should now be `0`, because
  nothing new exists past the cursor (fix #2 + #3 working together).

**Check dedup worked (fix #2):**
```powershell
psql -U postgres -d domain_db -c "SELECT query_id, model_used, count(*) FROM evaluation_logs GROUP BY query_id, model_used HAVING count(*) > 1;"
```
This should always return **0 rows** — if it ever returns something, that
would mean a duplicate slipped through (it shouldn't, the database itself
blocks it).

**Check the cursor (fix #3):**
```powershell
psql -U postgres -d domain_db -c "SELECT * FROM eval_cursor;"
```

---

## Part 8 — Moderation Queue API

### View pending items
```powershell
curl http://localhost:8005/moderation/queue
```

### Approve or reject an item
```powershell
Invoke-RestMethod -Method POST http://localhost:8005/moderation/123/decide `
-Headers @{"Content-Type"="application/json"} `
-Body '{
  "decision": "rejected",
  "reviewer": "your_name@company.com",
  "notes": "Answer is incomplete."
}'
```

---

## Part 9 — Metrics (Prometheus + Grafana)

The service exposes Prometheus metrics at:

```powershell
curl http://localhost:8005/metrics
```

Key metrics:

| Metric | Meaning |
|---|---|
| `evaluation_runs_total` | How many batch runs have completed |
| `evaluation_rows_evaluated_total` | Total rows scored, across all runs |
| `evaluation_rows_flagged_total` | How many ended up in moderation |
| `evaluation_judge_latency_seconds` | How long each judge call takes (per judge: `custom_judge` / `ragas`) |
| `evaluation_latest_overall_score{judge="ragas"}` | Last score from RAGAS |
| `evaluation_latest_overall_score{judge="custom_judge"}` | Last score from the custom judge |
| `moderation_queue_pending_items` | Current backlog waiting for human review |
| `http_request_duration_seconds` | API latency (automatic) |

### Step-by-step: connect Grafana (no Docker)

1. **Install Prometheus** (if you don't have it):
   - Download from [prometheus.io/download](https://prometheus.io/download/)
   - Unzip it anywhere, e.g. `C:\prometheus`

2. **Edit `prometheus.yml`** (inside the Prometheus folder) and add:
   ```yaml
   scrape_configs:
     - job_name: evaluation-service
       static_configs:
         - targets: ['localhost:8005']
       metrics_path: /metrics
   ```

3. **Start Prometheus:**
   ```powershell
   cd C:\prometheus
   .\prometheus.exe --config.file=prometheus.yml
   ```
   It runs on **http://localhost:9090**. Check it's pulling data:
   go to http://localhost:9090/targets — `evaluation-service` should show
   as **UP**.

4. **Install Grafana** (if you don't have it):
   - Download from [grafana.com/grafana/download](https://grafana.com/grafana/download/?platform=windows)
   - Run the installer, then start it (it installs as a Windows service,
     or run `grafana-server.exe` from the `bin` folder manually).

5. **Open Grafana:** http://localhost:3000 (default login: `admin` / `admin`,
   it will ask you to change the password on first login).

6. **Add Prometheus as a data source:**
   - Left menu → **Connections** → **Data sources** → **Add data source**
   - Choose **Prometheus**
   - URL: `http://localhost:9090`
   - Click **Save & test** — should say "Successfully queried the Prometheus API."

7. **Build a dashboard:**
   - Left menu → **Dashboards** → **New** → **New Dashboard** → **Add visualization**
   - Pick the **Prometheus** data source
   - In the query box type a metric name, e.g. `evaluation_rows_evaluated_total`
   - Click **Run query** — you should see your data
   - Repeat for the other metrics in the table above (one panel per metric is normal)
   - Click **Save dashboard** and give it a name like "Evaluation Service"

8. Suggested alert ideas (set these up under **Alerting** in Grafana once you're comfortable with panels):
   - `evaluation_rows_flagged_total / evaluation_rows_evaluated_total > 0.3` → more than 30% of answers are low quality
   - `moderation_queue_pending_items > 50` → review backlog is piling up
   - `evaluation_runs_total` not increasing for a long time → Beat or Worker has stopped

---

## Part 10 — Common Problems

### "ImportError: cannot import name 'evaluate_answer' from 'judge'"
Already fixed — `judge.py` exports both `JudgeService` (for the live
endpoint) and `evaluate_answer()` (for the batch job).

### "No module named 'db'" / "'tasks'" / "'routes'"
Already fixed — each folder has an `__init__.py`.

### "404 on /moderation/queue"
Already fixed — `main.py` mounts both `/evaluate` and `/moderation` routers.

### RAGAS crashes: "No module named 'langchain_community.chat_models.vertexai'"
Make sure `langchain-community==0.3.27` got installed exactly as pinned
in `requirements.txt`. If you installed packages one at a time instead of
`pip install -r requirements.txt`, this pin can get silently overwritten
by a newer version pulled in by `ragas`. Fix:
```powershell
pip install langchain-community==0.3.27
```

### "0 rows sampled" / "evaluated: 0" on every batch run
This is usually **correct, not a bug**. Check:
1. Are there new rows in `rag_query_logs` with `id` greater than the
   current cursor? Check: `SELECT * FROM eval_cursor;` then
   `SELECT count(*) FROM rag_query_logs WHERE id > (cursor value);`
2. `EVAL_SAMPLE_RATE` defaults to `0.05` (5%) — with only a handful of
   rows, random sampling can easily pick zero. Temporarily raise it in
   `.env` (e.g. `EVAL_SAMPLE_RATE=1.0`) while testing, then put it back.
3. Restart the worker/Beat after changing `.env` — they only read it once at startup.

### Beat fires the task but Worker shows nothing
Make sure the worker is listening on the right queue:
```powershell
celery -A celery_app worker --loglevel=info --pool=solo --queues=evaluation
```
Without `--queues=evaluation` it listens on the default `celery` queue
and never sees this task.

### Same query keeps getting evaluated more than once
It shouldn't — there's a database-level UNIQUE constraint on
`(query_id, model_used)` now. If you ever see duplicates, check:
```powershell
psql -U postgres -d domain_db -c "SELECT query_id, model_used, count(*) FROM evaluation_logs GROUP BY query_id, model_used HAVING count(*) > 1;"
```
If this returns rows, something bypassed `db/queries.py`'s
`save_evaluation_result()` function directly — that function is the only
place that should ever write to `evaluation_logs`.

### Context is still missing for some rows
That's expected for rows that were never sent through `POST /evaluate`
live. Check whether the row was scored live:
```powershell
psql -U postgres -d domain_db -c "SELECT * FROM live_evaluation_cache WHERE query = 'the exact question text';"
```
If nothing comes back, that query+answer was never scored live, so there
is genuinely nothing to recover — this is the structural limit explained
at the top of this guide.

---

## Part 11 — Quick Reference Card

```text
Install:        cd evaluation-service && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
Configure:       copy .env.example .env   (then edit GROQ_API_KEY and SYNC_DATABASE_URL)

Run (3 terminals, all with venv activated):
  Terminal 1:   python main.py
  Terminal 2:   celery -A celery_app worker --loglevel=info --pool=solo --queues=evaluation
  Terminal 3:   celery -A celery_app beat --loglevel=info

Test live:       curl http://localhost:8005/evaluate ...   (see Part 6)
Trigger batch:   python -c "from tasks.evaluate_batch import evaluate_recent_answers; print(evaluate_recent_answers.run())"
View metrics:    curl http://localhost:8005/metrics
Moderation UI:   curl http://localhost:8005/moderation/queue

Useful SQL:
  SELECT * FROM eval_cursor;                                          -- where the batch job is up to
  SELECT * FROM live_evaluation_cache ORDER BY created_at DESC;       -- recently cached context, waiting or used
  SELECT * FROM evaluation_logs ORDER BY evaluated_at DESC LIMIT 10;  -- recent scores
  SELECT * FROM moderation_queue WHERE status = 'pending';            -- backlog
```

---

*This guide covers the evaluation-service only. For the full project
(domain-service, ingestion, retrieval, generation, worker, frontend), see
the main project `README.md`.*
