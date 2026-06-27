# RAG System — Complete Windows Runner Guide

This document is a comprehensive, production-grade guide for setting up, configuring, initializing, and running the full Retrieval-Augmented Generation (RAG) system on Windows. It covers PostgreSQL version management, local standalone mode (no Docker required), multi-database configuration (relational + WSL2 graph database), service startup order, and Vite/React frontend launcher steps.

---

## 🏗️ System Architecture & Service Topology

| Component | Port | Technology | Description | Startup Command |
| :--- | :--- | :--- | :--- | :--- |
| **Relational DB** | `5434` | PostgreSQL 17 | Stores core entities, documents, query logs, evaluations, and cursors | Managed via Windows Service |
| **Graph DB** | `5434` | PostgreSQL 17 + Apache AGE (WSL2) | Hosts the graph ontology nodes/relationships for semantic Graph RAG | Started in WSL2 Ubuntu |
| **Cache & Queue** | `6379` | Redis | Handles session cache, result cache, and Celery task brokerage | Auto-started / Portable executable |
| **Auth Provider** | `8180` | Keycloak 26.5.0 | Provides OpenID Connect (OIDC) identity management | Auto-started / Portable executable |
| **Monolith API** | `8000` | FastAPI | Combined API gateway, ingestion, retrieval, and generation endpoints | `python run_services.py` |
| **Ingestion Worker** | — | Celery Ingestion Worker | Background PDF parsing, layout detection (Surya/PaddleOCR), and chunking | `python run_services.py --worker` |
| **Evaluation API** | `8005` | FastAPI | Dedicated evaluation backend for live judge and dashboard telemetry | `python run_services.py --evaluation` |
| **Vector DB** | — | Qdrant (Embedded) | Embedded vector store for semantic similarity. Files in `data/qdrant` | Run in-process (No server port) |
| **React Frontend** | `5173` | Vite + TypeScript + Tailwind | Dashboard interface for chat, upload, audit logs, and evaluations | `npm run dev` |

---

## 📋 System Prerequisites

Before running any commands, install the following on your Windows host:

1. **Python 3.11+** — check **"Add python.exe to PATH"** during installation
   - Download: https://www.python.org/downloads/windows/
2. **Node.js 18+ (LTS)** — used for the Vite dev server and frontend
   - Download: https://nodejs.org/
3. **PostgreSQL 17** — the project requires version 17 specifically (see Section below for removing older versions)
   - Download: https://www.postgresql.org/download/windows/
4. **Java Runtime Environment (JRE) 17+** — required for Keycloak
   - Download: https://adoptium.net/temurin/releases/
   - Ensure `JAVA_HOME` is set and `java` is in your PATH
5. **Microsoft Visual C++ Redistributable (x64, latest)** — required for PaddleOCR/PyTorch native extensions
   - Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
6. **(Optional) WSL2 with Ubuntu** — required ONLY for Apache AGE Graph RAG
   - Run `wsl --install` in PowerShell (as Administrator), then reboot

---

## 🗑️ Step 0: Stop & Remove Other PostgreSQL Versions

> [!IMPORTANT]
> This project requires **PostgreSQL 17**. Older versions (14, 15, 16) must be stopped (and ideally uninstalled) to avoid port conflicts and version mismatch errors.

### 0.1 Check What PostgreSQL Versions Are Installed

Open **Command Prompt (CMD)** as Administrator:

```cmd
:: List all PostgreSQL services
sc query type= all | findstr "postgresql"
```

Or check via Windows Services:
```cmd
services.msc
```

Look for entries named like:
- `postgresql-x64-14` (version 14 — must be stopped)
- `postgresql-x64-15` (version 15 — must be stopped)
- `postgresql-x64-16` (version 16 — must be stopped)
- `postgresql-x64-17` (version 17 — keep this one)

### 0.2 Stop All Non-17 PostgreSQL Versions

Run each of the following in **CMD as Administrator** for each old version you have:

```cmd
:: Stop PostgreSQL 14 (if installed)
net stop postgresql-x64-14

:: Stop PostgreSQL 15 (if installed)
net stop postgresql-x64-15

:: Stop PostgreSQL 16 (if installed)
net stop postgresql-x64-16

:: Disable auto-start for old versions so they don't restart on reboot
sc config postgresql-x64-14 start= disabled
sc config postgresql-x64-15 start= disabled
sc config postgresql-x64-16 start= disabled
```

> [!NOTE]
> If a service name doesn't exist, you'll see "The specified service does not exist" — that's fine, just continue.

### 0.3 Verify Only PostgreSQL 17 Is Running

```cmd
:: Check which PostgreSQL services are running
sc query postgresql-x64-14 2>nul | findstr "STATE"
sc query postgresql-x64-15 2>nul | findstr "STATE"
sc query postgresql-x64-16 2>nul | findstr "STATE"
sc query postgresql-x64-17 2>nul | findstr "STATE"
```

Expected output for version 17:
```
STATE              : 4  RUNNING
```

All other versions should show `STOPPED` or the service should not exist.

### 0.4 Uninstall Old PostgreSQL Versions (Recommended)

Old versions can be uninstalled via Windows Control Panel → Add or Remove Programs. Search for "PostgreSQL" and uninstall any version that is not 17.

---

## 💾 Step 1: Install PostgreSQL 17 via CMD

> [!TIP]
> If PostgreSQL 17 is already installed and running, skip to Step 2. Check with `psql --version` in CMD.

### 1.1 Download PostgreSQL 17 Installer

```cmd
:: Check if PostgreSQL 17 is already installed
psql --version
```

If the output shows `psql (PostgreSQL) 17.x`, PostgreSQL 17 is already installed. Skip to Step 1.4.

If not installed, download from:
- **URL:** https://www.postgresql.org/download/windows/
- **Direct installer link:** https://get.enterprisedb.com/postgresql/postgresql-17-windows-x64.exe

Or download via PowerShell:
```powershell
Invoke-WebRequest -Uri "https://get.enterprisedb.com/postgresql/postgresql-17-windows-x64.exe" -OutFile "$env:TEMP\pg17_installer.exe"
```

### 1.2 Install PostgreSQL 17 via CMD (Silent)

```cmd
:: Run installer silently via CMD (adjust path to where you downloaded it)
"%TEMP%\pg17_installer.exe" --mode unattended --unattendedmodeui minimal --superpassword "1234" --serverport 5434 --servicename "postgresql-x64-17"
```

> [!NOTE]
> The `--superpassword` sets the `postgres` user password. Replace `1234` with your preferred password and update `.env` accordingly. Port `5434` is used to avoid conflict with any existing PostgreSQL installation.

### 1.3 Add PostgreSQL 17 to PATH

```cmd
:: Add PostgreSQL 17 bin to PATH (run in CMD as Admin, adjust version if needed)
setx /M PATH "%PATH%;C:\Program Files\PostgreSQL\17\bin"
```

Then **close and reopen** your CMD window for the PATH change to take effect.

### 1.4 Verify PostgreSQL 17 Installation

```cmd
:: Check version
psql --version

:: Start the PostgreSQL 17 service (if not already running)
net start postgresql-x64-17

:: Check service status
sc query postgresql-x64-17 | findstr "STATE"
```

Expected:
```
psql (PostgreSQL) 17.x
STATE              : 4  RUNNING
```

---

## 🔧 Step 2: Configure the Python Virtual Environment

```powershell
:: Navigate to the project directory
cd "d:\Personal\Fixed Solutions\git files\Chatbot-Fixed-Team2"

:: Create the virtual environment
python -m venv .venv

:: Activate — PowerShell:
.venv\Scripts\Activate.ps1
:: Activate — CMD:
.venv\Scripts\activate.bat

:: Upgrade pip and install all dependencies
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

> [!NOTE]
> Installing dependencies may take 10–20 minutes as it downloads PyTorch CPU, PaddleOCR/PaddlePaddle, Surya, and other ML inference libraries (~2–3 GB total).

**✅ Verification:**
```powershell
python --version
pip show fastapi uvicorn sqlalchemy celery
```
You should see version numbers for all listed packages.

---

## ⚙️ Step 3: Set Up Environment Variables

```powershell
:: Copy the example template
copy .env.example .env
```

Open `.env` and configure the following **required** variables:

```ini
# PostgreSQL connection (port 5434 — matches this project's setup)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=1234        ← change to YOUR postgres password
POSTGRES_DB=domain_db
POSTGRES_PORT=5434
DATABASE_URL=postgresql+asyncpg://postgres:1234@localhost:5434/domain_db
SYNC_DATABASE_URL=postgresql://postgres:1234@localhost:5434/domain_db

# Groq API Key (get free key at https://console.groq.com)
GROQ_API_KEY=gsk_YOUR_KEY_HERE
```

**✅ Verification:**
```powershell
:: Check .env exists and is not empty
Get-Content .env | Select-Object -First 10
```

---

## 🗄️ Step 4: Create the Database & Run Migrations

### 4.1 Create the Application Database

```cmd
:: Set password for this session (replace 1234 with your password)
set PGPASSWORD=1234

:: Create the database
psql -h localhost -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
```

Expected output:
```
CREATE DATABASE
```

If you see `ERROR: database "domain_db" already exists`, that is fine — the database is already there.

**✅ Verification:**
```cmd
psql -h localhost -p 5434 -U postgres -l
```
You should see `domain_db` in the list of databases.

### 4.2 Run the Migration Script

The project includes a Python migration runner that intelligently detects whether Apache AGE is available and skips graph-specific commands if not.

```powershell
:: Make sure the virtual environment is active
.venv\Scripts\Activate.ps1

:: Run migration (creates all tables and seeds initial data)
python run_migration.py
```

Expected output:
```
Connecting to Relational database: localhost:5434/domain_db
  AGE extension available: False
  Relational migration complete (X statements executed).
```

> [!NOTE]
> `run_migration.py` uses smart schema parsing. It executes `migrations/setup_all.sql`. If Apache AGE is not detected (i.e., you haven't set up WSL2), it automatically skips graph ontology commands (`create_graph`, AGE-specific statements) and successfully initializes all relational schemas without failing.

**✅ Verification:**
```cmd
set PGPASSWORD=1234
psql -h localhost -p 5434 -U postgres -d domain_db -c "SELECT tablename FROM pg_tables WHERE schemaname='public';"
```
You should see tables: `users`, `domains`, `domain_configs`, `domain_roles`, `documents`, `document_chunks`, `rag_query_logs`.

### 4.3 Reset / Wipe Database State (Optional)

If you need to completely reset all data (files, query logs, chunks) back to a clean slate:

```powershell
python clear_database.py
```

Then re-run the migration to re-seed:

```powershell
python run_migration.py
```

---

## 🐧 Step 5: Start Ubuntu WSL2 (Required for Apache AGE Graph RAG)

> [!NOTE]
> This step is **only required** for the Graph RAG layer (Apache AGE). If you don't need graph-based retrieval, skip to Step 6. The rest of the system (vector search, BM25, LLM generation) works perfectly without WSL2.

### 5.1 Install WSL2 + Ubuntu (First Time Only)

Run in **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-22.04
```

Reboot your machine when prompted.

### 5.2 Run the Apache AGE Setup Script (First Time Only)

The `wsl2_setup_v2.sh` script installs PostgreSQL 17 + Apache AGE inside Ubuntu WSL2.

**Open Ubuntu WSL2:**
```powershell
wsl -d Ubuntu-22.04
```

**Inside the Ubuntu terminal:**
```bash
# Copy the setup script from the Windows project directory into WSL2
cp /mnt/d/Personal/Fixed\ Solutions/git\ files/Chatbot-Fixed-Team2/wsl2_setup_v2.sh ~/wsl2_setup.sh

# Make it executable and run
chmod +x ~/wsl2_setup.sh
~/wsl2_setup.sh
```

The script performs:
- Installs PostgreSQL 17 from official PGDG APT repository
- Clones and compiles Apache AGE (`PG17/v1.6.0-rc0`) from source
- Configures PostgreSQL to listen on port `5434` (avoids conflict with Windows PostgreSQL)
- Sets the `postgres` user password to `55555`
- Initializes the `rag_graph` in Apache AGE

### 5.3 Start Ubuntu (Every Time After Reboot)

Apache AGE runs inside WSL2. Before connecting from Windows, ensure Ubuntu is running:

```powershell
:: Start Ubuntu WSL2 in background
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"

:: Wait a few seconds, then verify it is running
wsl -l -v
```

Expected:
```
  NAME            STATE           VERSION
* Ubuntu-22.04    Running         2
```

**✅ Verification — Check WSL2 PostgreSQL is reachable from Windows:**
```cmd
set PGPASSWORD=1234
psql -h localhost -p 5434 -U postgres -c "SELECT version();"
```

Expected output contains: `PostgreSQL 17.x`

> [!TIP]
> PostgreSQL inside WSL2 is configured to start automatically via systemd when the Ubuntu instance starts. You only need to keep the Ubuntu instance running (the `tail -f /dev/null` command keeps it alive in background).

---

## 🏃 Step 6: Run the Backend Services

The project provides a Python orchestrator `run_services.py` that launches all infrastructure dependencies and Python processes.

### 6.1 Start All Services

```powershell
:: Ensure virtual environment is active
.venv\Scripts\Activate.ps1

:: Start all backend services (APIs + infra auto-download)
python run_services.py
```

**With document processing (Celery Worker + OCR):**
```powershell
python run_services.py --worker
```

**With evaluation service:**
```powershell
python run_services.py --worker --evaluation
```

### 6.2 Orchestrator Behavior

When `run_services.py` executes, it automatically:

1. **Downloads Redis** (first run) → portable Redis to `tools/redis/`, starts on port `6379`
2. **Downloads Keycloak 26.5.0** (first run) → extracted to `tools/keycloak/`, starts on port `8180`
3. **Graceful Degradation:**
   - If Java is missing → triggers a **dev JWT mock provider** (`scripts/dev_auth.py`)
   - If Redis fails → degrades to in-memory caching + synchronous ingestion
4. **Flushes stale Celery queues** — removes leftover tasks from previous sessions
5. **Sets ML environment variables:**
   - `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` — forces local/offline model loading
   - `CUDA_VISIBLE_DEVICES=""` — enforces CPU-only inference mode
   - `KMP_DUPLICATE_LIB_OK=TRUE` — prevents OpenMP DLL conflicts on Windows
   - `PADDLE_PDX_MODEL_SOURCE=BOS` — uses Baidu storage mirrors for OCR model downloads
6. **Starts all services in order:** domain-service (8001) → ingestion-service (8002) → retrieval-service (8003) → generation-service (8004) → [worker] → [evaluation 8005]

### 6.3 Launcher Arguments Reference

```powershell
python run_services.py                 # APIs + infra only (no document worker)
python run_services.py --worker        # also start Celery ingestion worker (required for OCR)
python run_services.py --evaluation    # also start evaluation-service on port 8005
python run_services.py --no-reload     # disable Uvicorn auto-reload (production-like)
python run_services.py --skip-infra    # skip Redis/Keycloak download (if already running externally)
```

**✅ Verification — Check all services are up:**
```powershell
:: Check service ports are listening
netstat -ano | findstr "LISTENING" | findstr ":8001 :8002 :8003 :8004 :6379 :8180"
```

```powershell
:: Check health endpoints
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8003/health
Invoke-RestMethod http://localhost:8004/generate/health
```

Each should return `{"status": "ok"}` or similar.

---

## 🎨 Step 7: Run the React Frontend

The React UI (`rag-ui/`) must be started in a **separate** terminal window.

```powershell
:: Navigate to frontend directory
cd rag-ui

:: Install dependencies (first time only)
npm install

:: Start the development server
npm run dev
```

**✅ Verification:**
Open your browser at: **http://localhost:5173**

You should see the login page.

### Sign In Options

**Dev Auth Mode (Recommended for local development):**
The frontend detects when Keycloak is bypassed. Use the Quick Access panel to log in as:

| User | User ID | Role |
|---|---|---|
| System Admin | `admin` | system_admin |
| Domain Manager | `manager` | domain_admin |
| Contributor | `contributor` | contributor |
| Viewer/Reader | `viewer` | reader |

**Keycloak Mode (Production):**
Click **Sign In with Keycloak** — redirects to `http://localhost:8180`. Default credentials: `admin` / `admin`.

---

## 🛠️ Step 8: Full System Verification

### 8.1 Service Health Endpoints

| Service | Health URL | Expected Response |
|---|---|---|
| Core API | http://localhost:8000/api/v1/domains/monitoring/health | `{"status": "ok"}` |
| Swagger UI (Domain) | http://localhost:8001/docs | Swagger page loads |
| Swagger UI (Ingestion) | http://localhost:8002/docs | Swagger page loads |
| Swagger UI (Retrieval) | http://localhost:8003/docs | Swagger page loads |
| Swagger UI (Generation) | http://localhost:8004/docs | Swagger page loads |
| Evaluation Swagger | http://localhost:8005/docs | Swagger page loads |
| Keycloak | http://localhost:8180/realms/rag-system | JSON realm config |

### 8.2 End-to-End Test

```powershell
:: Get a dev auth token
$token = (Invoke-RestMethod -Uri http://localhost:8001/domains/auth/login -Method POST -ContentType "application/json" -Body '{"user_id":"admin"}').token

:: Check token exists
Write-Host "Token acquired: $($token.Substring(0, 30))..."

:: Create a test domain
$domain = (Invoke-RestMethod -Uri http://localhost:8001/domains -Method POST -Headers @{"Authorization"="Bearer $token"} -ContentType "application/json" -Body '{"name":"TestDomain","description":"Quick test"}')
Write-Host "Domain created: $($domain.id)"
```

---

## 🛑 Stopping the Application

1. In the terminal running `run_services.py`, press **`Ctrl + C`** — the orchestrator captures the interrupt and gracefully terminates all spawned sub-processes (Uvicorn servers, Celery workers, Redis, and Keycloak).
2. In the frontend terminal, press **`Ctrl + C`** to stop the Vite server.

To stop individual PostgreSQL services:

```cmd
:: Stop Windows PostgreSQL 17
net stop postgresql-x64-17

:: Stop WSL2 Ubuntu (and therefore WSL2 PostgreSQL)
wsl --terminate Ubuntu-22.04
```

---

## 🔍 Troubleshooting

### 1. Celery Worker Fails to Start (billiard / multiprocessing errors)

**Symptom:** Celery crashes with billiard errors or multiprocessing failures.

**Solution:** Celery does not officially support the default forking pool on Windows. The orchestrator automatically appends `--pool=solo`. If launching manually:
```powershell
python -m celery -A worker worker --loglevel=info -Q ingestion --pool=solo
```

### 2. OpenMP Library Crash (`KMP_DUPLICATE_LIB_OK`)

**Symptom:** Python crashes with "multiple copies of the OpenMP runtime (libiomp5md.dll)" error.

**Solution:** Set in your shell before running:
```powershell
$env:KMP_DUPLICATE_LIB_OK="TRUE"
```

### 3. PostgreSQL Connection Refused on Port 5434

**Symptom:** Backend logs show `connection refused` or `FATAL: password authentication failed`.

**Solution — Windows PostgreSQL:**
```cmd
:: Start the Windows PostgreSQL 17 service
net start postgresql-x64-17

:: Verify it's running
sc query postgresql-x64-17 | findstr "STATE"
```

**Solution — WSL2 PostgreSQL:**
```powershell
:: Ensure WSL2 Ubuntu is running
wsl -l -v

:: If stopped, start it
start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
```

Then verify password matches your `.env`'s `POSTGRES_PASSWORD` / `AGE_DATABASE_DSN`.

### 4. Port Already In Use

```powershell
:: Find what process is using a port
netstat -ano | findstr ":5434"
netstat -ano | findstr ":8001"

:: Kill the process by PID
taskkill /PID <pid> /F
```

### 5. Out-of-Memory (OOM) / Paging File Exhaustion

**Symptom:** Services fail to initialize or Python crashes with memory errors.

**Solution:** RAG embeddings, GLiNER, and OCR models all load into RAM (~3–5 GB total). Ensure:
- At least 8 GB RAM free (16 GB recommended)
- Windows paging file set to system-managed
- At least 10 GB free disk space
- Heavy background apps (Docker, multiple IDEs) are closed

### 6. Offline Model Download Issues

**Symptom:** Backend fails with `ConnectionError` pointing to Hugging Face on startup.

**Solution:** On **first run**, temporarily allow online downloads:
```ini
# In .env — temporarily set to 0 for first run
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```
Run services once to download and cache models, then restore to `1`.

### 7. OCR DLL Load Failed (`libpaddle` error)

**Solution:** Install Microsoft Visual C++ Redistributable (x64):
- URL: https://aka.ms/vs/17/release/vc_redist.x64.exe
- Restart terminal after installing.

### 8. Unicode Errors in Worker Output

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8=1
chcp 65001
```

### 9. First Query Very Slow

**Expected behavior.** The retrieval service loads embedding and reranker models on first request (~10–30 seconds). Subsequent queries are fast. Identical repeat queries are cached instantly via Redis.

### 10. WSL2 PostgreSQL Not Reachable (`localhost:5434`)

Inside WSL2 Ubuntu terminal:
```bash
# Check PostgreSQL status
sudo service postgresql status

# Restart if needed
sudo service postgresql restart

# Verify listen_addresses
sudo grep listen_addresses /etc/postgresql/17/main/postgresql.conf

# Verify pg_hba.conf has Windows host rule
sudo grep "0.0.0.0" /etc/postgresql/17/main/pg_hba.conf
```

---

## 📋 Quick Reference Card

```text
═══════════════════════════════════════════════════════════
  RAG System — Quick Reference
═══════════════════════════════════════════════════════════

DATABASE SETUP (PostgreSQL 17 on port 5434):
  net stop postgresql-x64-16              # stop old versions
  net start postgresql-x64-17             # ensure PG17 runs
  psql -p 5434 -U postgres -c "CREATE DATABASE domain_db;"
  python run_migration.py                  # create tables + seed

WSL2 / APACHE AGE (optional — Graph RAG only):
  start "" wsl -d Ubuntu-22.04 -- bash -c "tail -f /dev/null"
  psql -h localhost -p 5434 -U postgres   # verify connection

BACKEND (from project root):
  .venv\Scripts\Activate.ps1
  python run_services.py                   # APIs only
  python run_services.py --worker          # APIs + OCR worker
  python run_services.py --worker --evaluation  # full stack

FRONTEND (new terminal):
  cd rag-ui && npm install && npm run dev

HEALTH CHECKS:
  http://localhost:8001/docs               # Domain service
  http://localhost:8002/docs               # Ingestion service
  http://localhost:8003/docs               # Retrieval service
  http://localhost:8004/docs               # Generation service
  http://localhost:8005/docs               # Evaluation service
  http://localhost:5173                    # React UI

STOP:
  Ctrl+C (in run_services.py terminal)
  Ctrl+C (in rag-ui terminal)
  net stop postgresql-x64-17              # stop Windows PG17
  wsl --terminate Ubuntu-22.04            # stop WSL2

DEFAULT USERS (Dev Auth):
  admin        → system_admin
  manager      → domain_admin
  contributor  → contributor
  viewer       → reader
═══════════════════════════════════════════════════════════
```
