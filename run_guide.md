# RAG System — Windows Runner Guide

This document is a comprehensive, production-grade guide for setting up, configuring, initializing, and running the full Retrieval-Augmented Generation (RAG) system on Windows. It covers local standalone mode (no Docker required for backend services), multi-database configuration (relational database + WSL2 graph database), services startup order, and Vite/React frontend launcher steps.

---

## 🏗️ System Architecture & Service Topology

The RAG application consists of a React frontend and a set of backend microservices launched in a unified or standalone process architecture. Here is the topology of all components:

| Component | Port | Backend Service / Technology | Description | Startup Launcher Command |
| :--- | :--- | :--- | :--- | :--- |
| **Relational DB** | `5432` | PostgreSQL 17 | Stores core entities, documents, query logs, evaluations, and cursors. | Managed via Windows Service |
| **Graph DB (Optional)** | `5434` | PostgreSQL 17 + Apache AGE (WSL2) | Hosts the graph ontology nodes/relationships for semantic Graph RAG. | Started in WSL2 Ubuntu |
| **Cache & Queue** | `6379` | Redis | Handles session cache, result cache, and Celery task brokerage. | Auto-started / Portable executable |
| **Auth Provider** | `8180` | Keycloak 26.2 | Provides OpenID Connect (OIDC) identity management. | Auto-started / Portable executable |
| **Monolith API** | `8000` | FastAPI | Combined API gateway, ingestion, retrieval, and generation endpoints. | `python -m uvicorn main:app` |
| **Ingestion Worker** | — | Celery Ingestion Worker | Background PDF parsing, layout detection (Surya/PaddleOCR), and chunking. | `celery -A worker worker --pool=solo` |
| **Evaluation API** | `8005` | FastAPI | Dedicated evaluation backend for live judge and dashboard telemetry. | `python -m uvicorn main:app` |
| **Evaluation Worker** | — | Celery Evaluation Worker | Background batch evaluation processing (custom judge + RAGAS pipelines). | `celery -A celery_app worker --pool=solo` |
| **Evaluation Scheduler** | — | Celery Beat | Triggers periodic batch evaluations every 30 minutes. | `celery -A celery_app beat` |
| **Vector DB** | — | Qdrant (Embedded) | Embedded vector store for semantic similarity. Files stored in `data/qdrant`. | Run in-process (No server port) |
| **React Frontend** | `5173` | Vite + TypeScript + Tailwind | Clean dashboard interface for chat, upload, audit logs, and evaluations. | `npm run dev` |

---

## 📋 System Prerequisites

Before running the launcher script, make sure the following applications are installed on your Windows host:

1. **Python 3.11+**
   * Download the installer from the [official Python website](https://www.python.org/downloads/windows/).
   * **Important:** During installation, ensure you check **"Add python.exe to PATH"**.
2. **Node.js 18+ (LTS)**
   * Download the MSI installer from the [Node.js website](https://nodejs.org/).
   * Used to run the Vite dev server and build frontend assets.
3. **PostgreSQL 17**
   * Download the installer from the [EnterpriseDB Windows installer page](https://www.postgresql.org/download/windows/).
   * During the installation wizard:
     * Keep the default port as `5432`.
     * Set the password for the default `postgres` user (e.g., `1234` or custom). Remember this password, as it will be used in the `.env` configuration.
4. **Java Runtime Environment (JRE) 17+**
   * Keycloak is a Java-based application. To run the portable Keycloak package locally without Docker, install the OpenJDK 17 LTS package (e.g., [Eclipse Temurin](https://adoptium.net/temurin/releases/)).
   * Ensure that the `JAVA_HOME` environment variable is set and the `java` executable is available in your PATH.
5. **(Optional) WSL2 with Ubuntu**
   * Required ONLY if you want to enable Apache AGE Graph-based RAG.
   * Install WSL2 by running `wsl --install` in PowerShell, then reboot.

---

## 🚀 Step-by-Step Installation

### Step 1: Clone or Open the Project
Open your terminal (PowerShell is recommended) and navigate to the project directory:
```powershell
cd "d:\Personal\Fixed Solutions\Project Files\v5"
```

### Step 2: Configure the Python Virtual Environment
Creating a virtual environment ensures that the packages do not conflict with system-wide python installations:

```powershell
# Create the virtual environment
python -m venv .venv

# Activate the virtual environment
# In PowerShell:
.venv\Scripts\Activate.ps1
# In Command Prompt (CMD):
.venv\Scripts\activate.bat

# Upgrade pip and install package dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Set Up Environment Variables
1. Duplicate `.env.example` in the project root and name it `.env`.
2. Open `.env` and verify/edit the following key variables:

```ini
# --- Relational Database Connection (Port 5432) ---
# Replace '1234' with the password you set during PostgreSQL installation.
POSTGRES_USER=postgres
POSTGRES_PASSWORD=1234
POSTGRES_DB=domain_db
DATABASE_URL=postgresql+asyncpg://postgres:1234@localhost:5432/domain_db
SYNC_DATABASE_URL=postgresql://postgres:1234@localhost:5432/domain_db

# --- Groq API Key (Cloud LLM) ---
# Required for judge LLM evaluation and question-answering generation.
# Obtain a key from https://console.groq.com/
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# --- Hugging Face Cache Redirection (Optional) ---
# Machine learning models (embeddings, GLiNER NER, Rerankers) take up ~5GB.
# Redirect cache from C: to D: (or another drive) by uncommenting and editing:
HF_HOME=D:\huggingface_cache

# --- Graph Database Connection DSN (Port 5434 in WSL2) ---
# Required only if you use Graph RAG. Note that wsl2_setup_v2.sh sets the password to '55555'.
AGE_DATABASE_DSN=postgresql://postgres:55555@localhost:5434/domain_db
AGE_GRAPH_NAME=rag_graph
```

---

## 🗄️ Database Initialization & Management

The backend supports two database utilities to initialize and clear database tables. Both of these scripts parse your `.env` configuration and execute SQL files under the `migrations/` folder.

### 1. Initialize Tables & Seed Default Data
To build database tables for the relational and graph schemas and insert seed records:
```powershell
python run_migration.py
```
> [!NOTE]
> `run_migration.py` is designed with smart schema parsing. It executes `migrations/setup_all.sql`. If Apache AGE is not detected on your PostgreSQL host, it automatically skips graph ontology commands (e.g., `create_graph`) and successfully initializes relational schemas (domain configs, query logs, evaluation metrics) without failing.

### 2. Reset / Wipe Database State
If you need to discard all loaded files, query logs, evaluation queues, and reset the databases back to a completely clean slate:
```powershell
python clear_database.py
```
After wiping the tables using `clear_database.py`, run the migration script again to re-seed initial schemas:
```powershell
python run_migration.py
```

---

## 🐧 Apache AGE Graph DB Setup (WSL2)

If you wish to use semantic Graph RAG, you must configure Apache AGE on a separate PostgreSQL 17 server running inside WSL2 on port `5434`.

1. Open your WSL2 terminal (Ubuntu).
2. Ensure you have the `wsl2_setup_v2.sh` script inside your WSL2 environment, or locate it in the windows project root (usually mounted at `/mnt/d/Personal/Fixed Solutions/Project Files/v5/wsl2_setup_v2.sh`).
3. Run the setup script with root privileges:
   ```bash
   chmod +x wsl2_setup_v2.sh
   ./wsl2_setup_v2.sh
   ```
   This script performs the following actions:
   * Installs PostgreSQL 17 packages and build utilities in Ubuntu.
   * Clones and compiles Apache AGE (`PG17/v1.6.0-rc0` tag) from source.
   * Configures Postgres to listen on port `5434` (to avoid conflicting with your Windows Postgres on port 5432).
   * Modifies `postgresql.conf` to load the `age` shared library and configures `pg_hba.conf` to accept connections originating from the Windows host virtual network adapter.
   * Sets the password for the `postgres` user inside WSL2 to `55555`.
   * Initializes the `rag_graph` in Apache AGE.
4. **Verification:** Test the connection from your Windows host (via PowerShell) to verify Windows services can reach the WSL2 database:
   ```powershell
   psql -h localhost -p 5434 -U postgres -c "SELECT version();"
   # Enter password: 55555 when prompted
   ```

---

## 🏃 Running the Backend Services

The project provides a single python orchestrator, `run_services.py`, located at the root of the project. It launches all infrastructure dependencies (portable Redis and Keycloak) and starts all python processes.

To start the entire backend service stack:
```powershell
# Ensure virtual environment is active
.venv\Scripts\Activate.ps1

# Run the services orchestrator
python run_services.py
```

### 🔍 Orchestrator Behavior & Automations
When `python run_services.py` executes, it performs several automations in the background:
1. **Infrastructure Auto-Provisioning:** It checks if Redis and Keycloak are running on their assigned ports. If they are missing:
   * It downloads portable Redis (GitHub Release zip) and Keycloak (v26.5.0 zip) to `tools/redis` and `tools/keycloak` in the workspace root.
   * It extracts the zip files and starts the local processes inside a new process group.
   * *Graceful Degradation:* If Java is missing or Keycloak fails to launch, the system automatically triggers a **dev JWT mock provider** (defined in `scripts/dev_auth.py`). If Redis fails to launch, the services degrade to in-memory caching and synchronous ingestion.
2. **Queue and Cache Purging:** It automatically issues a `flushdb` command to Redis DB 0 to clear dirty cache. It also purges the Celery `ingestion` queue so that any stale file processing tasks from previous sessions do not clutter your run.
3. **Environment Injection:** It sets essential ML optimizations:
   * Sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` to guarantee local/offline model loading.
   * Sets `CUDA_VISIBLE_DEVICES=""` to enforce lightweight CPU inference mode.
   * Injecting `KMP_DUPLICATE_LIB_OK=TRUE` to prevent OpenMP runtime crashes on Windows.
   * Injecting `PADDLE_PDX_MODEL_SOURCE=BOS` to bypass Hugging Face and use Baidu storage mirrors for reliable OCR weights download.
4. **Process Launching:** It starts the Uvicorn monolith gateway API on port `8000`, the Celery ingestion worker, the evaluation service API on port `8005`, the Celery evaluation worker, and the Celery Beat scheduler.

### ⚙️ Launcher Arguments Reference
You can pass flags to `run_services.py` to customize the execution:
* `python run_services.py --skip-infra` — Skips downloading or initiating Redis and Keycloak (useful if you are already running them in Docker containers or as manual Windows services).
* `python run_services.py --no-reload` — Disables the Uvicorn reload loop (saves memory and avoids file-watching overhead in production environments).

---

## 🎨 Running the React Frontend

The React client UI (`rag-ui`) is built on Vite, React, TypeScript, and TailwindCSS. It must be started in a separate terminal window.

```powershell
# Open a new terminal and navigate to the frontend directory
cd "d:\Personal\Fixed Solutions\Project Files\v5\rag-ui"

# Install package dependencies
npm install

# Run the development server
npm run dev
```

The Vite console will output the local server URL:
👉 **Local Web Interface URL:** `http://localhost:5173`

### 🔑 Sign In Instructions
When the browser loads the Login Page, the frontend detects whether OIDC is active. 

* **Dev Auth Mode (Recommended for Local Dev):**
  If Keycloak was bypassed or is not running, you can use the **Quick Access** panel on the login page.
  Click on any of the preloaded users to auto-fill their credentials:
  * **System Admin:** `admin` (or UUID: `652ec45e-1b68-478c-9bd3-81cc46fb24a9`)
  * **Domain Manager:** `manager`
  * **Regular Contributor:** `contributor` (or username: `contributor_test`)
  * **Reader/Viewer:** `viewer` (or username: `reader1`)
  Click **Sign In** to log into the web dashboard.
* **Keycloak Mode:**
  If Keycloak is active, click **Sign In with Keycloak (OIDC)**. You will be redirected to Keycloak's login interface at `http://localhost:8180`. Use the default seed admin credentials:
  * **Username:** `admin`
  * **Password:** `admin`

---

## 🛠️ Verification & Health Checks

Once the backend and frontend are running, you can verify service health by visiting these Swagger API endpoints in your browser:

* **Core RAG Service Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **Evaluation Service Swagger UI:** [http://localhost:8005/docs](http://localhost:8005/docs)
* **Verify System Health Endpoint:** [http://localhost:8000/api/v1/domains/monitoring/health](http://localhost:8000/api/v1/domains/monitoring/health)

---

## 🛑 Stopping the Application

To shut down all running background processes:
1. Go to the terminal window running `run_services.py` and press `Ctrl + C`.
2. The orchestrator script will capture the keyboard interrupt and run its cleanup hooks, gracefully terminating all spawned sub-processes (Uvicorn servers, Celery workers, Redis, and Keycloak).
3. In the frontend terminal, press `Ctrl + C` to stop the Vite development server.

---

## 🔍 Troubleshooting

### 1. Celery Worker Fails to Start or Crashes on Windows
* **Symptom:** Celery starts but crashes immediately with billiard errors or multiprocessing failures.
* **Solution:** Celery does not officially support the default spawn/forking pool on Windows. The orchestrator automatically appends `--pool=solo` to Celery command-lines when executing on Windows. If launching Celery manually, make sure you add `--pool=solo` to the command line:
  ```powershell
  python -m celery -A worker worker --loglevel=info -Q ingestion --pool=solo
  ```

### 2. OpenMP Library Initialization Crash
* **Symptom:** Python crashes with an error stating that multiple copies of the OpenMP runtime (libiomp5md.dll) have been loaded.
* **Solution:** The launcher script injects `KMP_DUPLICATE_LIB_OK=TRUE` into the environment. If running standalone python scripts, ensure this environment variable is set in your shell:
  ```powershell
  $env:KMP_DUPLICATE_LIB_OK="TRUE"
  ```

### 3. Out-of-Memory (OOM) or Paging File Exhaustion
* **Symptom:** Microservices fail to initialize, or Python crashes with memory allocation errors when parsing heavy documents.
* **Solution:** RAG embeddings and GLiNER models load into RAM. Ensure your Windows paging file size is set to system-managed, and at least 8GB of free disk space is available. You can also specify the maximum threads permitted by setting thread limit variables in your `.env` (these are default set to 1 or 2 by the launcher to prevent cores fighting for memory).

### 4. Offline Model Downloading Issues
* **Symptom:** Backend fails with a `ConnectionError` pointing to Hugging Face or Baidu Model Store during start.
* **Solution:** The launcher sets `HF_HUB_OFFLINE=1` to ensure network requests do not stall during inference. On your *first* run, you must download the models while online. You can temporarily edit `.env` and set:
  ```ini
  HF_HUB_OFFLINE=0
  TRANSFORMERS_OFFLINE=0
  ```
  Run the services once to let Hugging Face fetch and cache the models (like GLiNER and embeddings) to your local disk, then restore the offline flags to `1` for subsequent runs.

### 5. PostgreSQL Port 5432 Connection Refused
* **Symptom:** Monolith backend log shows `connection refused` or `FATAL: password authentication failed` for PostgreSQL database.
* **Solution:** Check if the Windows PostgreSQL Service is running:
  1. Open Windows **Services** app (`services.msc`).
  2. Locate `postgresql-x64-17`.
  3. Right-click and select **Start** or **Restart**.
  4. Ensure your password inside `.env` matches the password configured during installation.
