# Operational Runbook

**Project:** Multi-Domain RAG System  
**Version:** 1.0  
**Last Updated:** June 2026

This document provides step-by-step procedures for diagnosing and recovering from operational failures in the multi-domain RAG system.

---

## 1. Celery Worker Down

### Symptom
- Uploaded files remain stuck in the `pending` state indefinitely.
- No chunks are created in the database.
- Celery logs are inactive or worker process is not visible in task list.

### Diagnosis Steps
1. **Check Worker Logs:** Inspect logs to determine if the worker is running and connected to Redis:
   ```bash
   tail -n 100 logs/worker.log
   ```
2. **Check Celery Process:** On Windows, run the following PowerShell command to check if the worker process is running:
   ```powershell
   Get-Process -Name python | Where-Object { $_.CommandLine -like "*celery*" }
   ```
3. **Verify Redis Connection:** Ensure the worker can reach the Celery broker (Redis):
   ```powershell
   Test-NetConnection -ComputerName localhost -Port 6379
   ```

### Recovery Procedure
1. If the worker process is crashed or unresponsive, terminate it:
   ```powershell
   Stop-Process -Name python -Force
   ```
2. Restart the worker process from the project root:
   ```powershell
   .venv\Scripts\python.exe run_services.py --worker --skip-infra
   ```
3. Monitor startup logs to confirm the worker connects successfully and registers task handlers (`services.worker_service.tasks`).

---

## 2. Redis Cache & Broker Down

### Symptom
- API requests for ingestion fail immediately with broker connection errors.
- Monolith logs show `ConnectionError: Error connecting to localhost:6379`.
- Rate limiting and session caching fail.

### Diagnosis Steps
1. **Check Redis Service Status:**
   ```powershell
   Get-Service -Name Redis
   ```
   *(If running via Docker/WSL, check container state)*
   ```bash
   docker ps | grep redis
   ```
2. **Ping Redis:**
   ```powershell
   redis-cli ping
   ```
   Expected response: `PONG`.

### Recovery Procedure
1. **Restart Redis Service:**
   - If installed as a Windows service:
     ```powershell
     Restart-Service -Name Redis -Force
     ```
   - If running via Docker Compose:
     ```bash
     docker compose -f monitoring/docker-compose.monitoring.yml restart redis
     ```
2. **Verify Memory Usage:** If Redis crashed due to Out-Of-Memory (OOM), inspect memory configuration and purge expired keys:
   ```powershell
   redis-cli info memory
   ```
3. **Purge Celery Queues (Optional):** If the broker queue is corrupted, purge all pending tasks:
   ```powershell
   .venv\Scripts\celery.exe -A services.worker_service purge -f
   ```

---

## 3. PostgreSQL Database Outage

### Symptom
- Monolith API fails to start or throws 500 errors on all database queries.
- Logs show connection timeouts or `psycopg2.OperationalError: connection to server on socket "/tmp/.s.PGSQL.5434" failed`.

### Diagnosis Steps
1. **Check Database Port Binding:**
   ```powershell
   Get-NetTCPConnection -LocalPort 5434
   ```
2. **Test DB Credentials Connection:**
   ```powershell
   psql -h localhost -p 5434 -U postgres -d domain_db -c "SELECT 1;"
   ```

### Recovery Procedure
1. **Restart PostgreSQL:**
   - If running via standard Windows PostgreSQL service:
     ```powershell
     Restart-Service -Name postgresql-x64-17
     ```
2. **Check for DB Lockups:** If PostgreSQL is running but rejecting connections, check active connection count and terminate orphaned backends:
   ```sql
   SELECT pid, age(clock_timestamp(), query_start), substring(query,1,40) 
   FROM pg_stat_activity 
   WHERE state != 'idle' AND age(clock_timestamp(), query_start) > interval '5 minutes';
   ```
3. **Re-run Setup Migrations (If Schema Corrupted):**
   ```powershell
   .venv\Scripts\python.exe scripts/run_migration.py
   ```

---

## 4. LLM API Rate Limits or Outage

### Symptom
- Query answering returns 503 Service Unavailable or 429 Too Many Requests.
- p95 latency spikes dramatically.
- Logs show `HTTPError: 503 Server Error` from the Groq API client.

### Diagnosis Steps
1. **Verify API Credentials & Route:** Inspect `.env` to verify LLM settings:
   ```powershell
   Get-Content .env | Select-String "LLM_"
   ```
2. **Check Provider Health:** Ping the LLM provider API endpoint directly:
   ```powershell
   curl -I https://api.groq.com/openai/v1/models
   ```

### Recovery Procedure
1. **Switch to Local LLM Fallback (Ollama):** If Groq is down or rate-limited, switch the domain RAG config route to `local` via the UI Domain Setup panel.
2. **Verify Ollama Status:** Ensure Ollama is running locally and the target model is pulled:
   ```powershell
   ollama list
   ```
3. **Adjust Backoff / Retry Configs:** Ensure backend service environment has rate-limit retries enabled:
   ```env
   LLM_MAX_RETRIES=3
   LLM_RETRY_BACKOFF_FACTOR=2.0
   ```

---

## 5. OCR Ingestion Failures

### Symptom
- Uploading scanned PDFs or images completes with status `done` but `chunk_count` is 0.
- Log files contain `TesseractNotFoundError` or OCR processing timeouts.

### Diagnosis Steps
1. **Verify Tesseract installation path:** Check if the system can locate the Tesseract executable:
   ```powershell
   Get-Command tesseract
   ```
2. **Verify OCR service configuration:** Ensure `.env` points to the correct executable:
   ```env
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

### Recovery Procedure
1. **Configure OCR Executable Path:** If Tesseract is not in the system PATH, explicitly define the path in `.env`:
   ```env
   TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
   ```
2. **Restart Ingestion Workers:** Restart Celery to inherit the new environment variables:
   ```powershell
   Stop-Process -Name python -Force
   .venv\Scripts\python.exe run_services.py --worker --skip-infra
   ```
3. **Re-process failed documents:** Manually re-trigger ingestion for failed documents.
